import discord
from discord.ext import commands
from discord import app_commands

from database.db import Player, Kingdom, Sovereign, Character, generate_kingdom_coordinates
from database.vector import insert_history
from ai.engine import generate_kingdom_lore

GOVERNMENT_TYPES = [
    "Beilhique", "Despotado", "Domínio", "Ducado", "Emirado", "Império",
    "Horda", "Grão-Ducado", "Heptarquia", "Caganato", "Canato", "Reino",
    "Marcas", "Principado", "Satrapia", "Xogunato", "Sultanato", "Czarado", "Ulus"
]

class FundarNacaoModal(discord.ui.Modal, title='Fundar Nação'):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

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

        with self.bot.SessionLocal() as db:
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

        channel_name = f"reino-{kingdom_name.lower().replace(' ', '-')}"
        channel = await interaction.guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites
        )

        with self.bot.SessionLocal() as db:
            kingdom = db.get(Kingdom, kingdom_id)
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
            kingdom_name, self.sovereign_name.value.strip(), gov_type, build, pos_x, pos_y
        )

        lore_text = lore_data.get("lore", "Uma nova era se inicia nestas terras...")
        personagens_list = lore_data.get("personagens", [])

        with self.bot.SessionLocal() as db:
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
            insert_history(self.bot.chroma_collection, kingdom_id, f"A Fundação: {lore_text}")

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


class FoundationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="fundar_nacao", description="Funde sua nação para começar a jogar.")
    async def fundar_nacao(self, interaction: discord.Interaction):
        with self.bot.SessionLocal() as db:
            existing = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()
            if existing:
                await interaction.response.send_message("Você já fundou sua nação!", ephemeral=True)
                return
        await interaction.response.send_modal(FundarNacaoModal(self.bot))

async def setup(bot):
    await bot.add_cog(FoundationCog(bot))
