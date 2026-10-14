"""Hero piece: the pumpkin-silhouette Halloween portal.

Everything is built from bmesh/mesh data (no bpy.ops modelling):

* Portal_Dais            two-tier faceted stone platform (bottom at z = 0)
* Portal_Frame           chunky charcoal pumpkin ring with rib grooves, stem,
                         curly vine and a leaf on top
* Portal_Swirl           textured vortex fill (M_Swirl, export/textures/portal_swirl.png),
                         thin slab, front + back faces, slightly dished
* GLOW_fire_PortalSpin_A / GLOW_flame_PortalSpin_B
                         flat spiral-arm blades (front + back layer) that the
                         Roblox script rotates around the portal normal
                         (A = long orange arms, B = small pale-hot core)
* GLOW_fire_PortalRim_Inner / GLOW_fire_PortalRim_Outer
                         neon outlines: tube along the opening edge (front +
                         back) and a bold lip around the outer silhouette
                         (open arc, ends tucked under the dais top)
* GLOW_fire_PortalTendrils
                         zig-zag lightning arcs sticking out of the outline
* GLOW_ember_PortalCracks_Frame / GLOW_ember_PortalCracks_Dais
                         glowing rib seams on the frame and lava cracks in the dais

The portal faces -Y, is centred on the world origin and the dais bottom is
at z = 0. ``build_portal()`` fills ``PORTAL_INFO`` so other modules can place
things relative to it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy, bmesh, common  # noqa: E401,E402

import math  # noqa: E402

import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

# --------------------------------------------------------------------------
# Design parameters (studs)
# --------------------------------------------------------------------------
DAIS_R = 7.0            # outer radius of the lower tier
DAIS_LEDGE_Z = 0.42     # top of the lower tier (the step)
DAIS_TOP_R = 6.0        # radius of the upper tier top edge
DAIS_TOP_Z = 0.82       # walkable top of the dais
DAIS_SIDES = 24

FRAME_DEPTH = 2.0       # front-to-back thickness of the ring (before bulge)
FRAME_BEVEL = 0.22
FRAME_BULGE = 0.26      # lobes bulge forward/back by this much
GROOVE_DEPTH = 0.20     # rib grooves are recessed this much behind the flat face
OUTER_RIDGE = 0.22      # outer wall bulges out radially by this much
SINK = 0.55             # how far the pumpkin bottom sinks into the dais

# Outer pumpkin silhouette = union of ellipses (ex, ez, a, b) relative to the
# polar centre C. Center lobe, two mid lobes, two outer lobes -> 13 wide x 11 tall.
OUTER_LOBES = [
    (0.0, 0.0, 3.30, 5.50),
    (2.55, -0.25, 3.40, 5.05),
    (-2.55, -0.25, 3.40, 5.05),
    (4.15, -0.55, 2.35, 4.10),
    (-4.15, -0.55, 2.35, 4.10),
]
RING_W = 1.30           # nominal frame width (shrinks each ellipse)
INNER_DROP = 0.50       # inner opening sits lower -> thin bottom, thick top

CZ = DAIS_TOP_Z + OUTER_LOBES[0][3] - SINK   # z of the polar centre C

LIP_OUT = 0.64          # outer outline lip: outermost point past the outer wall (ridge is OUTER_RIDGE)
LIP_IN = 0.10           # how far the lip laps inwards onto the front/back faces
LIP_TUCK = 0.08         # open lip ends stop this far below the dais top

RIM_R = 0.17            # neon tube radius of the inner rim
RIM_INSET = 0.02        # tube centre sits this far inside the opening edge
RIM_PROTRUSION = RIM_R + RIM_INSET

SWIRL_TEX = "portal_swirl.png"
SWIRL_RES = 512

PORTAL_INFO = {}


# --------------------------------------------------------------------------
# Swirl texture
# --------------------------------------------------------------------------
def make_swirl_texture():
    """Generate export/textures/portal_swirl.png (512x512): orange/amber spiral
    arms on near-black with a hot pale core. Returns the bpy image."""
    img = bpy.data.images.get(SWIRL_TEX)
    if img is not None and img.size[0] == SWIRL_RES:
        return img
    n = SWIRL_RES
    c = (np.arange(n) + 0.5) / n * 2.0 - 1.0
    x, y = np.meshgrid(c, c)           # y up (row 0 = bottom in Blender)
    r = np.sqrt(x * x + y * y)
    phi = np.arctan2(y, x)
    lr = np.log(r + 0.035)

    # Wobble the spiral phase with smooth, angle-periodic waves (no aliasing).
    wob = (0.30 * np.sin(2 * phi + 5.0 * r + 1.3)
           + 0.20 * np.sin(5 * phi - 7.0 * r + 2.0)
           + 0.12 * np.sin(9 * phi + 11.0 * r + 0.4))

    twist = 2.35
    main = 0.5 + 0.5 * np.cos(3 * (phi + twist * lr) + 2.2 * wob)
    main = main ** 2.6
    fine = 0.5 + 0.5 * np.cos(7 * (phi + (twist * 1.25) * lr) + 3.5 * wob + 1.3)
    fine = fine ** 4.0
    # Sparse bright "streaks" along the arms.
    streak = 0.5 + 0.5 * np.cos(11 * (phi + twist * 0.9 * lr) - 1.7 * wob)
    streak = streak ** 12

    radial = np.clip(1.0 - r / 1.05, 0.0, 1.0)
    falloff = radial ** 0.4
    core = np.exp(-(r / 0.16) ** 2)
    halo = np.exp(-(r / 0.45) ** 2)

    v = (0.11 + 0.89 * main * falloff) * (0.62 + 0.38 * falloff)
    v += 0.28 * fine * falloff
    v += 0.35 * streak * radial
    v = v * (0.6 + 0.6 * halo) + 1.15 * core
    # Dark band towards the edge so the neon rim pops, faint ember right at the edge.
    edge = np.clip((r - 0.86) / 0.18, 0.0, 1.0)
    v = v * (1.0 - 0.5 * edge)
    v = np.clip(v, 0.0, 1.35)

    # Colour ramp (sRGB): near-black -> blood orange -> orange -> amber -> pale flame.
    stops = np.array([0.0, 0.16, 0.38, 0.62, 0.86, 1.08, 1.35])
    cols = np.array([
        [0.020, 0.010, 0.016],
        [0.110, 0.022, 0.010],
        [0.560, 0.120, 0.005],
        [0.960, 0.380, 0.020],
        [1.000, 0.620, 0.120],
        [1.000, 0.860, 0.480],
        [1.000, 0.970, 0.860],
    ])
    rgb = np.stack([np.interp(v, stops, cols[:, k]) for k in range(3)], axis=-1)
    rgba = np.concatenate([rgb, np.ones((n, n, 1))], axis=-1).astype(np.float32)

    if img is None:
        img = bpy.data.images.new(SWIRL_TEX, n, n, alpha=False)
    img.colorspace_settings.name = "sRGB"
    img.pixels.foreach_set(rgba.ravel())
    common.save_image(img, SWIRL_TEX)
    return img


# --------------------------------------------------------------------------
# Outline maths
# --------------------------------------------------------------------------
def _inner_lobes():
    return [(ex, ez - INNER_DROP, a - RING_W, b - RING_W) for ex, ez, a, b in OUTER_LOBES]


def _ray_ellipse(dx, dz, ex, ez, a, b):
    """Farthest hit of the ray from the polar centre (0,0) along (dx,dz)."""
    px, pz = -ex, -ez
    ia, ib = 1.0 / (a * a), 1.0 / (b * b)
    A = dx * dx * ia + dz * dz * ib
    B = 2.0 * (px * dx * ia + pz * dz * ib)
    C = px * px * ia + pz * pz * ib - 1.0
    disc = B * B - 4 * A * C
    if disc < 0:
        return None
    t = (-B + math.sqrt(disc)) / (2 * A)
    return t if t > 0 else None


def _envelope(theta, lobes):
    dx, dz = math.cos(theta), math.sin(theta)
    best, idx = 0.0, -1
    for i, (ex, ez, a, b) in enumerate(lobes):
        t = _ray_ellipse(dx, dz, ex, ez, a, b)
        if t is not None and t > best:
            best, idx = t, i
    return best, idx


def _switch_angles(lobes, fine=3000):
    """Angles where the envelope passes from one ellipse to the next (notches)."""
    out = []
    ths = [2 * math.pi * k / fine for k in range(fine + 1)]
    prev = _envelope(ths[0], lobes)[1]
    for k in range(1, len(ths)):
        cur = _envelope(ths[k], lobes)[1]
        if cur != prev:
            lo, hi = ths[k - 1], ths[k]
            for _ in range(30):
                mid = 0.5 * (lo + hi)
                if _envelope(mid, lobes)[1] == prev:
                    lo = mid
                else:
                    hi = mid
            out.append(0.5 * (lo + hi) % (2 * math.pi))
        prev = cur
    return out


def _inner_radius(theta, lobes):
    """Opening radius: lobed on top, a smooth bowl below (no bumps at the
    threshold so the bottom reads clean and walkable)."""
    r, _ = _envelope(theta, lobes)
    if math.sin(theta) >= 0:
        return r
    r_side = max(_envelope(0.0, lobes)[0], _envelope(math.pi, lobes)[0])
    r_bot = _envelope(-math.pi / 2, lobes)[0]
    c, s = math.cos(theta), math.sin(theta)
    # superellipse (exponent 2.4) through the widest and lowest points
    e = 2.4
    bowl = (abs(c / r_side) ** e + abs(s / r_bot) ** e) ** (-1.0 / e)
    return bowl


def _ang_dist(a, b):
    d = (a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


class Outline:
    """Sampled pumpkin outline: per-angle outer/inner radius around C=(0, CZ)."""

    def __init__(self, base_samples=104, groove_hw=0.07):
        self.inner_lobes = _inner_lobes()
        outer_sw = _switch_angles(OUTER_LOBES)
        inner_sw = [t for t in _switch_angles(self.inner_lobes) if math.sin(t) > 0.05]
        # Grooves at outer notches that are clearly above the dais.
        self.grooves = []
        for th in outer_sw:
            r, _ = _envelope(th, OUTER_LOBES)
            if CZ + r * math.sin(th) > DAIS_TOP_Z + 1.0:
                self.grooves.append(th)
        self.groove_hw = groove_hw
        fixed = list(outer_sw) + list(inner_sw)
        for g in self.grooves:
            fixed += [(g - groove_hw) % (2 * math.pi), (g + groove_hw) % (2 * math.pi)]
        fixed += [math.pi / 2]  # vertex exactly at the top for the stem
        ths = list(fixed)
        for k in range(base_samples):
            th = 2 * math.pi * (k + 0.5) / base_samples
            if all(_ang_dist(th, f) > 0.022 for f in fixed):
                ths.append(th)
        ths = sorted(set(round(t % (2 * math.pi), 7) for t in ths))
        self.thetas = ths
        self.fixed = set(round(f % (2 * math.pi), 7) for f in fixed)
        self.r_out = [_envelope(t, OUTER_LOBES)[0] for t in ths]
        self.r_in = [_inner_radius(t, self.inner_lobes) for t in ths]

    def __len__(self):
        return len(self.thetas)

    def dir(self, i):
        t = self.thetas[i]
        return math.cos(t), math.sin(t)

    def point(self, i, t, extra=0.0):
        """XZ point at ring parameter t (0 = opening edge, 1 = outer edge),
        ``extra`` studs further out along the ray."""
        dx, dz = self.dir(i)
        r = self.r_in[i] + t * (self.r_out[i] - self.r_in[i]) + extra
        return r * dx, CZ + r * dz

    def groove(self, i):
        th = self.thetas[i]
        g = 0.0
        for gt in self.grooves:
            g = max(g, 1.0 - _ang_dist(th, gt) / self.groove_hw)
        return max(g, 0.0)

    def outer_xz(self, extra=0.0):
        return [self.point(i, 1.0, extra) for i in range(len(self))]

    def inner_xz(self, extra=0.0):
        return [self.point(i, 0.0, extra) for i in range(len(self))]


def _inscribed(poly, cx, cz):
    """Distance from (cx, cz) to the closed polygon ``poly`` (list of (x, z))."""
    best = 1e9
    n = len(poly)
    for i in range(n):
        ax, az = poly[i]
        bx, bz = poly[(i + 1) % n]
        vx, vz = bx - ax, bz - az
        L = vx * vx + vz * vz
        u = 0.0 if L == 0 else max(0.0, min(1.0, ((cx - ax) * vx + (cz - az) * vz) / L))
        px, pz = ax + u * vx - cx, az + u * vz - cz
        best = min(best, math.hypot(px, pz))
    return best


# --------------------------------------------------------------------------
# Small bmesh builders
# --------------------------------------------------------------------------
def _frame_vectors(pts, i, closed, up_hint=None):
    n = len(pts)
    if closed:
        a, b = pts[(i - 1) % n], pts[(i + 1) % n]
    else:
        a, b = pts[max(i - 1, 0)], pts[min(i + 1, n - 1)]
    t = (b - a)
    if t.length < 1e-9:
        t = Vector((1, 0, 0))
    t.normalize()
    up = up_hint or Vector((0, 1, 0))
    if abs(t.dot(up)) > 0.95:
        up = Vector((0, 0, 1)) if abs(t.z) < 0.9 else Vector((1, 0, 0))
    nrm = t.cross(up).normalized()
    bin_ = nrm.cross(t).normalized()
    return t, nrm, bin_


def tube(bm, pts, radius, sides=4, closed=False, cap=True, phase=0.0, up_hint=None, tip=False):
    """Polygonal tube along ``pts`` (Vectors). ``radius`` float or list.
    ``tip`` collapses the last point to a sharp point. Returns new faces."""
    pts = [Vector(p) for p in pts]
    n = len(pts)
    radii = radius if isinstance(radius, (list, tuple)) else [radius] * n
    rings = []
    last = n - 1 if tip else n
    for i in range(last):
        _, nrm, bin_ = _frame_vectors(pts, i, closed, up_hint)
        ring = []
        for s in range(sides):
            a = phase + 2 * math.pi * s / sides
            ring.append(bm.verts.new(pts[i] + radii[i] * (math.cos(a) * nrm + math.sin(a) * bin_)))
        rings.append(ring)
    faces = []
    segs = len(rings) if closed else len(rings) - 1
    for i in range(segs):
        r0, r1 = rings[i], rings[(i + 1) % len(rings)]
        for s in range(sides):
            faces.append(bm.faces.new((r0[s], r0[(s + 1) % sides], r1[(s + 1) % sides], r1[s])))
    if not closed:
        if tip:
            tv = bm.verts.new(pts[-1])
            r = rings[-1]
            for s in range(sides):
                faces.append(bm.faces.new((r[s], r[(s + 1) % sides], tv)))
        elif cap:
            faces.append(bm.faces.new(list(reversed(rings[-1]))))
        if cap:
            faces.append(bm.faces.new(rings[0]))
    return faces


def _finish(name, bm, collection, key=None, groups=None, glow_key=None, jitter=0.0, seed=0):
    """bmesh -> flat shaded, palette-painted object with base origin."""
    if jitter > 0:
        common.jitter_bmesh(bm, jitter, seed)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if groups:
        for k, faces in groups.items():
            common.paint_bmesh_faces(bm, faces, k)
    elif key:
        common.paint_bmesh_faces(bm, bm.faces, key)
    obj = common.object_from_bmesh(name, bm, collection)
    obj.data.materials.append(common.mat_palette())
    if glow_key:
        common.make_glow(obj, glow_key, name)
    common.shade_flat(obj)
    common.origin_to_base(obj)
    return obj


# --------------------------------------------------------------------------
# Parts
# --------------------------------------------------------------------------
def _build_dais(collection, seed=11):
    rnd = common.rng(seed)
    bm = bmesh.new()
    n = DAIS_SIDES
    # (radius, z) rings from the bottom outer edge to the inner top.
    prof = [
        (DAIS_R, 0.0),
        (DAIS_R, DAIS_LEDGE_Z - 0.12),
        (DAIS_R - 0.16, DAIS_LEDGE_Z),
        (DAIS_TOP_R + 0.16, DAIS_LEDGE_Z),
        (DAIS_TOP_R + 0.16, DAIS_TOP_Z - 0.12),
        (DAIS_TOP_R, DAIS_TOP_Z),
        (4.25, DAIS_TOP_Z),
        (2.35, DAIS_TOP_Z),
    ]
    rings = []
    for j, (r, z) in enumerate(prof):
        ring = []
        for i in range(n):
            # Offset every other ring of tiles by half a step for a flagstone look.
            a = 2 * math.pi * (i + (0.5 if j >= 6 and j % 2 == 1 else 0.0)) / n
            rr = r + (rnd.uniform(-0.07, 0.07) if 0 < j else 0.0)
            zz = z + (rnd.uniform(-0.02, 0.02) if j >= 6 else 0.0)
            ring.append(bm.verts.new((rr * math.cos(a), rr * math.sin(a), zz)))
        rings.append(ring)
    centre = bm.verts.new((0, 0, DAIS_TOP_Z))
    groups = {}

    def add(face, key):
        groups.setdefault(key, []).append(face)

    for j in range(len(prof) - 1):
        for i in range(n):
            f = bm.faces.new((rings[j][i], rings[j][(i + 1) % n], rings[j + 1][(i + 1) % n], rings[j + 1][i]))
            if j in (0, 3):            # vertical walls
                key = "stone_dark" if (i + j) % 3 else "ash"
            elif j in (1, 4):          # bevels
                key = "stone"
            elif j == 2:               # ledge / step
                key = rnd.choice(["stone", "stone", "stone_light", "stone_dark"])
            else:                      # top tiles
                key = rnd.choice(["stone", "stone", "stone_light", "stone_dark", "stone"])
            add(f, key)
    for i in range(n):
        add(bm.faces.new((rings[-1][i], rings[-1][(i + 1) % n], centre)),
            rnd.choice(["stone", "stone_light", "stone_dark"]))
    add(bm.faces.new(list(reversed(rings[0]))), "stone_dark")
    return _finish("Portal_Dais", bm, collection, groups=groups)


def _crack_strip(bm, pts2d, z_top, z_bot, w0, w1, closed=False):
    """Flat glowing inlay along an XY polyline (top + side walls).
    ``closed``: ``pts2d`` is a loop (do NOT repeat the first point at the end);
    the strip wraps from the last point back to the first and gets no end caps,
    so it forms one clean closed ring with no seam."""
    n = len(pts2d)
    left, right = [], []
    for i in range(n):
        p = Vector((pts2d[i][0], pts2d[i][1], 0))
        if closed:
            a = Vector((*pts2d[(i - 1) % n], 0))
            b = Vector((*pts2d[(i + 1) % n], 0))
        else:
            a = Vector((*pts2d[max(i - 1, 0)], 0))
            b = Vector((*pts2d[min(i + 1, n - 1)], 0))
        t = (b - a).normalized()
        side = Vector((-t.y, t.x, 0))
        w = (w0 + (w1 - w0) * i / max(n - 1, 1)) * 0.5
        left.append(p + side * w)
        right.append(p - side * w)
    lt = [bm.verts.new((v.x, v.y, z_top)) for v in left]
    rt = [bm.verts.new((v.x, v.y, z_top)) for v in right]
    lb = [bm.verts.new((v.x, v.y, z_bot)) for v in left]
    rb = [bm.verts.new((v.x, v.y, z_bot)) for v in right]
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        bm.faces.new((lt[i], rt[i], rt[j], lt[j]))
        bm.faces.new((lb[i], lt[i], lt[j], lb[j]))
        bm.faces.new((rt[i], rb[i], rb[j], rt[j]))
        bm.faces.new((lb[i], lb[j], rb[j], rb[i]))
    if not closed:
        bm.faces.new((lt[0], lb[0], rb[0], rt[0]))
        bm.faces.new((lt[-1], rt[-1], rb[-1], lb[-1]))


def _build_dais_cracks(collection, seed=5):
    rnd = common.rng(seed)
    bm = bmesh.new()
    zt, zb = DAIS_TOP_Z + 0.045, DAIS_TOP_Z - 0.10
    starts = [(-1.9, -1.35, -112), (-0.5, -1.4, -84), (1.1, -1.4, -70), (2.3, -1.3, -40),
              (-2.4, -1.25, -150), (-1.5, 1.35, 105), (0.6, 1.4, 80), (2.0, 1.3, 50)]
    for sx, sy, ang in starts:
        a = math.radians(ang + rnd.uniform(-6, 6))
        pts = [(sx, sy)]
        x, y = sx, sy
        steps = rnd.randint(4, 6)
        for k in range(steps):
            a += rnd.uniform(-0.55, 0.55)
            step = rnd.uniform(0.55, 0.85)
            x += math.cos(a) * step
            y += math.sin(a) * step
            if math.hypot(x, y) > DAIS_TOP_R - 0.35:
                s = (DAIS_TOP_R - 0.35) / math.hypot(x, y)
                pts.append((x * s, y * s))
                break
            pts.append((x, y))
        _crack_strip(bm, pts, zt, zb, 0.2, 0.06)
    # Thin glowing circle inlay on the top tier.
    rc, segs = DAIS_TOP_R - 0.55, 48
    circ = [(rc * math.cos(2 * math.pi * k / segs), rc * math.sin(2 * math.pi * k / segs)) for k in range(segs)]
    _crack_strip(bm, circ, zt - 0.01, zb, 0.12, 0.12, closed=True)
    return _finish("PortalCracks_Dais", bm, collection, glow_key="ember")


def _frame_profile(ol, i):
    """Cross-section points (t, y) of the ring at sample i, as a closed loop."""
    w = ol.r_out[i] - ol.r_in[i]
    tb = min(FRAME_BEVEL / w, 0.2)
    g = ol.groove(i)
    d2 = FRAME_DEPTH / 2

    def front(t):
        bump = math.sin(math.pi * (t - tb) / (1 - 2 * tb))
        return -d2 - (FRAME_BULGE * (1 - g) - GROOVE_DEPTH * g) * bump

    ridge = OUTER_RIDGE / w
    pts = [
        (0.0, -d2 + FRAME_BEVEL),
        (tb, -d2),
        (0.3, front(0.3)),
        (0.5, front(0.5)),
        (0.7, front(0.7)),
        (1 - tb, -d2),
        (1.0, -d2 + FRAME_BEVEL),
        (1.0 + ridge, 0.0),
        (1.0, d2 - FRAME_BEVEL),
        (1 - tb, d2),
        (0.7, -front(0.7)),
        (0.5, -front(0.5)),
        (0.3, -front(0.3)),
        (tb, d2),
        (0.0, d2 - FRAME_BEVEL),
    ]
    return pts


# profile segment index -> palette key (segment j joins point j and j+1)
_FRAME_KEYS = {
    0: "ash", 1: "charcoal", 2: "charcoal", 3: "charcoal", 4: "charcoal", 5: "ash",
    6: "charcoal", 7: "charcoal", 8: "ash", 9: "charcoal", 10: "charcoal", 11: "charcoal",
    12: "charcoal", 13: "ash", 14: "black",
}


def _build_frame_ring(bm, ol, groups):
    n = len(ol)
    rings = []
    for i in range(n):
        ring = []
        for t, y in _frame_profile(ol, i):
            x, z = ol.point(i, t)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
    m = len(rings[0])
    for i in range(n):
        r0, r1 = rings[i], rings[(i + 1) % n]
        gi = max(ol.groove(i), ol.groove((i + 1) % n))
        for j in range(m):
            f = bm.faces.new((r0[j], r1[j], r1[(j + 1) % m], r0[(j + 1) % m]))
            key = _FRAME_KEYS[j]
            if gi > 0 and j in (1, 2, 3, 4, 9, 10, 11, 12):
                key = "black"
            groups.setdefault(key, []).append(f)


def _build_stem(bm, groups, top_z):
    """Chunky ridged, curved stem rising from the top of the centre lobe."""
    segs = 5
    sides = 10
    base_z = top_z - 0.6
    height = 2.9
    rings = []
    for k in range(segs + 1):
        s = k / segs
        cx = 0.75 * s ** 1.6          # curves to +X
        cz = base_z + height * s - 0.25 * s ** 2
        rad = 0.78 * (1 - s) + 0.48 * s
        tilt = 0.55 * s               # cross-section tilts with the curve
        ring = []
        for j in range(sides):
            a = 2 * math.pi * j / sides + 0.15
            rr = rad * (1.0 if j % 2 == 0 else 0.8)
            lx, ly = rr * math.cos(a), rr * math.sin(a)
            # rotate the ring around Y by tilt (lean towards +X)
            x = cx + lx * math.cos(tilt)
            z = cz - lx * math.sin(tilt)
            ring.append(bm.verts.new((x, ly, z)))
        rings.append(ring)
    # Flared base collar (sits on/into the frame).
    for k in range(segs):
        for j in range(sides):
            f = bm.faces.new((rings[k][j], rings[k][(j + 1) % sides],
                              rings[k + 1][(j + 1) % sides], rings[k + 1][j]))
            # near-black ridged stem (keeps the palette black + orange)
            groups.setdefault("wood_dark" if j % 2 else "stem_dark", []).append(f)
    top = bm.faces.new(list(rings[-1]))
    groups.setdefault("wood", []).append(top)
    bot = bm.faces.new(list(reversed(rings[0])))
    groups.setdefault("stem_dark", []).append(bot)
    return Vector((0.75, 0.0, base_z + height - 0.25))


def _build_vine(bm, groups, ol, top_z):
    """Curly vine: grows out of the stem, rides just above the outer neon lip
    over the top-left lobe and curls up into the sky so it reads against the
    background."""
    pts = []
    th0, th1 = math.radians(93), math.radians(126)
    steps = 7
    for k in range(steps + 1):
        f = k / steps
        th = th0 + (th1 - th0) * f
        r, _ = _envelope(th, OUTER_LOBES)
        lift = LIP_OUT + 0.20 + 0.30 * math.sin(math.pi * f * 0.8)
        pts.append(Vector((math.cos(th) * (r + lift), -0.45 + 0.35 * f, CZ + math.sin(th) * (r + lift))))
    end = pts[-1]
    tdir = (pts[-1] - pts[-2]).normalized()
    ang0 = math.atan2(-tdir.x, tdir.z)          # right of travel = outward
    R0 = 0.8
    cx, cz = end.x + R0 * math.cos(ang0), end.z + R0 * math.sin(ang0)
    turns = 1.45
    csteps = 15
    for k in range(1, csteps + 1):
        f = k / csteps
        a = ang0 + math.pi - 2 * math.pi * turns * f
        R = R0 * (1 - 0.72 * f)
        pts.append(Vector((cx + R * math.cos(a), end.y + 0.15 * f, cz + R * math.sin(a))))
    radii = [0.16 * (1 - 0.6 * k / (len(pts) - 1)) for k in range(len(pts))]
    faces = tube(bm, pts, radii, sides=5, closed=False, cap=True, phase=0.3)
    groups.setdefault("stem_dark", []).extend(faces)


def _leaf_mesh(bm, groups, origin, u_dir, n_dir, size=2.3, seed=3):
    """Low-poly pumpkin leaf: 5-lobed outline, folded along the midrib, thin slab.
    ``u_dir`` = midrib direction, ``n_dir`` = approximate leaf normal."""
    rnd = common.rng(seed)
    U = Vector(u_dir).normalized()
    N = Vector(n_dir)
    N = (N - U * N.dot(U)).normalized()
    V = N.cross(U)
    # Palmate 5-lobed outline around the blade centre c (polar, degrees).
    c = 0.42 * size
    shape = [(180, 0.10), (150, 0.34), (112, 0.47), (78, 0.39), (44, 0.57), (21, 0.46),
             (0, 0.64), (-21, 0.46), (-44, 0.57), (-78, 0.39), (-112, 0.47), (-150, 0.34)]
    outline = []
    for deg, rf in shape:
        a = math.radians(deg + rnd.uniform(-3, 3))
        r = size * rf * rnd.uniform(0.95, 1.05)
        outline.append((c + r * math.cos(a), r * math.sin(a)))
    m = len(outline)
    O = Vector(origin)

    def P(u, v, n):
        fold = 0.12 * size * (1 - min(abs(v) / (size * 0.46), 1.0))  # midrib ridge
        droop = -0.10 * size * (u / size) ** 2                       # tip droops
        return O + U * u + V * v + N * (n + fold + droop)

    th = 0.08
    top_c = bm.verts.new(P(c, 0, th))
    bot_c = bm.verts.new(P(c, 0, 0))
    top = [bm.verts.new(P(u, v, th)) for u, v in outline]
    bot = [bm.verts.new(P(u, v, 0)) for u, v in outline]
    # Near-black leaf: dark-green top with a few mossy facets, mossy underside.
    for k in range(m):
        k2 = (k + 1) % m
        groups.setdefault("moss_dark" if k % 4 == 1 else "stem_dark", []).append(
            bm.faces.new((top_c, top[k], top[k2])))
        groups.setdefault("moss_dark", []).append(bm.faces.new((bot_c, bot[k2], bot[k])))
        groups.setdefault("stem_dark", []).append(bm.faces.new((top[k], bot[k], bot[k2], top[k2])))


def _build_frame(collection, ol):
    bm = bmesh.new()
    groups = {}
    _build_frame_ring(bm, ol, groups)
    top_z = CZ + OUTER_LOBES[0][3]
    stem_tip = _build_stem(bm, groups, top_z)
    _build_vine(bm, groups, ol, top_z)
    # Leaf sprouting right from the stem base, tilted towards the viewer.
    _leaf_mesh(bm, groups, origin=(0.35, -0.35, top_z - 0.05), u_dir=(1.0, -0.35, 0.32),
               n_dir=(-0.1, -0.55, 0.85), size=2.7)
    obj = _finish("Portal_Frame", bm, collection, groups=groups, jitter=0.022, seed=21)
    return obj, stem_tip


def _build_frame_cracks(collection, ol):
    """Glowing seams in the bottom of each rib groove (front and back)."""
    bm = bmesh.new()
    for gt in ol.grooves:
        i = min(range(len(ol)), key=lambda k: _ang_dist(ol.thetas[k], gt))
        prof = _frame_profile(ol, i)
        for sgn in (1, -1):
            pts = []
            for t, y in [prof[0], prof[1], prof[2], prof[3], prof[4], prof[5], prof[6]]:
                x, z = ol.point(i, t)
                pts.append(Vector((x, sgn * y - sgn * 0.02, z)))
            # From the inner bevel, along the groove valley, to the outer bevel.
            tube(bm, pts, 0.1, sides=4, closed=False, cap=True, phase=math.pi / 4)
    return _finish("PortalCracks_Frame", bm, collection, glow_key="ember")


def _build_rims(collection, ol):
    """Neon outline tubes: inner opening edge (front + back) and outer ridge."""
    # Decimate: keep fixed (notch) samples and every other free sample.
    keep = []
    for i, th in enumerate(ol.thetas):
        if th in ol.fixed or i % 2 == 0:
            keep.append(i)
    d2 = FRAME_DEPTH / 2
    bm = bmesh.new()
    for sgn in (-1, 1):
        pts = []
        for i in keep:
            x, z = ol.point(i, 0.0, extra=-RIM_INSET)
            pts.append(Vector((x, sgn * (d2 - FRAME_BEVEL * 0.45), z)))
        tube(bm, pts, RIM_R, sides=4, closed=True, phase=math.pi / 4)
    inner = _finish("PortalRim_Inner", bm, collection, glow_key="fire")

    # Outer silhouette outline: a chunky lip that laps onto the front and back
    # faces and sticks out LIP_OUT - OUTER_RIDGE past the frame ridge, so it
    # reads as a bold neon outline head-on. Open arc: both capped ends are
    # tucked under the dais top instead of looping through the dais.
    th_r = _lip_end_angle(0.0, -math.pi / 2) % (2 * math.pi)
    th_l = _lip_end_angle(math.pi, 1.5 * math.pi) % (2 * math.pi)
    span = (th_l - th_r) % (2 * math.pi)
    arc = []
    for i in keep:
        d = (ol.thetas[i] - th_r) % (2 * math.pi)
        if 0.012 < d < span - 0.012:
            arc.append((d, ol.thetas[i], ol.r_out[i]))
    arc.sort()
    ring_params = [(th_r, None)] + [(t, r) for _, t, r in arc] + [(th_l, None)]
    bm = bmesh.new()
    rings = [[bm.verts.new(v) for v in _lip_ring(t, r)] for t, r in ring_params]
    m = len(rings[0])
    for a, b in zip(rings, rings[1:]):
        for j in range(m):
            j2 = (j + 1) % m
            bm.faces.new((a[j], b[j], b[j2], a[j2]))
    bm.faces.new(rings[0])
    bm.faces.new(list(reversed(rings[-1])))
    outer = _finish("PortalRim_Outer", bm, collection, glow_key="fire")
    return [inner, outer]


def _lip_profile():
    """Cross-section of the outer outline lip as (radial offset from the outer
    wall r_out, y), a convex closed loop. The last point sits inside the frame
    body (hidden); the first/second-last lap onto the front/back faces."""
    d2 = FRAME_DEPTH / 2
    return [
        (-LIP_IN, -(d2 + 0.06)), (0.22, -(d2 + 0.10)), (LIP_OUT - 0.10, -0.70 * d2), (LIP_OUT, 0.0),
        (LIP_OUT - 0.10, 0.70 * d2), (0.22, d2 + 0.10), (-LIP_IN, d2 + 0.06), (-0.28, 0.0),
    ]


def _lip_ring(theta, r_out=None):
    """World-space ring of the outline lip at outline angle ``theta``."""
    if r_out is None:
        r_out = _envelope(theta, OUTER_LOBES)[0]
    c, s = math.cos(theta), math.sin(theta)
    return [Vector(((r_out + dr) * c, y, CZ + (r_out + dr) * s)) for dr, y in _lip_profile()]


def _lip_end_angle(start, stop, steps=360):
    """Walk the outline from ``start`` (above the dais) towards ``stop`` (the
    bottom) and return the first angle where every visible lip vertex is at
    least LIP_TUCK below the dais top (the hidden inner vertex may stay above:
    it is inside the frame)."""
    target = DAIS_TOP_Z - LIP_TUCK

    def buried(th):
        return max(v.z for v in _lip_ring(th)[:-1]) <= target

    prev = start
    for k in range(1, steps + 1):
        th = start + (stop - start) * k / steps
        if buried(th):
            lo, hi = prev, th
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                if buried(mid):
                    hi = mid
                else:
                    lo = mid
            return hi
        prev = th
    return stop


def _build_tendrils(collection, ol, seed=9):
    """Lightning-like zig-zag arcs sticking out of the outer silhouette."""
    rnd = common.rng(seed)
    bm = bmesh.new()
    # Angles (degrees, 0 = +X, CCW) - none at the very top (stem) or bottom (dais).
    angles = [-12, 10, 35, 57, 143, 166, 190, 212]
    tips = []
    for k, deg in enumerate(angles):
        th = math.radians(deg + rnd.uniform(-4, 4))
        r, _ = _envelope(th, OUTER_LOBES)
        base_r = r + OUTER_RIDGE - 0.15
        bx, bz = math.cos(th) * base_r, CZ + math.sin(th) * base_r
        if bz < DAIS_TOP_Z + 1.2:
            continue
        # outward direction ~ radial
        out = Vector((math.cos(th), 0, math.sin(th)))
        if out.z < 0:                 # lower arcs flare sideways, not into the ground
            out.z *= 0.35
            out.normalize()
        side = Vector((-out.z, 0, out.x))
        length = rnd.uniform(1.9, 2.9) * (0.75 if k % 3 == 2 else 1.0)
        nseg = 4
        pts = [Vector((bx, 0, bz)) - out * 0.25]
        for s in range(1, nseg + 1):
            f = s / nseg
            zig = (0.42 if s % 2 else -0.42) * (1 - 0.35 * f) * rnd.uniform(0.75, 1.15)
            pts.append(Vector((bx, 0, bz)) + out * (length * f) + side * zig)
        radii = [0.27 * (1 - 0.8 * s / nseg) for s in range(nseg + 1)]
        tube(bm, pts, radii, sides=4, closed=False, cap=True, tip=True, phase=math.pi / 4)
        tips.append(tuple(pts[-1]))
        # Fork on some tendrils.
        if k % 2 == 0:
            p = pts[2]
            fdir = (out + side * (0.9 if rnd.random() < 0.5 else -0.9)).normalized()
            fpts = [p, p + fdir * 0.55 + side * 0.1, p + fdir * 1.05]
            tube(bm, fpts, [0.15, 0.09, 0.0], sides=4, closed=False, cap=True, tip=True, phase=math.pi / 4)
    obj = _finish("PortalTendrils", bm, collection, glow_key="fire")
    return obj, tips


def _blade(bm, cx, cz, y, r0, r1, phi0, sweep, wmax, segs, thick, head=0.25):
    """One flat spiral streak (thin slab) in the XZ plane at depth y.
    Archimedean sweep (r and angle both linear in s) so the outer end wraps
    around tangentially like matter being sucked into a vortex. Width peaks at
    ``head`` (0..1 along the streak) and tapers to sharp points at both ends."""
    def at(s):
        r = r0 + (r1 - r0) * s
        a = phi0 + sweep * s
        return Vector((cx + r * math.cos(a), 0, cz + r * math.sin(a)))

    left, right = [], []
    for k in range(segs + 1):
        s = k / segs
        p = at(s)
        t = (at(min(s + 0.01, 1.0)) - at(max(s - 0.01, 0.0))).normalized()
        nrm = Vector((-t.z, 0, t.x))
        if s <= head:
            w = wmax * math.sin(0.5 * math.pi * s / head) ** 0.7
        else:
            w = wmax * ((1 - s) / (1 - head)) ** 1.15
        w = max(w, 0.0)
        left.append(p + nrm * w * 0.5)
        right.append(p - nrm * w * 0.5)
    yf, yb = y - thick / 2, y + thick / 2
    # Pointed ends: first and last samples collapse to single verts.
    f0 = bm.verts.new((left[0].x, yf, left[0].z))
    b0 = bm.verts.new((left[0].x, yb, left[0].z))
    f1 = bm.verts.new((left[-1].x, yf, left[-1].z))
    b1 = bm.verts.new((left[-1].x, yb, left[-1].z))
    lf = [bm.verts.new((v.x, yf, v.z)) for v in left[1:-1]]
    rf = [bm.verts.new((v.x, yf, v.z)) for v in right[1:-1]]
    lb = [bm.verts.new((v.x, yb, v.z)) for v in left[1:-1]]
    rb = [bm.verts.new((v.x, yb, v.z)) for v in right[1:-1]]
    n = len(lf)
    for k in range(n - 1):
        bm.faces.new((lf[k], rf[k], rf[k + 1], lf[k + 1]))
        bm.faces.new((lb[k + 1], rb[k + 1], rb[k], lb[k]))
        bm.faces.new((lf[k], lf[k + 1], lb[k + 1], lb[k]))
        bm.faces.new((rf[k + 1], rf[k], rb[k], rb[k + 1]))
    for a, (tf, tb_) in ((0, (f0, b0)), (n - 1, (f1, b1))):
        bm.faces.new((lf[a], rf[a], tf))
        bm.faces.new((tb_, rb[a], lb[a]))
        bm.faces.new((lf[a], tf, tb_, lb[a]))
        bm.faces.new((rf[a], rb[a], tb_, tf))


def _hub(bm, cx, cz, y, radius, sides, thick, phase=0.0):
    f, b = [], []
    for k in range(sides):
        a = phase + 2 * math.pi * k / sides
        x, z = cx + radius * math.cos(a), cz + radius * math.sin(a)
        f.append(bm.verts.new((x, y - thick / 2, z)))
        b.append(bm.verts.new((x, y + thick / 2, z)))
    bm.faces.new(f)
    bm.faces.new(list(reversed(b)))
    for k in range(sides):
        k2 = (k + 1) % sides
        bm.faces.new((f[k2], f[k], b[k], b[k2]))


def _build_spinners(collection, cx, cz, rin):
    """Two counter-rotating sets of vortex streaks, each with a front and a back
    layer. Every streak has a twin rotated 180 deg, so each object is point-
    symmetric about (cx, cz) and its bbox centre is exactly the spin axis."""
    objs = []
    # A: 6 long orange streaks (3 point-symmetric pairs) wrapping around the eye.
    bm = bmesh.new()
    pairs_a = [  # (start r, end r, start angle, sweep, width)
        (0.55, 0.92, 0.0, 2.7, 0.62),
        (0.85, 0.80, 1.05, 2.4, 0.5),
        (1.15, 0.97, 2.1, 2.2, 0.42),
    ]
    for y in (-0.62, 0.62):
        for r0, r1f, a0, sweep, w in pairs_a:
            for half in (0.0, math.pi):
                _blade(bm, cx, cz, y, r0, rin * r1f, a0 + half, sweep, w, 12, 0.07, head=0.28)
    objs.append(_finish("PortalSpin_A", bm, collection, glow_key="fire"))
    # B: small hot core: 4 short pale-flame streaks + a glowing hub.
    bm = bmesh.new()
    for y in (-0.82, 0.82):
        for k in range(4):
            _blade(bm, cx, cz, y, 0.25, rin * 0.42, 0.4 + math.pi / 2 * k, 2.0, 0.5, 8, 0.07, head=0.3)
        _hub(bm, cx, cz, y, 0.42, 8, 0.08, phase=math.pi / 8)
    objs.append(_finish("PortalSpin_B", bm, collection, glow_key="flame"))
    return objs


def _build_swirl(collection, ol, ocx, ocz, uv_r):
    """Thin dished slab filling the opening, planar-UV'd from the front."""
    keep = [i for i, th in enumerate(ol.thetas) if th in ol.fixed or i % 2 == 0]
    edge = []
    for i in keep:
        x, z = ol.point(i, 0.0, extra=0.18)   # tuck 0.18 into the frame wall
        edge.append((x, z))
    scales = [1.0, 0.78, 0.55, 0.32, 0.12]
    ys = [0.36, 0.30, 0.22, 0.14, 0.08]     # half-thickness per ring (dish)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    cx0, cz0 = 0.0, CZ                      # polar centre (opening is star-shaped from here)
    m = len(edge)

    def ringpts(s):
        return [(cx0 + (x - cx0) * s, cz0 + (z - cz0) * s) for x, z in edge]

    front, back = [], []
    for s, hy in zip(scales, ys):
        pts = ringpts(s)
        front.append([bm.verts.new((x, -hy, z)) for x, z in pts])
        back.append([bm.verts.new((x, hy, z)) for x, z in pts])
    cf = bm.verts.new((cx0, -0.05, cz0))
    cb = bm.verts.new((cx0, 0.05, cz0))
    faces = []
    for layer, c, flip in ((front, cf, False), (back, cb, True)):
        for k in range(len(layer) - 1):
            a, b = layer[k], layer[k + 1]
            for j in range(m):
                j2 = (j + 1) % m
                quad = (a[j], a[j2], b[j2], b[j])
                faces.append(bm.faces.new(tuple(reversed(quad)) if flip else quad))
        last = layer[-1]
        for j in range(m):
            j2 = (j + 1) % m
            tri = (last[j], last[j2], c)
            faces.append(bm.faces.new(tuple(reversed(tri)) if flip else tri))
    # Edge band joining front and back (hidden inside the frame wall).
    for j in range(m):
        j2 = (j + 1) % m
        bm.faces.new((front[0][j2], front[0][j], back[0][j], back[0][j2]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        for loop in f.loops:
            co = loop.vert.co
            loop[uv].uv = (0.5 + (co.x - ocx) / (2 * uv_r), 0.5 + (co.z - ocz) / (2 * uv_r))
    obj = common.object_from_bmesh("Portal_Swirl", bm, collection)
    obj.data.materials.append(common.mat_swirl(make_swirl_texture()))
    common.shade_flat(obj)
    common.origin_to_base(obj)
    return obj


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
OBJECT_NAMES = [
    "Portal_Dais", "GLOW_ember_PortalCracks_Dais", "Portal_Frame", "GLOW_ember_PortalCracks_Frame",
    "GLOW_fire_PortalRim_Inner", "GLOW_fire_PortalRim_Outer", "GLOW_fire_PortalTendrils",
    "Portal_Swirl", "GLOW_fire_PortalSpin_A", "GLOW_flame_PortalSpin_B",
]
# Names used by earlier versions (removed too when rebuilding in an old file).
_LEGACY_NAMES = ["GLOW_ember_PortalRim_Outer", "GLOW_amber_PortalSpin_A"]


def _layout():
    """Pure geometry (no objects): outline, opening centre/radius, base info."""
    ol = Outline()
    inner_poly = ol.inner_xz()
    # Opening centre: x = 0, z chosen to maximise the inscribed circle.
    best = (0.0, CZ)
    for k in range(-40, 41):
        z = CZ + k * 0.05
        r = _inscribed(inner_poly, 0.0, z)
        if r > best[0]:
            best = (r, z)
    rin_raw, ocz = best
    rin = rin_raw - RIM_PROTRUSION   # clear radius inside the neon rim tube
    ocx = 0.0
    outer = ol.outer_xz(OUTER_RIDGE)
    xs = [x for x, _ in outer]
    zs = [z for _, z in outer]
    info = {
        "dais_radius": DAIS_R,
        "dais_top_radius": DAIS_TOP_R,
        "dais_ledge_z": DAIS_LEDGE_Z,
        "dais_top_z": DAIS_TOP_Z,
        "opening_center": (ocx, 0.0, ocz),
        "opening_inscribed_radius": rin,        # clear of the neon rim tube
        "opening_inscribed_radius_raw": rin_raw,
        "opening_bottom_z": min(z for _, z in inner_poly),
        "spin_center": (ocx, 0.0, ocz),         # = bbox centre of both PortalSpin objects
        "normal": (0.0, -1.0, 0.0),             # portal faces -Y
        "frame_width": max(xs) - min(xs),
        "frame_height": CZ + OUTER_LOBES[0][3] + 2.1,   # approx. stem top (exact after build)
        "frame_body_top_z": max(zs),
        "frame_depth": FRAME_DEPTH,
        "front_y": -(FRAME_DEPTH / 2 + FRAME_BULGE),   # front face of the ring (lobe bulge)
        "back_y": FRAME_DEPTH / 2 + FRAME_BULGE,
        "outline_outer_xz": [(round(x, 3), round(z, 3)) for x, z in outer[::3]],
    }
    return ol, info


def build_portal(collection=None):
    """Build every portal object (centred at origin, dais bottom z=0, facing -Y).
    Returns the list of objects and fills PORTAL_INFO."""
    for name in OBJECT_NAMES + _LEGACY_NAMES:   # rebuilding in the same file: avoid ".001" names
        old = bpy.data.objects.get(name)
        if old is not None:
            data = old.data
            bpy.data.objects.remove(old)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)
    ol, info = _layout()
    ocx, _, ocz = info["opening_center"]
    rin = info["opening_inscribed_radius"]
    uv_r = max(math.hypot(x - ocx, z - ocz) for x, z in ol.inner_xz(extra=0.18)) * 1.02

    objs = []
    objs.append(_build_dais(collection))
    objs.append(_build_dais_cracks(collection))
    frame, stem_tip = _build_frame(collection, ol)
    objs.append(frame)
    objs.append(_build_frame_cracks(collection, ol))
    objs.extend(_build_rims(collection, ol))
    tendrils, tips = _build_tendrils(collection, ol)
    objs.append(tendrils)
    objs.append(_build_swirl(collection, ol, ocx, ocz, uv_r))
    objs.extend(_build_spinners(collection, ocx, ocz, rin))

    info["frame_height"] = max(v.co.z + frame.location.z for v in frame.data.vertices)
    info["front_y_extent"] = min(v.co.y + frame.location.y for v in frame.data.vertices)  # incl. leaf
    info["stem_tip"] = tuple(round(c, 3) for c in stem_tip)
    info["tendril_tips"] = [tuple(round(c, 3) for c in t) for t in tips]
    info["tri_count"] = sum(common.tri_count(o) for o in objs)
    info["objects"] = [o.name for o in objs]
    PORTAL_INFO.clear()
    PORTAL_INFO.update(info)
    return objs


def demo():
    common.reset_scene()
    objs = build_portal()
    total = 0
    problems = []
    for o in objs:
        tc = common.tri_count(o)
        total += tc
        problems += common.validate_object(o)
        print(f"  {o.name:34s} {tc:6d} tris")
    print(f"PORTAL TOTAL: {total} tris")
    for p in problems:
        print("  PROBLEM:", p)
    for k, v in PORTAL_INFO.items():
        if k not in ("outline_outer_xz", "objects"):
            print(f"  {k}: {v}")
    import preview
    views = os.environ.get("PORTAL_VIEWS", "front,three_quarter,back,low_hero").split(",")
    samples = int(os.environ.get("PORTAL_SAMPLES", "32"))
    focus = ((0.0, 0.0, 6.4), 8.2)
    night = os.environ.get("PORTAL_DAY", "0") != "1"
    out = "previews/_wip/portal.png" if night else "previews/_wip/portal_day.png"
    preview.render(out, views=views, res=(960, 600), samples=samples, focus=focus, night=night)


# Geometry-only info is available right after import (no objects needed);
# build_portal() refreshes it and adds mesh-derived keys.
PORTAL_INFO.update(_layout()[1])

if __name__ == "__main__":
    demo()
