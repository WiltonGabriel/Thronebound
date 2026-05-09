import discord
from discord.ext import commands
from discord import app_commands
import random
import math
import datetime

from database.db import Kingdom, Sovereign, Character, ActionQueue, ReviewQueue
from database.vector import insert_history, query_history
from ai.engine import classify_action, generate_immediate_feedback, resolve_action, answer_oracle
from utils.mechanics import handle_succession

class PlayerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="status", description="Veja o status da sua nação.")
    async def status(self, interaction: discord.Interaction):
        with self.bot.SessionLocal() as db:
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
                    heir = db.get(Character, sov.designated_heir_id)
                    heir_name = heir.nome if heir else "Desconhecido"
                else:
                    heir_name = "Nenhum"
                embed.add_field(name="Herdeiro", value=heir_name, inline=False)
            else:
                embed.add_field(name="Soberano", value="Morto", inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="conselho", description="Veja os membros do seu Pequeno Conselho.")
    async def conselho(self, interaction: discord.Interaction):
        with self.bot.SessionLocal() as db:
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

    @app_commands.command(name="leis", description="Veja as leis atuais do seu Reino.")
    async def leis(self, interaction: discord.Interaction):
        with self.bot.SessionLocal() as db:
            kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()
            if not kingdom:
                await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
                return

            embed = discord.Embed(title=f"Leis de {kingdom.name}", color=discord.Color.green())
            embed.add_field(name="Autoridade", value=kingdom.lei_autoridade, inline=False)
            embed.add_field(name="Sucessão", value=kingdom.lei_sucessao, inline=False)
            embed.add_field(name="Gênero", value=kingdom.lei_genero, inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="nomear_herdeiro", description="Designe o seu sucessor usando o NOME exato do personagem.")
    async def nomear_herdeiro(self, interaction: discord.Interaction, nome_exato: str):
        with self.bot.SessionLocal() as db:
            kingdom = db.query(Kingdom).filter_by(player_id=interaction.user.id, is_active=True).first()

            if not kingdom:
                await interaction.response.send_message("Você não possui uma nação ativa.", ephemeral=True)
                return

            sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
            if not sov:
                await interaction.response.send_message("Seu soberano está morto, você não pode nomear herdeiros.", ephemeral=True)
                return

            if kingdom.cooldown_herdeiro > 0:
                await interaction.response.send_message(f"Você não pode nomear um novo herdeiro agora. Aguarde o cooldown de {kingdom.cooldown_herdeiro} ações (Decretos) passar.", ephemeral=True)
                return

            target_char = db.query(Character).filter(Character.kingdom_id == kingdom.id, Character.nome.ilike(f"%{nome_exato}%"), Character.is_alive == True).first()

            if not target_char:
                await interaction.response.send_message(f"Nenhum personagem vivo com o nome '{nome_exato}' foi encontrado na sua corte/dinastia.", ephemeral=True)
                return

            blood_relations = ["filho", "filha", "irmão", "irma", "irmã", "sobrinho", "sobrinha", "neto", "neta"]
            if target_char.relacao_familiar.lower() not in blood_relations:
                await interaction.response.send_message(f"Você não pode nomear '{target_char.nome}' como herdeiro. Apenas parentes de sangue podem herdar o trono (relação atual: {target_char.relacao_familiar}).", ephemeral=True)
                return

            sov.designated_heir_id = target_char.id
            kingdom.cooldown_herdeiro = 15 # 3 cycles
            db.commit()
            await interaction.response.send_message(f"Herdeiro designado com sucesso: **{target_char.nome}** ({target_char.idade} anos). Seu reino entra em um período de transição política de 15 ações onde você não poderá nomear outro herdeiro.", ephemeral=False)

    @app_commands.command(name="dinastia", description="Veja a sua árvore genealógica e membros da família real.")
    async def dinastia(self, interaction: discord.Interaction):
        with self.bot.SessionLocal() as db:
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
                    heir = db.get(Character, sov.designated_heir_id)
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

    @app_commands.command(name="alterar_lei", description="Gasta 1 ação para alterar uma lei do Reino.")
    async def alterar_lei(self, interaction: discord.Interaction, tipo: str, nova_lei: str):
        await interaction.response.defer(ephemeral=False)

        with self.bot.SessionLocal() as db:
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

    @app_commands.command(name="a", description="Realize um Decreto Oficial. Custa 1 Ação.")
    async def acao_oficial(self, interaction: discord.Interaction, texto: str):
        await interaction.response.defer(ephemeral=False)

        with self.bot.SessionLocal() as db:
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

            if not sov.designated_heir_id:
                await interaction.followup.send("A sucessão está em perigo. Você não pode governar sem antes garantir o futuro do reino. Use o comando `/nomear_herdeiro` imediatamente.")
                return

            if kingdom.acoes_restantes <= 0:
                await interaction.followup.send("Você não possui mais ações (Decretos) disponíveis para hoje. Aguarde o ciclo virar à meia-noite.")
                return

            if kingdom.cooldown_herdeiro > 0:
                kingdom.cooldown_herdeiro -= 1

            classification_data = await classify_action(texto)
            gasta_acao = classification_data.get("gasta_acao", True)

            if not gasta_acao:
                await interaction.followup.send("Seus conselheiros não consideraram isso um Decreto digno de gastar os recursos do reino. Tente uma ordem mais logística ou externa.")
                return

            kingdom.acoes_restantes -= 1
            kingdom.acoes_gastas += 1
            acoes_restantes_agora = kingdom.acoes_restantes

            ciclo_completo = False
            if kingdom.acoes_gastas >= 5:
                ciclo_completo = True
                kingdom.acoes_gastas = 0
                db.commit() # Commit actions before aging to avoid lock issues

                from utils.mechanics import roll_universal_aging_and_death
                await roll_universal_aging_and_death(db, kingdom.id, interaction.channel)

                # Re-fetch Sovereign, checking by desc ID to get the latest even if dead
                sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id).order_by(Sovereign.id.desc()).first()

            db.commit()

            importante = classification_data.get("importante", False)
            classification = classification_data.get("classificacao", "Demorada")
            active_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

            # Send to Admin Review if marked as "Importante"
            if importante:
                review_action = ReviewQueue(
                    kingdom_id=kingdom.id,
                    action_text=texto,
                    acoes_restantes_agora=acoes_restantes_agora,
                    ciclo_completo=ciclo_completo,
                    status="pending"
                )
                db.add(review_action)
                db.commit()

                await interaction.followup.send(f"⚠️ **Seu decreto é de Alta Importância:** '{texto}'.\nOs deuses e mestres estão avaliando as ramificações de sua escolha. Aguarde a resolução oficial antes de celebrar.\n`[Ações restantes: {acoes_restantes_agora}/5]`")

                admin_channel = discord.utils.get(interaction.guild.text_channels, name="mesa-do-mestre")
                if admin_channel:
                    await admin_channel.send(f"🔴 **REVISÃO PENDENTE:** O reino de {kingdom.name} enviou uma ação crítica: '{texto}'.\nUse `/rr {kingdom.id} <resultado>` para arbitrar as consequências.")
                return

            if classification == "Instantânea":
                status_dict = {
                    "gold": kingdom.gold,
                    "army": kingdom.army,
                    "influence": kingdom.influence,
                    "estabilidade": kingdom.estabilidade,
                    "leis": f"Autoridade: {kingdom.lei_autoridade}, Sucessão: {kingdom.lei_sucessao}, Gênero: {kingdom.lei_genero}",
                    "acoes_restantes": acoes_restantes_agora
                }

                history = query_history(self.bot.chroma_collection, kingdom.id, texto)

                resolution = await resolve_action(status_dict, texto, context_history=history, ciclo_completo=ciclo_completo, characters=active_characters)
                narrative = resolution.get("narrativa", "O tempo passa, mas nada muda.")
                db_updates = resolution.get("atualizacao_db", {})
                char_updates = resolution.get("atualizacao_personagens", [])

                kingdom.gold = max(0, kingdom.gold + db_updates.get("ouro", 0))
                kingdom.army = max(0, kingdom.army + db_updates.get("exercito", 0))
                kingdom.influence = max(0, kingdom.influence + db_updates.get("influencia", 0))
                kingdom.estabilidade = max(0, min(100, kingdom.estabilidade + db_updates.get("estabilidade", 0)))

                if db_updates.get("soberano_morto", False) and sov:
                    sov.is_alive = False

                for cu in char_updates:
                    char_id = cu.get("id")
                    if char_id:
                        c = db.get(Character, char_id)
                        if c and c.kingdom_id == kingdom.id:
                            c.lealdade = max(0, min(100, c.lealdade + cu.get("lealdade", 0)))
                            c.poder = max(0, min(100, c.poder + cu.get("poder", 0)))
                            is_alive_update = cu.get("is_alive")
                            if is_alive_update is False:
                                c.is_alive = False

                db.commit()

                history_record = f"Soberano decretou: '{texto}'. Consequência: '{narrative}'"
                insert_history(self.bot.chroma_collection, kingdom.id, history_record)

                final_text = f"**Decreto:** {texto}\n\n{narrative}\n\n`[Ações restantes: {acoes_restantes_agora}/5]`"
                await interaction.followup.send(final_text)

                if sov and not sov.is_alive:
                    await handle_succession(interaction.channel, self.bot, kingdom.id)

            else:
                feedback = await generate_immediate_feedback(texto)

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

    @app_commands.command(name="p", description="Consulte o Oráculo/Conselheiros. (Não custa ações)")
    async def pergunta_oraculo(self, interaction: discord.Interaction, texto: str):
        await interaction.response.defer(ephemeral=False)

        with self.bot.SessionLocal() as db:
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

            history = query_history(self.bot.chroma_collection, kingdom.id, texto)
            active_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

            answer = await answer_oracle(status_dict, texto, context_history=history, characters=active_characters)

            await interaction.followup.send(f"**Pergunta:** {texto}\n\n**Oráculo:**\n{answer}")

async def setup(bot):
    await bot.add_cog(PlayerCog(bot))
