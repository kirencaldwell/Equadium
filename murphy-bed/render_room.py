"""Perspective render of the murphy bed + shelves inside the room (bed closed and open).

Run:  python3 murphy-bed/render_room.py
Uses the geometry from design.py. Room dimensions not yet measured are ASSUMED (see design.py).
"""
import math
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

import design as d

PAINT = '#f1eee6'          # painted cabinet color
WALL = '#b9d0d4'           # room wall color from the photo
FLOOR = '#a8744a'
ROOM_D = 130.0             # window-wall length (ASSUMED)
SILL, HEAD = 36.0, 80.0    # window sill / head heights (ASSUMED)
WIN_W = 48.0               # window width (ASSUMED)
BASE_EXIST = 4.5           # existing baseboard height (ASSUMED)


def box_faces(x, y, z, dx, dy, dz):
    v = [(x, y, z), (x + dx, y, z), (x + dx, y + dy, z), (x, y + dy, z),
         (x, y, z + dz), (x + dx, y, z + dz), (x + dx, y + dy, z + dz), (x, y + dy, z + dz)]
    idx = [((0, 3, 2, 1), (0, 0, -1)), ((4, 5, 6, 7), (0, 0, 1)), ((0, 1, 5, 4), (0, -1, 0)),
           ((2, 3, 7, 6), (0, 1, 0)), ((1, 2, 6, 5), (1, 0, 0)), ((3, 0, 4, 7), (-1, 0, 0))]
    return [([v[i] for i in f], n) for f, n in idx]


def shade(hexcol, n):
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    k = {(0, 0, 1): 1.0, (0, -1, 0): 0.95, (0, 1, 0): 0.95, (1, 0, 0): 0.82, (-1, 0, 0): 0.78, (0, 0, -1): 0.6}[n]
    return '#%02x%02x%02x' % tuple(min(255, int(c * k)) for c in (r, g, b))


class Camera:
    def __init__(self, eye, target, fov_deg, aspect):
        self.eye = eye
        f = [t - e for t, e in zip(target, eye)]
        n = math.sqrt(sum(c * c for c in f))
        self.f = [c / n for c in f]
        r = (self.f[1] * 1 - self.f[2] * 0, self.f[2] * 0 - self.f[0] * 1, 0)   # f x up(z)
        n = math.hypot(r[0], r[1])
        self.r = (r[0] / n, r[1] / n, 0)
        self.u = (self.r[1] * self.f[2] - self.r[2] * self.f[1],
                  self.r[2] * self.f[0] - self.r[0] * self.f[2],
                  self.r[0] * self.f[1] - self.r[1] * self.f[0])
        self.k = 1 / math.tan(math.radians(fov_deg) / 2)
        self.aspect = aspect

    def to_cam(self, p):
        q = [p[i] - self.eye[i] for i in range(3)]
        dot = lambda a, b: sum(x * y for x, y in zip(a, b))
        return (dot(q, self.r), dot(q, self.u), dot(q, self.f))

    def project_poly(self, pts):
        cam = [self.to_cam(p) for p in pts]
        near = 1.0
        out = []
        for i, a in enumerate(cam):         # clip against near plane
            b = cam[(i + 1) % len(cam)]
            ina, inb = a[2] >= near, b[2] >= near
            if ina:
                out.append(a)
            if ina != inb:
                t = (near - a[2]) / (b[2] - a[2])
                out.append(tuple(a[j] + t * (b[j] - a[j]) for j in range(3)))
        if len(out) < 3:
            return None
        return [(self.k * x / z, self.k * self.aspect * y / z) for x, y, z in out]

    def dist(self, pts):
        c = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
        return sum((c[i] - self.eye[i]) ** 2 for i in range(3))


PANEL = '#e3dccb'          # face panel field, a touch darker than the applied strips
STRIP = '#fbf9f4'


def scene(open_bed):
    """Return (name, (x, y, z, dx, dy, dz), color, decal) solids. Decals are drawn on top of the panel they sit on."""
    solids = []
    for name, g, x, y, z, dx, dy, dz in d.boxes():
        if g == 'facetrim':
            if not open_bed:                                  # closed: strips sit on the panel, facing the room
                solids.append((name, (x, y, z, dx, dy, dz), STRIP, True))
            continue
        if open_bed and (name.startswith('bed face') or name.startswith('bed frame')):
            continue
        col = PANEL if name.startswith('bed face') else ('#ffffff' if g == 'trim' else PAINT)
        solids.append((name, (x, y, z, dx, dy, dz), col, False))
    xb = d.SCRIBE + d.TOWER_W
    if open_bed:
        # Open: the bed pivots down, so the paneled FACE points at the floor. The panel runs from ~5" to the 87" projection.
        face_x = xb + d.T + (d.BED_W - 2 * d.T - d.FACE_PANEL_W) / 2
        y0 = d.BED_OPEN_PROJ - d.FACE_PANEL_H
        panel_z = 14.25                                       # underside of the panel (approximate deck height)
        solids.append(('face panel (underside)', (face_x, y0, panel_z, d.FACE_PANEL_W, d.FACE_PANEL_H, 0.75), PANEL, False))
        frame_w = d.FACE_PANEL_W - 0.5
        solids.append(('frame', (face_x + 0.25, y0 + 0.25, panel_z + 0.75, frame_w, d.FACE_PANEL_H - 0.5, 5.875), '#8a6a45', False))
        solids.append(('mattress', (face_x + (d.FACE_PANEL_W - 60) / 2, y0 + 0.9, panel_z + 0.75 + 5.875, 60, 80, 10), '#c4592f', False))
        # The applied strips are on the underside of this panel (facing the floor), so a standing viewer cannot see them;
        # see draw_open_face() for that side.
        for lx in (face_x + 3, face_x + d.FACE_PANEL_W - 4):
            solids.append(('leg', (lx, d.BED_OPEN_PROJ - 2.5, 0, 1, 1, panel_z), '#222222', False))
    # room fixtures on the walls
    solids.append(('existing baseboard (bed wall)', (0, 0, 0, d.WALL_W, 0.5, BASE_EXIST), '#ffffff', False))
    solids.append(('existing baseboard (window wall)', (0, 0, 0, 0.5, ROOM_D, BASE_EXIST), '#ffffff', False))
    solids.append(('existing crown (bed wall)', (0, 0, d.CEIL_H - d.EXIST_CROWN_H, d.WALL_W, d.EXIST_CROWN_PROJ, d.EXIST_CROWN_H), '#ffffff', False))
    solids.append(('existing crown (window wall)', (0, 0, d.CEIL_H - d.EXIST_CROWN_H, d.EXIST_CROWN_PROJ, ROOM_D, d.EXIST_CROWN_H), '#ffffff', False))
    wy = d.WINDOW_FROM_WALL
    solids.append(('window glass', (0, wy, SILL, 0.3, WIN_W, HEAD - SILL), '#cfe6ef', False))
    solids.append(('window sill', (0, wy - 2, SILL - 1.5, 2, WIN_W + 4, 1.5), '#ffffff', False))
    for (yy, ww) in ((wy - 2.5, 2.5), (wy + WIN_W, 2.5)):
        solids.append(('window casing', (0, yy, SILL, 0.8, ww, HEAD - SILL + 2.5), '#ffffff', False))
    solids.append(('window casing head', (0, wy - 2.5, HEAD, 0.8, WIN_W + 5, 2.5), '#ffffff', False))
    return solids


def render(open_bed, path, eye=(108, 128, 58), target=(62, 0, 46), title=None):
    cam = Camera(eye=eye, target=target, fov_deg=92, aspect=1.0)
    polys = []
    W, D, H = d.WALL_W, ROOM_D, d.CEIL_H
    big = [([(0, 0, H), (W, 0, H), (W, D, H), (0, D, H)], '#f4f4f4'),            # ceiling
           ([(0, 0, 0), (W, 0, 0), (W, D, 0), (0, D, 0)], FLOOR),                  # floor
           ([(0, 0, 0), (W, 0, 0), (W, 0, H), (0, 0, H)], WALL),                   # bed wall
           ([(0, 0, 0), (0, D, 0), (0, D, H), (0, 0, H)], shade(WALL, (-1, 0, 0))),  # window wall
           ([(W, 0, 0), (W, D, 0), (W, D, H), (W, 0, H)], shade(WALL, (1, 0, 0)))]    # far end wall
    for pts, col in big:
        p = cam.project_poly(pts)
        if p:
            polys.append((1e12, p, col, '#6b6b6b', 0.35))
    for name, (x, y, z, dx, dy, dz), col, decal in scene(open_bed):
        for pts, n in box_faces(x, y, z, dx, dy, dz):
            # back-face culling: face normal must point toward the camera
            c = [sum(p[i] for p in pts) / 4 for i in range(3)]
            if sum(n[i] * (cam.eye[i] - c[i]) for i in range(3)) <= 0:
                continue
            p = cam.project_poly(pts)
            if p:
                # decals (applied moulding) are painted after everything else so the big panel face cannot cover them
                polys.append((-1.0 if decal else cam.dist(pts), p, shade(col, n),
                              '#5a5240' if decal else '#6b6b6b', 0.7 if decal else 0.35))
    polys.sort(key=lambda t: -t[0])
    fig, ax = plt.subplots(figsize=(10, 10))
    for _, p, col, ec, lw in polys:
        ax.add_patch(Polygon(p, closed=True, fc=col, ec=ec, lw=lw))
    ax.set_xlim(-1.0, 1.0)
    ax.set_ylim(-1.0, 1.0)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_title(title or ('Murphy bed open' if open_bed else 'Murphy bed closed'))
    fig.savefig(path, dpi=110, bbox_inches='tight')
    plt.close(fig)


def draw_open_face(path):
    """The bed seen from the floor looking up when it is open: this is the paneled face you see when it is closed."""
    from matplotlib.patches import Rectangle
    fig, ax = plt.subplots(figsize=(7, 10))
    face_x = (d.BED_W - d.FACE_PANEL_W) / 2          # relative to the bed cabinet's left edge
    y0 = d.BED_OPEN_PROJ - d.FACE_PANEL_H
    ax.add_patch(Rectangle((-6, -4), d.BED_W + 12, 4, fc='#c9c9c9', ec='k'))
    ax.text(d.BED_W / 2, -2, 'wall / bed cabinet', ha='center', va='center', fontsize=9)
    ax.add_patch(Rectangle((face_x, y0), d.FACE_PANEL_W, d.FACE_PANEL_H, fc=PANEL, ec='k', lw=1.5))
    for n, px, pz, pw, ph in d.face_trim_layout():
        ax.add_patch(Rectangle((face_x + px, y0 + pz), pw, ph, fc=STRIP, ec='#5a5240', lw=0.8))
    for lx in (face_x + 3, face_x + d.FACE_PANEL_W - 4):
        ax.add_patch(Rectangle((lx - 0.5, d.BED_OPEN_PROJ - 3), 1.5, 1.5, fc='#222'))
    for hx in (d.BED_W / 2 - d.HANDLE_OFFSET, d.BED_W / 2 + d.HANDLE_OFFSET):
        ax.plot([hx] * 2, [y0 + d.HANDLE_Z[0] - d.FACE_PANEL_BOTTOM, y0 + d.HANDLE_Z[1] - d.FACE_PANEL_BOTTOM], 'k', lw=3)
    ax.text(d.BED_W / 2, d.BED_OPEN_PROJ + 4, 'foot end (legs)', ha='center', fontsize=9)
    ax.annotate('', (-9, 0), (-9, d.BED_OPEN_PROJ), arrowprops=dict(arrowstyle='<->'))
    ax.text(-11, d.BED_OPEN_PROJ / 2, f'{d.BED_OPEN_PROJ:.0f}" from wall', rotation=90, va='center', ha='right', fontsize=9)
    ax.set_xlim(-16, d.BED_W + 8)
    ax.set_ylim(d.BED_OPEN_PROJ + 9, -8)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_title('Bed open, looking up from the floor: this paneled face points down,\nand is the front you see when the bed is closed', fontsize=10)
    fig.savefig(path, dpi=120, bbox_inches='tight')
    plt.close(fig)


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    os.makedirs(out, exist_ok=True)
    render(False, os.path.join(out, 'room_closed.png'))
    render(True, os.path.join(out, 'room_open.png'), title='Murphy bed open (the paneled face is underneath, facing the floor)')
    draw_open_face(os.path.join(out, 'open_bed_face.png'))
    print('wrote room_closed.png, room_open.png, open_bed_face.png')


if __name__ == '__main__':
    main()
