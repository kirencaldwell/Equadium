import sympy as sp
import random

CONFIG = {
    "board_dimensions": (25, 25),
    "max_rack_size": 15,
    "require_plus_c": True,
    "max_turns": 75,
    "tiles": {
        # Variables & Polynomials
        "x": {"count": 6, "points": 1},
        "(x+a)": {"count": 6, "points": 3},
        "(x+b)": {"count": 6, "points": 3},
        "x**2": {"count": 6, "points": 2},
        "x**3": {"count": 2, "points": 3},
        "x**4": {"count": 2, "points": 3},
        "e^x": {"count": 3, "points": 3},
        "exp(": {"count": 3, "points": 3},
        "sin(x)": {"count": 4, "points": 5},
        "cos(x)": {"count": 4, "points": 5},
        #"sin(": {"count": 4, "points": 5},
        #"cos(": {"count": 4, "points": 5},
        
        # Numbers & Basic Operators
        "a": {"count": 4, "points": 1},
        "b": {"count": 4, "points": 1},
        "2": {"count": 6, "points": 1},
        "3": {"count": 4, "points": 1},
        "4": {"count": 4, "points": 1},
        "1/2": {"count": 4, "points": 2},
        "1/3": {"count": 4, "points": 3},
        "1/6": {"count": 4, "points": 3},
        "+": {"count": 6, "points": 1},
        #"*" : {"count": 16, "points": 0},  # <--- MAKE SURE THIS LINE IS HERE
        
        # Calculus Operations (Multipliers)
        "d/dx(": {"count": 6, "points": 7, "expr_multiplier": 2}, 
        "int(": {"count": 4, "points": 10, "expr_multiplier": 3},
        ")": {"count": 10, "points": 0},
        "C": {"count": 10, "points": 5},
    },
    "equals_tile": {
        "count": 40,
        "points": 0
    }
}
