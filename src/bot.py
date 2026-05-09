import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

import datetime
import random
from discord.ext import tasks

from db import init_db, Player, Kingdom, Sovereign, ActionQueue, generate_kingdom_coordinates
from ai import classify_action, generate_immediate_feedback, resolve_action
from vector_db import init_chroma, insert_history, query_history

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))

class ThroneboundBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.SessionLocal = init_db()
        self.chroma_collection = init_chroma()
        self.synced = False

    async def setup_hook(self):
        if not self.synced:
            await self.tree.sync()
            self.synced = True
            print("Command tree synced.")
        self.game_loop.start()
        self.daily_reset.start()

    @tasks.loop(time=datetime.time(hour=3, minute=0, tzinfo=datetime.timezone.utc))
    async def daily_reset(self):
        """
        Resets remaining actions to 5 every midnight (Brasília time / 03:00 UTC).
        """
        await self.wait_until_ready()
        with self.SessionLocal() as db:
            db.query(Kingdom).filter_by(is_active=True).update({"acoes_restantes": 5})
            db.commit()
            print("Ações diárias resetadas com sucesso para todos os reinos.")

    @tasks.loop(minutes=1)
    async def game_loop(self):
        """
        Background task that runs every minute to process delayed actions,
        handle daily aging, and check for Game Over conditions.
        """
        await self.wait_until_ready()

        with self.SessionLocal() as db:
            now = datetime.datetime.utcnow()

            # 1. Process Action Queue
            pending_actions = db.query(ActionQueue).filter(
                ActionQueue.status == "pending",
                ActionQueue.resolve_at <= now
            ).all()

            for action in pending_actions:
                kingdom = db.query(Kingdom).filter_by(id=action.kingdom_id).first()
                if not kingdom or not kingdom.is_active:
                    action.status = "resolved"
                    continue

                sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
                if not sov:
                    action.status = "resolved"
                    continue

                # Process via AI
                status_dict = {
                    "gold": kingdom.gold,
                    "army": kingdom.army,
                    "influence": kingdom.influence
                }

                # Retrieve RAG History
                history = query_history(self.chroma_collection, kingdom.id, action.action_text)

                resolution = await resolve_action(status_dict, action.action_text, context_history=history)
                narrative = resolution.get("narrativa", "O tempo passa, mas nada muda.")
                db_updates = resolution.get("atualizacao_db", {})

                # Apply DB Updates, guarding against negative values if possible
                kingdom.gold = max(0, kingdom.gold + db_updates.get("ouro", 0))
                kingdom.army = max(0, kingdom.army + db_updates.get("exercito", 0))
                kingdom.influence = max(0, kingdom.influence + db_updates.get("influencia", 0))

                if db_updates.get("soberano_morto", False):
                    sov.is_alive = False

                action.status = "resolved"
                db.commit()

                # Save to Chroma DB
                history_record = f"Soberano ordenou (Ação Demorada): '{action.action_text}'. Consequência: '{narrative}'"
                insert_history(self.chroma_collection, kingdom.id, history_record)

                # Send result to the channel
                channel = self.get_channel(kingdom.channel_id)
                if channel:
                    # Assuming Discord has fetched the channel, we send the narrative
                    await channel.send(f"📜 **Relatório do Corvo:**\n{narrative}")

            # 2. Game Over / Succession Logic
            # We fetch all active kingdoms to check for deaths that happened during AI Resolution or Age Check
            active_kingdoms = db.query(Kingdom).filter_by(is_active=True).all()

            for kingdom in active_kingdoms:
                # Check the most recent sovereign for this kingdom
                latest_sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id).order_by(Sovereign.id.desc()).first()

                if not latest_sov:
                    continue

                if not latest_sov.is_alive:
                    channel = self.get_channel(kingdom.channel_id)

                    if not latest_sov.designated_heir_name:
                        kingdom.is_active = False
                        db.commit()
                        if channel:
                            await channel.send("💀 **Seu soberano morreu sem deixar herdeiros! O reino caiu em ruínas. Game Over.**")
                    else:
                        # Heir takes over
                        if channel:
                            await channel.send(f"👑 **O rei está morto! Longa vida ao rei! O herdeiro {latest_sov.designated_heir_name} assume o trono.**")

                        new_sov = Sovereign(
                            kingdom_id=kingdom.id,
                            name=latest_sov.designated_heir_name,
                        age=latest_sov.designated_heir_age if latest_sov.designated_heir_age is not None else 20
                        )
                        db.add(new_sov)
                        db.commit()

bot = ThroneboundBot()

# Setup standard government types
GOVERNMENT_TYPES = [
    "Beilhique", "Despotado", "Domínio", "Ducado", "Emirado", "Império",
    "Horda", "Grão-Ducado", "Heptarquia", "Caganato", "Canato", "Reino",
    "Marcas", "Principado", "Satrapia", "Xogunato", "Sultanato", "Czarado", "Ulus"
]

class FundarNacaoModal(discord.ui.Modal, title='Fundar Nação'):
    kingdom_name = discord.ui.TextInput(label='Nome do Reino', required=True)
    sovereign_name = discord.ui.TextInput(label='Nome do Soberano', required=True)
    government_type = discord.ui.TextInput(
        label='Tipo de Governo',
        required=True,
        placeholder='Ex: Reino, Império, Ducado...'
    )
    build_type = discord.ui.TextInput(
        label='Foco Inicial',
        required=True,
        placeholder='Militar, Mercantil ou Diplomática'
    )

    async def on_submit(self, interaction: discord.Interaction):
        gov_type = self.government_type.value.strip().title()
        build = self.build_type.value.strip().capitalize()

        if gov_type not in GOVERNMENT_TYPES:
            await interaction.response.send_message(
                f"Tipo de governo inválido! Escolha um destes: {', '.join(GOVERNMENT_TYPES)}",
                ephemeral=True
            )
            return

        if build not in ["Militar", "Mercantil", "Diplomática"]:
            await interaction.response.send_message(
                "Foco inicial inválido! Escolha: Militar, Mercantil ou Diplomática",
                ephemeral=True
            )
            return

        with bot.SessionLocal() as db:
            player_id = interaction.user.id

            # Check if already has active kingdom
            existing_kingdom = db.query(Kingdom).filter_by(player_id=player_id, is_active=True).first()
            if existing_kingdom:
                await interaction.response.send_message("Você já possui uma nação ativa!", ephemeral=True)
                return

            # Ensure player exists
            player = db.query(Player).filter_by(discord_id=player_id).first()
            if not player:
                player = Player(discord_id=player_id)
                db.add(player)
                db.commit()

            # Create Kingdom
            gold, army, influence = 1000, 100, 50
            if build == "Militar":
                army += 50
            elif build == "Mercantil":
                gold += 500
            elif build == "Diplomática":
                influence += 30

            pos_x, pos_y = generate_kingdom_coordinates()

            kingdom = Kingdom(
                player_id=player_id,
                name=self.kingdom_name.value.strip(),
                government_type=gov_type,
                build_type=build,
                gold=gold,
                army=army,
                influence=influence,
                pos_x=pos_x,
                pos_y=pos_y
            )
            db.add(kingdom)
            db.commit()

            # Create Sovereign
            sovereign = Sovereign(
                kingdom_id=kingdom.id,
                name=self.sovereign_name.value.strip(),
                age=20
            )
            db.add(sovereign)
            db.commit()

            kingdom_id = kingdom.id
            kingdom_name = kingdom.name

        # Create discord channel
        category = discord.utils.get(interaction.guild.categories, name="Reinos Ativos")
        if not category:
            category = await interaction.guild.create_category("Reinos Ativos")

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Assuming admin role might be added later, can adjust overwrites as needed.
        channel_name = f"reino-{kingdom_name.lower().replace(' ', '-')}"
        channel = await interaction.guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites
        )

        with bot.SessionLocal() as db:
            kingdom = db.query(Kingdom).get(kingdom_id)
            kingdom.channel_id = channel.id
            db.commit()

        await interaction.response.send_message(
            f"Nação fundada com sucesso! Seu reino está em {channel.mention}.",
            ephemeral=True
        )


@bot.tree.command(name="fundar_nacao", description="Funde sua nação para começar a jogar.")
async def fundar_nacao(interaction: discord.Interaction):
    with bot.SessionLocal() as db:
        existing = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()
        if existing:
            await interaction.response.send_message("Você já fundou sua nação!", ephemeral=True)
            return
    await interaction.response.send_modal(FundarNacaoModal())


@bot.tree.command(name="status", description="Veja o status da sua nação.")
async def status(interaction: discord.Interaction):
    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()

        if not kingdom:
            await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
            return

        sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()

        embed = discord.Embed(title=f"{kingdom.government_type} de {kingdom.name}", color=discord.Color.gold())
        embed.add_field(name="Ouro", value=kingdom.gold)
        embed.add_field(name="Exército", value=kingdom.army)
        embed.add_field(name="Influência", value=kingdom.influence)

        if sov:
            embed.add_field(name="Soberano", value=f"{sov.name} ({sov.age} anos)", inline=False)
            heir = sov.designated_heir_name or "Nenhum"
            embed.add_field(name="Herdeiro", value=heir, inline=False)
        else:
            embed.add_field(name="Soberano", value="Morto", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="nomear_herdeiro", description="Designe o seu sucessor.")
async def nomear_herdeiro(interaction: discord.Interaction, nome: str):
    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()

        if not kingdom:
            await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
            return

        sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
        if not sov:
            await interaction.response.send_message("Seu soberano está morto, você não pode nomear herdeiros.", ephemeral=True)
            return

        sov.designated_heir_name = nome
        sov.designated_heir_age = 15 # Start heir age at 15
        db.commit()
        await interaction.response.send_message(f"Herdeiro designado com sucesso: **{nome}** (15 anos).", ephemeral=True)


# Admin Commands
@bot.tree.command(name="admin_reset_nacao", description="[ADMIN] Reseta a nação de um jogador.")
@commands.has_permissions(administrator=True)
async def admin_reset_nacao(interaction: discord.Interaction, membro: discord.Member):
    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(player_id=membro.id, is_active=True).first()
        if not kingdom:
            await interaction.response.send_message("Este jogador não possui uma nação ativa.", ephemeral=True)
            return

        kingdom.is_active = False
        db.commit()
        await interaction.response.send_message(f"A nação de {membro.mention} foi resetada. Ele pode fundar uma nova.", ephemeral=True)


@bot.tree.command(name="admin_evento", description="[ADMIN] Injeta contexto global.")
@commands.has_permissions(administrator=True)
async def admin_evento(interaction: discord.Interaction, evento: str):
    # Inject global context into ChromaDB for all active kingdoms
    with bot.SessionLocal() as db:
        active_kingdoms = db.query(Kingdom).filter_by(is_active=True).all()
        for k in active_kingdoms:
            insert_history(bot.chroma_collection, k.id, f"EVENTO GLOBAL: {evento}")

    await interaction.response.send_message(f"Evento global registrado e inserido nas memórias de todos os reinos ativos: {evento}", ephemeral=False)

@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author.bot:
        return

    # Ignore command messages so they can be processed by command tree
    if message.content.startswith(bot.command_prefix):
        return

    with bot.SessionLocal() as db:
        # Check if the channel belongs to an active kingdom
        kingdom = db.query(Kingdom).filter_by(channel_id=message.channel.id, is_active=True).first()
        if not kingdom:
            # Not a kingdom channel, ignore
            return

        # Ensure the message is from the kingdom's owner
        if message.author.id != kingdom.player_id:
            return

        # Check if sovereign is alive
        sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
        if not sov:
            await message.channel.send("Seu soberano está morto. Seu reino caiu. Aguarde a administração ou seu fim definitivo.", delete_after=10)
            return

        # Control Actions per Day
        if kingdom.acoes_restantes <= 0:
            await message.reply("Você não possui mais ações (Decretos) disponíveis para hoje. Aguarde o ciclo virar à meia-noite.")
            return

        # Decrease remaining actions
        kingdom.acoes_restantes -= 1
        kingdom.acoes_gastas += 1

        ciclo_completo = False
        if kingdom.acoes_gastas >= 5:
            ciclo_completo = True
            kingdom.acoes_gastas = 0

            # Age Sovereign and Heir by 1 year
            sov.age += 1
            if sov.designated_heir_age is not None:
                sov.designated_heir_age += 1

            # Old age death check (75+)
            if sov.age >= 75:
                death_chance = (sov.age - 74) * 0.05
                if random.random() < death_chance:
                    sov.is_alive = False

        # Commit action changes before potentially long AI call
        db.commit()

        # 1. Classify Action
        action_text = message.content
        classification_data = await classify_action(action_text)
        classification = classification_data.get("classificacao", "Demorada")

        if classification == "Instantânea":
            # Resolve immediately
            status_dict = {
                "gold": kingdom.gold,
                "army": kingdom.army,
                "influence": kingdom.influence
            }

            # Retrieve RAG History
            history = query_history(bot.chroma_collection, kingdom.id, action_text)

            # Send a typing indicator since Ollama might take a moment
            async with message.channel.typing():
                resolution = await resolve_action(status_dict, action_text, context_history=history, ciclo_completo=ciclo_completo)
                narrative = resolution.get("narrativa", "O tempo passa, mas nada muda.")
                db_updates = resolution.get("atualizacao_db", {})

                # Apply DB Updates, guarding against negative values if possible
                kingdom.gold = max(0, kingdom.gold + db_updates.get("ouro", 0))
                kingdom.army = max(0, kingdom.army + db_updates.get("exercito", 0))
                kingdom.influence = max(0, kingdom.influence + db_updates.get("influencia", 0))

                if db_updates.get("soberano_morto", False):
                    sov.is_alive = False

                db.commit()

                # Save to Chroma DB
                history_record = f"Soberano diz: '{action_text}'. Consequência: '{narrative}'"
                insert_history(bot.chroma_collection, kingdom.id, history_record)

                await message.reply(narrative)

        else:
            # Delayed action
            # Send immediate feedback
            async with message.channel.typing():
                feedback = await generate_immediate_feedback(action_text)
                await message.reply(feedback)

            # Logistics and Distance calculation
            import math
            reino_destino = classification_data.get("reino_destino")
            dist_estimada = classification_data.get("distancia_estimada", 100)
            dist_real = float(dist_estimada)

            if reino_destino:
                # Try to find the target kingdom in DB
                # A simple fuzzy match or exact match. For exact:
                target_k = db.query(Kingdom).filter(Kingdom.name.ilike(f"%{reino_destino}%"), Kingdom.is_active==True).first()
                if target_k:
                    # distance = sqrt((x2 - x1)^2 + (y2 - y1)^2)
                    dist_real = math.sqrt((target_k.pos_x - kingdom.pos_x)**2 + (target_k.pos_y - kingdom.pos_y)**2)

            # Every 100 units of distance equals 1 cycle of delay (1 real day / 24 hours).
            # For demonstration purposes, we will translate 1 cycle to 1 hour real time delay so the player doesn't have to literally wait 24h for a test.
            # Production formula: delay_in_hours = (dist_real / 100.0) * 24
            # We'll use the production formula as requested.
            delay_in_hours = (dist_real / 100.0) * 24

            # As a safeguard to not wait days during this demo, we'll cap or keep it realistic. Let's just follow the rules exactly.
            resolve_time = datetime.datetime.utcnow() + datetime.timedelta(hours=delay_in_hours)

            # Since the action is delayed, the AI won't resolve it now.
            # But the 'ciclo_completo' flag generated during the message handling needs to be tracked.
            # To keep things simple, delayed actions won't re-trigger aging (they trigger when the action is queued, which was done above).

            # Append a note to the action text if it completed a cycle so the background task knows.
            if ciclo_completo:
                action_text += " [SYSTEM: Este evento completa um Ciclo]"

            new_action = ActionQueue(
                kingdom_id=kingdom.id,
                action_text=action_text,
                resolve_at=resolve_time,
                status="pending"
            )
            db.add(new_action)
            db.commit()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

if __name__ == "__main__":
    if DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_token_here":
        bot.run(DISCORD_TOKEN)
    else:
        print("Please set DISCORD_TOKEN in .env file")
