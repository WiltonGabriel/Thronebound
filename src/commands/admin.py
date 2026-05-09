import discord
from discord.ext import commands
from discord import app_commands

from database.models import Kingdom, Character, ReviewQueue, Sovereign, ActionQueue, Tile
import datetime
from database.vector import insert_history, query_history
from ai.engine import resolve_action
from utils.mechanics import handle_succession

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

    @app_commands.command(name="fp_reino", description="[ADMIN] Altera atributos do Reino (ouro, exercito, estabilidade, influencia).")
    @app_commands.checks.has_permissions(administrator=True)
    async def fp_reino(self, interaction: discord.Interaction, id_reino: int, atributo: str, valor: int):
        with self.bot.SessionLocal() as db:
            kingdom = db.get(Kingdom, id_reino)
            if not kingdom:
                await interaction.response.send_message("Reino não encontrado.", ephemeral=True)
                return

            atributo = atributo.lower()
            if hasattr(kingdom, atributo):
                setattr(kingdom, atributo, valor)
                db.commit()
                await interaction.response.send_message(f"Atributo '{atributo}' do Reino '{kingdom.name}' alterado para {valor}.", ephemeral=False)
            else:
                await interaction.response.send_message(f"Atributo '{atributo}' inválido.", ephemeral=True)

    @app_commands.command(name="fp_personagem", description="[ADMIN] Altera atributos de um Personagem (poder, lealdade, idade).")
    @app_commands.checks.has_permissions(administrator=True)
    async def fp_personagem(self, interaction: discord.Interaction, id_personagem: int, atributo: str, valor: int):
        with self.bot.SessionLocal() as db:
            char = db.get(Character, id_personagem)
            if not char:
                await interaction.response.send_message("Personagem não encontrado.", ephemeral=True)
                return

            atributo = atributo.lower()
            if hasattr(char, atributo):
                setattr(char, atributo, valor)
                db.commit()
                await interaction.response.send_message(f"Atributo '{atributo}' de '{char.nome}' alterado para {valor}.", ephemeral=False)
            else:
                await interaction.response.send_message(f"Atributo '{atributo}' inválido.", ephemeral=True)

    @app_commands.command(name="fa", description="[ADMIN] Força uma ação sem rolagem de IA que afete o mundo.")
    @app_commands.checks.has_permissions(administrator=True)
    async def fa(self, interaction: discord.Interaction, id_reino: int, acao_descrita: str):
        with self.bot.SessionLocal() as db:
            kingdom = db.get(Kingdom, id_reino)
            if not kingdom:
                await interaction.response.send_message("Reino não encontrado.", ephemeral=True)
                return

            insert_history(self.bot.chroma_collection, kingdom.id, f"INTERVENÇÃO DIVINA: {acao_descrita}")

            channel = self.bot.get_channel(kingdom.channel_id)
            if channel:
                await channel.send(f"⚡ **A Vontade dos Deuses se manifesta:**\n{acao_descrita}")
            await interaction.response.send_message(f"Ação forçada executada no reino de {kingdom.name}.", ephemeral=True)

    @app_commands.command(name="rr", description="[ADMIN] Resolve a ação pendente de revisão mais antiga de um reino.")
    @app_commands.checks.has_permissions(administrator=True)
    async def rr(self, interaction: discord.Interaction, id_reino: int, resultado: str):
        await interaction.response.defer(ephemeral=False)

        with self.bot.SessionLocal() as db:
            kingdom = db.get(Kingdom, id_reino)
            if not kingdom:
                await interaction.followup.send("Reino não encontrado.")
                return

            review_item = db.query(ReviewQueue).filter_by(kingdom_id=kingdom.id, status="pending").order_by(ReviewQueue.id.asc()).first()

            if not review_item:
                await interaction.followup.send(f"O reino de {kingdom.name} não possui ações pendentes de revisão.")
                return

            sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id).order_by(Sovereign.id.desc()).first()

            status_dict = {
                "gold": kingdom.gold,
                "army": kingdom.army,
                "influence": kingdom.influence,
                "estabilidade": kingdom.estabilidade,
                "leis": f"Autoridade: {kingdom.lei_autoridade}, Sucessão: {kingdom.lei_sucessao}, Gênero: {kingdom.lei_genero}",
                "acoes_restantes": review_item.acoes_restantes_agora
            }

            history = query_history(self.bot.chroma_collection, kingdom.id, review_item.action_text)
            active_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

            resolution = await resolve_action(
                kingdom_status=status_dict,
                action_text=review_item.action_text,
                context_history=history,
                ciclo_completo=review_item.ciclo_completo,
                characters=active_characters,
                master_override=resultado
            )

            narrative = resolution.get("narrativa", "O mestre arbitrou o destino, e assim se fez.")
            db_updates = resolution.get("atualizacao_db", {})
            char_updates = resolution.get("atualizacao_personagens", [])

            history_record = f"Soberano decretou: '{review_item.action_text}'. Mestre decidiu: '{resultado}'. Consequência final: '{narrative}'"

            # Re-run logic to determine if the original action was delayed or instant
            from ai.engine import classify_action
            classification_data = await classify_action(review_item.action_text)
            classification = classification_data.get("classificacao", "Demorada")

            if classification == "Instantânea":
                review_item.status = "resolved"

                from utils.mechanics import apply_db_updates
                sov_died = apply_db_updates(db, kingdom.id, db_updates, char_updates)

                db.commit()

                insert_history(self.bot.chroma_collection, kingdom.id, history_record)

                channel = self.bot.get_channel(kingdom.channel_id)
                if channel:
                    final_text = f"**Decreto Julgado (Mestre):** {review_item.action_text}\n\n{narrative}\n\n`[Ações restantes: {review_item.acoes_restantes_agora}/5]`"
                    await channel.send(final_text)

                await interaction.followup.send(f"Ação (Instantânea) do reino {kingdom.name} resolvida com sucesso e enviada ao jogador.")

                if sov_died:
                    await handle_succession(channel, self.bot, kingdom.id)
            else:
                # Delayed action
                reino_destino = classification_data.get("reino_destino")
                dist_estimada = classification_data.get("distancia_estimada", 100)

                from utils.mechanics import enqueue_delayed_action
                enqueue_delayed_action(db, kingdom.id, review_item.action_text, reino_destino, dist_estimada, review_item.ciclo_completo, narrative, resolution)

                review_item.status = "resolved"
                db.commit()

                insert_history(self.bot.chroma_collection, kingdom.id, history_record)

                channel = self.bot.get_channel(kingdom.channel_id)
                if channel:
                    final_text = f"**Decreto Julgado (Mestre):** {review_item.action_text}\n\n*Os emissários partiram. O resultado será revelado em breve.*"
                    await channel.send(final_text)

                await interaction.followup.send(f"Ação (Demorada) do reino {kingdom.name} julgada e inserida na fila logística com sucesso. Atributos só serão alterados quando a fila resolver.")

async def setup(bot):
    await bot.add_cog(AdminCog(bot))
