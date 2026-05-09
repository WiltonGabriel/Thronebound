import random

from database.db import Kingdom, Sovereign, Character
from ai.engine import validate_legal_heir

async def handle_succession(channel, bot, kingdom_id: int):
    """
    Handles the death of a Sovereign. Validates the designated heir against the legal heir.
    Applies regency penalties if the heir is under 16.
    """
    with bot.SessionLocal() as db:
        kingdom = db.get(Kingdom, kingdom_id)
        if not kingdom or not kingdom.is_active:
            return

        latest_sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id).order_by(Sovereign.id.desc()).first()
        if not latest_sov or latest_sov.is_alive:
            return

        if not latest_sov.designated_heir_id:
            kingdom.is_active = False
            db.commit()
            if channel:
                await channel.send("💀 **Seu soberano morreu sem deixar herdeiros! O reino caiu em ruínas. Game Over.**")
            return

        # AI Legal Validation
        family_members = db.query(Character).filter(
            Character.kingdom_id == kingdom.id,
            Character.is_alive == True,
            Character.relacao_familiar != "Nenhum"
        ).all()

        legal_heir_name = await validate_legal_heir(family_members, kingdom.lei_sucessao, kingdom.lei_genero)
        designated_heir = db.get(Character, latest_sov.designated_heir_id)

        if not designated_heir:
            kingdom.is_active = False
            db.commit()
            if channel:
                await channel.send("💀 **Seu soberano morreu e seu herdeiro designado não pôde ser encontrado! O reino caiu em ruínas. Game Over.**")
            return

        # Compare designated heir with legal heir
        if designated_heir.nome.lower() == legal_heir_name.lower():

            # Check for Regency
            regency_msg = ""
            if designated_heir.idade < 16:
                kingdom.estabilidade = max(0, kingdom.estabilidade - 15)
                regency_msg = f"\n⚠️ **Regência Declarada:** Como {designated_heir.nome} tem apenas {designated_heir.idade} anos, um conselho de regência governará em seu nome. A estabilidade caiu drasticamente (-15)."

            if channel:
                await channel.send(f"👑 **O rei está morto! A sucessão ocorreu de forma pacífica e legal. Longa vida ao rei! O herdeiro {designated_heir.nome} assume o trono.**{regency_msg}")

            new_sov = Sovereign(
                kingdom_id=kingdom.id,
                name=designated_heir.nome,
                age=designated_heir.idade
            )
            db.add(new_sov)
            db.commit()
        else:
            # Usurpation / Civil War -> Game Over
            kingdom.is_active = False
            db.commit()
            if channel:
                await channel.send(f"🔥 **GUERRA CIVIL!** O Soberano morreu e tentou passar o trono para {designated_heir.nome}, mas a lei exigia que o trono fosse para **{legal_heir_name}**. O reino se despedaçou em chamas e sangue pela usurpação. **Game Over.**")

async def roll_universal_aging_and_death(db, kingdom_id: int, channel):
    """
    To be called whenever a cycle completes. Ages all characters and the Sovereign,
    and rolls death chances for anyone >= 75.
    Announces council vacancies if a councilor dies.
    """
    kingdom = db.get(Kingdom, kingdom_id)
    if not kingdom:
        return

    # 1. Age the Sovereign separately since they are in their own table
    sov = db.query(Sovereign).filter_by(kingdom_id=kingdom.id, is_alive=True).first()
    if sov:
        sov.age_up()
        sov.roll_natural_death()

    # 2. Age all living Characters (Council and Family)
    all_characters = db.query(Character).filter_by(kingdom_id=kingdom.id, is_alive=True).all()

    for char in all_characters:
        char.age_up()

        # Determine if character died naturally this cycle
        if char.roll_natural_death():
            # Check if this character is the active Sovereign (in case they are mapped)
            if sov and sov.is_alive and sov.name == char.nome:
                sov.is_alive = False

            # Announce Council death
            if char.cargo_conselho != "Nenhum":
                if channel:
                    await channel.send(f"🕯️ **Luto Oficial:** O {char.cargo_conselho} **{char.nome}** faleceu de causas naturais aos {char.idade} anos. O cargo agora está vago.")
                char.cargo_conselho = "Nenhum"

    db.commit()
