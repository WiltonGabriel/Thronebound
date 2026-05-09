import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

from database.models import init_db
from database.vector import init_chroma
from engine.map_generator import generate_procedural_map
import asyncio

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

class ThroneboundBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.SessionLocal = init_db()
        self.chroma_collection = init_chroma()
        self.synced = False

    async def setup_hook(self):
        # Setup Procedural World Map
        with self.SessionLocal() as db:
            generate_procedural_map(db, seed=42)
            print("Procedural map loaded/verified.")

        # Load Cogs
        await self.load_extension("commands.foundation")
        await self.load_extension("commands.player")
        await self.load_extension("commands.admin")
        await self.load_extension("tasks.loops")

        if not self.synced:
            await self.tree.sync()
            self.synced = True
            print("Command tree synced.")

bot = ThroneboundBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

async def start_dashboard():
    import uvicorn
    from web.dashboard import app
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    if not DISCORD_TOKEN or DISCORD_TOKEN == "your_discord_token_here":
        print("Please set DISCORD_TOKEN in .env file")
        return

    dashboard_task = asyncio.create_task(start_dashboard())

    async with bot:
        await bot.start(DISCORD_TOKEN)

    await dashboard_task

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
