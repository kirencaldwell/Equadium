"""Design checks for the murphy bed + shelf towers.  Run:  python3 -m unittest -v murphy-bed/test_design.py

These tests check that the design is internally consistent, matches the Create-A-Bed plans,
fits the room (using the assumed dimensions in design.py), and that the cut list/sheet layouts
are buildable with a 1/8" kerf. They do NOT prove structural soundness or replace a dry fit and
the manufacturer's sign-off on the tower attachment.
"""
import math
import os
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design as d

EPS = 1e-6


def by_name(parts):
    return {p.name: p for p in parts}


class KitMatchesPlans(unittest.TestCase):
    """Values copied from Create-A-Bed Queen Vertical plans, p.6 bill of materials."""
    PLANS = {
        'B Side rail': (2, 5.875, 81.5), 'C Foot rail': (1, 3, 62), 'D Head rail': (1, 5.875, 62),
        'E Bed face panel': (2, 32, 81.75), 'F Headboard': (1, 15.875, 64.375),
        'G Cabinet vertical': (2, 15.875, 87.125), 'H Header board': (1, 14.375, 64.375),
        'H Header front rail': (1, 2.75, 64.375), 'H Header rear rail': (1, 2.75, 64.375),
        'J Mattress support': (2, 31, 80), 'A Frame strut': (10, 1.5, 60.5), 'A Frame side': (2, 1.5, 80),
        'H Mounting cleat': (2, 1.5, 14.375), 'I Leg support rail': (1, 0.75, 60.25),
    }

    def test_every_kit_part_matches_the_bom(self):
        parts = by_name(d.bed_parts())
        self.assertEqual(set(parts), set(self.PLANS))
        for name, (qty, w, l) in self.PLANS.items():
            p = parts[name]
            self.assertEqual((p.qty, p.w, p.l), (qty, w, l), name)

    def test_cabinet_envelope_matches_plans_p2(self):
        self.assertEqual((d.BED_W, d.BED_H, d.BED_D, d.BED_OPEN_PROJ), (65.875, 87.125, 15.875, 87.0))

    def test_kit_parts_are_dimensionally_consistent_with_each_other(self):
        k = by_name(d.bed_parts())
        T = d.T
        # cabinet width = header length + two verticals; depth = header board + two 3/4" rails
        self.assertAlmostEqual(k['H Header board'].l + 2 * T, d.BED_W)
        self.assertAlmostEqual(k['H Header board'].w + 2 * T, d.BED_D)
        self.assertAlmostEqual(k['F Headboard'].l, k['H Header board'].l)
        self.assertAlmostEqual(k['G Cabinet vertical'].w, d.BED_D)
        self.assertAlmostEqual(k['G Cabinet vertical'].l, d.BED_H)
        # inner frame: struts + 2 frame sides = head/foot rail length; mattress deck = frame
        frame_w = k['A Frame strut'].l + 2 * T
        self.assertAlmostEqual(frame_w, k['D Head rail'].l)
        self.assertAlmostEqual(k['C Foot rail'].l, k['D Head rail'].l)
        self.assertAlmostEqual(2 * k['J Mattress support'].w, frame_w)
        self.assertAlmostEqual(k['J Mattress support'].l, k['A Frame side'].l)
        # side rails run the frame length plus head and foot rail thickness
        self.assertAlmostEqual(k['B Side rail'].l, k['A Frame side'].l + 2 * T)
        # face panels overhang the frame (+ side rails) by 1/4" per side (plans p.19-20)
        self.assertAlmostEqual(2 * k['E Bed face panel'].w, frame_w + 2 * T + 0.5)
        self.assertAlmostEqual(k['E Bed face panel'].l, k['B Side rail'].l + 0.25)

    def test_panel_fits_between_verticals_with_working_gap(self):
        k = by_name(d.bed_parts())
        panel_w = 2 * k['E Bed face panel'].w
        inner = d.BED_W - 2 * d.T
        gap_each_side = (inner - panel_w) / 2
        self.assertGreater(gap_each_side, 0.1)
        self.assertLess(gap_each_side, 0.5)

    def test_mattress_fits_the_deck(self):
        k = by_name(d.bed_parts())
        self.assertGreaterEqual(2 * k['J Mattress support'].w, 60)    # queen mattress 60"
        self.assertGreaterEqual(k['J Mattress support'].l, 80)        # x 80"


class FitsTheRoom(unittest.TestCase):
    def test_widths_add_up_to_the_wall(self):
        self.assertAlmostEqual(2 * d.TOWER_W + d.BED_W + 2 * d.SCRIBE, d.WALL_W)

    def test_bed_is_centered(self):
        left = d.SCRIBE + d.TOWER_W
        right = d.WALL_W - (left + d.BED_W)
        self.assertAlmostEqual(left, right)
        self.assertAlmostEqual(left + d.BED_W / 2, d.WALL_W / 2)

    def test_towers_are_wide_enough_to_be_useful(self):
        self.assertGreater(d.TOWER_W, 18)
        door_w = d.TOWER_W / 2 - 0.125
        self.assertGreater(door_w, 10)
        self.assertLess(door_w, 24)       # doors much wider than ~24" sag/rack

    def test_crown_clears_existing_crown_even_with_floor_drop(self):
        gap = d.CEIL_H - d.EXIST_CROWN_H - (d.BED_H + d.NEW_CROWN_H) - d.FLOOR_DROP
        self.assertGreaterEqual(gap, 0.5, 'new crown would hit the existing crown')

    def test_unit_height_leaves_room_to_tilt_into_place(self):
        # a 87-1/8" cabinet stood up from the floor needs a diagonal clearance (depth x height)
        diag = math.hypot(d.BED_D, d.BED_H)
        self.assertLess(diag, d.CEIL_H - d.FLOOR_DROP)

    def test_window_clearance(self):
        self.assertGreaterEqual(d.WINDOW_FROM_WALL - d.TOWER_D, 6.0)   # casing + elbow room

    def test_room_is_deep_enough_for_open_bed(self):     # ROOM_D is assumed until measured
        self.assertGreaterEqual(d.ROOM_D, d.BED_OPEN_PROJ + 24)

    def test_base_moulding_stock_covers_floor_drop(self):
        self.assertGreaterEqual(d.BASE_H + d.FLOOR_DROP + 0.25, d.BASE_H + d.FLOOR_DROP)
        rows = dict(d.trim_list())
        self.assertIn(d.frac(d.BASE_H + d.FLOOR_DROP + 0.25), rows['Base moulding, front run'])

    def test_base_moulding_is_proud_of_the_face_panel(self):
        # face panel bottom sits ~2-5/8" above the floor (plans), below the 4" base, so the base
        # must stand in front of the panel (plinth), not recessed with it. Confirm with the maker.
        self.assertLess(d.FACE_PANEL_BOTTOM, d.BASE_H)
        name, group, x, y, z, dx, dy, dz = [b for b in d.boxes() if b[0] == 'base molding'][0]
        self.assertGreaterEqual(y, d.TOWER_D - EPS)         # starts at the front plane, i.e. in front of the panel
        self.assertGreaterEqual(dy, 0.5)                    # and stands proud by at least 1/2"


class AnchorsAndStructure(unittest.TestCase):
    def test_enough_studs_behind_the_header_rail(self):
        rail = by_name(d.bed_parts())['H Header rear rail'].l
        self.assertGreaterEqual(math.floor(rail / d.STUD_OC), 4,
                                'kit needs 4 wall anchors through the header rear rail (plans p.10)')

    def test_adjustable_shelf_sag(self):
        # simply supported, uniform book load; conservative birch-ply E along the face grain
        shelf = [p for p in d.tower_parts() if 'adjustable' in p.name][0]
        span, depth = shelf.l, shelf.w                  # grain runs along the span (see sheet packer)
        E, I = 1.0e6, depth * d.T ** 3 / 12             # psi, in^4
        load = 30.0 / 12                                # 30 lb per foot of shelf run
        sag = 5 * load * span ** 4 / (384 * E * I)
        self.assertLess(sag, span / 300, f'sag {sag:.3f}" too large')

    def test_fixed_panels_span_the_same_as_shelves(self):
        fixed = [p for p in d.tower_parts() if 'fixed divider' in p.name][0]
        shelf = [p for p in d.tower_parts() if 'adjustable' in p.name][0]
        self.assertAlmostEqual(fixed.l - 0.25, shelf.l)

    def test_shelf_openings_hold_books(self):
        gap = (d.BED_H - d.T - d.LOWER_H - 3 * d.T) / 4
        self.assertGreaterEqual(gap, 9.5)
        self.assertLessEqual(gap, 14.0)

    def test_lower_cabinet_opening_vs_door_height(self):
        opening = d.LOWER_H - d.T - (d.BASE_H + d.T)
        door_h = [p for p in d.tower_parts() if 'door' in p.name][0].l
        self.assertGreater(door_h, opening - 5)
        self.assertLess(door_h, opening + 2 * d.T + 1)


class ModelGeometry(unittest.TestCase):
    def test_no_solids_overlap(self):
        bx = d.boxes()
        for i, (n1, _, x1, y1, z1, dx1, dy1, dz1) in enumerate(bx):
            for n2, _, x2, y2, z2, dx2, dy2, dz2 in bx[i + 1:]:
                ox = min(x1 + dx1, x2 + dx2) - max(x1, x2)
                oy = min(y1 + dy1, y2 + dy2) - max(y1, y2)
                oz = min(z1 + dz1, z2 + dz2) - max(z1, z2)
                self.assertFalse(ox > EPS and oy > EPS and oz > EPS, f'{n1} overlaps {n2}')

    def test_everything_is_inside_the_room(self):
        for n, g, x, y, z, dx, dy, dz in d.boxes():
            self.assertGreaterEqual(x, -EPS, n)
            self.assertLessEqual(x + dx, d.WALL_W + EPS, n)
            self.assertGreaterEqual(y, -EPS, n)
            self.assertLessEqual(z + dz, d.CEIL_H - d.EXIST_CROWN_H, n)

    def test_tower_tops_match_the_bed_cabinet_top(self):
        tops = {n: round(z + dz, 4) for n, g, x, y, z, dx, dy, dz in d.boxes() if n.startswith(('tower side', 'tower back', 'bed vertical'))}
        self.assertEqual(set(tops.values()), {d.BED_H}, tops)
        top_panels = [round(z + dz, 4) for n, g, x, y, z, dx, dy, dz in d.boxes() if n.startswith('tower fixed')]
        self.assertEqual(max(top_panels), d.BED_H)

    def test_fronts_are_flush(self):
        fronts = {}
        for n, g, x, y, z, dx, dy, dz in d.boxes():
            if n.startswith(('bed vertical', 'door')):
                fronts[n] = round(y + dy, 4)
        self.assertEqual(set(fronts.values()), {d.BED_D})

    def test_cut_list_matches_model(self):
        want = Counter()
        for p in d.tower_parts():
            if p.mat == 'solid' or 'Scribe' in p.name:
                continue
            t = {'3/4 ply': 0.75, '1/4 ply': 0.25}[p.mat]
            want.update({tuple(sorted(round(v, 4) for v in (t, p.w, p.l))): p.qty})
        have = Counter()
        for n, g, x, y, z, dx, dy, dz in d.boxes():
            if n.startswith(('tower side', 'tower fixed', 'tower shelf', 'tower back', 'door')):
                have[tuple(sorted(round(v, 4) for v in (dx, dy, dz)))] += 1
        self.assertEqual(have, want)

    def test_scad_has_one_cube_per_box(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, 'm.scad')
            d.write_scad(path)
            with open(path) as f:
                txt = f.read()
        self.assertEqual(txt.count('cube(['), len(d.boxes()) + 1)    # + room reference wall


class SheetLayouts(unittest.TestCase):
    def setUp(self):
        sheet_parts = [p for p in d.bed_parts() + d.tower_parts() if p.mat != 'solid']
        self.items34 = d.expand([p for p in sheet_parts if p.mat == '3/4 ply'])
        self.items14 = d.expand([p for p in sheet_parts if p.mat == '1/4 ply'])

    def test_kerf_is_at_least_an_eighth_inch(self):
        self.assertGreaterEqual(d.KERF, 0.125)

    def test_every_part_is_placed_exactly_once_and_cuts_are_buildable(self):
        for items in (self.items34, self.items14):
            sheets = d.pack(items)
            placed = Counter((round(w, 4), round(h, 4)) for s in sheets for _, _, _, w, h in s['placed'])
            self.assertEqual(placed, Counter((round(w, 4), round(h, 4)) for _, w, h in items))
            d.check_layout(sheets)      # inside trimmed sheet; every gap >= kerf

    def test_no_part_needs_rotating_across_the_grain(self):
        for _, w, l in self.items34 + self.items14:
            self.assertLessEqual(w, d.SHEET_W - d.EDGE_TRIM)
            self.assertLessEqual(l, d.SHEET_L - d.EDGE_TRIM)

    def test_kit_cutting_guide_has_no_room_for_kerf_and_trim(self):
        # why we re-laid out the bed parts: 15-7/8" + 32" + kerf + edge trim > 48"
        self.assertGreater(15.875 + 32 + d.KERF + d.EDGE_TRIM, d.SHEET_W)

    def test_sheet_area_is_sufficient(self):
        for items in (self.items34, self.items14):
            need = sum(w * h for _, w, h in items)
            have = len(d.pack(items)) * d.SHEET_W * d.SHEET_L
            self.assertLessEqual(need, have)
            self.assertGreater(need / have, 0.40, 'suspiciously wasteful layout')


class FacePanelMoulding(unittest.TestCase):
    def rects(self):
        return d.face_trim_layout()

    def test_strips_stay_inside_the_panel_with_clearance(self):
        min_inset = 0.25     # absolute: nothing may sit closer than 1/4" to a panel edge
        self.assertGreaterEqual(d.FACE_TRIM_INSET, min_inset)
        for n, x, z, w, h in self.rects():
            self.assertGreaterEqual(x, min_inset - EPS, n)
            self.assertLessEqual(x + w, d.FACE_PANEL_W - min_inset + EPS, n)
            self.assertLessEqual(z + h, d.FACE_PANEL_H - min_inset + EPS, n)

    def test_strips_clear_the_base_moulding(self):
        lowest = min(d.FACE_PANEL_BOTTOM + z for n, x, z, w, h in self.rects())
        self.assertGreaterEqual(lowest, d.BASE_H + 0.125 - EPS)

    def test_strips_do_not_overlap_each_other(self):
        r = self.rects()
        for i, (n1, x1, z1, w1, h1) in enumerate(r):
            for n2, x2, z2, w2, h2 in r[i + 1:]:
                ox = min(x1 + w1, x2 + w2) - max(x1, x2)
                oz = min(z1 + h1, z2 + h2) - max(z1, z2)
                self.assertFalse(ox > EPS and oz > EPS, f'{n1} overlaps {n2}')

    def test_panels_are_real_panels(self):
        stiles = sorted(x for n, x, z, w, h in self.rects() if n == 'Face moulding stile')
        self.assertEqual(len(stiles), 5)
        gaps = [stiles[i + 1] - stiles[i] - d.FACE_TRIM_W for i in range(4)]
        for g in gaps:
            self.assertAlmostEqual(g, gaps[0])
            self.assertGreater(g, 6)
        # centre stile covers the seam between the two 32" face panels
        self.assertAlmostEqual(stiles[2] + d.FACE_TRIM_W / 2, d.FACE_PANEL_W / 2)

    def test_handles_clear_every_strip(self):
        for hx in (d.FACE_PANEL_W / 2 - d.HANDLE_OFFSET, d.FACE_PANEL_W / 2 + d.HANDLE_OFFSET):
            hz0, hz1 = (z - d.FACE_PANEL_BOTTOM for z in d.HANDLE_Z)
            for n, x, z, w, h in self.rects():
                hit = (hx + 0.25 > x and hx - 0.25 < x + w) and (hz1 > z and hz0 < z + h)
                self.assertFalse(hit, f'handle at {hx} hits {n}')
        self.assertTrue(60 <= d.HANDLE_Z[0] and d.HANDLE_Z[1] <= 72)     # kit: 5'-6' (plans p.21)

    def test_moulding_barely_proud_of_the_cabinet_front(self):
        fronts = {round(y + dy, 4) for n, g, x, y, z, dx, dy, dz in d.boxes() if g == 'facetrim'}
        self.assertEqual(len(fronts), 1)
        self.assertLessEqual(fronts.pop() - d.BED_D, 0.5)

    def test_face_panel_matches_the_kit(self):
        face = [b for b in d.boxes() if b[0] == 'bed face (closed)'][0]
        self.assertAlmostEqual(face[5], 2 * by_name(d.bed_parts())['E Bed face panel'].w)
        inner = d.BED_W - 2 * d.T
        self.assertAlmostEqual(face[2] - (d.SCRIBE + d.TOWER_W + d.T), (inner - face[5]) / 2)   # centered in the opening

    def test_model_and_cut_list_agree(self):
        self.assertEqual(sum(1 for b in d.boxes() if b[1] == 'facetrim'), len(d.face_trim_layout()))
        listed = sum(p.qty * p.l for p in d.face_trim_parts())
        laid = sum(max(w, h) for n, x, z, w, h in d.face_trim_layout())
        self.assertAlmostEqual(listed, laid)

    def test_weight_stays_inside_the_kits_mattress_budget(self):
        w = d.face_trim_weight_lb()
        self.assertLess(w, 7.5)                                  # under half of the kit's 65-80 lb mattress band
        self.assertLess(w / (d.moving_mass_lb() + d.MATTRESS_LB), 0.05)
        self.assertLessEqual(d.MATTRESS_LB + w, d.MATTRESS_MAX_LB,
                             'mattress + moulding exceeds the 80 lb the springs are set for')

    def test_split_style_also_works(self):
        saved = d.FACE_PANEL_STYLE
        d.FACE_PANEL_STYLE = 'split'
        try:
            rects = d.face_trim_layout()
            self.assertEqual(sum(1 for r in rects if r[0] == 'Face moulding middle rail'), 4)
            for hx in (d.FACE_PANEL_W / 2 - d.HANDLE_OFFSET, d.FACE_PANEL_W / 2 + d.HANDLE_OFFSET):
                hz0, hz1 = (z - d.FACE_PANEL_BOTTOM for z in d.HANDLE_Z)
                for n, x, z, w, h in rects:
                    self.assertFalse((hx + 0.25 > x and hx - 0.25 < x + w) and (hz1 > z and hz0 < z + h), n)
        finally:
            d.FACE_PANEL_STYLE = saved


class ReportsAndDrawings(unittest.TestCase):
    def test_reports_and_drawings_generate(self):
        with tempfile.TemporaryDirectory() as t:
            s34, s14 = d.write_reports(t)
            d.write_scad(os.path.join(t, 'model.scad'))
            d.draw_all(t, s34, s14)
            for f in ('cutlist.md', 'cutlist.csv', 'model.scad', 'front_elevation.png', 'plan.png',
                      'side_section.png', 'iso.png', 'sheets_3-4.png', 'sheets_1-4.png', 'trim_guide.md'):
                self.assertGreater(os.path.getsize(os.path.join(t, f)), 500, f)
            with open(os.path.join(t, 'cutlist.md')) as f:
                md = f.read()
            n_shelves = sum(p.qty for p in d.tower_parts() if 'adjustable' in p.name)
            n_doors = sum(p.qty for p in d.tower_parts() if 'door' in p.name)
            self.assertIn(f'5 mm shelf pins: {4 * n_shelves}', md)
            self.assertIn(f'hinges, full overlay: {2 * n_doors}', md)
            self.assertIn(f'{len(s34)} sheets 3/4" ply', md)


if __name__ == '__main__':
    unittest.main(verbosity=2)
