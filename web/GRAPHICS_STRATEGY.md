# Equadium Graphic Assets Strategy

This document outlines the best practices for generating and integrating high-quality graphical assets into the Equadium web interface.

## 1. Asset Generation
To ensure the graphics are sharp, scalable, and easy to manipulate:
*   **Format:** Request **SVG (Scalable Vector Graphics)** from the designer.
*   **Why SVGs:**
    *   **Scalability:** They remain crisp at any size (perfect for mobile to desktop scaling).
    *   **Manipulability:** Since SVGs are text-based XML, they can be styled, colored, and animated directly via CSS or JavaScript (e.g., turning a tile green or orange based on validity).
*   **Deliverables:** Request a directory of individual `.svg` files—one for each unique symbol defined in `game_config.py`.

## 2. Technical Integration Plan
To prepare the codebase for these assets, we will move toward a declarative rendering approach.

### A. Asset Mapping
We will create a centralized mapping in the frontend JavaScript to associate game data symbols with their corresponding asset paths:
```javascript
const TILE_ASSETS = {
    "x": "/assets/tiles/x.svg",
    "=": "/assets/tiles/equals.svg",
    // ...
};
```

### B. Inline SVG Rendering
Instead of using `innerText` for tile symbols, we will render `<img>` tags or, ideally, **inline the SVG code directly**. This allows us to apply CSS classes directly to the SVG elements for state-based styling (e.g., changing stroke colors for validation).

## 3. Responsive Board Design
To ensure the board looks professional on any device, we will update the CSS layout to use modern responsive techniques:
*   **`aspect-ratio: 1 / 1;`**: Guarantees grid cells remain perfect squares.
*   **`width: clamp(min, preferred, max);`**: Ensures the board is fluid, scaling between defined bounds based on screen width.

## 4. Next Steps for Implementation
1.  **Preparation:** Nanobanana generates the SVG icons.
2.  **Deployment:** Place the SVG files in `web/static/assets/`.
3.  **Refactoring:** Update `renderBoard()` and `renderRack()` in `index.html` to map tile data to these assets and apply appropriate CSS classes.
