from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from sqladmin import Admin, ModelView
import uvicorn
from sqlalchemy import create_engine

from database.models import Player, Kingdom, Sovereign, Character, Tile, ConfigRule, ActionQueue, ReviewQueue
from engine.map_generator import render_map
from database.vector import init_chroma

app = FastAPI(title="Thronebound Dashboard")

# Dependency DB
engine = create_engine('sqlite:///data/thronebound.db', connect_args={'check_same_thread': False})
admin = Admin(app, engine, base_url="/admin")

# SQLAdmin Views
class PlayerView(ModelView, model=Player):
    column_list = [Player.discord_id, Player.joined_at]

class KingdomView(ModelView, model=Kingdom):
    column_list = [Kingdom.id, Kingdom.name, Kingdom.estabilidade, Kingdom.is_active]
    form_columns = [Kingdom.name, Kingdom.estabilidade, Kingdom.lei_autoridade, Kingdom.lei_sucessao, Kingdom.lei_genero, Kingdom.acoes_restantes, Kingdom.is_active]

class SovereignView(ModelView, model=Sovereign):
    column_list = [Sovereign.id, Sovereign.name, Sovereign.age, Sovereign.is_alive]

class CharacterView(ModelView, model=Character):
    column_list = [Character.id, Character.nome, Character.cargo_conselho, Character.lealdade, Character.poder, Character.is_alive]
    column_searchable_list = [Character.nome]

class ConfigRuleView(ModelView, model=ConfigRule):
    column_list = [ConfigRule.key, ConfigRule.value, ConfigRule.description]

class ActionQueueView(ModelView, model=ActionQueue):
    column_list = [ActionQueue.id, ActionQueue.kingdom_id, ActionQueue.status, ActionQueue.resolve_at]

class ReviewQueueView(ModelView, model=ReviewQueue):
    column_list = [ReviewQueue.id, ReviewQueue.kingdom_id, ReviewQueue.status]

# Register views
admin.add_view(PlayerView)
admin.add_view(KingdomView)
admin.add_view(SovereignView)
admin.add_view(CharacterView)
admin.add_view(ConfigRuleView)
admin.add_view(ActionQueueView)
admin.add_view(ReviewQueueView)

@app.get("/map", response_class=Response)
def get_map():
    """Returns the current rendered map image."""
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as db:
        img_bytes = render_map(db)
        return Response(content=img_bytes, media_type="image/png")

@app.get("/rag/{kingdom_id}", response_class=HTMLResponse)
def view_rag(kingdom_id: int):
    """Simple view to read the ChromaDB history for a kingdom."""
    collection = init_chroma()
    results = collection.get(where={"kingdom_id": kingdom_id})

    html = f"<h1>RAG History for Kingdom {kingdom_id}</h1><ul>"
    if results and 'documents' in results:
        for doc in results['documents']:
            html += f"<li>{doc}</li>"
    html += "</ul>"
    return html

def run_dashboard():
    uvicorn.run(app, host="0.0.0.0", port=8080)
