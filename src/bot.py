import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

import datetime
import random
from discord.ext import tasks

from db import init_db, Player, Kingdom, Sovereign, ActionQueue, Character, generate_kingdom_coordinates
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
                    "influence": kingdom.influence,
                    "estabilidade": kingdom.estabilidade,
                    "leis": f"Autoridade: {kingdom.lei_autoridade}, Sucessão: {kingdom.lei_sucessao}, Gênero: {kingdom.lei_genero}"
                }

                # Retrieve RAG History
                history = query_history(self.chroma_collection, kingdom.id, action.action_text)

                ciclo_completo = "[SYSTEM: Este evento completa um Ciclo]" in action.action_text
                clean_action_text = action.action_text.replace(" [SYSTEM: Este evento completa um Ciclo]", "")

                active_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

                resolution = await resolve_action(status_dict, clean_action_text, context_history=history, ciclo_completo=ciclo_completo, characters=active_characters)
                narrative = resolution.get("narrativa", "O tempo passa, mas nada muda.")
                db_updates = resolution.get("atualizacao_db", {})
                char_updates = resolution.get("atualizacao_personagens", [])

                # Apply DB Updates, guarding against negative values if possible
                kingdom.gold = max(0, kingdom.gold + db_updates.get("ouro", 0))
                kingdom.army = max(0, kingdom.army + db_updates.get("exercito", 0))
                kingdom.influence = max(0, kingdom.influence + db_updates.get("influencia", 0))
                kingdom.estabilidade = max(0, min(100, kingdom.estabilidade + db_updates.get("estabilidade", 0)))

                if db_updates.get("soberano_morto", False):
                    sov.is_alive = False

                # Apply character updates
                for cu in char_updates:
                    char_id = cu.get("id")
                    if char_id:
                        c = db.query(Character).get(char_id)
                        if c and c.kingdom_id == kingdom.id:
                            c.lealdade = max(0, min(100, c.lealdade + cu.get("lealdade", 0)))
                            c.poder = max(0, min(100, c.poder + cu.get("poder", 0)))
                            is_alive_update = cu.get("is_alive")
                            if is_alive_update is False:
                                c.is_alive = False

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

                    if not latest_sov.designated_heir_id:
                        kingdom.is_active = False
                        db.commit()
                        if channel:
                            await channel.send("💀 **Seu soberano morreu sem deixar herdeiros! O reino caiu em ruínas. Game Over.**")
                    else:
                        # Heir takes over
                        heir = db.query(Character).get(latest_sov.designated_heir_id)
                        if heir:
                            if channel:
                                await channel.send(f"👑 **O rei está morto! Longa vida ao rei! O herdeiro {heir.nome} assume o trono.**")

                            new_sov = Sovereign(
                                kingdom_id=kingdom.id,
                                name=heir.nome,
                                age=heir.idade
                            )
                            db.add(new_sov)
                            db.commit()
                        else:
                            # Edge case: heir character was deleted or died but pointer remained
                            kingdom.is_active = False
                            db.commit()
                            if channel:
                                await channel.send("💀 **Seu soberano morreu e seu herdeiro não pôde ser encontrado! O reino caiu em ruínas. Game Over.**")

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

        # Send loading message so discord doesn't timeout the interaction
        await interaction.response.send_message(
            f"Nação fundada com sucesso! Os deuses estão forjando seu mundo em {channel.mention}...",
            ephemeral=True
        )
        loading_msg = await channel.send("⏳ Os deuses estão forjando as terras, as leis e sua corte. Aguarde...")

        # Call AI for initial Lore & Family Generation
        lore_data = await generate_kingdom_lore(
            kingdom_name, self.sovereign_name.value.strip(), gov_type, build_type, pos_x, pos_y
        )

        lore_text = lore_data.get("lore", "Uma nova era se inicia nestas terras...")
        personagens_list = lore_data.get("personagens", [])

        with bot.SessionLocal() as db:
            # Save Characters
            for char_data in personagens_list:
                c = Character(
                    kingdom_id=kingdom_id,
                    nome=char_data.get("nome", "Desconhecido"),
                    idade=char_data.get("idade", 30),
                    relacao_familiar=char_data.get("relacao_familiar", "Nenhum"),
                    cargo_conselho=char_data.get("cargo_conselho", "Nenhum"),
                    poder=char_data.get("poder", 50),
                    lealdade=char_data.get("lealdade", 50),
                    personalidade=char_data.get("personalidade", "Neutro")
                )
                db.add(c)
            db.commit()

            # Save initial lore to ChromaDB
            insert_history(bot.chroma_collection, kingdom.id, f"A Fundação: {lore_text}")

        # Edit loading message to Initial Presentation
        await loading_msg.edit(content=f"# Crônicas de {kingdom_name}\n\n{lore_text}")

        embed = discord.Embed(title=f"Status Inicial de {kingdom_name}", color=discord.Color.blue())
        embed.add_field(name="Ouro", value=gold)
        embed.add_field(name="Exército", value=army)
        embed.add_field(name="Influência", value=influence)
        embed.add_field(name="Estabilidade", value="50/100")
        await channel.send(embed=embed)

        tutorial = (
            "📜 **Guia do Soberano:**\n"
            "- Use `/a <ação>` para enviar Decretos. Se a ação envolver o reino, os atributos (ouro, exército, estabilidade) e a lealdade da corte reagirão.\n"
            "- Use `/conselho` e `/leis` para ver os pilares do seu poder.\n"
            "- Você possui **5 ações por dia**. A cada 5 ações, **1 Ciclo** se fecha, o tempo passa e todos envelhecem.\n"
            "- Use `/p <pergunta>` para consultar a corte sem gastar ações.\n"
            "- Sobreviva às intrigas, mantenha a Estabilidade alta, e nomeie seu herdeiro antes que a morte o alcance."
        )
        await channel.send(tutorial)


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
        embed.add_field(name="Estabilidade", value=f"{kingdom.estabilidade}/100", inline=False)

        if sov:
            embed.add_field(name="Soberano", value=f"{sov.name} ({sov.age} anos)", inline=False)
            if sov.designated_heir_id:
                heir = db.query(Character).get(sov.designated_heir_id)
                heir_name = heir.nome if heir else "Desconhecido"
            else:
                heir_name = "Nenhum"
            embed.add_field(name="Herdeiro", value=heir_name, inline=False)
        else:
            embed.add_field(name="Soberano", value="Morto", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="conselho", description="Veja os membros do seu Pequeno Conselho.")
async def conselho(interaction: discord.Interaction):
    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()
        if not kingdom:
            await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
            return

        conselheiros = db.query(Character).filter(
            Character.kingdom_id == kingdom.id,
            Character.cargo_conselho != "Nenhum",
            Character.is_alive == True
        ).all()

        embed = discord.Embed(title=f"O Pequeno Conselho de {kingdom.name}", color=discord.Color.purple())

        if not conselheiros:
            embed.description = "Seu conselho está vazio. Nomeie membros para garantir a estabilidade."
        else:
            for c in conselheiros:
                embed.add_field(
                    name=f"{c.cargo_conselho}: {c.nome}",
                    value=f"Idade: {c.idade} | Lealdade: {c.lealdade} | Poder: {c.poder}\nPerfil: *{c.personalidade}*",
                    inline=False
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="leis", description="Veja as leis atuais do seu Reino.")
async def leis(interaction: discord.Interaction):
    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()
        if not kingdom:
            await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Leis de {kingdom.name}", color=discord.Color.green())
        embed.add_field(name="Autoridade", value=kingdom.lei_autoridade, inline=False)
        embed.add_field(name="Sucessão", value=kingdom.lei_sucessao, inline=False)
        embed.add_field(name="Gênero", value=kingdom.lei_genero, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="nomear_herdeiro", description="Designe o seu sucessor usando o NOME exato do personagem.")
async def nomear_herdeiro(interaction: discord.Interaction, nome_exato: str):
    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()

        if not kingdom:
            await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
            return

        sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
        if not sov:
            await interaction.response.send_message("Seu soberano está morto, você não pode nomear herdeiros.", ephemeral=True)
            return

        # Find the character
        target_char = db.query(Character).filter(Character.kingdom_id == kingdom.id, Character.nome.ilike(f"%{nome_exato}%"), Character.is_alive == True).first()

        if not target_char:
            await interaction.response.send_message(f"Nenhum personagem vivo com o nome '{nome_exato}' foi encontrado na sua corte/dinastia.", ephemeral=True)
            return

        sov.designated_heir_id = target_char.id
        db.commit()
        await interaction.response.send_message(f"Herdeiro designado com sucesso: **{target_char.nome}** ({target_char.idade} anos).", ephemeral=True)


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
        family_members = db.query(Character).filter(
            Character.kingdom_id == kingdom.id,
            Character.is_alive == True,
            Character.relacao_familiar != "Nenhum"
        ).all()

        embed = discord.Embed(title=f"Dinastia de {kingdom.name}", color=discord.Color.dark_red())

        if sov:
            embed.add_field(name="Soberano", value=f"👑 {sov.name} ({sov.age} anos)", inline=False)
            if sov.designated_heir_id:
                heir = db.query(Character).get(sov.designated_heir_id)
                if heir:
                    embed.add_field(name="Herdeiro Declarado", value=f"⭐ {heir.nome} ({heir.idade} anos)", inline=False)
        else:
            embed.add_field(name="Soberano", value="💀 Falecido (Trono Vazio)", inline=False)

        if family_members:
            fam_text = ""
            for fm in family_members:
                fam_text += f"- **{fm.nome}** ({fm.relacao_familiar}, {fm.idade} anos) [Poder: {fm.poder} | Lealdade: {fm.lealdade}]\n"
            embed.add_field(name="Membros Vivos", value=fam_text, inline=False)
        else:
            embed.add_field(name="Membros Vivos", value="Sua linhagem está por um fio...", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="alterar_lei", description="Gasta 1 ação para alterar uma lei do Reino.")
async def alterar_lei(interaction: discord.Interaction, tipo: str, nova_lei: str):
    await interaction.response.defer(ephemeral=False)

    with bot.SessionLocal() as db:
        kingdom = db.query(Kingdom).filter_by(channel_id=interaction.channel_id, is_active=True).first()
        if not kingdom:
            await interaction.followup.send("Este comando só pode ser usado no canal do seu reino ativo.")
            return

        if interaction.user.id != kingdom.player_id:
            await interaction.followup.send("Apenas o Soberano pode alterar leis.")
            return

        if kingdom.acoes_restantes <= 0:
            await interaction.followup.send("Você não possui mais ações (Decretos) para alterar leis hoje.")
            return

        if kingdom.estabilidade < 60:
            await interaction.followup.send("A estabilidade do reino está muito baixa para aprovar novas leis sem uma revolta. Aumente a estabilidade primeiro.")
            return

        tipo_lower = tipo.lower()
        if "autoridade" in tipo_lower:
            kingdom.lei_autoridade = nova_lei
        elif "sucessão" in tipo_lower or "sucessao" in tipo_lower:
            kingdom.lei_sucessao = nova_lei
        elif "gênero" in tipo_lower or "genero" in tipo_lower:
            kingdom.lei_genero = nova_lei
        else:
            await interaction.followup.send("Tipo de lei inválido. Use 'Autoridade', 'Sucessão' ou 'Gênero'.")
            return

        kingdom.acoes_restantes -= 1
        kingdom.acoes_gastas += 1
        kingdom.estabilidade -= 10 # Cost of changing laws
        db.commit()

        await interaction.followup.send(f"📜 O Soberano decretou uma nova lei de **{tipo.title()}**: *{nova_lei}*. A estabilidade caiu temporariamente devido às mudanças.\n`[Ações restantes: {kingdom.acoes_restantes}/5]`")

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

            # Age Sovereign and Family/Council by 1 year
            sov.age += 1

            all_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()
            for char in all_characters:
                char.idade += 1

            # Old age death check (75+)
            if sov.age >= 75:
                death_chance = (sov.age - 74) * 0.05
                if random.random() < death_chance:
                    sov.is_alive = False

        # Commit action changes before AI call
        db.commit()

        classification = classification_data.get("classificacao", "Demorada")

        # Get characters to pass to AI
        active_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

        if classification == "Instantânea":
            status_dict = {
                "gold": kingdom.gold,
                "army": kingdom.army,
                "influence": kingdom.influence,
                "estabilidade": kingdom.estabilidade,
                "leis": f"Autoridade: {kingdom.lei_autoridade}, Sucessão: {kingdom.lei_sucessao}, Gênero: {kingdom.lei_genero}",
                "acoes_restantes": acoes_restantes_agora
            }

            history = query_history(bot.chroma_collection, kingdom.id, texto)

            resolution = await resolve_action(status_dict, texto, context_history=history, ciclo_completo=ciclo_completo, characters=active_characters)
            narrative = resolution.get("narrativa", "O tempo passa, mas nada muda.")
            db_updates = resolution.get("atualizacao_db", {})
            char_updates = resolution.get("atualizacao_personagens", [])

            kingdom.gold = max(0, kingdom.gold + db_updates.get("ouro", 0))
            kingdom.army = max(0, kingdom.army + db_updates.get("exercito", 0))
            kingdom.influence = max(0, kingdom.influence + db_updates.get("influencia", 0))
            kingdom.estabilidade = max(0, min(100, kingdom.estabilidade + db_updates.get("estabilidade", 0)))

            if db_updates.get("soberano_morto", False):
                sov.is_alive = False

            # Apply character updates
            for cu in char_updates:
                char_id = cu.get("id")
                if char_id:
                    c = db.query(Character).get(char_id)
                    if c and c.kingdom_id == kingdom.id:
                        c.lealdade = max(0, min(100, c.lealdade + cu.get("lealdade", 0)))
                        c.poder = max(0, min(100, c.poder + cu.get("poder", 0)))
                        is_alive_update = cu.get("is_alive")
                        if is_alive_update is False:
                            c.is_alive = False

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
            "estabilidade": kingdom.estabilidade,
            "leis": f"Autoridade: {kingdom.lei_autoridade}, Sucessão: {kingdom.lei_sucessao}, Gênero: {kingdom.lei_genero}",
            "acoes_restantes": kingdom.acoes_restantes
        }

        history = query_history(bot.chroma_collection, kingdom.id, texto)
        active_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

        answer = await answer_oracle(status_dict, texto, context_history=history, characters=active_characters)

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
