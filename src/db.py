from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Float, ForeignKey, DateTime, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import datetime
import random
import math

Base = declarative_base()

class Player(Base):
    __tablename__ = 'players'
    discord_id = Column(BigInteger, primary_key=True)
    joined_at = Column(DateTime, default=datetime.datetime.utcnow)
    kingdom = relationship("Kingdom", back_populates="player", uselist=False)

class Kingdom(Base):
    __tablename__ = 'kingdoms'
    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(BigInteger, ForeignKey('players.discord_id')) # Removed unique=True to allow resets
    name = Column(String, unique=True, nullable=False)
    government_type = Column(String, nullable=False)
    build_type = Column(String, nullable=False) # Militar, Mercantil, Diplomática
    gold = Column(Integer, default=1000)
    army = Column(Integer, default=100)
    influence = Column(Integer, default=50)

    acoes_restantes = Column(Integer, default=5)
    acoes_gastas = Column(Integer, default=0)

    # Crusader Kings mechanics
    estabilidade = Column(Integer, default=50) # 0 to 100
    lei_autoridade = Column(String, default="Autonomia dos Vassalos")
    lei_sucessao = Column(String, default="Partição Confederada")
    lei_genero = Column(String, default="Preferência Masculina")

    # Map coordinates
    pos_x = Column(Float, nullable=False)
    pos_y = Column(Float, nullable=False)

    # Discord channel
    channel_id = Column(BigInteger, nullable=True)

    is_active = Column(Boolean, default=True)

    player = relationship("Player", back_populates="kingdom")
    sovereigns = relationship("Sovereign", back_populates="kingdom")
    characters = relationship("Character", back_populates="kingdom")

class Sovereign(Base):
    __tablename__ = 'sovereigns'
    id = Column(Integer, primary_key=True, autoincrement=True)
    kingdom_id = Column(Integer, ForeignKey('kingdoms.id'))
    name = Column(String, nullable=False)
    age = Column(Integer, default=20) # 20 years old start
    is_alive = Column(Boolean, default=True)
    designated_heir_id = Column(Integer, ForeignKey('characters.id'), nullable=True)

    kingdom = relationship("Kingdom", back_populates="sovereigns")
    heir = relationship("Character", foreign_keys=[designated_heir_id])

class Character(Base):
    __tablename__ = 'characters'
    id = Column(Integer, primary_key=True, autoincrement=True)
    kingdom_id = Column(Integer, ForeignKey('kingdoms.id'))
    nome = Column(String, nullable=False)
    idade = Column(Integer, nullable=False)
    is_alive = Column(Boolean, default=True)

    relacao_familiar = Column(String, default="Nenhum") # Filho, Irmão, Consorte, Nenhum
    cargo_conselho = Column(String, default="Nenhum") # Chanceler, Tesoureiro, Marechal, Espião, Capelão, Nenhum

    poder = Column(Integer, default=50) # 0 to 100
    lealdade = Column(Integer, default=50) # 0 to 100
    personalidade = Column(String, nullable=False) # e.g. "Ambicioso e cruel"

    kingdom = relationship("Kingdom", back_populates="characters")

class ActionQueue(Base):
    __tablename__ = 'action_queue'
    id = Column(Integer, primary_key=True, autoincrement=True)
    kingdom_id = Column(Integer, ForeignKey('kingdoms.id'))
    action_text = Column(Text, nullable=False)
    resolve_at = Column(DateTime, nullable=False)
    status = Column(String, default="pending") # pending, resolved

def init_db(db_path='sqlite:///data/thronebound.db'):
    engine = create_engine(db_path, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session

# Map Generation Logic (Outer Ring)
def generate_kingdom_coordinates(center_x=500, center_y=500, inner_radius=200, outer_radius=500):
    """
    Generates a random coordinate (x, y) outside the inner_radius but inside the outer_radius.
    This creates an 'outer ring' spawn zone, leaving the center (mountains) empty.
    """
    while True:
        # Random point in a square
        x = random.uniform(center_x - outer_radius, center_x + outer_radius)
        y = random.uniform(center_y - outer_radius, center_y + outer_radius)

        # Calculate distance to center
        dist = math.sqrt((x - center_x)**2 + (y - center_y)**2)

        # Check if it's within the ring
        if inner_radius <= dist <= outer_radius:
            return round(x, 2), round(y, 2)
