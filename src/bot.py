import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

import datetime
import random
from discord.ext import tasks

from db import init_db, Player, Kingdom, Sovereign, ActionQueue, FamilyMember, generate_kingdom_coordinates
from ai import classify_action, generate_immediate_feedback, resolve_action, generate_kingdom_lore, answer_oracle
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

        await interaction.response.defer(ephemeral=True)

        # Call AI for initial Lore & Family Generation
        lore_data = await generate_kingdom_lore(
            kingdom_name, self.sovereign_name.value.strip(), gov_type, build_type, pos_x, pos_y
        )

        lore_text = lore_data.get("lore", "Uma nova era se inicia nestas terras...")
        family_list = lore_data.get("familia", [])

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

            # Save Family
            for fam in family_list:
                fm = FamilyMember(
                    kingdom_id=kingdom.id,
                    name=fam.get("nome", "Desconhecido"),
                    relation=fam.get("relacao", "Parente"),
                    age=fam.get("idade", 20)
                )
                db.add(fm)
            db.commit()

            # Save initial lore to ChromaDB
            insert_history(bot.chroma_collection, kingdom.id, f"A Fundação: {lore_text}")

        # Send Initial Presentation in the Kingdom Channel
        await channel.send(f"# Crônicas de {kingdom_name}\n\n{lore_text}")

        embed = discord.Embed(title=f"Status Inicial de {kingdom_name}", color=discord.Color.blue())
        embed.add_field(name="Ouro", value=gold)
        embed.add_field(name="Exército", value=army)
        embed.add_field(name="Influência", value=influence)
        await channel.send(embed=embed)

        tutorial = (
            "📜 **Guia do Soberano:**\n"
            "- Use `/a <ação>` para enviar Decretos (ações que gastam tempo e recursos, como enviar emissários ou atacar).\n"
            "- Você possui **5 ações por dia**. Quando você gasta 5 ações, **1 Ciclo (Semana)** se completa, e seus personagens envelhecem 1 ano.\n"
            "- Use `/p <pergunta>` para consultar o Oráculo/Conselheiros sobre o mundo ou seu reino sem gastar ações.\n"
            "- Use `/dinastia` para conferir sua árvore familiar e `/nomear_herdeiro` para garantir o futuro do reino."
        )
        await channel.send(tutorial)

        await interaction.followup.send(
            f"Nação fundada com sucesso! Seu reino está em {channel.mention}."
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

@bot.tree.command(name="dinastia", description="Veja a sua árvore genealógica e membros da família real.")
async def dinastia(interaction: discord.Interaction):
    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()
        if not kingdom:
            await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
            return

        sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
        family_members = db.query(FamilyMember).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

        embed = discord.Embed(title=f"Dinastia de {kingdom.name}", color=discord.Color.dark_red())

        if sov:
            embed.add_field(name="Soberano", value=f"👑 {sov.name} ({sov.age} anos)", inline=False)
            if sov.designated_heir_name:
                age_str = f" ({sov.designated_heir_age} anos)" if sov.designated_heir_age is not None else ""
                embed.add_field(name="Herdeiro Declarado", value=f"⭐ {sov.designated_heir_name}{age_str}", inline=False)
        else:
            embed.add_field(name="Soberano", value="💀 Falecido (Trono Vazio)", inline=False)

        if family_members:
            fam_text = ""
            for fm in family_members:
                fam_text += f"- **{fm.name}** ({fm.relation}, {fm.age} anos)\n"
            embed.add_field(name="Membros Vivos", value=fam_text, inline=False)
        else:
            embed.add_field(name="Membros Vivos", value="Sua linhagem está por um fio...", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="a", description="Realize um Decreto Oficial. Custa 1 Ação.")
async def acao_oficial(interaction: discord.Interaction, texto: str):
    await interaction.response.defer(ephemeral=False)

    with bot.SessionLocal() as db:
        # Check if the channel belongs to the active kingdom
        kingdom = db.query(Kingdom).filter_by(channel_id=interaction.channel_id, is_active=True).first()
        if not kingdom:
            await interaction.followup.send("Este comando só pode ser usado no canal do seu reino ativo.")
            return

        if interaction.user.id != kingdom.player_id:
            await interaction.followup.send("Apenas o Soberano pode decretar ações aqui.")
            return

        sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
        if not sov:
            await interaction.followup.send("Seu soberano está morto. Seu reino caiu. Aguarde a administração ou seu fim definitivo.")
            return

        if kingdom.acoes_restantes <= 0:
            await interaction.followup.send("Você não possui mais ações (Decretos) disponíveis para hoje. Aguarde o ciclo virar à meia-noite.")
            return

        # 1. Classify Action to see if it spends a point
        classification_data = await classify_action(texto)
        gasta_acao = classification_data.get("gasta_acao", True)

        if not gasta_acao:
            await interaction.followup.send("Seus conselheiros não consideraram isso um Decreto digno de gastar os recursos do reino. Tente uma ordem mais logística ou externa.")
            return

        # Decrease remaining actions
        kingdom.acoes_restantes -= 1
        kingdom.acoes_gastas += 1
        acoes_restantes_agora = kingdom.acoes_restantes

        ciclo_completo = False
        if kingdom.acoes_gastas >= 5:
            ciclo_completo = True
            kingdom.acoes_gastas = 0

            # Age Sovereign, Heir, and Family by 1 year
            sov.age += 1
            if sov.designated_heir_age is not None:
                sov.designated_heir_age += 1

            family_members = db.query(FamilyMember).filter_by(kingdom_id=kingdom.id, is_alive=True).all()
            for fm in family_members:
                fm.age += 1

            # Old age death check (75+)
            if sov.age >= 75:
                death_chance = (sov.age - 74) * 0.05
                if random.random() < death_chance:
                    sov.is_alive = False

        # Commit action changes before AI call
        db.commit()

        classification = classification_data.get("classificacao", "Demorada")

        if classification == "Instantânea":
            status_dict = {
                "gold": kingdom.gold,
                "army": kingdom.army,
                "influence": kingdom.influence,
                "acoes_restantes": acoes_restantes_agora
            }

            history = query_history(bot.chroma_collection, kingdom.id, texto)

            resolution = await resolve_action(status_dict, texto, context_history=history, ciclo_completo=ciclo_completo)
            narrative = resolution.get("narrativa", "O tempo passa, mas nada muda.")
            db_updates = resolution.get("atualizacao_db", {})

            kingdom.gold = max(0, kingdom.gold + db_updates.get("ouro", 0))
            kingdom.army = max(0, kingdom.army + db_updates.get("exercito", 0))
            kingdom.influence = max(0, kingdom.influence + db_updates.get("influencia", 0))

            if db_updates.get("soberano_morto", False):
                sov.is_alive = False

            db.commit()

            history_record = f"Soberano decretou: '{texto}'. Consequência: '{narrative}'"
            insert_history(bot.chroma_collection, kingdom.id, history_record)

            final_text = f"**Decreto:** {texto}\n\n{narrative}\n\n`[Ações restantes: {acoes_restantes_agora}/5]`"
            await interaction.followup.send(final_text)

        else:
            # Delayed action
            feedback = await generate_immediate_feedback(texto)

            import math
            reino_destino = classification_data.get("reino_destino")
            dist_estimada = classification_data.get("distancia_estimada", 100)
            dist_real = float(dist_estimada)

            if reino_destino:
                target_k = db.query(Kingdom).filter(Kingdom.name.ilike(f"%{reino_destino}%"), Kingdom.is_active==True).first()
                if target_k:
                    dist_real = math.sqrt((target_k.pos_x - kingdom.pos_x)**2 + (target_k.pos_y - kingdom.pos_y)**2)

            delay_in_hours = (dist_real / 100.0) * 24
            resolve_time = datetime.datetime.utcnow() + datetime.timedelta(hours=delay_in_hours)

            texto_fila = texto
            if ciclo_completo:
                texto_fila += " [SYSTEM: Este evento completa um Ciclo]"

            new_action = ActionQueue(
                kingdom_id=kingdom.id,
                action_text=texto_fila,
                resolve_at=resolve_time,
                status="pending"
            )
            db.add(new_action)
            db.commit()

            final_text = f"**Decreto Enviado:** {texto}\n\n*_{feedback}_*\n\n`[Ações restantes: {acoes_restantes_agora}/5]`"
            await interaction.followup.send(final_text)

@bot.tree.command(name="p", description="Consulte o Oráculo/Conselheiros. (Não custa ações)")
async def pergunta_oraculo(interaction: discord.Interaction, texto: str):
    await interaction.response.defer(ephemeral=False)

    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(channel_id=interaction.channel_id, is_active=True).first()
        if not kingdom:
            await interaction.followup.send("Este comando só pode ser usado no canal do seu reino ativo.")
            return

        status_dict = {
            "gold": kingdom.gold,
            "army": kingdom.army,
            "influence": kingdom.influence,
            "acoes_restantes": kingdom.acoes_restantes
        }

        history = query_history(bot.chroma_collection, kingdom.id, texto)
        family_members = db.query(FamilyMember).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

        answer = await answer_oracle(status_dict, texto, context_history=history, family_members=family_members)

        await interaction.followup.send(f"**Pergunta:** {texto}\n\n**Oráculo:**\n{answer}")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

if __name__ == "__main__":
    if DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_token_here":
        bot.run(DISCORD_TOKEN)
    else:
        print("Please set DISCORD_TOKEN in .env file")
