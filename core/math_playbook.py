import sympy as sp
from collections import defaultdict
import itertools
import json
import os

from core.math_engine import MathEngine

class MathPlaybook:
    def __init__(self, config, max_length=5, cache_path="/Users/kirencaldwell/Documents/Equadium/playbook_cache.json"):
        # Maps a canonical SymPy result to a list of symbol sequences
        # e.g., { 2*x: [ ['d/dx(', 'x**2', ')'], ['2', '*', 'x'] ] }
        self.catalog = defaultdict(list)
        self.engine = MathEngine(config)
        self.cache_path = cache_path
        
        # 1. Determine unique symbols for metadata
        unique_symbols = sorted([s for s in config["tiles"].keys() if s != "="])
        
        # 2. Try loading from cache
        if self._load_cache(unique_symbols, max_length):
            return
            
        # 3. Cache miss: build catalog and save to cache
        self._build_catalog(config["tiles"], max_length)
        self._save_cache(unique_symbols, max_length)

    def _load_cache(self, unique_symbols, max_length):
        if not os.path.exists(self.cache_path):
            return False
        try:
            with open(self.cache_path, "r") as f:
                data = json.load(f)
            
            metadata = data.get("metadata", {})
            if metadata.get("max_length") != max_length:
                return False
            if metadata.get("unique_symbols") != unique_symbols:
                return False
            if metadata.get("cache_version") != "v2":
                return False
            
            # Metadata matches! Load catalog
            catalog_raw = data.get("catalog", {})
            for expr_str, recipes in catalog_raw.items():
                try:
                    expr = sp.sympify(expr_str)
                except Exception:
                    expr = self.engine._parse_expression(expr_str)
                self.catalog[expr] = recipes
            print(f"✅ Playbook loaded from cache ({self.cache_path}) with {len(self.catalog)} unique identities.")
            return True
        except Exception as e:
            print(f"⚠️ Failed to load playbook cache: {e}")
            return False

    def _save_cache(self, unique_symbols, max_length):
        try:
            catalog_raw = {}
            for expr, recipes in self.catalog.items():
                catalog_raw[str(expr)] = recipes
                
            data = {
                "metadata": {
                    "unique_symbols": unique_symbols,
                    "max_length": max_length,
                    "cache_version": "v2"
                },
                "catalog": catalog_raw
            }
            with open(self.cache_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"💾 Playbook cached to {self.cache_path}.")
        except Exception as e:
            print(f"⚠️ Failed to save playbook cache: {e}")


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
