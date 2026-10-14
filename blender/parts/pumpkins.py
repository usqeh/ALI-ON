"""Pumpkins, jack-o'-lanterns, pumpkin piles and candy corn (Halloween props).

Everything is built from bmesh / mesh data (the jack-o'-lantern face is carved
with a Boolean *modifier* that is applied through the evaluated mesh - no
bpy.ops modelling).

Public API (all objects are linked to the scene, flat shaded, scale 1, origin
at the bottom centre of each object, placed at ``location`` with the Z
rotation stored as object rotation):

* make_pumpkin(name, location, rotation_z, size, variant, seed, color, stem)
      -> [obj]                      lobed low-poly pumpkin (body + stem in one mesh)
      variant: 'round' | 'tall' | 'squat' | 'gourd'
      color:   'pumpkin' | 'deep' | 'amber' | 'charcoal' | 'bone'
* make_jack_o_lantern(name, location, rotation_z, size, face, seed, color)
      -> [shell, glow]              shell = ``name`` (e.g. 'JackOLantern_01'),
                                    glow  = 'GLOW_fire_<name>_Face'
      face:    'classic' | 'scary' | 'happy'
* make_pumpkin_pile(prefix, location, rotation_z, seed, count, with_jack)
      -> [objs]                     natural cluster, everything rests on z=0
                                    or on another pumpkin
* make_candy_corn(name, location, rotation_z, size, seed) -> [obj]

``size`` is the overall width (diameter) of a pumpkin in studs; for the candy
corn it is the height. The jack-o'-lantern face looks towards -Y.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy, bmesh, common  # noqa: E401,E402

import math  # noqa: E402

import preview  # noqa: E402

from mathutils import Matrix, Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402
from mathutils.kdtree import KDTree  # noqa: E402

# --------------------------------------------------------------------------
# Colour ways (palette keys) and body variants
# --------------------------------------------------------------------------
COLORWAYS = {
    "pumpkin": dict(front="pumpkin", side="pumpkin_rib", top="pumpkin_light",
                    bottom="pumpkin_dark", dimple="pumpkin_dark",
                    stem=("stem_green", "stem_dark"), cap="wood",
                    leaf=("stem_green", "stem_dark")),
    "deep": dict(front="pumpkin_rib", side="pumpkin_dark", top="pumpkin",
                 bottom="rust", dimple="rust",
                 stem=("stem_green", "stem_dark"), cap="wood",
                 leaf=("stem_green", "stem_dark")),
    "amber": dict(front="amber", side="pumpkin_light", top="amber",
                  bottom="pumpkin", dimple="pumpkin_rib",
                  stem=("stem_green", "stem_dark"), cap="wood",
                  leaf=("stem_green", "stem_dark")),
    "charcoal": dict(front="ash", side="charcoal", top="stone_dark",
                     bottom="black", dimple="black",
                     stem=("wood", "wood_dark"), cap="wood",
                     leaf=("stem_dark", "moss_dark")),
    "bone": dict(front="candle_wax", side="bone", top="candle_wax",
                 bottom="bone", dimple="stone_light",
                 stem=("stem_green", "stem_dark"), cap="wood",
                 leaf=("stem_green", "stem_dark")),
}

# profile: rings bottom -> top as (radius fraction of R, height fraction of H).
# bot/top: height fraction of the bottom / top centre vertex (dimples).
VARIANTS = {
    "round": dict(ribs=9, hw=0.68, groove=0.15, bot=0.05, top=0.86,
                  profile=[(0.34, 0.00), (0.74, 0.08), (0.96, 0.28), (1.00, 0.50),
                           (0.92, 0.73), (0.66, 0.92), (0.30, 0.995)],
                  stem_h=0.20, stem_r=0.070),
    "tall": dict(ribs=8, hw=0.92, groove=0.13, bot=0.04, top=0.90,
                 profile=[(0.38, 0.00), (0.78, 0.07), (0.97, 0.24), (1.00, 0.45),
                          (0.94, 0.67), (0.74, 0.87), (0.36, 0.985)],
                 stem_h=0.24, stem_r=0.066),
    "squat": dict(ribs=10, hw=0.50, groove=0.19, bot=0.06, top=0.80,
                  profile=[(0.40, 0.00), (0.80, 0.11), (0.98, 0.33), (1.00, 0.53),
                           (0.91, 0.78), (0.62, 0.96), (0.30, 0.99)],
                  stem_h=0.13, stem_r=0.066),
    "gourd": dict(ribs=6, hw=1.30, groove=0.08, bot=0.03, top=1.0,
                  profile=[(0.42, 0.00), (0.84, 0.06), (1.00, 0.20), (0.93, 0.36),
                           (0.60, 0.50), (0.42, 0.63), (0.40, 0.79), (0.33, 0.93)],
                  stem_h=0.18, stem_r=0.090),
    # jack-o'-lantern body: rounder and a bit taller so the face fits
    "jack": dict(ribs=8, hw=0.76, groove=0.12, bot=0.05, top=0.88,
                 profile=[(0.36, 0.00), (0.76, 0.08), (0.97, 0.27), (1.00, 0.49),
                          (0.93, 0.72), (0.68, 0.915), (0.30, 0.99)],
                 stem_h=0.17, stem_r=0.068),
}


JACK_MAX_TRIS = 1000   # shell + glow face together

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
class _Groups(dict):
    """palette key -> list of bmesh faces."""

    def add(self, key, faces):
        if not isinstance(faces, (list, tuple)):
            faces = [faces]
        self.setdefault(key, []).extend(faces)


def _finish(name, bm, groups):
    """Paint the face groups, build the object (M_Palette, flat shaded)."""
    for key, faces in groups.items():
        live = [f for f in faces if f.is_valid]
        if live:
            common.paint_bmesh_faces(bm, live, key)
    for f in bm.faces:
        f.material_index = 0
    obj = common.object_from_bmesh(name, bm)
    obj.data.materials.append(common.mat_palette())
    common.shade_flat(obj)
    return obj


def _place(obj, location, rotation_z):
    """Origin -> bottom centre of the mesh, then put the object's *local*
    frame at ``location`` rotated by ``rotation_z`` (objects built together in
    one local frame keep their relative placement)."""
    obj.matrix_world = Matrix.Identity(4)
    common.origin_to_base(obj)
    off = obj.matrix_world.translation.copy()
    obj.matrix_world = (Matrix.Translation(Vector(location))
                        @ Matrix.Rotation(rotation_z, 4, "Z")
                        @ Matrix.Translation(off))
    return obj


def _floor_bmesh(bm, verts=None):
    """Shift so the lowest vertex sits at z = 0."""
    vs = list(verts if verts is not None else bm.verts)
    zmin = min(v.co.z for v in vs)
    for v in bm.verts:
        v.co.z -= zmin


def _frames(pts):
    """Parallel-transport frames (tangent, normal, binormal) along a polyline.
    Interior tangents bisect the joint (mitre), so every ring is cut at half
    the turning angle - this is what ``_bend_turns`` relies on."""
    n = len(pts)
    tans = []
    for i in range(n):
        if 0 < i < n - 1:
            t = (pts[i] - pts[i - 1]).normalized() + (pts[i + 1] - pts[i]).normalized()
            if t.length < 1e-6:                  # 180 degree reversal: fall back to the chord
                t = pts[i + 1] - pts[i - 1]
        else:
            t = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        tans.append(t.normalized())
    ref = Vector((0, 0, 1)) if abs(tans[0].z) < 0.9 else Vector((1, 0, 0))
    nrm = tans[0].cross(ref).normalized()
    out = []
    for t in tans:
        nrm = (nrm - t * nrm.dot(t)).normalized()
        out.append((t, nrm, t.cross(nrm)))
    return out


def _tube(bm, pts, radii, sides, phase=0.0, cap_end=True, cap_start=False, squash=1.0):
    """Tube along ``pts``. Returns (rings, faces_per_side, cap_faces).
    Normals point outwards by construction."""
    frames = _frames(pts)
    angles = [phase + 2 * math.pi * k / sides for k in range(sides)]
    rings = []
    for p, r, (t, n, b) in zip(pts, radii, frames):
        rings.append([bm.verts.new(p + (n * math.cos(a) + b * math.sin(a) * squash) * r) for a in angles])
    per_side = [[] for _ in range(sides)]
    for i in range(len(rings) - 1):
        for k in range(sides):
            k2 = (k + 1) % sides
            per_side[k].append(bm.faces.new((rings[i][k], rings[i][k2], rings[i + 1][k2], rings[i + 1][k])))
    caps = []
    if cap_end:
        caps.append(bm.faces.new(rings[-1]))
    if cap_start:
        caps.append(bm.faces.new(list(reversed(rings[0]))))
    return rings, per_side, caps


def _surface_fn(var, W, H):
    """Crest height of the pumpkin top as a function of horizontal radius."""
    R = W / 2
    prof = var["profile"]
    top_i = max(range(len(prof)), key=lambda i: prof[i][1])
    pts = [(0.0, var["top"] * H)]
    pts += [(prof[i][0] * R, prof[i][1] * H) for i in range(len(prof) - 1, top_i, -1)]
    eq = max(range(len(prof)), key=lambda i: prof[i][0])
    pts += [(prof[i][0] * R, prof[i][1] * H) for i in range(top_i, eq - 1, -1)]

    def f(rho):
        for (r0, z0), (r1, z1) in zip(pts, pts[1:]):
            if r0 <= rho <= r1 and r1 > r0:
                return z0 + (z1 - z0) * (rho - r0) / (r1 - r0)
        return pts[-1][1]
    return f


# --------------------------------------------------------------------------
# Pumpkin body
# --------------------------------------------------------------------------
def _body(bm, groups, var, W, H, cw, seed, segs_per_rib=3, jitter=0.008):
    """Lobed, flattened sphere. Returns dict with useful info."""
    r = common.rng(seed)
    R = W / 2
    N = var["ribs"]
    prof = var["profile"]
    g = var["groove"]
    S = segs_per_rib
    offs = [0.0, 0.3, 0.7] if S == 3 else [0.0, 0.5]
    rib_s = [1.0 + r.uniform(-0.05, 0.05) for _ in range(N)]
    phase = r.uniform(0, 2 * math.pi)
    sx, sy = 1.0 + r.uniform(-0.04, 0.04), 1.0 + r.uniform(-0.04, 0.04)

    rings = []
    for rf, zf in prof:
        w = 0.55 + 0.45 * math.sin(math.pi * min(max(zf, 0.0), 1.0))
        ring = []
        for k in range(N):
            for j, o in enumerate(offs):
                a = phase + 2 * math.pi * (k + o) / N
                if j == 0:
                    s = 0.5 * (rib_s[k] + rib_s[k - 1]) * (1.0 - g * w)
                else:
                    s = rib_s[k]
                rr = R * rf * s
                ring.append(bm.verts.new((rr * math.cos(a) * sx, rr * math.sin(a) * sy, H * zf)))
        rings.append(ring)
    tilt = Vector((r.uniform(-0.03, 0.03) * W, r.uniform(-0.03, 0.03) * W, 0))
    v_bot = bm.verts.new((0, 0, H * var["bot"]))
    v_top = bm.verts.new((tilt.x, tilt.y, H * var["top"]))

    M = N * S
    nb = len(rings) - 1
    z_top_ring = max(zf for _, zf in prof)
    for b in range(nb):
        zmid = 0.5 * (prof[b][1] + prof[b + 1][1])
        dimple_band = prof[b + 1][1] < prof[b][1]          # band going down into the dimple
        for m in range(M):
            m2 = (m + 1) % M
            f = bm.faces.new((rings[b][m], rings[b][m2], rings[b + 1][m2], rings[b + 1][m]))
            j = m % S
            if S == 3:
                front = j == 1
            else:
                front = j == 0
            if b == 0:
                key = cw["bottom"]
            elif dimple_band:
                key = cw["dimple"] if not front else cw["side"]
            elif zmid > 0.80 * z_top_ring and front:
                key = cw["top"]
            elif zmid < 0.2 and not front:
                key = cw["bottom"]
            else:
                key = cw["front"] if front else cw["side"]
            groups.add(key, f)
    for m in range(M):
        m2 = (m + 1) % M
        groups.add(cw["bottom"], bm.faces.new((v_bot, rings[0][m2], rings[0][m])))
        groups.add(cw["dimple"], bm.faces.new((v_top, rings[-1][m], rings[-1][m2])))

    body_verts = [v for ring in rings for v in ring] + [v_bot, v_top]
    if jitter:
        common.jitter_bmesh(bm, jitter * W, seed=seed + 101, verts=body_verts)
    _floor_bmesh(bm, body_verts)
    return dict(top=Vector(v_top.co), R=R, H=H, surf=_surface_fn(var, W, H), verts=body_verts)


def _bend_turns(lens, radii, want, keep=0.42):
    """Clamp the turning angle at each interior ring of a mitred tube so its
    inner side never folds back on itself (which would make the faces of the
    bend self-intersect).

    lens:  segment lengths (n - 1 for n rings), radii: ring radii (n),
    want:  desired turning angle (radians) at rings 1 .. n - 2, all bending
           the same way (a planar hook).
    A ring of radius r cut at half its turning angle t pulls the inner side of
    each neighbouring segment back by r * tan(t / 2); that is kept below
    ``keep`` * the shorter neighbouring segment, so every inner edge keeps at
    least (1 - 2 * keep) of its length. Angle cut at one ring is handed to the
    rings below it that still have room (tip first), so the total curl - the
    overall look of the stem - is kept whenever the tube can take it."""
    caps = [2.0 * math.atan(keep * min(lens[i], lens[i + 1]) / radii[i + 1]) for i in range(len(want))]
    got = [max(0.0, min(w, c)) for w, c in zip(want, caps)]
    excess = sum(want) - sum(got)
    for i in reversed(range(len(got))):
        if excess <= 1e-9:
            break
        add = min(excess, caps[i] - got[i])
        got[i] += add
        excess -= add
    return got


def _stem(bm, groups, info, var, W, cw, seed, curl=None):
    """Curly stem growing out of the top dimple (5-sided, hooked end).

    The centre line is a planar hook built from segment lengths and turning
    angles; ``_bend_turns`` limits each bend to what the stem's thickness
    allows, so the curl never folds the tube into itself."""
    r = common.rng(seed + 7)
    h = var["stem_h"] * W * r.uniform(0.9, 1.1)
    rs = var["stem_r"] * W
    ang = r.uniform(0, 2 * math.pi)
    u = Vector((math.cos(ang), math.sin(ang), 0))
    curl = r.uniform(0.45, 1.0) if curl is None else curl
    top = info["top"]
    surf = info["surf"]
    base = Vector((top.x, top.y, 0))
    z0 = top.z - 0.02 * W
    zf = surf(1.5 * rs) + 0.01 * W        # flare ring rests on the dimple slope
    zf = max(zf, z0 + 0.04 * W)
    radii = [rs * 1.05, rs * 1.55, rs * 1.0, rs * 0.92, rs * 0.84, rs * 0.78]
    # ring 0 sits inside the body, ring 1 is the flare on the dimple, then
    # four segments rise and hook over towards ``u``.
    lens = [zf - z0, 0.36 * h, 0.36 * h, 0.28 * h, 0.24 * h]
    up = Vector((0, 0, 1))

    def centre_line(turns):
        pts = [base + Vector((0, 0, z0))]
        heading = 0.0                      # angle from +Z towards u
        for i, L in enumerate(lens):
            if i:
                heading += turns[i - 1]
            pts.append(pts[-1] + (u * math.sin(heading) + up * math.cos(heading)) * L)
        return pts

    def clear_of_body(pts):
        """Lowest point of every ring above the flare stays above the top."""
        for i in range(2, len(pts)):
            nxt = pts[min(i + 1, len(pts) - 1)]
            t = ((pts[i] - pts[i - 1]).normalized() + (nxt - pts[i]).normalized()).normalized()
            down = t * t.z - up
            low = pts[i] + (down.normalized() * radii[i] if down.length > 1e-6 else Vector())
            if low.z < surf(math.hypot(low.x, low.y)) + 0.02 * W:
                return False
        return True

    for _ in range(8):                     # relax the curl if the hooked tip would dip into the top
        want = [math.radians(5.0)] + [curl * math.radians(a) for a in (12.0, 30.0, 62.0)]
        pts = centre_line(_bend_turns(lens, radii, want))
        if clear_of_body(pts):
            break
        curl *= 0.8
    rings, per_side, caps = _tube(bm, pts, radii, 5, phase=r.uniform(0, 1))
    for k, faces in enumerate(per_side):
        groups.add(cw["stem"][k % 2], faces)
    groups.add(cw["cap"], caps)
    return u


def _vine(bm, groups, info, W, cw, u, seed):
    """Thin curly tendril: runs out along the top, then curls up (spiral)."""
    r = common.rng(seed + 13)
    R = info["R"]
    surf = info["surf"]
    top = info["top"]
    c0 = Vector((top.x, top.y, 0))
    v = Vector((-u.y, u.x, 0))
    tr = 0.022 * W
    pts = []
    for rho in (0.10, 0.22, 0.34):
        p = c0 + u * (rho * R)
        pts.append(Vector((p.x, p.y, surf(math.hypot(p.x, p.y)) + tr * 0.6)))
    P = pts[-1]
    rc0 = 0.15 * R * r.uniform(0.85, 1.15)
    C = P + Vector((0, 0, rc0))
    turns = 1.5
    n = 9
    for i in range(1, n + 1):
        t = i / n
        phi = -math.pi / 2 + t * turns * 2 * math.pi
        rc = rc0 * (1.0 - 0.68 * t)
        cc = C + v * (0.5 * rc0 * t)          # sideways drift: a helix, not a flat loop
        pts.append(cc + u * (rc * math.cos(phi)) + Vector((0, 0, rc * math.sin(phi))))
    radii = [tr * (1.0 - 0.45 * i / (len(pts) - 1)) for i in range(len(pts))]
    rings, per_side, caps = _tube(bm, pts, radii, 3, cap_end=True)
    for k, faces in enumerate(per_side):
        groups.add(cw["stem"][0] if k == 0 else cw["stem"][1], faces)
    groups.add(cw["stem"][1], caps)


def _leaf(bm, groups, info, W, cw, direction, seed, length=None):
    """Small folded leaf lying on the top of the pumpkin."""
    r = common.rng(seed + 29)
    R = info["R"]
    surf = info["surf"]
    top = info["top"]
    L = (length or 0.62 * R) * r.uniform(0.9, 1.1)
    wd = 0.42 * L
    u = direction.normalized()
    v = Vector((-u.y, u.x, 0))
    c0 = Vector((top.x, top.y, 0)) + u * (0.10 * R)
    outline = [(0.0, 0.0), (0.30, 0.55), (0.72, 0.38), (1.0, 0.0), (0.72, -0.38), (0.30, -0.55)]
    ridge = [(0.30, 0.0), (0.72, 0.0)]
    th = 0.02 * W

    def pos(a, b, lift):
        p = c0 + u * (a * L) + v * (b * wd)
        return Vector((p.x, p.y, surf(math.hypot(p.x, p.y)) + lift))

    top_o = [bm.verts.new(pos(a, b, 0.03 * W - 0.02 * W * abs(b))) for a, b in outline]
    top_r = [bm.verts.new(pos(a, b, 0.03 * W + 0.035 * L)) for a, b in ridge]
    bot_o = [bm.verts.new(Vector(vv.co) - Vector((0, 0, th))) for vv in top_o]
    b0, l1, l2, tip, r2, r1 = top_o
    m1, m2 = top_r
    light, dark = cw["leaf"]
    groups.add(light, [bm.faces.new((b0, m1, l1)), bm.faces.new((m1, m2, l2, l1)), bm.faces.new((m2, tip, l2))])
    groups.add(dark, [bm.faces.new((b0, r1, m1)), bm.faces.new((r1, r2, m2, m1)), bm.faces.new((r2, tip, m2))])
    # the outline runs clockwise seen from above
    groups.add(dark, bm.faces.new(bot_o))
    n = len(top_o)
    for i in range(n):
        j = (i + 1) % n
        groups.add(dark, bm.faces.new((top_o[i], top_o[j], bot_o[j], bot_o[i])))


# --------------------------------------------------------------------------
# Public: pumpkin
# --------------------------------------------------------------------------
def _pumpkin_bmesh(variant, size, seed, color, stem, vine=None, leaf=None):
    var = VARIANTS[variant]
    cw = COLORWAYS[color]
    W = size
    r = common.rng(seed)
    H = W * var["hw"] * r.uniform(0.95, 1.05)
    S = 2 if (size < 1.5 or variant == "gourd") else 3
    bm = bmesh.new()
    groups = _Groups()
    if variant == "gourd":
        cw = dict(cw)
        cw["top"] = {"pumpkin": "amber", "deep": "pumpkin", "amber": "candle_wax"}.get(color, cw["top"])
    info = _body(bm, groups, var, W, H, cw, seed, segs_per_rib=S)
    if stem:
        u = _stem(bm, groups, info, var, W, cw, seed)
        roll_v, roll_l, side = r.random(), r.random(), r.random()
        if vine is None:
            vine = variant != "gourd" and size >= 1.4 and roll_v < 0.65
        if leaf is None:
            leaf = variant != "gourd" and size >= 1.6 and roll_l < 0.6
        if vine:
            _vine(bm, groups, info, W, cw, -u if side < 0.5 else Vector((-u.y, u.x, 0)), seed)
        if leaf:
            a = math.atan2(u.y, u.x) + (2.3 if side >= 0.5 else -2.3)
            _leaf(bm, groups, info, W, cw, Vector((math.cos(a), math.sin(a), 0)), seed)
    return bm, groups, info


def make_pumpkin(name, location=(0, 0, 0), rotation_z=0.0, size=2.0, variant="round",
                 seed=0, color="pumpkin", stem=True, vine=None, leaf=None):
    """Low-poly lobed pumpkin. ``size`` = width in studs. ``vine`` / ``leaf``:
    True/False to force the curly tendril / leaf, None = decided by ``seed``.
    Returns [obj]."""
    bm, groups, _info = _pumpkin_bmesh(variant, size, seed, color, stem, vine, leaf)
    obj = _finish(name, bm, groups)
    _place(obj, location, rotation_z)
    return [obj]


# --------------------------------------------------------------------------
# Jack-o'-lantern face shapes (2D, x right / z up, units of R, z relative to
# the face centre). Polygons may be concave but must not self-intersect.
# --------------------------------------------------------------------------
def _ccw(poly):
    a = 0.0
    for (x0, z0), (x1, z1) in zip(poly, poly[1:] + poly[:1]):
        a += x0 * z1 - x1 * z0
    return poly if a > 0 else list(reversed(poly))


def _mouth(x_c, z_upper, z_lower, top_teeth, bottom_teeth, n=6, zig=None):
    """Mouth polygon between two edge functions. Teeth = (x0, x1, length)
    rectangles (or triangles with zig) protruding into the opening."""
    up = []
    xs = [-x_c + 2 * x_c * i / n for i in range(n + 1)]
    pts_x = sorted(set([round(x, 4) for x in xs]))
    # upper edge, left -> right, with teeth hanging down
    for x in pts_x:
        up.append((x, z_upper(x)))
    for (x0, x1, ln) in top_teeth:
        up = [p for p in up if not (x0 - 1e-4 <= p[0] <= x1 + 1e-4)]
        if zig:
            tooth = [(x0, z_upper(x0)), (0.5 * (x0 + x1), z_upper(0.5 * (x0 + x1)) - ln), (x1, z_upper(x1))]
        else:
            tooth = [(x0, z_upper(x0)), (x0 + 0.01, z_upper(x0) - ln), (x1 - 0.01, z_upper(x1) - ln), (x1, z_upper(x1))]
        up += tooth
        up.sort(key=lambda p: p[0])
    low = []
    for x in pts_x[1:-1]:
        low.append((x, z_lower(x)))
    for (x0, x1, ln) in bottom_teeth:
        low = [p for p in low if not (x0 - 1e-4 <= p[0] <= x1 + 1e-4)]
        if zig:
            tooth = [(x0, z_lower(x0)), (0.5 * (x0 + x1), z_lower(0.5 * (x0 + x1)) + ln), (x1, z_lower(x1))]
        else:
            tooth = [(x0, z_lower(x0)), (x0 + 0.01, z_lower(x0) + ln), (x1 - 0.01, z_lower(x1) + ln), (x1, z_lower(x1))]
        low += tooth
        low.sort(key=lambda p: p[0])
    poly = up + list(reversed(low))
    return _ccw(poly)


def _face_shapes(face):
    feats = []
    if face == "classic":
        for s in (-1, 1):
            cx = 0.36 * s
            feats.append(_ccw([(cx - 0.17, 0.14), (cx + 0.17, 0.14), (cx + 0.04 * s, 0.47)]))
        feats.append(_ccw([(-0.085, -0.03), (0.085, -0.03), (0.0, 0.12)]))
        xc = 0.64

        def zu(x):
            return -0.08 - 0.15 * (1 - (x / xc) ** 2)

        def zl(x):
            return -0.08 - 0.46 * max(0.0, 1 - (x / xc) ** 2) ** 0.7

        feats.append(_mouth(xc, zu, zl, top_teeth=[(-0.11, 0.07, 0.13)],
                            bottom_teeth=[(-0.40, -0.26, 0.12), (0.20, 0.34, 0.12)], n=6))
    elif face == "scary":
        for s in (-1, 1):
            feats.append(_ccw([(0.62 * s, 0.44), (0.12 * s, 0.25), (0.40 * s, 0.08)]))
        feats.append(_ccw([(-0.07, 0.07), (0.07, 0.07), (0.0, -0.06)]))
        xc = 0.68

        def zu(x):
            return -0.08 - 0.10 * (1 - (x / xc) ** 2) + 0.06 * (x / xc) ** 4

        def zl(x):
            return -0.08 - 0.44 * max(0.0, 1 - (x / xc) ** 2) ** 0.8 + 0.06 * (x / xc) ** 4

        feats.append(_mouth(xc, zu, zl,
                            top_teeth=[(-0.46, -0.30, 0.16), (-0.10, 0.08, 0.20), (0.26, 0.42, 0.16)],
                            bottom_teeth=[(-0.30, -0.12, 0.17), (0.10, 0.28, 0.17)], n=8, zig=True))
    elif face == "happy":
        for s in (-1, 1):
            cx = 0.34 * s
            arc = [(cx + 0.17 * math.cos(a), 0.17 + 0.20 * math.sin(a))
                   for a in [math.pi * i / 6 for i in range(7)]]
            feats.append(_ccw(arc))
        feats.append(_ccw([(-0.07, -0.02), (0.07, -0.02), (0.0, 0.09)]))
        xc = 0.62

        def zu(x):
            return -0.06 - 0.07 * (1 - (x / xc) ** 2) + 0.04 * (x / xc) ** 2

        def zl(x):
            return -0.06 - 0.50 * max(0.0, 1 - (x / xc) ** 2) ** 0.55 + 0.04 * (x / xc) ** 2

        feats.append(_mouth(xc, zu, zl, top_teeth=[(-0.20, -0.04, 0.12)],
                            bottom_teeth=[(0.10, 0.25, 0.11)], n=8))
    else:
        raise ValueError(f"unknown face {face!r}")
    return feats


def _carve_parts(face, info, W, H, face_scale, face_z, flesh, depth, bvh):
    """Build (cutter bmesh, glow-cup bmesh) for the face features.

    cutter: closed prisms from outside the pumpkin to ``flesh`` behind the
            surface (Boolean-subtracted from the shell -> holes with walls).
    cup:    the glowing inside: walls from that ring to a flat floor
            ``depth`` behind the surface, normals facing the viewer."""
    R = info["R"]
    zc = face_z * H
    feats = _face_shapes(face)
    cut = bmesh.new()
    cup = bmesh.new()
    floor_pts = []
    y_front = -W
    for poly in feats:
        pts = [(x * R * face_scale, zc + z * R * face_scale) for x, z in poly]
        ys = []
        for x, z in pts:
            hit = bvh.ray_cast(Vector((x, -3 * W, z)), Vector((0, 1, 0)))
            ys.append(hit[0].y if hit[0] is not None else 0.0)
        y_back = max(ys) + depth
        n = len(pts)
        # cutter prism (front ring -> mid ring)
        f_ring = [cut.verts.new((x, y_front, z)) for x, z in pts]
        m_ring = [cut.verts.new((x, y + flesh, z)) for (x, z), y in zip(pts, ys)]
        floor_pts += [Vector(v.co) for v in m_ring]
        cut.faces.new(f_ring)                       # facing -Y (pts are CCW from the front)
        cut.faces.new(list(reversed(m_ring)))
        for i in range(n):
            j = (i + 1) % n
            cut.faces.new((f_ring[i], m_ring[i], m_ring[j], f_ring[j]))
        # glow cup (mid ring -> flat floor), facing into the cavity
        c_mid = [cup.verts.new((x, y + flesh, z)) for (x, z), y in zip(pts, ys)]
        c_bot = [cup.verts.new((x, y_back, z)) for x, z in pts]
        cup.faces.new(c_bot)                        # floor faces -Y (towards the viewer)
        for i in range(n):
            j = (i + 1) % n
            cup.faces.new((c_mid[i], c_mid[j], c_bot[j], c_bot[i]))
    bmesh.ops.recalc_face_normals(cut, faces=cut.faces[:])
    return cut, cup, floor_pts


def _lid_parts(info, W, bvh, seed, teeth=5, rho=0.45, amp=0.075, width=0.034, depth=0.05):
    """Zig-zag lid cut around the stem: (cutter bmesh, glow strip bmesh).
    The strip is the bottom of the slot, facing up (light leaking out)."""
    r = common.rng(seed + 41)
    R = info["R"]
    K = 2 * teeth
    phase = r.uniform(0, 2 * math.pi)
    cut = bmesh.new()
    strip = bmesh.new()
    rings = {"ti": [], "to": [], "bi": [], "bo": []}
    srings = {"bi": [], "bo": []}
    ztop = info["H"] * 1.6
    for i in range(K):
        th = phase + 2 * math.pi * i / K
        rr = (rho + (amp if i % 2 == 0 else -amp)) * R
        for side, off in (("i", -0.5 * width * W), ("o", 0.5 * width * W)):
            x, y = (rr + off) * math.cos(th), (rr + off) * math.sin(th)
            hit = bvh.ray_cast(Vector((x, y, ztop)), Vector((0, 0, -1)))
            zs = hit[0].z if hit[0] is not None else info["H"]
            rings["t" + side].append(cut.verts.new((x, y, ztop)))
            rings["b" + side].append(cut.verts.new((x, y, zs - depth * W)))
            srings["b" + side].append(strip.verts.new((x, y, zs - depth * W)))
    ti, to, bi, bo = rings["ti"], rings["to"], rings["bi"], rings["bo"]
    for i in range(K):
        j = (i + 1) % K
        cut.faces.new((to[i], to[j], ti[j], ti[i]))
        cut.faces.new((bo[i], bi[i], bi[j], bo[j]))
        cut.faces.new((ti[i], ti[j], bi[j], bi[i]))
        cut.faces.new((to[i], bo[i], bo[j], to[j]))
        sbo, sbi = srings["bo"], srings["bi"]
        strip.faces.new((sbo[i], sbo[j], sbi[j], sbi[i]))       # faces up
    bmesh.ops.recalc_face_normals(cut, faces=cut.faces[:])
    return cut, strip, [Vector(v.co) for v in bi + bo]


def _boolean_carve(shell, cut_bm, tag):
    """Subtract ``cut_bm`` from ``shell`` (exact Boolean modifier, applied via
    the evaluated mesh). Returns (bmesh of the result, faces that came from
    the cutter). The shell must only use M_Palette before the call."""
    cutter = common.object_from_bmesh(f"{shell.name}_{tag}", cut_bm)
    cutter.data.materials.append(common.mat_glow())
    mod = shell.modifiers.new(tag, "BOOLEAN")
    mod.object = cutter
    mod.operation = "DIFFERENCE"
    mod.solver = "EXACT"
    mod.material_mode = "TRANSFER"
    common.apply_modifiers(shell)
    cut_me = cutter.data
    bpy.data.objects.remove(cutter)
    bpy.data.meshes.remove(cut_me)
    glow_idx = {i for i, m in enumerate(shell.data.materials) if m is not None and m.name == "M_Glow"}
    bm = bmesh.new()
    bm.from_mesh(shell.data)
    bm.normal_update()
    carved = [f for f in bm.faces if f.material_index in glow_idx]
    return bm, carved


def _split_floor(carved, floor_pts, tol=1e-4):
    """Carved faces whose vertices all lie on the cutter's inner cap are the
    floor of the cut (replaced by the glow mesh); the rest are walls."""
    kd = KDTree(len(floor_pts))
    for i, p in enumerate(floor_pts):
        kd.insert(p, i)
    kd.balance()
    floor, walls = [], []
    for f in carved:
        on_cap = all(kd.find(v.co)[2] < tol for v in f.verts)
        (floor if on_cap else walls).append(f)
    return floor, walls


def _drop_crumbs(bm, max_faces=6):
    """Delete tiny disconnected face islands (Boolean slivers)."""
    seen = set()
    crumbs = []
    for f in bm.faces:
        if f in seen:
            continue
        seen.add(f)
        stack, isl = [f], []
        while stack:
            g = stack.pop()
            isl.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        stack.append(h)
        if len(isl) <= max_faces:
            crumbs.extend(isl)
    if crumbs:
        bmesh.ops.delete(bm, geom=crumbs, context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")


def _store_shell(shell, bm):
    """Write ``bm`` back into the shell with M_Palette as its only material."""
    for f in bm.faces:
        f.material_index = 0
    bm.to_mesh(shell.data)
    bm.free()
    while len(shell.data.materials) > 1:
        shell.data.materials.pop(index=len(shell.data.materials) - 1)
    shell.data.materials[0] = common.mat_palette()


def make_jack_o_lantern(name, location=(0, 0, 0), rotation_z=0.0, size=2.5, face="classic",
                        seed=0, color="pumpkin", face_scale=None, lid=True):
    """Carved jack-o'-lantern facing -Y.

    Returns [shell, glow]: shell ``name`` (pumpkin with real carved holes and
    lit-flesh walls, optional zig-zag lid cut) and ``GLOW_fire_<name>_Face``
    (the glowing inside of the carved holes + the light leaking out of the lid
    cut; it sits exactly in the openings, no coplanar overlap)."""
    var = VARIANTS["jack"]
    cw = COLORWAYS[color]
    W = size
    r = common.rng(seed)
    H = W * var["hw"] * r.uniform(0.97, 1.03)
    bm = bmesh.new()
    groups = _Groups()
    info = _body(bm, groups, var, W, H, cw, seed, segs_per_rib=3, jitter=0.006)
    # Paint the body now (UVs survive the Boolean), the stem comes later.
    for key, faces in groups.items():
        common.paint_bmesh_faces(bm, faces, key)
    bvh = BVHTree.FromBMesh(bm)
    fs = face_scale or {"classic": 1.0, "scary": 0.98, "happy": 1.0}.get(face, 1.0)
    cut_bm, glow_bm, face_floor = _carve_parts(face, info, W, H, fs, 0.50, flesh=0.055 * W,
                                               depth=0.16 * W, bvh=bvh)
    lid_bm = None
    if lid:
        lid_cut, lid_bm, lid_floor = _lid_parts(info, W, bvh, seed)

    shell = common.object_from_bmesh(name, bm)
    shell.data.materials.append(common.mat_palette())

    # Pass 1: face. Floor of each cut -> removed (the glow cup sits there),
    # walls -> lit pumpkin flesh.
    bm, carved = _boolean_carve(shell, cut_bm, "CarveFace")
    floor, walls = _split_floor(carved, face_floor)
    bmesh.ops.delete(bm, geom=floor, context="FACES")
    common.paint_bmesh_faces(bm, [f for f in walls if f.is_valid], "amber")
    _drop_crumbs(bm)
    _store_shell(shell, bm)

    # Pass 2: lid cut (slot floor -> glow strip, slot walls -> flesh).
    if lid:
        bm, carved = _boolean_carve(shell, lid_cut, "CarveLid")
        floor, walls = _split_floor(carved, lid_floor)
        bmesh.ops.delete(bm, geom=floor, context="FACES")
        common.paint_bmesh_faces(bm, [f for f in walls if f.is_valid], "amber")
        _drop_crumbs(bm)
        _store_shell(shell, bm)

    # Stem on top.
    bm = bmesh.new()
    bm.from_mesh(shell.data)
    groups = _Groups()
    _stem(bm, groups, info, var, W, cw, seed, curl=r.uniform(0.6, 1.0))
    for key, faces in groups.items():
        common.paint_bmesh_faces(bm, faces, key)
    _store_shell(shell, bm)
    common.shade_flat(shell)

    if lid_bm is not None:
        tmp = bpy.data.meshes.new("_tmp_lid")
        lid_bm.to_mesh(tmp)
        lid_bm.free()
        glow_bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
    glow = common.object_from_bmesh(name + "_FaceTmp", glow_bm)
    common.make_glow(glow, "fire", f"{name}_Face")
    common.shade_flat(glow)
    if lid and common.tri_count(shell) + common.tri_count(glow) > JACK_MAX_TRIS:
        # budget guard: rebuild without the lid cut (never hit with the
        # default 8 ribs / 5 teeth, kept for custom settings)
        for o in (shell, glow):
            me = o.data
            bpy.data.objects.remove(o)
            bpy.data.meshes.remove(me)
        return make_jack_o_lantern(name, location, rotation_z, size, face, seed, color, face_scale, lid=False)
    _place(shell, location, rotation_z)
    _place(glow, location, rotation_z)
    return [shell, glow]


# --------------------------------------------------------------------------
# Candy corn
# --------------------------------------------------------------------------
def make_candy_corn(name, location=(0, 0, 0), rotation_z=0.0, size=0.6, seed=0):
    """Upright candy corn kernel (amber base, orange middle, wax-white tip).
    ``size`` = height in studs. Returns [obj]."""
    H = size
    a, b = 0.40 * H, 0.21 * H           # half width (x) / half depth (y) at the base
    sides = 8
    prof = [(0.84, 0.0), (1.0, 0.07), (0.88, 0.36), (0.62, 0.64), (0.26, 0.91)]
    keys = ["amber", "amber", "pumpkin", "candle_wax"]
    bm = bmesh.new()
    groups = _Groups()
    rings = []
    for s, zf in prof:
        ring = []
        for k in range(sides):
            ang = 2 * math.pi * (k + 0.5) / sides
            c, sn = math.cos(ang), math.sin(ang)
            # squarish super-ellipse cross-section
            ex = math.copysign(abs(c) ** 0.8, c)
            ey = math.copysign(abs(sn) ** 0.8, sn)
            ring.append(bm.verts.new((a * s * ex, b * s * ey * (1.0 - 0.25 * zf), H * zf)))
        rings.append(ring)
    for i in range(len(rings) - 1):
        for k in range(sides):
            k2 = (k + 1) % sides
            groups.add(keys[i], bm.faces.new((rings[i][k], rings[i][k2], rings[i + 1][k2], rings[i + 1][k])))
    tip = bm.verts.new((0, 0, H))
    for k in range(sides):
        groups.add("candle_wax", bm.faces.new((rings[-1][k], rings[-1][(k + 1) % sides], tip)))
    groups.add("amber", bm.faces.new(list(reversed(rings[0]))))
    common.jitter_bmesh(bm, 0.012 * H, seed=seed + 3)
    _floor_bmesh(bm)
    obj = _finish(name, bm, groups)
    _place(obj, location, rotation_z)
    return [obj]


# --------------------------------------------------------------------------
# Pile
# --------------------------------------------------------------------------
def _world_verts(obj):
    mw = obj.matrix_world
    return [mw @ v.co for v in obj.data.vertices]


def _bvh_world(obj):
    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    polys = [list(p.vertices) for p in obj.data.polygons]
    return BVHTree.FromPolygons(verts, polys)


def _footprint(obj, axis_xy):
    vs = _world_verts(obj)
    zmax = max(v.z for v in vs)
    zmin = min(v.z for v in vs)
    lim = zmin + 0.7 * (zmax - zmin)
    return max(math.hypot(v.x - axis_xy[0], v.y - axis_xy[1]) for v in vs if v.z <= lim)


def make_pumpkin_pile(prefix, location=(0, 0, 0), rotation_z=0.0, seed=0, count=4, with_jack=True):
    """A natural cluster of ``count`` pumpkins (a jack-o'-lantern among them
    when ``with_jack``). Pieces touch but do not interpenetrate; with
    count >= 5 a small pumpkin rests on top of the biggest one.
    Returns all created objects."""
    r = common.rng(seed)
    specs = []
    n_jack = 1 if with_jack and count >= 1 else 0
    if n_jack:
        specs.append(dict(kind="jack", size=r.uniform(2.3, 2.6),
                          face=r.choice(["classic", "scary", "happy"])))
    rest = count - n_jack
    pattern = ["big", "medium", "small", "small", "medium", "small", "medium", "small"]
    small_colors = ["amber", "charcoal", "bone", "pumpkin", "deep"]
    r.shuffle(small_colors)
    med_colors = ["charcoal", "pumpkin", "deep"]
    for i in range(rest):
        role = pattern[i % len(pattern)]
        if role == "big":
            specs.append(dict(kind="pumpkin", size=r.uniform(2.9, 3.4),
                              variant=r.choice(["squat", "round"]), color="pumpkin"))
        elif role == "medium":
            specs.append(dict(kind="pumpkin", size=r.uniform(1.7, 2.1),
                              variant=r.choice(["round", "tall"]),
                              color=med_colors[(i // 3) % len(med_colors)]))
        else:
            specs.append(dict(kind="pumpkin", size=r.uniform(0.95, 1.3),
                              variant=r.choice(["round", "gourd", "squat", "round"]),
                              color=small_colors[i % len(small_colors)]))
    # one small one stacked on the biggest pumpkin when the pile is large enough
    stack_i = None
    bigs = [i for i, s in enumerate(specs) if s["kind"] == "pumpkin"]
    if count >= 5 and len(bigs) >= 2:
        base_i = max(bigs, key=lambda i: specs[i]["size"])
        smalls = [i for i in bigs if specs[i]["size"] < 1.4]
        if smalls:
            stack_i = smalls[-1]
            specs[base_i]["stem"] = False
            specs[base_i]["variant"] = "squat"
            specs[stack_i]["on"] = base_i
            specs[stack_i]["variant"] = "round"

    order = sorted(range(len(specs)), key=lambda i: -specs[i]["size"])
    rot_m = Matrix.Rotation(rotation_z, 3, "Z")
    loc = Vector(location)
    placed = []          # (index, local xy Vector, radius)
    out = []
    objs_by_i = {}
    n_p = 0
    n_j = 0
    jack_at = None
    for i in order:
        s = specs[i]
        own_rot = r.uniform(-math.pi, math.pi)
        if s["kind"] == "jack":
            own_rot = r.uniform(-0.35, 0.35)
        # build at the origin first to measure the footprint
        if s["kind"] == "jack":
            n_j += 1
            objs = make_jack_o_lantern(f"{prefix}_JackOLantern_{n_j:02d}", (0, 0, 0), own_rot,
                                       size=s["size"], face=s["face"], seed=seed * 31 + i)
        else:
            n_p += 1
            objs = make_pumpkin(f"{prefix}_Pumpkin_{n_p:02d}", (0, 0, 0), own_rot, size=s["size"],
                                variant=s["variant"], seed=seed * 31 + i, color=s["color"],
                                stem=s.get("stem", True))
        rad = max(_footprint(o, (0, 0)) for o in objs[:1])
        if "on" in s:
            objs_by_i[i] = (objs, None, rad, own_rot)
            continue
        if not placed:
            pos = Vector((0.0, 0.0))
        else:
            # candidates touching an already placed pumpkin
            best = None
            if s["kind"] == "jack":
                target = Vector((0.2, -1.2))
            else:
                target = Vector((0.0, 0.5 * (s["size"] - 2.0)))
            for (pj, pp, pr) in placed:
                for k in range(36):
                    a = 2 * math.pi * k / 36
                    d = pr + rad + 0.03
                    cand = pp + Vector((math.cos(a), math.sin(a))) * d
                    ok = all((cand - qp).length >= (qr + rad + 0.02) for (_, qp, qr) in placed)
                    if not ok:
                        continue
                    score = (cand - target).length + r.uniform(0, 0.35)
                    if jack_at is not None:
                        # keep the jack-o'-lantern's face free (it looks towards -Y)
                        dj = cand - jack_at[0]
                        if dj.y < 0.3 * jack_at[1] and abs(dj.x) < 0.85 * (jack_at[1] + rad):
                            score += 100.0
                    if best is None or score < best[0]:
                        best = (score, cand)
            pos = best[1]
        if s["kind"] == "jack":
            jack_at = (pos, rad)
        placed.append((i, pos, rad))
        objs_by_i[i] = (objs, pos, rad, own_rot)

    # final placement (pile frame -> world)
    for i in order:
        objs, pos, rad, own_rot = objs_by_i[i]
        if pos is None:
            continue
        p3 = loc + rot_m @ Vector((pos.x, pos.y, 0.0))
        for o in objs:
            _move_rigid(o, p3, rotation_z)
        out.extend(objs)

    # stacked one: rest it on top of its base by ray casting down
    if stack_i is not None and stack_i in objs_by_i:
        objs, _, rad, own_rot = objs_by_i[stack_i]
        base_objs = objs_by_i[specs[stack_i]["on"]][0]
        base = base_objs[0]
        bpos = base.matrix_world.translation
        off = Vector((r.uniform(-0.08, 0.08), r.uniform(-0.08, 0.08), 0)) * specs[specs[stack_i]["on"]]["size"]
        target = Vector((bpos.x, bpos.y, 0)) + off
        for o in objs:
            _move_rigid(o, target, 0.0)
        lift = _rest_lift(objs[0], base)
        for o in objs:
            o.matrix_world = Matrix.Translation(Vector((0, 0, lift + 0.01))) @ o.matrix_world
        out.extend(objs)
    return out


def _rest_lift(top_obj, base_obj):
    """How far ``top_obj`` must move up so it rests on ``base_obj`` without
    interpenetrating (vertical ray casts both ways, world space)."""
    up, down = Vector((0, 0, 1)), Vector((0, 0, -1))
    base_bvh = _bvh_world(base_obj)
    top_bvh = _bvh_world(top_obj)
    vs = _world_verts(top_obj)
    zmin = min(v.z for v in vs)
    hmax = max(v.z for v in vs) - zmin
    lift = 0.0
    for v in vs:                                   # top's lower verts above the base surface
        if v.z - zmin > 0.4 * hmax:
            continue
        hit = base_bvh.ray_cast(Vector((v.x, v.y, v.z + 50.0)), down)
        if hit[0] is not None:
            lift = max(lift, hit[0].z - v.z)
    for v in _world_verts(base_obj):               # base verts below the top's underside
        hit = top_bvh.ray_cast(Vector((v.x, v.y, zmin - 50.0)), up)
        if hit[0] is not None:
            lift = max(lift, v.z - hit[0].z)
    return lift


def _move_rigid(obj, new_origin_xy, extra_rot):
    """Rotate an object (built around the world origin) by ``extra_rot`` about
    the world Z axis through the origin, then translate by ``new_origin_xy``."""
    obj.matrix_world = (Matrix.Translation(Vector((new_origin_xy[0], new_origin_xy[1], new_origin_xy[2] if len(new_origin_xy) > 2 else 0.0)))
                        @ Matrix.Rotation(extra_rot, 4, "Z") @ obj.matrix_world)


# --------------------------------------------------------------------------
# Demo / catalogue
# --------------------------------------------------------------------------
def _check_normals(obj):
    """Signed volume per object (positive = outward normals on closed parts)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    vol = bm.calc_volume(signed=True)
    bm.free()
    return vol


def _tight_focus(objs):
    """Framing for a wide, flat catalogue row (preview frames a bounding
    sphere into the vertical field of view, which wastes the image width)."""
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    c = (lo + hi) / 2
    return (c, max((hi - lo).length / 2 / 1.45, 1.0))


def demo(views=("front", "three_quarter"), only=None, samples=32):
    common.reset_scene()
    lineup, jacks = [], []
    rows = [
        (2.6, [("Pumpkin_Round_01", 2.0, "round", "pumpkin", 1),
               ("Pumpkin_Tall_01", 2.0, "tall", "pumpkin", 2),
               ("Pumpkin_Squat_01", 3.2, "squat", "pumpkin", 3),
               ("Pumpkin_Charcoal_01", 2.0, "round", "charcoal", 5)]),
        (-0.4, [("Pumpkin_Gourd_01", 1.2, "gourd", "pumpkin", 4),
                ("Pumpkin_Deep_01", 1.6, "tall", "deep", 6),
                ("Pumpkin_Bone_01", 1.4, "squat", "bone", 7),
                ("Pumpkin_Amber_01", 1.1, "round", "amber", 8),
                ("Pumpkin_CharcoalGourd_01", 1.1, "gourd", "charcoal", 9)]),
    ]
    for ry, row in rows:
        width = sum(sz for _, sz, _, _, _ in row) + 0.7 * (len(row) - 1)
        x = -width / 2
        for nm, sz, var, col, sd in row:
            x += sz / 2
            lineup += make_pumpkin(nm, (x, ry, 0), 0.3 * sd, size=sz, variant=var, seed=sd, color=col)
            x += sz / 2 + 0.7
    for k in range(3):
        lineup += make_candy_corn(f"CandyCorn_{k + 1:02d}", (-1.0 + 1.0 * k, -2.4, 0), 0.5 * k - 0.5,
                                  size=0.6 + 0.15 * k, seed=k)
    for k, face in enumerate(("classic", "scary", "happy")):
        jacks += make_jack_o_lantern(f"JackOLantern_{k + 1:02d}", (-6.0 + 3.2 * k, -0.5, 0), 0.0,
                                     size=2.5, face=face, seed=20 + k)
    jacks += make_pumpkin_pile("PumpkinPile_A", (4.6, 0.5, 0), 0.0, seed=4, count=5, with_jack=True)

    allobjs = lineup + jacks
    total = 0
    print("=== pumpkins demo ===")
    for o in allobjs:
        t = common.tri_count(o)
        total += t
        probs = common.validate_object(o)
        print(f"  {o.name:40s} {t:5d} tris  vol={_check_normals(o):7.3f}  {'; '.join(probs)}")
    print(f"  total {total} tris in {len(allobjs)} objects")

    if only in (None, "lineup"):
        for o in jacks:
            o.hide_render = True
        preview.render("previews/_wip/pumpkins_lineup.png", views=list(views), objects=lineup,
                       res=(960, 600), samples=samples, focus=_tight_focus(lineup))
        for o in jacks:
            o.hide_render = False
    if only in (None, "jacks"):
        for o in lineup:
            o.hide_render = True
        preview.render("previews/_wip/pumpkins_jacks.png", views=list(views), objects=jacks,
                       res=(960, 600), samples=samples, focus=_tight_focus(jacks))
        for o in lineup:
            o.hide_render = False


if __name__ == "__main__":
    _views = ("front", "three_quarter")
    _only = None
    for arg in sys.argv[1:]:
        if arg.startswith("--views="):
            _views = tuple(arg.split("=", 1)[1].split(","))
        elif arg.startswith("--only="):
            _only = arg.split("=", 1)[1]
    demo(_views, _only)
