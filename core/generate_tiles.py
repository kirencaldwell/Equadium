import os

# --- THE DESIGN SYSTEM (Edit colors and fonts here!) ---
THEME = {
    "bg_color": "#ffffff",          # Wordle-white tile background
    "border_color": "#d3d6da",      # Muted gray border
    "text_color": "#121212",        # NYT charcoal-black text
    "border_width": "2",            # Pixel thickness of tile frame
    "font_sans": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "font_mono": "SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"
}

# --- SYMBOL CONFIGURATION ---
# Tuple format: (type, string_to_render, font_size)
# Types: 'text' (sans-serif), 'mono' (monospace), 'fraction' (stacked)
# font_size is optional; defaults are applied per type if omitted.
TILES_TO_GENERATE = {
    # Single-character variables — monospace, generous size
    "x":      ("mono",     "x",      44),
    "y":      ("mono",     "y",      44),
    "b":      ("mono",     "b",      44),
    # Operators — sans-serif, generous size
    "=":      ("text",     "=",      44),
    "+":      ("text",     "+",      44),
    "-":      ("text",     "−",      44),   # True mathematical minus sign
    "*":      ("text",     "×",      44),
    "/":      ("text",     "÷",      44),
    # Parentheses — tall glyphs need a slightly smaller size and fine-tuned baseline
    "(":      ("paren",    "(",      52),
    ")":      ("paren",    ")",      52),
    # Calculus operators
    "d/dx(":  ("fraction", ("d", "dx"), None),
    "int(":   ("text",     "∫",      52),
    "C":      ("text",     "C",      44),
    # Powers — superscripts read well at full size
    "x**2":   ("mono",     "x²",     40),
    "x**3":   ("mono",     "x³",     40),
    "x**4":   ("mono",     "x⁴",     40),
    # Fractions
    "1/2":    ("fraction", ("1", "2"),   None),
    "1/3":    ("fraction", ("1", "3"),   None),
    "1/6":    ("fraction", ("1", "6"),   None),
    "1/63":   ("fraction", ("1", "63"),  None),
    # Compound expressions — must be smaller to fit the tile
    "(x+a)":  ("mono",     "(x+a)",  26),
    "(x+b)":  ("mono",     "(x+b)",  26),
    "e^x":    ("mono",     "eˣ",     40),
    "cos(x)": ("mono",     "cos",    36),
    # Numbers
    "2":      ("text",     "2",      44),
    "3":      ("text",     "3",      44),
    "4":      ("text",     "4",      44),
}

# --- FILENAME SANITIZER ---
def sanitize_filename(symbol):
    """Map a game symbol to a safe, unambiguous filename stem."""
    return (symbol
            .replace("/",  "_div_")
            .replace("(",  "_paren_")
            .replace(")",  "_paren_close_")
            .replace("*",  "_mul_")
            .replace("^",  "_pow_"))

# --- SVG TEMPLATES ---
def make_text_tile(symbol, font, font_size=44):
    """Standard single-glyph tile with a centred text label."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect x="2" y="2" width="96" height="96" fill="{THEME['bg_color']}" stroke="{THEME['border_color']}" stroke-width="{THEME['border_width']}" rx="4"/>
    <text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" font-family="{font}" font-size="{font_size}" font-weight="600" fill="{THEME['text_color']}">{symbol}</text>
</svg>"""

def make_paren_tile(symbol, font_size=52):
    """
    Parenthesis tile.  Parens have a very tall ascender/descender which causes
    dominant-baseline="middle" to sit low.  We nudge the y-anchor up slightly
    and use a geometric centre rather than typographic baseline compensation.
    """
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect x="2" y="2" width="96" height="96" fill="{THEME['bg_color']}" stroke="{THEME['border_color']}" stroke-width="{THEME['border_width']}" rx="4"/>
    <text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" font-family="{THEME['font_sans']}" font-size="{font_size}" font-weight="400" fill="{THEME['text_color']}">{symbol}</text>
</svg>"""

def make_fraction_tile(num, denom):
    """Stacked fraction or d/dx tile with a horizontal rule."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect x="2" y="2" width="96" height="96" fill="{THEME['bg_color']}" stroke="{THEME['border_color']}" stroke-width="{THEME['border_width']}" rx="4"/>
    <text x="50%" y="36%" dominant-baseline="middle" text-anchor="middle" font-family="{THEME['font_mono']}" font-size="24" font-weight="700" fill="{THEME['text_color']}">{num}</text>
    <line x1="30" y1="50" x2="70" y2="50" stroke="{THEME['text_color']}" stroke-width="3" stroke-linecap="round"/>
    <text x="50%" y="68%" dominant-baseline="middle" text-anchor="middle" font-family="{THEME['font_mono']}" font-size="22" font-weight="700" letter-spacing="-1" fill="{THEME['text_color']}">{denom}</text>
</svg>"""

# --- GENERATION ENGINE ---
def generate_assets():
    output_dir = "web/static/assets/tiles"
    os.makedirs(output_dir, exist_ok=True)
    print(f"🚀 Generating assets in {output_dir}...")

    generated_map = {}
    for symbol, config in TILES_TO_GENERATE.items():
        style, data, font_size = config

        filename = sanitize_filename(symbol)
        filepath = os.path.join(output_dir, f"{filename}.svg")

        if style == "fraction":
            svg_content = make_fraction_tile(data[0], data[1])
        elif style == "paren":
            svg_content = make_paren_tile(data, font_size or 52)
        elif style == "mono":
            svg_content = make_text_tile(data, THEME["font_mono"], font_size or 44)
        else:  # "text"
            svg_content = make_text_tile(data, THEME["font_sans"], font_size or 44)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg_content.strip())

        generated_map[symbol] = f"/assets/tiles/{filename}.svg"
        print(f"  ✓  {symbol!r:12s} → {filename}.svg  (size={font_size}, style={style})")

    print(f"\n✅ Successfully generated {len(TILES_TO_GENERATE)} vector tiles.")
    return generated_map

if __name__ == "__main__":
    generate_assets()
