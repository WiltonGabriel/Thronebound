import math
import random
import io
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.colors as mcolors
from noise import pnoise2

from database.models import Tile, Kingdom

MAP_WIDTH = 50
MAP_HEIGHT = 50
SCALE = 10.0

BIOMES = {
    "Oceano": {"color": "#1f78b4", "resource": 0.5, "habitability": 0.0},
    "Planície": {"color": "#33a02c", "resource": 1.0, "habitability": 1.2},
    "Floresta": {"color": "#2ca02c", "resource": 1.5, "habitability": 0.8},
    "Montanha": {"color": "#7f7f7f", "resource": 2.0, "habitability": 0.3},
    "Deserto": {"color": "#fdbf6f", "resource": 0.8, "habitability": 0.2},
    "Pântano": {"color": "#b2df8a", "resource": 1.2, "habitability": 0.4},
}

def generate_procedural_map(db_session, seed=None):
    """
    Generates the initial map tiles using Perlin noise and populates the database.
    """
    if seed is None:
        seed = random.randint(0, 1000)

    tiles_to_insert = []

    # Check if map already generated
    existing = db_session.query(Tile).first()
    if existing:
        return

    for y in range(MAP_HEIGHT):
        for x in range(MAP_WIDTH):
            # Base elevation noise
            elevation = pnoise2(x / SCALE,
                                y / SCALE,
                                octaves=4,
                                persistence=0.5,
                                lacunarity=2.0,
                                repeatx=1024,
                                repeaty=1024,
                                base=seed)

            # Moisture noise
            moisture = pnoise2(x / SCALE + 100,
                               y / SCALE + 100,
                               octaves=4,
                               persistence=0.5,
                               lacunarity=2.0,
                               repeatx=1024,
                               repeaty=1024,
                               base=seed+1)

            biome_name = determine_biome(elevation, moisture, x, y)
            biome_stats = BIOMES[biome_name]

            t = Tile(
                x=x,
                y=y,
                biome=biome_name,
                resource_index=biome_stats["resource"],
                habitability=biome_stats["habitability"],
                kingdom_id=None
            )
            tiles_to_insert.append(t)

    db_session.bulk_save_objects(tiles_to_insert)
    db_session.commit()

def determine_biome(elevation: float, moisture: float, x: int, y: int) -> str:
    """
    Determines the biome based on Perlin noise values and a central mountain mask.
    """
    # Force center to be mountain
    center_x, center_y = MAP_WIDTH / 2, MAP_HEIGHT / 2
    dist_to_center = math.sqrt((x - center_x)**2 + (y - center_y)**2)

    if dist_to_center < 5:
        return "Montanha"

    if elevation < -0.15:
        return "Oceano"
    elif elevation > 0.3:
        return "Montanha"
    else:
        if moisture < -0.2:
            return "Deserto"
        elif moisture > 0.2:
            return "Pântano" if elevation < 0 else "Floresta"
        else:
            return "Planície"

def claim_starting_tiles(db_session, kingdom_id: int) -> Tile:
    """
    Finds a suitable starting location (high habitability) and claims it for the kingdom.
    Returns the capital Tile.
    """
    # Find an unclaimed, high habitability tile that is not ocean or mountain
    suitable_tiles = db_session.query(Tile).filter(
        Tile.kingdom_id == None,
        Tile.biome.in_(["Planície", "Floresta"])
    ).order_by(Tile.habitability.desc()).limit(50).all()

    if not suitable_tiles:
        # Fallback if map is full
        suitable_tiles = db_session.query(Tile).filter(Tile.kingdom_id == None).all()

    chosen = random.choice(suitable_tiles)
    chosen.kingdom_id = kingdom_id

    # Claim some adjacent tiles
    neighbors = db_session.query(Tile).filter(
        Tile.x >= chosen.x - 1, Tile.x <= chosen.x + 1,
        Tile.y >= chosen.y - 1, Tile.y <= chosen.y + 1,
        Tile.kingdom_id == None,
        Tile.biome != "Oceano"
    ).all()

    for n in neighbors:
        if random.random() > 0.3: # 70% chance to claim adjacent
            n.kingdom_id = kingdom_id

    db_session.commit()
    return chosen

def render_map(db_session) -> bytes:
    """
    Queries the database and uses matplotlib to draw the grid and claimed kingdoms.
    Returns the image as bytes.
    """
    tiles = db_session.query(Tile).all()

    # Setup plot
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.set_aspect('equal')
    ax.axis('off') # Hide axes

    # Assign distinct colors to kingdoms
    kingdoms = db_session.query(Kingdom).all()
    kingdom_colors = {}

    cmap = plt.get_cmap('Set3')
    for idx, k in enumerate(kingdoms):
        kingdom_colors[k.id] = mcolors.to_hex(cmap(idx % 12))

    for t in tiles:
        base_color = BIOMES[t.biome]["color"]

        # Determine edge color (kingdom border)
        edge_color = 'none'
        linewidth = 0
        if t.kingdom_id is not None:
            edge_color = kingdom_colors.get(t.kingdom_id, 'black')
            linewidth = 2

            # Slightly tint the claimed tile with the kingdom color
            # Simple alpha blend approximation
            # Actually, let's just use the biome color and a thick border for claimed

        rect = patches.Rectangle((t.x, t.y), 1, 1, linewidth=linewidth, edgecolor=edge_color, facecolor=base_color)
        ax.add_patch(rect)

    ax.set_xlim(0, MAP_WIDTH)
    ax.set_ylim(0, MAP_HEIGHT)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150, facecolor='#f0f0f0')
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()
