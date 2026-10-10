import sympy as sp
import random

CONFIG = {
    "board_dimensions": (25, 25),
    "max_rack_size": 15,
    "require_plus_c": True,
    "max_turns": 75,
    "stall_rounds": 3,  # game ends after this many full rounds with no tiles played (0 = off)
    "tiles": {
        # Variables & Polynomials
        "x": {"count": 14, "points": 1},
        "(x+a)": {"count": 7, "points": 3},
        "(x+b)": {"count": 3, "points": 3},
        "x**2": {"count": 11, "points": 2},
        "x**3": {"count": 3, "points": 3},
        "x**4": {"count": 2, "points": 3},
        "e^x": {"count": 4, "points": 3},
        "exp(": {"count": 4, "points": 3},
        "sin(x)": {"count": 8, "points": 5},
        "cos(x)": {"count": 5, "points": 5},
        "ln(x)": {"count": 6, "points": 5},
        "1/x": {"count": 6, "points": 3},
        #"sin(": {"count": 4, "points": 5},
        #"cos(": {"count": 4, "points": 5},
        
        # Numbers & Basic Operators
        "a": {"count": 5, "points": 1},
        "b": {"count": 5, "points": 1},
        "k": {"count": 6, "points": 0},   # the wild constant: any single number can stand in for k
        "2": {"count": 14, "points": 1},
        "3": {"count": 7, "points": 1},
        "4": {"count": 6, "points": 1},
        "1/2": {"count": 6, "points": 2},
        "1/3": {"count": 5, "points": 3},
        "1/6": {"count": 4, "points": 3},
        "+": {"count": 14, "points": 1},
        "-": {"count": 8, "points": 1},
        #"*" : {"count": 16, "points": 0},  # <--- MAKE SURE THIS LINE IS HERE
        
        # Calculus Operations (Multipliers)
        "d/dx(": {"count": 5, "points": 7, "expr_multiplier": 2}, 
        "int(": {"count": 5, "points": 10, "expr_multiplier": 3},
        ")": {"count": 14, "points": 0},
        "C": {"count": 6, "points": 5},
    },
    "equals_tile": {
        "count": 40,
        "points": 0
    }
}
