import sympy as sp
from collections import defaultdict
import itertools
import json
import os
import hashlib
import requests
from dotenv import load_dotenv

from core.math_engine import MathEngine

# Load environment variables
load_dotenv()

class MathPlaybook:
    def __init__(self, config, max_length=5, cache_path="/Users/kirencaldwell/Documents/Equadium/playbook_cache.json"):
        # Maps a canonical SymPy result to a list of symbol sequences
        # e.g., { 2*x: [ ['d/dx(', 'x**2', ')'], ['2', '*', 'x'] ] }
        self.catalog = defaultdict(list)
        self.engine = MathEngine(config)
        self.cache_path = cache_path
        self.max_length = max_length
        
        # Determine unique symbols for metadata
        self.unique_symbols = sorted([s for s in config["tiles"].keys() if s != "="])
        
        # Create a stable identifier for database caching
        symbols_str = ",".join(self.unique_symbols)
        symbols_hash = hashlib.md5(symbols_str.encode('utf-8')).hexdigest()
        self.cache_id = f"v2_len{max_length}_{symbols_hash}"
        
        # Read Supabase credentials
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        
        # 1. Try loading from cache (Supabase first, then local fallback)
        if self._load_cache():
            return
            
        # 2. Cache miss: build catalog and save to cache
        self._build_catalog(config["tiles"], max_length)
        self._save_cache()

    def _load_cache(self):
        # Try Supabase if configured
        if self.supabase_url and self.supabase_key:
            try:
                print(f"📡 Querying Supabase for playbook cache '{self.cache_id}'...")
                url = f"{self.supabase_url.rstrip('/')}/rest/v1/playbook_cache?id=eq.{self.cache_id}"
                headers = {
                    "apikey": self.supabase_key,
                    "Authorization": f"Bearer {self.supabase_key}"
                }
                res = requests.get(url, headers=headers, timeout=10)
                if res.status_code == 200:
                    records = res.json()
                    if records:
                        data = records[0]
                        catalog_raw = data.get("catalog", {})
                        self._load_catalog_dict(catalog_raw)
                        print(f"✅ Playbook loaded from Supabase cache ({self.cache_id}) with {len(self.catalog)} unique identities.")
                        return True
                else:
                    print(f"⚠️ Supabase GET returned status: {res.status_code}")
            except Exception as e:
                print(f"⚠️ Failed to load playbook from Supabase: {e}")

        # Fallback to local file cache
        if not os.path.exists(self.cache_path):
            return False
        try:
            with open(self.cache_path, "r") as f:
                data = json.load(f)
            
            metadata = data.get("metadata", {})
            if metadata.get("max_length") != self.max_length:
                return False
            if metadata.get("unique_symbols") != self.unique_symbols:
                return False
            if metadata.get("cache_version") != "v2":
                return False
            
            self._load_catalog_dict(data.get("catalog", {}))
            print(f"✅ Playbook loaded from local file cache ({self.cache_path}) with {len(self.catalog)} unique identities.")
            return True
        except Exception as e:
            print(f"⚠️ Failed to load local playbook cache: {e}")
            return False

    def _load_catalog_dict(self, catalog_raw):
        for expr_str, recipes in catalog_raw.items():
            try:
                expr = sp.sympify(expr_str)
            except Exception:
                expr = self.engine._parse_expression(expr_str)
            self.catalog[expr] = recipes

    def _save_cache(self):
        catalog_raw = {}
        for expr, recipes in self.catalog.items():
            catalog_raw[str(expr)] = recipes
            
        metadata = {
            "unique_symbols": self.unique_symbols,
            "max_length": self.max_length,
            "cache_version": "v2"
        }

        # Save to Supabase if configured
        if self.supabase_url and self.supabase_key:
            try:
                print(f"📤 Uploading playbook cache to Supabase...")
                url = f"{self.supabase_url.rstrip('/')}/rest/v1/playbook_cache"
                headers = {
                    "apikey": self.supabase_key,
                    "Authorization": f"Bearer {self.supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-dupes"
                }
                payload = {
                    "id": self.cache_id,
                    "metadata": metadata,
                    "catalog": catalog_raw
                }
                res = requests.post(url, headers=headers, json=payload, timeout=15)
                if res.status_code in (200, 201):
                    print(f"💾 Playbook successfully cached in Supabase database under ID: {self.cache_id}")
                    return
                else:
                    print(f"⚠️ Supabase POST returned status: {res.status_code}, response: {res.text}")
            except Exception as e:
                print(f"⚠️ Failed to save playbook to Supabase: {e}")

        # Local file fallback
        try:
            data = {
                "metadata": metadata,
                "catalog": catalog_raw
            }
            with open(self.cache_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"💾 Playbook cached locally to {self.cache_path}.")
        except Exception as e:
            print(f"⚠️ Failed to save local playbook cache: {e}")



    def _looks_like_garbage(self, seq):
        """Fast sequence checks to skip invalid configurations."""
        # 1. Run sequence grammatical checks
        gram_valid, _ = self.engine.validate_sequence(list(seq))
        if not gram_valid:
            return True

        expr_str = "".join(seq)
        # Ends with an open parenthesis
        if expr_str[-1] in ['d/dx(', 'int(']: return True
        # Mismatched parentheses
        if expr_str.count('(') != expr_str.count(')'): return True
        # Empty parentheses
        if '()' in expr_str: return True
        return False

    def _build_catalog(self, tile_config, max_length):
        print("🧠 Pre-computing mathematical playbook...")
        x = sp.Symbol('x')
        
        # Extract just the unique mathematical symbols (exclude "=")
        unique_symbols = [s for s in tile_config.keys() if s != "="]
        
        for length in range(1, max_length + 1):
            sequences = itertools.product(unique_symbols, repeat=length)
            print("len(sequences) = ", len(list(sequences)))
            for seq in itertools.product(unique_symbols, repeat=length):
                if self._looks_like_garbage(seq):
                    continue
                expr_str = "".join(seq)
                    
                try:
                    # Pipe through the game's actual parser
                    parsed_expr = self.engine._parse_expression(expr_str)
                    simplified_result = sp.simplify(parsed_expr)
                    
                    # Store the recipe!
                    self.catalog[simplified_result].append(list(seq))
                except:
                    pass # Invalid math string, ignore silently
        
        print(f"✅ Playbook built with {len(self.catalog)} unique identities.")
