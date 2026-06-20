import sys
import os

# Add root directory to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.game_config import CONFIG
from core.math_playbook import MathPlaybook

def build():
    print("🚀 Triggering Math Playbook build and cache sync...")
    # MathPlaybook constructor automatically checks cache (including Supabase)
    # and if it is a cache miss, builds and saves it.
    playbook = MathPlaybook(CONFIG, max_length=4)
    print(f"✅ Playbook contains {len(playbook.catalog)} items.")

if __name__ == "__main__":
    build()
