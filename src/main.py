import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

from database.db import init_db
from database.vector import init_chroma

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

if __name__ == "__main__":
    if DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_token_here":
        bot.run(DISCORD_TOKEN)
    else:
        print("Please set DISCORD_TOKEN in .env file")
