# Guest-room murphy bed + shelves

Parametric design for a centered Create-A-Bed queen vertical bed flanked by two shelf towers.

    pip install matplotlib
    python3 murphy-bed/design.py      # edit the parameters at the top first

Outputs in `out/`: `cutlist.md` / `cutlist.csv`, `model.scad` (open in free OpenSCAD),
`front_elevation.png`, `plan.png`, `side_section.png`, `iso.png`, `sheets_*.png`.

The bed cabinet parts come straight from the kit plans (pp. 2, 5, 6) and must not be altered.
Values marked ASSUMED in `design.py` (ceiling, crown, base moulding, window width, room depth)
need to be measured before cutting.

## Checking the design

    python3 -m unittest -v murphy-bed/test_design.py

31 checks: kit parts match the plans' bill of materials and each other, everything fits the room
(widths, centering, ceiling/crown gap with floor drop, window clearance, open-bed depth), no solids
overlap, the cut list matches the 3D model, shelf sag, wall-anchor count, and every sheet layout keeps
a 1/8" kerf and edge trim. These show the design is internally consistent; they do not replace a
dry fit or the manufacturer's sign-off on how the towers attach.
