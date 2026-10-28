"""Parametric guest-room murphy bed + shelving design.

Run:  python3 murphy-bed/design.py
Writes cut list, sheet layouts, an OpenSCAD model and preview drawings to murphy-bed/out/.

Coordinates (inches): x along the bed wall (0 = corner with the window wall),
y out from the bed wall, z up from the floor.

The bed cabinet follows the Create-A-Bed "Queen Vertical, Adjustable, plywood" plans
exactly (UPDATED_Queen_Adjustable.pdf, pp. 2, 5, 6). Everything else is ours.
"""
import csv
import math
import os
from dataclasses import dataclass, field
from fractions import Fraction

# ----------------------------------------------------------------------------
# Parameters. Values marked (ASSUMED) must be measured before cutting.
# ----------------------------------------------------------------------------
WALL_W = 132.0          # bed wall length (given: 11 ft)
CEIL_H = 96.0           # ceiling height (given: 8 ft, ASSUMED level)
WINDOW_FROM_WALL = 30.0  # window starts 2.5 ft from the bed wall (given)
ROOM_D = 130.0          # window-wall length, only used for drawings (ASSUMED)

EXIST_CROWN_H = 3.5     # existing room crown height (ASSUMED)
EXIST_CROWN_PROJ = 3.5  # ... and how far it sticks out (ASSUMED)
NEW_CROWN_H = 3.5       # new crown, same style as existing (ASSUMED)
NEW_CROWN_PROJ = 3.5
BASE_H = 4.0            # new base molding / toe-kick height (ASSUMED)
EXIST_BASE_H = 4.5      # existing room baseboard (ASSUMED)
EXIST_BASE_T = 0.5
SCRIBE = 0.75           # fitting gap left at each wall end
FLOOR_DROP = 1.0        # floor falls this much across the 132" wall (ASSUMED; measure it)

# Create-A-Bed queen vertical (plans p.2 and p.6)
BED_W, BED_H, BED_D = 65.875, 87.125, 15.875
BED_OPEN_PROJ = 87.0

# Shelf towers: flush with the bed cabinet front so the crown runs straight.
T = 0.75                # plywood thickness
BACK_T = 0.25
TOWER_D = BED_D
TOWER_W = (WALL_W - BED_W - 2 * SCRIBE) / 2      # bed centered => both equal
CARCASS_D = TOWER_D - T                          # overlay doors add T, so door faces sit flush with the bed cabinet front
SIDE_D = CARCASS_D - BACK_T                      # sides/fixed panels sit in front of the applied 1/4" back
SHELF_D = SIDE_D - 0.25
STUD_OC = 16.0          # wall stud spacing (ASSUMED; the kit needs 4 anchor points in the header rail)
FACE_PANEL_H = 81.75    # plans p.6 part E
HEADER_FRONT_RAIL = 2.75  # plans p.6 part H front rail covers the top of the opening
FACE_PANEL_BOTTOM = BED_H - HEADER_FRONT_RAIL - FACE_PANEL_H   # ~2-5/8" above the floor
LOWER_H = 36.0          # top of lower (doored) section
KERF = 0.125            # user's saw blade is at least 1/8"; every cut in the layouts costs this much
EDGE_TRIM = 0.125       # factory edge trimmed off one long edge and one end of every sheet
SHEET_W, SHEET_L = 48.0, 96.0


def frac(x, denom=16):
    """Format inches as a woodworker fraction, e.g. 15-7/8."""
    f = Fraction(round(x * denom), denom)
    whole, rem = divmod(f.numerator, f.denominator)
    if rem == 0:
        return f'{whole}"'
    return f'{whole}-{rem}/{f.denominator}"' if whole else f'{rem}/{f.denominator}"'


@dataclass
class Part:
    name: str
    group: str      # bed | tower
    mat: str        # '3/4 ply' | '1/4 ply' | 'solid'
    qty: int
    w: float        # across the grain / sheet width
    l: float        # along the grain / sheet length
    note: str = ''


# ----------------------------------------------------------------------------
# Cut list
# ----------------------------------------------------------------------------
def bed_parts():
    """Straight from the Create-A-Bed bill of materials (plans p.6)."""
    return [
        Part('B Side rail', 'bed', '3/4 ply', 2, 5.875, 81.5, 'plywood only; pivot/ball-stud holes per plans pp.8-17'),
        Part('C Foot rail', 'bed', '3/4 ply', 1, 3, 62),
        Part('D Head rail', 'bed', '3/4 ply', 1, 5.875, 62),
        Part('E Bed face panel', 'bed', '3/4 ply', 2, 32, 81.75, 'best face out; edge-band long edges'),
        Part('F Headboard', 'bed', '3/4 ply', 1, 15.875, 64.375),
        Part('G Cabinet vertical', 'bed', '3/4 ply', 2, 15.875, 87.125, 'notch rear bottom for baseboard (plans p.8, step 14)'),
        Part('H Header board', 'bed', '3/4 ply', 1, 14.375, 64.375),
        Part('H Header front rail', 'bed', '3/4 ply', 1, 2.75, 64.375),
        Part('H Header rear rail', 'bed', '3/4 ply', 1, 2.75, 64.375, 'glue + screw; carries the wall anchors'),
        Part('J Mattress support', 'bed', '1/4 ply', 2, 31, 80),
        Part('A Frame strut', 'bed', 'solid', 10, 1.5, 60.5, '3/4" thick; poplar/maple/clear pine'),
        Part('A Frame side', 'bed', 'solid', 2, 1.5, 80, '3/4" thick'),
        Part('H Mounting cleat', 'bed', 'solid', 2, 1.5, 14.375, '3/4" thick'),
        Part('I Leg support rail', 'bed', 'solid', 1, 0.75, 60.25, '3/4" square'),
    ]


def tower_parts():
    n = 2                                   # two identical towers (bed centered)
    inner = TOWER_W - 2 * T
    side_d = SIDE_D                         # back panel applied over rear edges; doors flush to bed front
    door_w = TOWER_W / 2 - 0.125
    door_h = LOWER_H - BASE_H - 0.125
    return [
        Part('Tower side', 'tower', '3/4 ply', 2 * n, side_d, BED_H, 'notch rear bottom for baseboard'),
        Part('Tower top', 'tower', '3/4 ply', n, side_d, inner),
        Part('Tower bottom (at base-molding height)', 'tower', '3/4 ply', n, side_d, inner),
        Part('Tower fixed divider (lower/upper)', 'tower', '3/4 ply', n, side_d, inner),
        Part('Tower adjustable shelf', 'tower', '3/4 ply', 3 * n, SHELF_D, inner - 0.25,
             'band front edge; 5 mm shelf-pin holes'),
        Part('Tower door (full overlay)', 'tower', '3/4 ply', 2 * n, door_w, door_h),
        Part('Tower back', 'tower', '1/4 ply', n, TOWER_W, BED_H),
        Part('Scribe strip (rip to fit walls)', 'tower', '3/4 ply', 2, 3.0, BED_H, 'face-attached to outer tower sides'),
    ]


def trim_list():
    run = WALL_W
    return [
        ('Crown moulding, front run', f'one 12 ft length (front run is {frac(run)}, wall to wall, so no splice); square ends scribed to the side walls'),
        ('Crown moulding, end returns', 'none: the unit runs wall to wall. Only needed if you leave an end exposed (see trim_guide.md)'),
        ('Crown backer blocks', f'3/4" ply, {frac(NEW_CROWN_H)} x {frac(NEW_CROWN_PROJ)} triangles (45-degree spring angle), one every 16" = ~{math.ceil(run / 16) + 1} blocks'),
        ('Base moulding, front run', f'{WALL_W / 12:.1f} ft ({frac(run)}), one 12 ft length of stock at least {frac(BASE_H + FLOOR_DROP + 0.25)} tall, scribed to the floor (top edge level); butts into the side-wall baseboards'),
        ('Edge banding', '13/16" iron-on, ~80 ft for bed (plans) + ~65 ft for towers/shelves'),
    ]


# ----------------------------------------------------------------------------
# Sheet packer (guillotine, grain along length, no rotation)
# ----------------------------------------------------------------------------
def expand(parts):
    out = []
    for p in parts:
        out += [(f'{p.name}', p.w, p.l)] * p.qty
    return out


def pack_once(items, remnants, order):
    items = sorted(items, key=order)
    sheets = [{'free': [(0, 0, w, h)], 'placed': [], 'remnant': True, 'w': w, 'l': h} for (w, h) in remnants]
    for name, pw, pl in items:
        spot = None
        for si, s in enumerate(sheets):
            best = None
            for ri, (rx, ry, rw, rh) in enumerate(s['free']):
                if pw <= rw + 1e-9 and pl <= rh + 1e-9:
                    score = min(rw - pw, rh - pl)
                    if best is None or score < best[0]:
                        best = (score, ri)
            if best:
                spot = (si, best[1])
                break
        if spot is None:
            sheets.append({'free': [(0, 0, SHEET_W - EDGE_TRIM, SHEET_L - EDGE_TRIM)], 'placed': [], 'remnant': False, 'w': SHEET_W, 'l': SHEET_L})
            spot = (len(sheets) - 1, 0)
        s = sheets[spot[0]]
        rx, ry, rw, rh = s['free'].pop(spot[1])
        s['placed'].append((name, rx, ry, pw, pl))
        lw, lh = rw - pw - KERF, rh - pl - KERF
        if (rw - pw) < (rh - pl):
            news = [(rx + pw + KERF, ry, lw, pl), (rx, ry + pl + KERF, rw, lh)]
        else:
            news = [(rx + pw + KERF, ry, lw, rh), (rx, ry + pl + KERF, pw, lh)]
        s['free'] += [r for r in news if r[2] > 0.5 and r[3] > 0.5]
    return sheets


def pack(items, remnants=()):
    orders = [lambda i: (-i[2], -i[1]), lambda i: (-i[1] * i[2]), lambda i: (-i[1], -i[2])]
    best = None
    for o in orders:
        res = pack_once(items, remnants, o)
        new = sum(1 for s in res if not s['remnant'])
        waste = sum(s['w'] * s['l'] for s in res if not s['remnant']) - sum(
            pw * pl for s in res if not s['remnant'] for _, _, _, pw, pl in s['placed'])
        if best is None or (new, waste) < best[0]:
            best = ((new, waste), res)
    return best[1]


def check_layout(sheets):
    """Assert every part is inside the trimmed sheet and every pair is >= KERF apart. Returns min slack."""
    worst = 9e9
    for s in sheets:
        pl = s['placed']
        for i, (_, x1, y1, w1, h1) in enumerate(pl):
            assert x1 >= -1e-9 and y1 >= -1e-9
            assert x1 + w1 <= s['w'] - EDGE_TRIM + 1e-9 and y1 + h1 <= s['l'] - EDGE_TRIM + 1e-9, 'part outside trimmed sheet'
            for _, x2, y2, w2, h2 in pl[i + 1:]:
                gap = max(x2 - (x1 + w1), x1 - (x2 + w2), y2 - (y1 + h1), y1 - (y2 + h2))
                assert gap >= KERF - 1e-9, f'parts closer than kerf ({gap:.4f})'
                worst = min(worst, gap)
    return worst


# ----------------------------------------------------------------------------
# 3D boxes for the model/preview: (name, group, x, y, z, dx, dy, dz)
# ----------------------------------------------------------------------------
def boxes():
    b = []
    tw = TOWER_W
    x_left = SCRIBE
    x_bed = SCRIBE + tw
    x_right = x_bed + BED_W
    # bed cabinet
    b.append(('bed vertical L', 'bed', x_bed, 0, 0, T, BED_D, BED_H))
    b.append(('bed vertical R', 'bed', x_right - T, 0, 0, T, BED_D, BED_H))
    b.append(('bed header', 'bed', x_bed + T, 0, BED_H - HEADER_FRONT_RAIL, BED_W - 2 * T, BED_D, HEADER_FRONT_RAIL))
    b.append(('bed face (closed)', 'bed', x_bed + T + 0.5, BED_D - 0.75 - 0.125, FACE_PANEL_BOTTOM, BED_W - 2 * T - 1.0, 0.75, FACE_PANEL_H))
    b.append(('bed frame (closed)', 'bedin', x_bed + T + 0.75, BED_D - 0.875 - 6.0, FACE_PANEL_BOTTOM, BED_W - 2 * T - 1.5, 6.0, FACE_PANEL_H - 0.75))
    for nm, x0 in (('L', x_left), ('R', x_right)):
        b.append((f'tower side out {nm}', 'tower', x0, BACK_T, 0, T, SIDE_D, BED_H))
        b.append((f'tower side in {nm}', 'tower', x0 + tw - T, BACK_T, 0, T, SIDE_D, BED_H))
        b.append((f'tower back {nm}', 'tower', x0, 0, 0, tw, BACK_T, BED_H))
        for z in (BASE_H, LOWER_H - T, BED_H - T):
            b.append((f'tower fixed {nm} z{z}', 'tower', x0 + T, BACK_T, z, tw - 2 * T, SIDE_D, T))
        up_lo, up_hi = LOWER_H, BED_H - T
        gap = (up_hi - up_lo - 3 * T) / 4
        for k in range(1, 4):
            z = up_lo + k * gap + (k - 1) * T
            b.append((f'tower shelf {nm} {k}', 'shelf', x0 + T + 0.125, BACK_T, z, tw - 2 * T - 0.25, SHELF_D, T))
        dw = tw / 2 - 0.125
        for j in range(2):
            b.append((f'door {nm}{j}', 'door', x0 + 0.0625 + j * (dw + 0.125), TOWER_D - T, BASE_H, dw, T, LOWER_H - BASE_H - 0.125))
    # trim
    b.append(('base molding', 'trim', SCRIBE, TOWER_D, 0, WALL_W - 2 * SCRIBE, 0.75, BASE_H))
    b.append(('crown', 'trim', SCRIBE, 0, BED_H, WALL_W - 2 * SCRIBE, TOWER_D + NEW_CROWN_PROJ, NEW_CROWN_H))
    return b


COLORS = {'bed': '#c9a46c', 'bedin': '#a98550', 'tower': '#d8c29a', 'shelf': '#cfb284', 'door': '#e6d5b0', 'trim': '#f3ede0'}


def write_scad(path):
    lines = ['// Generated by design.py - open in OpenSCAD (free).  Units: inches.', 'union() {']
    for name, g, x, y, z, dx, dy, dz in boxes():
        lines.append(f'  // {name}\n  translate([{x:.4f},{y:.4f},{z:.4f}]) cube([{dx:.4f},{dy:.4f},{dz:.4f}]);')
    lines.append('}')
    lines.append('// Room reference (wall, ceiling): comment out if unwanted')
    lines.append(f'%translate([0,-1,0]) cube([{WALL_W},1,{CEIL_H}]);')
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


# ----------------------------------------------------------------------------
# Reports
# ----------------------------------------------------------------------------
def sheet_report(title, sheets):
    new = [s for s in sheets if not s['remnant']]
    out = [f'**{title}: {len(new)} sheet(s)**', '']
    for i, s in enumerate(sheets):
        out.append(f'- Sheet {i + 1}: ' + '; '.join(f'{n} {frac(w)}x{frac(h)}' for n, _, _, w, h in s['placed']))
    return out


def write_trim_guide(outdir):
    notch_h, notch_d = EXIST_BASE_H + 0.125, EXIST_BASE_T + 0.125
    g = f"""# Trim guide

All trim values come from the parameters in `design.py`; the ones marked (assumed) need your measurements.
The Create-A-Bed plans only show trim as *options* (p.3) and give two instructions: notch the cabinet
verticals for existing baseboard (assembly booklet p.8, step 14) and apply base moulding across the front
after the bed is closed (p.11, step 20). Everything else below is our design, not the maker's.

## 1. Crown moulding (front run, {frac(NEW_CROWN_H)} tall x {frac(NEW_CROWN_PROJ)} projection, assumed)

- **Match the room.** Use the same profile as the existing crown. Take a 6" offcut or a photo with a profile
  gauge from a hidden spot (closet, behind a door) to your supplier. The assumed size is a 45-degree spring
  angle crown, which has equal height and projection.
- **Stock.** One 12 ft piece. The front run is {frac(WALL_W)} wall to wall, so there is no splice.
- **Backer blocks.** Cut 3/4" ply triangles with legs of {frac(NEW_CROWN_H)} x {frac(NEW_CROWN_PROJ)} (45-degree hypotenuse).
  Screw them to the tops of the towers and the bed header front rail about every 16" (about {math.ceil(WALL_W / 16) + 1}).
  Nail the crown to them. Do not hang the crown from the bed header alone, and do not add weight to the bed itself.
- **Cutting the ends.** No mitres are needed for a straight wall-to-wall run. Cut one end 1/16" long, hold it
  against the first side wall, mark the wall's angle, and trim to fit. Then do the other end the same way, a hair
  long first. Check with a test-fit before nailing. Walls are rarely square, so scribe each end.
- **If you leave an end exposed** (a return back to the wall): cut the crown upside down and backwards on the
  mitre saw, resting against the fence at its spring angle. For a 90-degree outside corner use mitre 35.3 degrees /
  bevel 30 degrees on a 45-degree spring crown, or mitre 31.6 / bevel 33.9 on a 38-degree one. Cut a scrap first.
- **Clearance.** New crown top is {frac(BED_H + NEW_CROWN_H)}; existing crown starts at {frac(CEIL_H - EXIST_CROWN_H)}.
  With the assumed {frac(FLOOR_DROP)} floor drop the gap is {frac(CEIL_H - EXIST_CROWN_H - BED_H - NEW_CROWN_H - FLOOR_DROP)}.
  Do the dry fit before cutting the crown.

## 2. Base moulding (front run)

- **Stock.** At least {frac(BASE_H + FLOOR_DROP + 0.25)} tall: {frac(BASE_H)} showing at the high point plus {frac(FLOOR_DROP)} for the
  assumed floor drop and 1/4" extra. A 1x6 ripped to size works.
- **Scribing to the floor.** Set the cabinet level first. Mark the base's top line level at {frac(BASE_H)} above the shim
  height. Set a compass (or a block and pencil) to the largest floor gap, trace the floor onto the board, cut to the line with a
  jigsaw or block plane, and back-bevel the cut about 5 degrees so the bottom edge meets the floor tight.
- **Cutting the ends.** Square ends that butt into the existing baseboards on the side walls, scribed like the crown.
- **Fastening.** Glue and finish-nail it to the front edges of the tower bottoms. Do not nail it to the bed's face panel.
- **Bed clearance.** The face panel bottom sits about {frac(FACE_PANEL_BOTTOM)} above the floor, so the base must stand in front of it
  (the kit's p.21 drawing). Confirm with Create-A-Bed before you rely on that.

## 3. Baseboard notches (kit step 14, extended to the towers)

If you keep the existing baseboard (assumed {frac(EXIST_BASE_H)} tall x {frac(EXIST_BASE_T)} thick), notch the rear bottom of each bed
vertical **and** each tower side: **{frac(notch_h)} tall x {frac(notch_d)} deep**. For the 1/4" tower backs, cut the bottom edge
{frac(notch_h)} short instead. Simplest alternative: remove the baseboard behind the unit and patch it later.

## 4. Edge banding (the "F" edges)

- Use 13/16" iron-on veneer: bed face panels (long edges), cabinet verticals (front edges), header rails, tower fronts, shelf fronts.
- Iron slowly at medium-high heat, press with a block, trim the overhang with a banding trimmer or sharp chisel,
  and lightly sand the edge flush. Do the end grain first, then the long edges.

## 5. Face-panel moulding (optional, kit p.3)

The kit's "full-length" and "split" panel mouldings add weight to the face panel. The gas springs are balanced for
the plain panel, so call Create-A-Bed (1-877-966-3852) before adding any.

## 6. Order of work

1. Remove or notch the baseboard. 2. Set the bed cabinet level and plumb on shims and anchor it into studs.
3. Set the towers to its height and tie them to the cabinet and wall. 4. Fit and scribe the scribe strips to the side walls.
5. Fit the crown (dry fit first), then the base moulding. 6. Caulk, fill, and paint.
"""
    with open(os.path.join(outdir, 'trim_guide.md'), 'w') as f:
        f.write(g)


def write_reports(outdir):
    bed, tower = bed_parts(), tower_parts()
    rows = []
    for grp in (bed, tower):
        for p in grp:
            rows.append([p.group, p.name, p.mat, p.qty, frac(p.w), frac(p.l), p.note])
    with open(os.path.join(outdir, 'cutlist.csv'), 'w', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['group', 'part', 'material', 'qty', 'width (across grain)', 'length (along grain)', 'notes'])
        wr.writerows(rows)

    sheet_parts = [p for p in bed + tower if p.mat != 'solid']
    s34 = pack(expand([p for p in sheet_parts if p.mat == '3/4 ply']))
    s14 = pack(expand([p for p in sheet_parts if p.mat == '1/4 ply']))
    check_layout(s34)
    check_layout(s14)

    md = []
    md.append('# Cut list\n')
    md.append(f'Centered layout on an {WALL_W / 12:.0f} ft wall: towers {frac(TOWER_W)} wide each, bed cabinet {frac(BED_W)}, '
              f'{frac(SCRIBE)} fitting gap at each wall. Everything {frac(BED_H)} tall + {frac(NEW_CROWN_H)} crown '
              f'= {frac(BED_H + NEW_CROWN_H)} under a {frac(CEIL_H)} ceiling.\n')
    for title, grp in (('Murphy bed cabinet (Create-A-Bed plans, do not alter)', bed), ('Shelf towers (x2, mirror-identical)', tower)):
        md.append(f'## {title}\n')
        md.append('| Part | Material | Qty | Width | Length | Notes |')
        md.append('|---|---|---|---|---|---|')
        for p in grp:
            md.append(f'| {p.name} | {p.mat} | {p.qty} | {frac(p.w)} | {frac(p.l)} | {p.note} |')
        md.append('')
    md.append('## Sheet goods\n')
    md.append(f'All sheet parts (bed cabinet and towers) are packed together with a {frac(KERF)} kerf between every pair of parts '
              f'and {frac(EDGE_TRIM)} trimmed off one long edge and one end of each sheet (grain along the 96" length, no rotation). '
              'The bed-kit parts keep the exact sizes from the plans; only their positions on the sheet differ from the plans\' cutting guide, '
              'because that guide leaves only 1/8" of slack (15-7/8" + 32" on a 48" sheet) and fails once edge trim is allowed for.\n')
    md += sheet_report('3/4" plywood', s34) + ['']
    md += sheet_report('1/4" plywood', s14) + ['']
    new34 = sum(1 for s in s34 if not s['remnant'])
    new14 = sum(1 for s in s14 if not s['remnant'])
    md.append(f'**Order total: {new34} sheets 3/4" ply, {new14} sheets 1/4" ply** (4x8). '
              'Add one spare 3/4" sheet for mistakes. Plans call for 4 + 2 for the bed alone, so the towers add '
              f'{new34 - 4} + {new14 - 2}.\n')
    md.append('## Trim\n')
    md += [f'- {a}: {b}' for a, b in trim_list()]
    md.append('')
    md.append('## Hardware (not in the kit)\n')
    md += ['- 5 mm shelf pins: 24 (4 per shelf x 6)',
           '- Concealed hinges, full overlay: 8 (2 per door x 4) + 4 door pulls',
           '- 3" coarse-thread screws or lags into studs: 4 per bed header (kit plans p.10) - confirm stud locations',
           '- Tower-to-wall anchors: 2 cleats or 4 screws per tower; tower-to-bed-cabinet clamp screws through the adjoining sides',
           '- #8 x 1-1/4" and 1-1/2" screws, glue, 1-1/2" finish nails (per kit BOM)']
    md.append('\n## Assumptions to verify\n')
    md += [f'- Ceiling {frac(CEIL_H)}, measured from the HIGH point of the floor (floor is NOT level)',
           f'- Existing crown {frac(EXIST_CROWN_H)} tall x {frac(EXIST_CROWN_PROJ)} proud; new crown {frac(NEW_CROWN_H)} tall (matches style, not measured)',
           f'- Gap between new crown top ({frac(BED_H + NEW_CROWN_H)}) and bottom of existing crown ({frac(CEIL_H - EXIST_CROWN_H)}) is {frac(CEIL_H - EXIST_CROWN_H - BED_H - NEW_CROWN_H)} on a level floor. With a {frac(FLOOR_DROP)} floor drop the unit is set level from the HIGH end of the floor, so the gap shrinks to {frac(CEIL_H - EXIST_CROWN_H - BED_H - NEW_CROWN_H - FLOOR_DROP)}. Ceiling must be measured from the high point of the floor.',
           f'- Floor not level (assumed {frac(FLOOR_DROP)} across the wall): level and plumb the bed cabinet FIRST on shims at the high point, set the towers to match its top, then scribe the base moulding to the floor. Shims go behind the base moulding. The bed cabinet must be plumb or the face-panel gap will be uneven (plans, assembly p.11 steps 19-20).',
           f'- Open bed: the kit legs are fixed tubes, so on a floor that falls up to {frac(FLOOR_DROP)} one leg may hang off the floor. Check the leg ends for adjusters, plan on adjustable glides or a shim, and ask Create-A-Bed.',
           f'- Base moulding {frac(BASE_H)} tall at the high point ({frac(BASE_H + FLOOR_DROP)} at the low end after scribing); check the bed face panel clears it',
           '- Bed wall is flat and has studs at usable spacing; the kit requires wood/metal-stud/masonry anchoring (plans p.10)',
           f'- Window starts {frac(WINDOW_FROM_WALL)} from the bed wall: tower depth {frac(TOWER_D)} leaves {frac(WINDOW_FROM_WALL - TOWER_D)} to the window edge (trim not included)',
           f'- Room is deep enough: open bed projects {frac(BED_OPEN_PROJ)} from the wall, so need > {frac(BED_OPEN_PROJ + 24)} for walking clearance (window-wall length not yet given)']
    with open(os.path.join(outdir, 'cutlist.md'), 'w') as f:
        f.write('\n'.join(md) + '\n')
    write_trim_guide(outdir)
    return s34, s14


# ----------------------------------------------------------------------------
# Drawings
# ----------------------------------------------------------------------------
def draw_all(outdir, s34, s14):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    xl = SCRIBE
    xb = SCRIBE + TOWER_W
    xr = xb + BED_W

    # --- front elevation ---
    fig, ax = plt.subplots(figsize=(11, 8.5))
    ax.add_patch(Rectangle((0, CEIL_H - EXIST_CROWN_H), WALL_W, EXIST_CROWN_H, fc='#eee', ec='#999', hatch='//'))
    ax.add_patch(Rectangle((0, 0), WALL_W, CEIL_H, fc='none', ec='k'))
    for x0 in (xl, xr):
        ax.add_patch(Rectangle((x0, 0), TOWER_W, BED_H, fc=COLORS['tower'], ec='k'))
        for z in (BASE_H, LOWER_H, BED_H - T):
            ax.plot([x0, x0 + TOWER_W], [z, z], 'k', lw=1)
        gap = (BED_H - T - LOWER_H - 3 * T) / 4
        for k in range(1, 4):
            z = LOWER_H + k * gap + (k - 1) * T
            ax.plot([x0 + T, x0 + TOWER_W - T], [z, z], color='#8a6d3b', lw=1.5)
        ax.plot([x0 + TOWER_W / 2] * 2, [BASE_H, LOWER_H], 'k', lw=1)
        for hx in (x0 + TOWER_W / 2 - 2.5, x0 + TOWER_W / 2 + 2.5):
            ax.plot([hx] * 2, [LOWER_H - 9, LOWER_H - 3], color='k', lw=2)
    ax.add_patch(Rectangle((xb, 0), BED_W, BED_H, fc=COLORS['bed'], ec='k'))
    ax.add_patch(Rectangle((xb + T + 0.5, 4), BED_W - 2 * T - 1, 81.75, fc='#e3c590', ec='k'))
    ax.plot([xb + BED_W / 2] * 2, [4, 85.75], 'k', lw=1)
    for hx in (xb + BED_W / 2 - 3, xb + BED_W / 2 + 3):
        ax.plot([hx] * 2, [62, 70], 'k', lw=2.5)
    ax.fill([0, WALL_W, WALL_W], [0, 0, -FLOOR_DROP], color='#c33', alpha=0.5)
    ax.plot([0, WALL_W], [0, -FLOOR_DROP], color='#c33', lw=1.5)
    ax.text(WALL_W / 2, -FLOOR_DROP - 4, f'floor falls {frac(FLOOR_DROP)} (assumed): unit set level at high end, base moulding scribed', ha='center', fontsize=7, color='#c33')
    ax.add_patch(Rectangle((xl, 0), WALL_W - 2 * SCRIBE, BASE_H, fc=COLORS['trim'], ec='k'))
    ax.add_patch(Rectangle((xl, BED_H), WALL_W - 2 * SCRIBE, NEW_CROWN_H, fc=COLORS['trim'], ec='k'))
    ax.text(xl + TOWER_W / 2, 60, 'open shelves\n(3 adjustable)', ha='center', fontsize=9)
    ax.text(xl + TOWER_W / 2, 20, 'doors', ha='center', fontsize=9)
    ax.text(xb + BED_W / 2, 40, 'MURPHY BED\n(Create-A-Bed queen vertical)', ha='center', fontsize=10)
    ax.annotate('', (xl, -12), (xb, -12), arrowprops=dict(arrowstyle='<->'))
    ax.text((xl + xb) / 2, -16, frac(TOWER_W), ha='center', fontsize=9)
    ax.annotate('', (xb, -12), (xr, -12), arrowprops=dict(arrowstyle='<->'))
    ax.text((xb + xr) / 2, -16, frac(BED_W), ha='center', fontsize=9)
    ax.annotate('', (xr, -12), (xr + TOWER_W, -12), arrowprops=dict(arrowstyle='<->'))
    ax.text(xr + TOWER_W / 2, -16, frac(TOWER_W), ha='center', fontsize=9)
    ax.text(WALL_W + 2, BED_H / 2, f'{frac(BED_H)}', rotation=90, va='center', fontsize=9)
    ax.text(WALL_W + 2, CEIL_H - EXIST_CROWN_H / 2, 'existing crown', va='center', fontsize=7)
    ax.set_xlim(-8, WALL_W + 10)
    ax.set_ylim(-22, CEIL_H + 4)
    ax.set_aspect('equal')
    ax.set_title('Front elevation - bed wall (window wall at left, x = 0)')
    ax.axis('off')
    fig.savefig(os.path.join(outdir, 'front_elevation.png'), dpi=130, bbox_inches='tight')
    plt.close(fig)

    # --- plan view ---
    fig, ax = plt.subplots(figsize=(10, 9))
    ax.add_patch(Rectangle((0, 0), WALL_W, ROOM_D, fc='none', ec='k', lw=2))
    ax.add_patch(Rectangle((xl, 0), TOWER_W, TOWER_D, fc=COLORS['tower'], ec='k'))
    ax.add_patch(Rectangle((xr, 0), TOWER_W, TOWER_D, fc=COLORS['tower'], ec='k'))
    ax.add_patch(Rectangle((xb, 0), BED_W, BED_D, fc=COLORS['bed'], ec='k'))
    ax.add_patch(Rectangle((xb, BED_D), BED_W, BED_OPEN_PROJ - BED_D, fc='#bcd', ec='#369', ls='--', alpha=0.6))
    ax.text(WALL_W / 2, 55, f'bed open\n{frac(BED_OPEN_PROJ)} from wall (queen, 60" x 80" mattress)', ha='center', fontsize=10)
    ax.plot([0, 0], [WINDOW_FROM_WALL, WINDOW_FROM_WALL + 48], color='#06c', lw=6)
    ax.text(3, WINDOW_FROM_WALL + 24, 'window\n(starts 30"; width assumed)', fontsize=8, color='#06c')
    ax.annotate('', (0, TOWER_D), (0, WINDOW_FROM_WALL), arrowprops=dict(arrowstyle='<->', color='r'))
    ax.text(-4, (TOWER_D + WINDOW_FROM_WALL) / 2, f'{frac(WINDOW_FROM_WALL - TOWER_D)}\nclear', rotation=90, va='center', ha='right', color='r', fontsize=8)
    ax.text(WALL_W / 2, 7, 'shelf tower | bed cabinet | shelf tower', ha='center', fontsize=9)
    ax.set_xlim(-20, WALL_W + 10)
    ax.set_ylim(ROOM_D + 8, -8)
    ax.set_aspect('equal')
    ax.set_title('Plan view (top = bed wall). Room depth beyond 87" and door not yet known.')
    ax.axis('off')
    fig.savefig(os.path.join(outdir, 'plan.png'), dpi=130, bbox_inches='tight')
    plt.close(fig)

    # --- side section ---
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot([0, 0], [0, CEIL_H], 'k', lw=3)
    ax.plot([0, 120], [0, 0], 'k', lw=2)
    ax.plot([0, 120], [CEIL_H, CEIL_H], 'k', lw=2)
    ax.add_patch(Rectangle((0, CEIL_H - EXIST_CROWN_H), EXIST_CROWN_PROJ, EXIST_CROWN_H, fc='#eee', ec='#666', hatch='//'))
    ax.add_patch(Rectangle((0, 0), BED_D, BED_H, fc=COLORS['bed'], ec='k', alpha=0.8))
    ax.add_patch(Rectangle((0, BED_H), BED_D + NEW_CROWN_PROJ, NEW_CROWN_H, fc=COLORS['trim'], ec='k'))
    ax.add_patch(Rectangle((BED_D - 0.75 - 6.9, 4), 7, 81.75, fc='#a98550', ec='k', alpha=0.9))
    # open bed (approximate deck height: legs ~14" + 12" mattress)
    ax.add_patch(Rectangle((BED_D - 1, 14), BED_OPEN_PROJ - BED_D + 1, 12 + 6, fc='#bcd', ec='#369', ls='--', alpha=0.6))
    ax.text(55, 36, 'bed open (deck height approximate)', ha='center', fontsize=9, color='#369')
    ax.text(BED_D / 2, BED_H / 2, 'cabinet', rotation=90, ha='center', va='center')
    ax.annotate('', (0, -4), (BED_OPEN_PROJ, -4), arrowprops=dict(arrowstyle='<->'))
    ax.text(BED_OPEN_PROJ / 2, -9, f'{frac(BED_OPEN_PROJ)} projection', ha='center', fontsize=9)
    ax.annotate('', (-4, BED_H + NEW_CROWN_H), (-4, CEIL_H - EXIST_CROWN_H), arrowprops=dict(arrowstyle='<->', color='r'))
    ax.text(-6, (BED_H + NEW_CROWN_H + CEIL_H - EXIST_CROWN_H) / 2,
            f'{frac(CEIL_H - EXIST_CROWN_H - BED_H - NEW_CROWN_H)}\ngap', color='r', ha='right', va='center', fontsize=8)
    ax.set_xlim(-14, 118)
    ax.set_ylim(-14, CEIL_H + 4)
    ax.set_aspect('equal')
    ax.set_title('Side section (wall at left)')
    ax.axis('off')
    fig.savefig(os.path.join(outdir, 'side_section.png'), dpi=130, bbox_inches='tight')
    plt.close(fig)

    # --- isometric ---
    fig = plt.figure(figsize=(11, 8))
    ax = fig.add_subplot(111, projection='3d')

    def faces(x, y, z, dx, dy, dz):
        v = [(x, y, z), (x + dx, y, z), (x + dx, y + dy, z), (x, y + dy, z),
             (x, y, z + dz), (x + dx, y, z + dz), (x + dx, y + dy, z + dz), (x, y + dy, z + dz)]
        idx = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
        return [[v[i] for i in f] for f in idx]

    for name, g, x, y, z, dx, dy, dz in boxes():
        ax.add_collection3d(Poly3DCollection(faces(x, y, z, dx, dy, dz), facecolors=COLORS[g], edgecolors='#555', linewidths=0.4))
    ax.set_xlim(0, WALL_W)
    ax.set_ylim(0, 60)
    ax.set_zlim(0, CEIL_H)
    ax.set_box_aspect((WALL_W, 65, CEIL_H))
    ax.view_init(elev=14, azim=68)
    ax.set_axis_off()
    ax.set_title('Model preview (bed closed)')
    fig.savefig(os.path.join(outdir, 'iso.png'), dpi=130, bbox_inches='tight')
    plt.close(fig)

    # --- sheet layouts ---
    for tag, sheets in (('3-4', s34), ('1-4', s14)):
        n = len(sheets)
        fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 8.5), squeeze=False)
        for ax, s in zip(axes[0], sheets):
            ax.add_patch(Rectangle((0, 0), s['w'], s['l'], fc='#f6f1e6', ec='k'))
            for name, x, y, w, h in s['placed']:
                ax.add_patch(Rectangle((x, y), w, h, fc='#d8c29a', ec='k'))
                ax.text(x + w / 2, y + h / 2, f'{name.replace("Tower ", "T ").split(" (")[0]}\n{frac(w)} x {frac(h)}', ha='center', va='center', fontsize=6, rotation=90 if w < 20 and h > 40 else 0)
            ax.set_xlim(-2, SHEET_W + 2)
            ax.set_ylim(-2, SHEET_L + 2)
            ax.set_aspect('equal')
            ax.set_title('Sheet %d' % (sheets.index(s) + 1), fontsize=9)
            ax.axis('off')
        fig.suptitle(f'{tag.replace("-", "/")}" plywood layouts, bed cabinet + towers (grain runs vertical)')
        fig.savefig(os.path.join(outdir, f'sheets_{tag}.png'), dpi=110, bbox_inches='tight')
        plt.close(fig)


def main():
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    os.makedirs(outdir, exist_ok=True)
    s34, s14 = write_reports(outdir)
    write_scad(os.path.join(outdir, 'model.scad'))
    draw_all(outdir, s34, s14)
    print(f'wrote {outdir}')


if __name__ == '__main__':
    main()
