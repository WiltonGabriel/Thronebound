import discord
from discord.ext import commands
from discord import app_commands

from database.db import Kingdom
from database.vector import insert_history

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="admin_reset_nacao", description="[ADMIN] Reseta a nação de um jogador.")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_reset_nacao(self, interaction: discord.Interaction, membro: discord.Member):
        with self.bot.SessionLocal() as db:
            kingdom = db.query(Kingdom).filter_by(player_id=membro.id, is_active=True).first()
            if not kingdom:
                await interaction.response.send_message("Este jogador não possui uma nação ativa.", ephemeral=True)
                return

            kingdom.is_active = False
            db.commit()
            await interaction.response.send_message(f"A nação de {membro.mention} foi resetada. Ele pode fundar uma nova.", ephemeral=True)

    @app_commands.command(name="admin_evento", description="[ADMIN] Injeta contexto global.")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_evento(self, interaction: discord.Interaction, evento: str):
        with self.bot.SessionLocal() as db:
            active_kingdoms = db.query(Kingdom).filter_by(is_active=True).all()
            for k in active_kingdoms:
                insert_history(self.bot.chroma_collection, k.id, f"EVENTO GLOBAL: {evento}")

        await interaction.response.send_message(f"Evento global registrado e inserido nas memórias de todos os reinos ativos: {evento}", ephemeral=False)

async def setup(bot):
    await bot.add_cog(AdminCog(bot))
