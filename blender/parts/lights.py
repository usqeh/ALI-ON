"""Light sources and witchy props: braziers, a crooked lantern post, candle
clusters, a bubbling cauldron, a witch hat and spider webs.

Everything is built from bmesh / mesh data (no bpy.ops modelling), coloured
through the palette texture (M_Palette) and the glow material (M_Glow).

Public API (all objects are linked to the scene, flat shaded, scale 1, origin
at the bottom centre of each object's own bbox; the objects of one prop are
built in one design frame whose ground point (post foot, pot axis, crown axis
...) is placed at ``location`` with ``rotation_z`` stored as object rotation,
so the object origins sit near, not exactly on, ``location``). Every factory
returns a list of objects, solid parts first:

* make_brazier(name, location, rotation_z, height=4.5, side='L', seed=0)
      -> [name (iron tripod + pumpkin-lobed bowl + coals),
          'GLOW_fire_BrazierFlame_<side>'      outer flame tongues,
          'GLOW_flame_BrazierFlameCore_<side>' pale inner core,
          'GLOW_ember_BrazierEmbers_<side>'    glowing coals in the bowl]
      ``height`` = top of the iron bowl rim; the flames add ~50 % on top.
* make_lantern_post(name, location, rotation_z, height=7.0, seed=0)
      -> [name (stone footing + crooked iron shepherd's-crook post + chain +
          lantern cage), 'GLOW_amber_LanternGlow_##' (glass block)]
* make_candles(prefix, location, rotation_z, count=3, seed=0, height=1.3,
               wax='cream', join_flames=False)
      -> [prefix (all candles + drips + wax puddles + wicks in one mesh),
          'GLOW_flame_CandleFlame_##' x count (or one joined flame object)]
      wax: 'cream' | 'black' | 'orange'
* make_cauldron(name, location, rotation_z, size=3.0, seed=0, liquid_key='fire',
                fire=True)
      -> [name (iron pot on legs, ring handles, logs, wooden paddle),
          'GLOW_<liquid_key>_CauldronLiquid<sfx>' (surface + bubble blobs),
          'GLOW_ember_CauldronFlame<sfx>' (flames licking the pot; fire=True)]
      <sfx> is the trailing '_NN' of ``name`` ('' for plain 'Cauldron').
* make_witch_hat(name, location, rotation_z, size=1.5, seed=0) -> [hat]
      ``size`` = brim diameter. ``location`` = centre of the crown base (the
      brim underside, z = 0), so the hat can be dropped onto a pumpkin:
      location = top centre of the pumpkin body (sink it ~0.05-0.1). The
      floppy tip leans towards local +X.
* make_spider_web(name, location, rotation_z, size=2.5, corner='TL', seed=0,
                  spider=True, at_corner=False) -> [web]
      A flat web in the local XZ plane (faces -Y). corner: 'TL','TR','BL','BR'
      (a quarter web spanning a 90 degree corner; the anchor corner is the
      named bbox corner) or 'full' (a round web, ``size`` = radius).
      Default: ``location`` = origin (bottom centre of the web's bbox).
      at_corner=True: ``location`` = the anchor point (corner / hub) instead.
      spider_web_anchor_offset(size, corner) ~ anchor - origin (local).

Naming follows the Roblox VFX contract (see roblox/EXPORT_NOTES.md): only the
actual fire meshes contain 'Flame', lantern glass contains 'LanternGlow', the
cauldron surface contains 'CauldronLiquid'. Solid props never use those words.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy, bmesh, common  # noqa: E401,E402

import math  # noqa: E402
import re  # noqa: E402

from mathutils import Matrix, Vector  # noqa: E402

import preview  # noqa: E402

TAU = 2 * math.pi


# --------------------------------------------------------------------------
# Small mesh-building toolkit
# --------------------------------------------------------------------------
class _Mesh:
    """A bmesh plus palette face groups (key -> faces)."""

    def __init__(self):
        self.bm = bmesh.new()
        self.groups = {}

    def add(self, key, faces):
        if isinstance(faces, bmesh.types.BMFace):
            faces = [faces]
        self.groups.setdefault(key, []).extend(faces)

    def verts(self):
        return list(self.bm.verts)

    def finish(self, name, default_key="iron"):
        bm = self.bm
        painted = set()
        for key, faces in self.groups.items():
            live = [f for f in faces if f.is_valid]
            common.paint_bmesh_faces(bm, live, key)
            painted.update(live)
        rest = [f for f in bm.faces if f not in painted]
        if rest:
            common.paint_bmesh_faces(bm, rest, default_key)
        for f in bm.faces:
            f.material_index = 0
        _warn_clash(name)
        obj = common.object_from_bmesh(name, bm)
        obj.data.materials.append(common.mat_palette())
        common.shade_flat(obj)
        self.bm = None
        return obj

    def finish_glow(self, key, desc):
        final = f"GLOW_{key}_{desc}"
        _warn_clash(final)
        obj = common.object_from_bmesh(final, self.bm)
        common.make_glow(obj, key, desc)
        common.shade_flat(obj)
        self.bm = None
        return obj


def _warn_clash(name):
    if bpy.data.objects.get(name) is not None:
        print(f"[lights] WARNING: object name {name!r} already exists - Blender will add a suffix")


def _free_index(prefix, start=1):
    i = start
    while bpy.data.objects.get(f"{prefix}{i:02d}") is not None:
        i += 1
    return i


def _suffix(name):
    m = re.search(r"(_\d+)$", name)
    return m.group(1) if m else ""


def _place(obj, location, rotation_z):
    """Origin -> bottom centre of the mesh, then put the design frame at
    ``location`` rotated by ``rotation_z`` (objects built together in one
    design frame keep their relative placement)."""
    obj.matrix_world = Matrix.Identity(4)
    common.origin_to_base(obj)
    off = obj.matrix_world.translation.copy()
    obj.matrix_world = (Matrix.Translation(Vector(location))
                        @ Matrix.Rotation(rotation_z, 4, "Z")
                        @ Matrix.Translation(off))
    return obj


def _scale_bm(bm, s):
    if abs(s - 1.0) > 1e-9:
        bmesh.ops.scale(bm, vec=(s, s, s), verts=list(bm.verts))


def _lift_bm(bm, dz):
    if abs(dz) > 1e-9:
        bmesh.ops.translate(bm, vec=(0.0, 0.0, dz), verts=list(bm.verts))


def _clamp_ground(bm):
    """Snap everything that dips below the ground plane (tilted tube caps,
    leaning flame bases) up to exactly z = 0, so the prop rests flush."""
    for v in bm.verts:
        if v.co.z < 0.0:
            v.co.z = 0.0


def _bridge(bm, A, B):
    """Faces between two rings (a ring of length 1 is a pole). Outward normals
    when B follows A along the profile with the solid on the right."""
    faces = []
    n = max(len(A), len(B))
    if len(A) > 1 and len(B) > 1:
        for k in range(n):
            k2 = (k + 1) % n
            faces.append(bm.faces.new((A[k], A[k2], B[k2], B[k])))
    elif len(A) == 1 and len(B) > 1:
        for k in range(n):
            faces.append(bm.faces.new((A[0], B[(k + 1) % n], B[k])))
    elif len(A) > 1 and len(B) == 1:
        for k in range(n):
            faces.append(bm.faces.new((A[k], A[(k + 1) % n], B[0])))
    return faces


def _frames(pts, ref=None):
    """Parallel-transport frames (tangent, normal, binormal) along a polyline."""
    n = len(pts)
    tans = []
    for i in range(n):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, n - 1)]
        tans.append((b - a).normalized())
    if ref is None:
        ref = Vector((1, 0, 0)) if abs(tans[0].z) > 0.9 else Vector((0, 0, 1))
    nrm = tans[0].cross(ref).normalized()
    out = []
    for t in tans:
        nrm = (nrm - t * nrm.dot(t)).normalized()
        out.append((t, nrm, t.cross(nrm)))
    return out


def _tube(M, key, pts, radii, sides, phase=0.0, twist=0.0, cap_start=True, cap_end=True,
          squash=1.0, ref=None):
    """Tube along ``pts``; radius 0 at an end makes a pointed pole."""
    bm = M.bm
    pts = [Vector(p) for p in pts]
    frames = _frames(pts, ref)
    rings = []
    for i, (p, r, (t, n, b)) in enumerate(zip(pts, radii, frames)):
        if r <= 1e-6:
            rings.append([bm.verts.new(p)])
            continue
        ph = phase + twist * i
        rings.append([bm.verts.new(p + (n * math.cos(a) + b * math.sin(a) * squash) * r)
                      for a in (ph + TAU * k / sides for k in range(sides))])
    faces = []
    for A, B in zip(rings, rings[1:]):
        faces += _bridge(bm, A, B)
    if cap_start and len(rings[0]) > 1:
        faces.append(bm.faces.new(list(reversed(rings[0]))))
    if cap_end and len(rings[-1]) > 1:
        faces.append(bm.faces.new(rings[-1]))
    if key is not None:
        M.add(key, faces)
    return rings, faces


def _lathe(M, profile, segs, center=(0.0, 0.0), phase=0.0, rmul=None, keys=None,
           cap_start=False, cap_end=False, zjit=None):
    """Surface of revolution. ``profile`` = [(r, z), ...] in traversal order
    (solid on the right -> outward normals); r <= 0 is a pole.
    rmul(angle, ring_index) -> radius multiplier. keys: one palette key per
    gap (list) or a single key. zjit(angle, ring_index) -> z offset."""
    bm = M.bm
    cx, cy = center
    angles = [phase + TAU * k / segs for k in range(segs)]
    rings = []
    for i, (r, z) in enumerate(profile):
        if r <= 1e-6:
            rings.append([bm.verts.new((cx, cy, z))])
            continue
        ring = []
        for a in angles:
            rr = r * (rmul(a, i) if rmul else 1.0)
            zz = z + (zjit(a, i) if zjit else 0.0)
            ring.append(bm.verts.new((cx + rr * math.cos(a), cy + rr * math.sin(a), zz)))
        rings.append(ring)
    gaps = []
    for i, (A, B) in enumerate(zip(rings, rings[1:])):
        f = _bridge(bm, A, B)
        gaps.append(f)
        if keys is not None:
            M.add(keys[i] if isinstance(keys, (list, tuple)) else keys, f)
    caps = []
    if cap_start and len(rings[0]) > 1:
        caps.append(bm.faces.new(list(reversed(rings[0]))))
    if cap_end and len(rings[-1]) > 1:
        caps.append(bm.faces.new(rings[-1]))
    return rings, gaps, caps


def _box(M, key, center, size, mat=None):
    """Axis box (optionally transformed by 4x4 ``mat`` about its centre)."""
    res = bmesh.ops.create_cube(M.bm, size=1.0)
    verts = res["verts"]
    m = Matrix.Translation(Vector(center)) @ (mat or Matrix.Identity(4)) @ Matrix.Diagonal((*size, 1.0))
    bmesh.ops.transform(M.bm, matrix=m, verts=verts)
    faces = list({f for v in verts for f in v.link_faces})
    if key is not None:
        M.add(key, faces)
    return verts, faces


def _taper_box(M, keys, cx, cy, z0, z1, w0, d0, w1, d1, top=True, bottom=False):
    """Tapered block; keys = (top, side, bottom)."""
    bm = M.bm
    lo = [bm.verts.new((cx + sx * w0 / 2, cy + sy * d0 / 2, z0)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    hi = [bm.verts.new((cx + sx * w1 / 2, cy + sy * d1 / 2, z1)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    sides = _bridge(bm, lo, hi)
    M.add(keys[1], sides)
    out = list(sides)
    if top:
        f = bm.faces.new(hi)
        M.add(keys[0], f)
        out.append(f)
    if bottom:
        f = bm.faces.new(list(reversed(lo)))
        M.add(keys[2], f)
        out.append(f)
    return lo + hi, out


def _ico(M, key, center, radius, scale=(1.0, 1.0, 1.0), rot=None, jitter=0.0, seed=0):
    res = bmesh.ops.create_icosphere(M.bm, subdivisions=1, radius=1.0)
    verts = res["verts"]
    m = (Matrix.Translation(Vector(center)) @ (rot or Matrix.Identity(4))
         @ Matrix.Diagonal((radius * scale[0], radius * scale[1], radius * scale[2], 1.0)))
    bmesh.ops.transform(M.bm, matrix=m, verts=verts)
    if jitter:
        common.jitter_bmesh(M.bm, jitter, seed, verts)
    faces = list({f for v in verts for f in v.link_faces})
    if key is not None:
        M.add(key, faces)
    return verts, faces


def _gem(M, key, center, r, rnd, squash=0.75):
    """Octahedron chunk (8 tris) - embers, sparks."""
    bm = M.bm
    c = Vector(center)
    a0 = rnd.uniform(0, TAU)
    ring = [bm.verts.new(c + Vector((math.cos(a0 + k * TAU / 4) * r * rnd.uniform(0.8, 1.2),
                                     math.sin(a0 + k * TAU / 4) * r * rnd.uniform(0.8, 1.2), 0)))
            for k in range(4)]
    top = bm.verts.new(c + Vector((0, 0, r * squash * rnd.uniform(0.9, 1.3))))
    bot = bm.verts.new(c - Vector((0, 0, r * squash)))
    faces = _bridge(bm, [bot], ring) + _bridge(bm, ring, [top])
    M.add(key, faces)
    return faces


def _torus(M, key, center, R, r, axis, segs=8, sides=4, stretch=1.0):
    """Low-poly torus. ``axis`` = ring normal (Vector). ``stretch`` elongates
    the ring along the in-plane axis closest to world Z (chain links)."""
    bm = M.bm
    axis = Vector(axis).normalized()
    up = Vector((0, 0, 1))
    u = (up - axis * up.dot(axis))
    u = u.normalized() if u.length > 1e-6 else Vector((1, 0, 0))
    v = axis.cross(u)
    c = Vector(center)
    rings = []
    for i in range(segs):
        a = TAU * i / segs
        d = u * math.cos(a) * stretch + v * math.sin(a)
        p = c + d * R
        dn = d.normalized()
        rings.append([bm.verts.new(p + (dn * math.cos(b) + axis * math.sin(b)) * r)
                      for b in (TAU * k / sides for k in range(sides))])
    faces = []
    for i in range(segs):
        A, B = rings[i], rings[(i + 1) % segs]
        for k in range(sides):
            k2 = (k + 1) % sides
            faces.append(bm.faces.new((A[k], B[k], B[k2], A[k2])))
    M.add(key, faces)
    return faces


def _basis(x, y, z, origin=(0, 0, 0)):
    m = Matrix.Identity(4)
    for i in range(3):
        m[i][0], m[i][1], m[i][2] = x[i], y[i], z[i]
        m[i][3] = origin[i]
    return m


# --------------------------------------------------------------------------
# Flames
# --------------------------------------------------------------------------
TONGUE_T = (0.0, 0.18, 0.40, 0.62, 0.81, 1.0)  # default tongue profile (t, radius)
TONGUE_R = (0.80, 1.0, 0.90, 0.64, 0.34, 0.0)


def _tongue(M, base, h, r, lean=(0.0, 0.0), wiggle=0.0, wdir=0.0, twist=0.22, sides=6,
            phase=0.0, squash=1.0, key="glow_fire", prof=None, bow=0.0):
    """One chunky cartoon flame tongue: a twisted, bulging, bent cone.
    bow = 0: the tongue splays out along ``lean``; bow = 1: it bulges out and
    its tip curls back up (cartoon 'S' lick, tip points upward)."""
    ts, rs = prof or (TONGUE_T, TONGUE_R)
    pts = []
    for t in ts:
        w = math.sin(t * math.pi * 1.3) * wiggle * h
        o = (1.0 - bow) * t * t + bow * 0.9 * math.sin(math.pi * t * 0.85)
        pts.append(Vector((base[0] + lean[0] * h * o + math.cos(wdir) * w,
                           base[1] + lean[1] * h * o + math.sin(wdir) * w,
                           base[2] + h * t)))
    _tube(M, key, pts, [r * k for k in rs], sides, phase=phase, twist=twist,
          cap_start=True, squash=squash)


def _flame_cluster(M, specs, z0, rnd, mir=1.0, key="glow_fire", prof=None, wig=(0.03, 0.07), bow=0.0):
    """specs: (angle_deg|None, dist, h, r, lean, dx, dy)."""
    for ang, dist, h, r, lean, dx, dy in specs:
        if ang is None:
            bx, by, lx, ly = dx * mir, dy, 0.04 * mir, 0.0
        else:
            a = math.radians(ang)
            ca, sa = math.cos(a) * mir, math.sin(a)
            bx, by = ca * dist + dx * mir, sa * dist + dy
            lx, ly = ca * lean, sa * lean
        hh = h * rnd.uniform(0.92, 1.08)
        _tongue(M, (bx, by, z0), hh, r * rnd.uniform(0.95, 1.05), lean=(lx, ly),
                wiggle=rnd.uniform(*wig), wdir=rnd.uniform(0, TAU),
                twist=rnd.uniform(0.16, 0.3) * (1 if rnd.random() < 0.5 else -1),
                phase=rnd.uniform(0, TAU), key=key, prof=prof, bow=bow)


# --------------------------------------------------------------------------
# Brazier
# --------------------------------------------------------------------------
# Design units are for height 4.5 and scaled afterwards.
_BOWL = [  # (r, z above bowl bottom, lobe depth, key of the gap above)
    (0.24, 0.00, 0.00, "iron"),
    (0.66, 0.10, 0.08, "iron"),
    (0.98, 0.32, 0.13, "iron"),
    (1.10, 0.60, 0.13, "iron"),
    (1.08, 0.82, 0.10, "iron_light"),
    (1.22, 0.92, 0.05, "iron_light"),
    (1.24, 1.04, 0.04, "iron_light"),
    (1.02, 1.07, 0.04, "black"),
    (0.96, 0.86, 0.04, "coal"),
    (0.00, 0.96, 0.00, None),
]
_COLUMN = [  # (r, z) - string z values are relative to the bowl bottom 'zb'
    (0.00, 0.52), (0.17, 0.64), (0.25, 0.84), (0.27, 1.04), (0.25, 1.22), (0.15, 1.36),
    (0.135, 2.02), (0.24, 2.18), (0.26, 2.32), (0.15, 2.48), (0.135, "zb-0.40"),
    (0.25, "zb-0.18"), (0.33, "zb-0.03"), (0.28, "zb+0.06"), (0.00, "zb+0.06"),
]
_LEG = [(0.10, 1.12), (0.55, 1.00), (0.90, 0.62), (1.06, 0.25), (1.17, 0.10),
        (1.32, 0.13), (1.37, 0.27), (1.25, 0.32)]
_LEG_R = [0.15, 0.135, 0.12, 0.11, 0.10, 0.09, 0.08, 0.07]

# Flames: (angle, dist, h, r, lean, dx, dy); angle -90 = front (-Y), None = centre.
_BRAZIER_OUTER = [
    (None, 0.0, 2.45, 0.62, 0.0, 0.0, 0.14),
    (-38, 0.46, 1.30, 0.42, 0.36, 0.0, 0.0),
    (28, 0.46, 1.75, 0.44, 0.30, 0.0, 0.0),
    (152, 0.46, 1.60, 0.44, 0.30, 0.0, 0.0),
    (218, 0.46, 1.15, 0.42, 0.36, 0.0, 0.0),
]
_BRAZIER_CORE = [
    (None, 0.0, 1.50, 0.46, 0.0, 0.0, -0.22),
    (-60, 0.26, 0.85, 0.27, 0.30, 0.0, -0.12),
]
FAT_T = (0.0, 0.14, 0.33, 0.53, 0.72, 0.88, 1.0)
FAT_R = (0.80, 1.0, 0.97, 0.80, 0.54, 0.24, 0.0)


def make_brazier(name, location=(0, 0, 0), rotation_z=0.0, height=4.5, side="L", seed=0):
    """Black iron tripod brazier with a pumpkin-lobed bowl of glowing coals and
    a chunky two-layer cartoon flame. ``height`` = top of the bowl rim."""
    s = height / 4.5
    side = str(side)
    mir = -1.0 if side.upper().startswith("R") else 1.0
    rnd = common.rng(seed * 31 + 7)
    H = 4.5
    zb = H - 1.07  # bowl bottom (design units)

    # ---- iron stand + bowl + coal bed -----------------------------------
    M = _Mesh()
    lobes, segs = 8, 24
    ph0 = math.pi / 8

    def lobe(a, i):
        u = (a - ph0) * lobes / TAU
        return 1.0 - _BOWL[i][2] * (0.5 + 0.5 * math.cos(TAU * u))

    prof = [(r, zb + z) for r, z, _, _ in _BOWL]
    rings, gaps, caps = _lathe(M, prof, segs, phase=ph0, rmul=lobe, cap_start=True)
    coal_faces = []
    for i, g in enumerate(gaps):
        key = _BOWL[i][3]
        if key == "coal":
            coal_faces += g
        elif key:
            M.add(key, g)
    M.add("iron", caps)
    # lumpy coal bed: jitter the coal rings a little in z
    for ring in rings[-2:]:
        for v in ring:
            v.co.z += rnd.uniform(-0.03, 0.03)
    for f in coal_faces:
        M.add(rnd.choice(("charcoal", "charcoal", "black", "ash")), f)
    # coal lumps
    for k in range(4):
        a = TAU * k / 4 + rnd.uniform(-0.3, 0.3) + 0.4
        d = rnd.uniform(0.55, 0.78)
        _gem(M, rnd.choice(("charcoal", "black")), (math.cos(a) * d, math.sin(a) * d, zb + 0.92),
             rnd.uniform(0.15, 0.2), rnd, squash=0.6)

    # column
    def zval(z):
        if isinstance(z, str):
            return zb + float(z[2:])
        return z
    col_prof = [(r, zval(z)) for r, z in _COLUMN]
    _lathe(M, col_prof, 8, phase=math.pi / 8, keys="iron")
    # legs (tripod with scroll feet): two at the front, one at the back
    for k, ang in enumerate((90.0, 210.0, 330.0)):
        a = math.radians(ang + rnd.uniform(-6, 6))
        ca, sa = math.cos(a), math.sin(a)
        pts = [Vector((ca * rho, sa * rho, z)) for rho, z in _LEG]
        _tube(M, "iron", pts, _LEG_R, 5, phase=0.3, cap_start=False, cap_end=True,
              ref=Vector((-sa, ca, 0)))
    _scale_bm(M.bm, s)
    # The scroll feet are tube rings, so their lowest vertex sits a hair above
    # z = 0: drop the whole brazier (stand, flames, embers) onto the ground.
    drop = -min(v.co.z for v in M.bm.verts)
    _lift_bm(M.bm, drop)
    stand = M.finish(name)

    # ---- flames -----------------------------------------------------------
    z0 = zb + 0.88
    Mo = _Mesh()
    _flame_cluster(Mo, _BRAZIER_OUTER, z0, rnd, mir, prof=(FAT_T, FAT_R), wig=(0.05, 0.09), bow=1.0)
    _scale_bm(Mo.bm, s)
    _lift_bm(Mo.bm, drop)
    outer = Mo.finish_glow("fire", f"BrazierFlame_{side}")

    Mc = _Mesh()
    _flame_cluster(Mc, _BRAZIER_CORE, z0, rnd, mir, key="glow_flame", prof=(FAT_T, FAT_R), bow=1.0)
    _scale_bm(Mc.bm, s)
    _lift_bm(Mc.bm, drop)
    core = Mc.finish_glow("flame", f"BrazierFlameCore_{side}")

    # ---- glowing embers among the coals -----------------------------------
    Me = _Mesh()
    for k in range(7):
        a = TAU * (k + 0.5) / 7 + rnd.uniform(-0.25, 0.25)
        d = rnd.uniform(0.66, 0.86)
        _gem(Me, "glow_ember", (math.cos(a) * d, math.sin(a) * d, zb + 0.98),
             rnd.uniform(0.10, 0.14), rnd)
    _scale_bm(Me.bm, s)
    _lift_bm(Me.bm, drop)
    embers = Me.finish_glow("ember", f"BrazierEmbers_{side}")

    out = [stand, outer, core, embers]
    for o in out:
        _place(o, location, rotation_z)
    return out


# --------------------------------------------------------------------------
# Lantern post
# --------------------------------------------------------------------------
def _crook_spine(H, rnd):
    """Spine of the post + shepherd's crook (design units for H = 7)."""
    Rc = 0.74
    zc = H - Rc - 0.13
    pts, radii = [], []
    post = [(0.78, 0.165), (1.45, 0.16), (1.53, 0.22), (1.63, 0.22), (1.71, 0.155),
            (2.9, 0.15), (4.05, 0.145), (4.13, 0.20), (4.23, 0.20), (4.31, 0.14),
            (5.3, 0.135), (zc - 0.35, 0.13), (zc, 0.128)]
    for z, r in post:
        pts.append(Vector((0.0, 0.0, z)))
        radii.append(r)
    for k in range(1, 9):  # arc over the top, 180 -> 0 degrees
        th = math.radians(180 - 22.5 * k)
        pts.append(Vector((Rc + Rc * math.cos(th), 0.0, zc + Rc * math.sin(th))))
        radii.append(0.126 - 0.016 * k / 8)
    tip = pts[-1]
    for dz, r in ((-0.10, 0.10), (-0.17, 0.15), (-0.27, 0.155), (-0.36, 0.0)):
        pts.append(Vector((tip.x, 0.0, tip.z + dz)))
        radii.append(r)
    return pts, radii, Rc, zc


def _crooked(p, H, ph):
    """Hand-made bend: smooth sideways wobble growing with height."""
    t = max(p.z - 0.78, 0.0) / H
    return Vector((p.x + 0.10 * math.sin(t * math.pi * 1.1 + ph[0]) - 0.10 * math.sin(ph[0]) + 0.05 * t,
                   p.y + 0.08 * math.sin(t * math.pi * 0.9 + ph[1]) - 0.08 * math.sin(ph[1]),
                   p.z))


def _lantern(M, hang, rnd, keys=("iron", "iron_light"), scale=1.0):
    """Square lantern cage hanging from ``hang`` (top of its ring). Returns the
    glass glow mesh (a separate _Mesh)."""
    G = _Mesh()
    # Build around the local origin (0,0,0) = hang point, then transform.
    start = len(M.bm.verts)
    k_dark, k_light = keys
    # ring + knob
    _torus(M, k_light, (0, 0, -0.11), 0.10, 0.032, axis=(0, 1, 0), segs=8, sides=4, stretch=1.15)
    _lathe(M, [(0.0, -0.30), (0.09, -0.28), (0.09, -0.20), (0.05, -0.19), (0.0, -0.19)], 6, keys=k_dark)
    # roof pyramid with eave
    bm = M.bm
    apex = bm.verts.new((0, 0, -0.24))
    base = [bm.verts.new((sx * 0.52, sy * 0.52, -0.66)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    M.add(k_dark, _bridge(bm, base, [apex]))
    _box(M, k_light, (0, 0, -0.69), (1.08, 1.08, 0.08))
    _box(M, k_dark, (0, 0, -0.77), (0.90, 0.90, 0.09))
    # corner posts
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        _box(M, k_dark, (sx * 0.37, sy * 0.37, -1.17), (0.10, 0.10, 0.82))
    # bottom slab + knob
    _box(M, k_light, (0, 0, -1.62), (0.92, 0.92, 0.12))
    _box(M, k_dark, (0, 0, -1.71), (0.70, 0.70, 0.08))
    tipv = bm.verts.new((0, 0, -1.94))
    bot = [bm.verts.new((sx * 0.20, sy * 0.20, -1.73)) for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1))]
    M.add(k_dark, _bridge(bm, bot, [tipv]))
    # glass (glow) - inset between the posts, ends buried in the slabs
    _box(G, "glow_amber", (0, 0, -1.17), (0.68, 0.68, 0.84))
    # tilt + yaw the whole lantern a touch, move to the hang point
    tilt = (Matrix.Translation(hang) @ Matrix.Scale(scale, 4)
            @ Matrix.Rotation(math.radians(rnd.uniform(8, 20)), 4, "Z")
            @ Matrix.Rotation(math.radians(rnd.uniform(-4, 4)), 4, "X")
            @ Matrix.Rotation(math.radians(rnd.uniform(-3, 3)), 4, "Y"))
    M.bm.verts.ensure_lookup_table()
    bmesh.ops.transform(M.bm, matrix=tilt, verts=M.bm.verts[start:])
    bmesh.ops.transform(G.bm, matrix=tilt, verts=list(G.bm.verts))
    return G


def make_lantern_post(name, location=(0, 0, 0), rotation_z=0.0, height=7.0, seed=0):
    """Crooked iron shepherd's-crook post on a stone footing with a hanging
    square lantern. The lantern hangs on the +X side of the post."""
    s = height / 7.0
    H = 7.0
    rnd = common.rng(seed * 17 + 3)
    ph = (rnd.uniform(0, TAU), rnd.uniform(0, TAU))
    M = _Mesh()
    # stone footing (two chunky tapered blocks, slightly irregular)
    v1, _ = _taper_box(M, ("stone", "stone_dark", "stone_dark"), 0, 0, 0.0, 0.36, 1.2, 1.2, 1.06, 1.06)
    v2, _ = _taper_box(M, ("stone_light", "stone", "stone"), 0, 0, 0.32, 0.60, 0.92, 0.92, 0.76, 0.76)
    common.jitter_bmesh(M.bm, 0.025, seed + 1, [v for v in v1 + v2 if v.co.z > 0.01])
    # iron foot flare
    _lathe(M, [(0.38, 0.54), (0.36, 0.66), (0.24, 0.80), (0.2, 0.98), (0.0, 0.98)], 8,
           phase=math.pi / 8, keys=("iron", "iron_light", "iron", "iron"))
    # post + crook
    pts, radii, Rc, zc = _crook_spine(H, rnd)
    pts = [_crooked(p, H, ph) for p in pts]
    _tube(M, "iron", pts, radii, 6, phase=0.2, cap_start=False, ref=Vector((0, 1, 0)))
    # scroll ornament inside the crook
    scroll = []
    for k in range(15):
        u = k / 14
        ang = math.radians(196 - 430 * u)
        R = (Rc - 0.085) if u < 0.22 else (Rc - 0.085) - 0.47 * ((u - 0.22) / 0.78) ** 0.85
        scroll.append(_crooked(Vector((Rc + R * math.cos(ang), 0.0, zc + R * math.sin(ang))), H, ph))
    _tube(M, "iron_light", scroll, [0.07] * 14 + [0.05], 4, phase=math.pi / 4,
          cap_start=True, cap_end=True, ref=Vector((0, 1, 0)))
    # chain from the crook tip
    tip = pts[-1]
    _torus(M, "iron_light", tip + Vector((0, 0, -0.10)), 0.09, 0.032, axis=(0, 1, 0), segs=6, sides=4, stretch=1.5)
    _torus(M, "iron_light", tip + Vector((0, 0, -0.33)), 0.09, 0.032, axis=(1, 0, 0), segs=6, sides=4, stretch=1.5)
    G = _lantern(M, tip + Vector((0, 0, -0.40)), rnd, scale=1.3)
    _scale_bm(M.bm, s)
    _scale_bm(G.bm, s)
    post = M.finish(name)
    idx = _free_index("GLOW_amber_LanternGlow_")
    glow = G.finish_glow("amber", f"LanternGlow_{idx:02d}")
    out = [post, glow]
    for o in out:
        _place(o, location, rotation_z)
    return out


# --------------------------------------------------------------------------
# Candles
# --------------------------------------------------------------------------
_WAX = {
    "cream": ("candle_wax", "bone", "black"),
    "black": ("charcoal", "ash", "bone"),
    "orange": ("pumpkin", "pumpkin_light", "black"),
}
_CANDLE_H = (1.0, 0.68, 0.46, 0.84, 0.57, 0.36, 0.75, 0.5)
FLAME_T = (0.0, 0.16, 0.38, 0.62, 0.84, 1.0)
FLAME_R = (0.0, 0.78, 1.0, 0.74, 0.36, 0.0)


def _candle(M, cx, cy, rc, hc, rnd, wax, zp):
    body_k, shade_k, wick_k = wax
    bm = M.bm
    _lathe(M, [(rc * 1.06, 0.0), (rc, 0.08), (rc, hc - 0.03)], 8, center=(cx, cy),
           phase=rnd.uniform(0, 1), keys=body_k)
    # melted collar with drips (shell slightly outside the body, tucked in below)
    n = 12
    ph = rnd.uniform(0, TAU)
    lengths = [rnd.uniform(0.04, 0.08) for _ in range(n)]
    for c in rnd.sample(range(0, n, 2), 3 if hc < 0.8 else 4):
        L = max(rnd.uniform(0.3, 0.58) * hc, 0.18)
        lengths[c] = max(lengths[c], L)
        lengths[(c - 1) % n] = max(lengths[(c - 1) % n], L * 0.32)
        lengths[(c + 1) % n] = max(lengths[(c + 1) % n], L * 0.42)
    T, D, U, I = [], [], [], []
    for k in range(n):
        a = ph + TAU * k / n
        ca, sa = math.cos(a), math.sin(a)
        drip = lengths[k] > 0.15
        ro = rc * (1.15 if drip else 1.08)
        T.append(bm.verts.new((cx + ca * rc * 1.075, cy + sa * rc * 1.075, hc + rnd.uniform(-0.018, 0.018))))
        D.append(bm.verts.new((cx + ca * ro, cy + sa * ro, hc - lengths[k])))
        U.append(bm.verts.new((cx + ca * rc * 0.86, cy + sa * rc * 0.86, hc - lengths[k] - 0.045)))
        I.append(bm.verts.new((cx + ca * rc * 0.62, cy + sa * rc * 0.62, hc - 0.05 + rnd.uniform(-0.01, 0.01))))
    pole = bm.verts.new((cx, cy, hc - 0.04))
    M.add(shade_k, _bridge(bm, U, D))
    M.add(body_k, _bridge(bm, D, T))
    M.add(body_k, _bridge(bm, T, I))
    M.add(shade_k, _bridge(bm, I, [pole]))
    # wick
    lean = Vector((rnd.uniform(-0.02, 0.02), rnd.uniform(-0.02, 0.02), 0))
    _tube(M, wick_k, [Vector((cx, cy, hc - 0.06)), Vector((cx, cy, hc + 0.1)) + lean],
          [0.024, 0.02], 4, cap_start=False)
    # wax puddle (top heights differ per candle so overlapping puddles never z-fight)
    pr = [rc * 1.6 * rnd.uniform(0.85, 1.15) for _ in range(10)]
    _lathe(M, [(1.0, 0.0), (0.8, zp), (0.55, zp)], 10, center=(cx, cy), phase=rnd.uniform(0, 1),
           rmul=lambda a, i, pr=pr: pr[int(round((a % TAU) / TAU * 10)) % 10] if i < 2 else rc * 1.6,
           keys=(shade_k, body_k))
    return Vector((cx, cy, hc + 0.1)) + lean


def make_candles(prefix, location=(0, 0, 0), rotation_z=0.0, count=3, seed=0, height=1.3,
                 wax="cream", join_flames=False):
    """Cluster of melted candles of varied heights with teardrop flames.
    ``height`` = tallest candle (without flame)."""
    rnd = common.rng(seed * 11 + 5)
    wk = _WAX.get(wax, _WAX["cream"])
    M = _Mesh()
    placed = []
    for i in range(count):
        h = max(height * _CANDLE_H[i % len(_CANDLE_H)] * rnd.uniform(0.92, 1.06), 0.35)
        rc = 0.15 + 0.07 * min(h / max(height, 1e-3), 1.0) + rnd.uniform(-0.01, 0.01)
        if not placed:
            pos = (0.0, 0.06)
        else:
            pos = None
            for attempt in range(200):
                j = rnd.randrange(len(placed))
                a = rnd.uniform(0, TAU)
                d = placed[j][2] + rc + rnd.uniform(0.05, 0.16)
                p = (placed[j][0] + math.cos(a) * d, placed[j][1] + math.sin(a) * d)
                if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= q[2] + rc + 0.05 for q in placed):
                    if pos is None or math.hypot(*p) < math.hypot(*pos):
                        pos = p
                    if attempt > 30:
                        break
        if pos is None:  # crowded: just put it on the outside of the cluster
            pos = (max(q[0] + q[2] for q in placed) + rc + 0.05, 0.0)
        placed.append((pos[0], pos[1], rc, h))
    tips = []
    for i, (x, y, rc, h) in enumerate(placed):
        tips.append((_candle(M, x, y, rc, h, rnd, wk, 0.035 + 0.007 * i), rc))
    body = M.finish(prefix)
    out = [body]
    flames = []
    Gj = _Mesh() if join_flames else None
    for tip, rc in tips:
        G = Gj or _Mesh()
        rf = min(max(rc * 0.52, 0.075), 0.12)
        hf = rf * 3.7
        base = tip + Vector((0, 0, -0.05))
        lx, ly = rnd.uniform(-0.25, 0.25) * rf, rnd.uniform(-0.25, 0.25) * rf
        pts = [base + Vector((lx * t * t * 3, ly * t * t * 3, hf * t)) for t in FLAME_T]
        _tube(G, "glow_flame", pts, [rf * k for k in FLAME_R], 6, phase=rnd.uniform(0, TAU), twist=0.15)
        if not join_flames:
            idx = _free_index("GLOW_flame_CandleFlame_")
            flames.append(G.finish_glow("flame", f"CandleFlame_{idx:02d}"))
    if join_flames:
        idx = _free_index("GLOW_flame_CandleFlame_")
        flames.append(Gj.finish_glow("flame", f"CandleFlame_{idx:02d}"))
    out += flames
    for o in out:
        _place(o, location, rotation_z)
    return out


# --------------------------------------------------------------------------
# Cauldron
# --------------------------------------------------------------------------
_POT = [  # (r, z, key of the gap above) - design units for size 3
    (0.00, 0.52, "iron"),
    (0.56, 0.56, "iron"),
    (1.00, 0.72, "iron"),
    (1.32, 0.98, "iron"),
    (1.47, 1.24, "iron_light"),
    (1.53, 1.29, "iron_light"),
    (1.53, 1.40, "iron_light"),
    (1.47, 1.45, "iron"),
    (1.44, 1.66, "iron"),
    (1.30, 1.92, "iron"),
    (1.13, 2.08, "iron"),
    (1.15, 2.15, "iron_light"),
    (1.33, 2.21, "iron_light"),
    (1.35, 2.35, "iron_light"),
    (1.25, 2.44, "iron_light"),
    (1.06, 2.45, "black"),
    (1.02, 2.30, "black"),
    (1.00, 2.10, "black"),
    (0.00, 2.08, None),
]


def make_cauldron(name, location=(0, 0, 0), rotation_z=0.0, size=3.0, seed=0, liquid_key="fire",
                  fire=True):
    """Black iron witch cauldron on stubby legs over crossed logs, with a
    glowing bubbling brew and (optionally) flames licking up its sides.
    ``size`` = belly diameter."""
    s = size / 3.0
    rnd = common.rng(seed * 19 + 11)
    sfx = _suffix(name)
    M = _Mesh()
    segs = 14
    LIFT = 0.3
    _lathe(M, [(r, z + LIFT) for r, z, _ in _POT], segs, phase=0.1,
           keys=[k for _, _, k in _POT[:-1]])
    # ring handles on lugs (left/right)
    for sx in (-1, 1):
        _box(M, "iron_light", (sx * 1.36, 0, 2.2 + LIFT), (0.26, 0.2, 0.16))
        _torus(M, "iron_light", (sx * 1.47, 0, 1.98 + LIFT), 0.21, 0.055, axis=(1, 0, 0), segs=8, sides=4)
    # stubby legs
    for ang in (55.0, 175.0, 295.0):
        a = math.radians(ang)
        ca, sa = math.cos(a), math.sin(a)
        pts = [Vector((ca * rho, sa * rho, z)) for rho, z in ((0.70, 1.08), (0.84, 0.62), (0.94, 0.22), (0.97, 0.0))]
        _tube(M, "iron", pts, [0.16, 0.13, 0.15, 0.18], 6, cap_start=False, cap_end=True,
              ref=Vector((-sa, ca, 0)))
    # crossed logs under the pot
    for k, ang in enumerate((10.0, 100.0, 190.0, 280.0)):
        a = math.radians(ang + rnd.uniform(-8, 8))
        ca, sa = math.cos(a), math.sin(a)
        rl = rnd.uniform(0.14, 0.17)
        z = rl + (0.05 if k % 2 else 0.0)
        p0 = Vector((ca * 0.05, sa * 0.05, z + 0.08))
        p1 = Vector((ca * rnd.uniform(1.6, 1.85), sa * rnd.uniform(1.6, 1.85), z))
        rings, faces = _tube(M, None, [p0, p1], [rl, rl * 0.95], 6, phase=rnd.uniform(0, 1))
        M.add("wood_dark", faces[:-2])
        M.add("wood", faces[0:12:2])
        M.add("wood_light", faces[-2:])
    # wooden paddle leaning on the rim
    pa = [Vector((0.25, 0.15, 2.2)), Vector((-0.45, -0.18, 3.05)), Vector((-0.92, -0.42, 3.75))]
    _tube(M, "wood_light", pa, [0.09, 0.085, 0.08], 6, phase=0.4)
    _scale_bm(M.bm, s)
    _clamp_ground(M.bm)  # tilted leg caps / log ends poke a little below z = 0
    pot = M.finish(name)
    out = [pot]

    # brew surface + bubble blobs
    G = _Mesh()
    _lathe(G, [(1.05, 2.50), (0.68, 2.55), (0.0, 2.57)], segs, phase=0.1,
           zjit=lambda a, i: (0.03 * math.sin(3 * a + 1.3) if i == 1 else 0.0), keys="glow_fire")
    for k in range(5):
        a = TAU * k / 5 + rnd.uniform(-0.4, 0.4)
        d = rnd.uniform(0.15, 0.72)
        rr = rnd.uniform(0.09, 0.21) * (1.0 if k else 1.25)
        _ico(G, "glow_fire", (math.cos(a) * d, math.sin(a) * d, 2.55 + rr * 0.15), rr,
             scale=(1, 1, 0.85), jitter=0.015, seed=seed + k)
    _scale_bm(G.bm, s)
    out.append(G.finish_glow(liquid_key, f"CauldronLiquid{sfx}"))

    if fire:
        F = _Mesh()
        for k in range(6):  # licks curling up around the belly, 30 deg off the legs
            a = math.radians(25 + 60 * k + rnd.uniform(-8, 8))
            ca, sa = math.cos(a), math.sin(a)
            g = rnd.uniform(0.85, 1.15)
            spine = [(0.62, 0.14), (0.98, 0.46), (1.26, 0.78), (1.43, 0.78 + 0.24 * g), (1.50, 0.78 + 0.5 * g)]
            pts = [Vector((ca * rho, sa * rho, z)) for rho, z in spine]
            _tube(F, "glow_ember", pts, [0.2, 0.26, 0.22, 0.13, 0.0], 5, phase=rnd.uniform(0, TAU),
                  twist=0.3, ref=Vector((-sa, ca, 0)))
        for k in range(3):  # fat tongues on the logs, under the pot
            a = TAU * k / 3 + 0.5
            _tongue(F, (math.cos(a) * 0.3, math.sin(a) * 0.3, 0.2), rnd.uniform(0.8, 0.95), 0.3,
                    lean=(math.cos(a) * 0.35, math.sin(a) * 0.35), key="glow_ember",
                    prof=(FAT_T, FAT_R), phase=rnd.uniform(0, TAU))
        _scale_bm(F.bm, s)
        _clamp_ground(F.bm)  # the licks' first rings lean below the ground
        out.append(F.finish_glow("ember", f"CauldronFlame{sfx}"))

    for o in out:
        _place(o, location, rotation_z)
    return out


# --------------------------------------------------------------------------
# Witch hat
# --------------------------------------------------------------------------
_LIGHT = Vector((0.45, -0.6, 0.65)).normalized()


def make_witch_hat(name, location=(0, 0, 0), rotation_z=0.0, size=1.5, seed=0):
    """Black witch hat with an orange band, a buckle and a crooked, flopping
    tip. ``size`` = brim diameter; the crown base sits at the origin (z=0)."""
    k = size / 1.5
    rnd = common.rng(seed * 23 + 9)
    M = _Mesh()
    rb, rc, hc, t = 0.75, 0.40, 1.42, 0.05
    hb = 0.19
    a_up = rnd.uniform(0, TAU)
    w0 = rnd.uniform(0, TAU)

    def wave(a):
        return 0.075 * (0.5 + 0.5 * math.cos(a - a_up)) + 0.03 * (0.5 + 0.5 * math.sin(3 * a + w0))

    def zjit(a, i):
        f = (0.0, 0.0, 0.35, 1.0, 1.0, 0.35, 0.0, 0.0)[i]
        return wave(a) * f

    brim = [(0.0, 0.0), (rc, 0.0), (0.58, 0.0), (rb, 0.0), (rb + 0.015, t), (0.58, t), (rc * 0.85, t), (0.0, t)]
    rings, gaps, caps = _lathe(M, brim, 16, phase=0.0, zjit=zjit)
    M.add("black", gaps[0] + gaps[1] + gaps[2])
    M.add("ash", gaps[3])
    M.add("charcoal", gaps[4] + gaps[5] + gaps[6])

    # crown: bent cone along an integrated spine
    us = [0.0, hb / hc, 0.25, 0.38, 0.50, 0.60, 0.70, 0.79, 0.87, 0.94, 1.0]
    bend = math.radians(rnd.uniform(115, 135))
    psi = rnd.uniform(-0.5, 0.5)  # azimuth of the flop (around +X)
    pts = [Vector((0, 0, t - 0.012))]
    p = pts[0].copy()
    for u0, u1 in zip(us, us[1:]):
        um = (u0 + u1) / 2
        phi = bend * max(0.0, (um - 0.36) / 0.64) ** 1.3
        d = Vector((math.sin(phi) * math.cos(psi), math.sin(phi) * math.sin(psi), math.cos(phi)))
        p = p + d * (u1 - u0) * hc
        pts.append(p.copy())
    radii = []
    for i, u in enumerate(us):
        r = rc * (1.0 - u) ** 0.62
        if abs(u - 0.50) < 0.01:
            r *= 0.85  # crumple
        if i == 1:
            r = rc * 0.94
        radii.append(r)
    radii[-1] = 0.0
    rings, faces = _tube(M, None, pts, radii, 8, phase=math.radians(22.5), twist=0.07, cap_start=False)
    M.bm.normal_update()
    for f in faces:
        d = f.normal.dot(_LIGHT)
        M.add("ash" if d > 0.45 else ("charcoal" if d > -0.25 else "black"), f)

    # orange band hugging the first crown segment
    def push(ring, c, dr):
        return [M.bm.verts.new(c + (v.co - c) * (1.0 + dr / max((v.co - c).length, 1e-6))) for v in ring]
    c0, c1 = pts[0], pts[1]
    bb = push(rings[0], c0, 0.035)
    bt = push(rings[1], c1, 0.035)
    bi = push(rings[1], c1, -0.012)
    band_wall = _bridge(M.bm, bb, bt)
    M.add("pumpkin", band_wall)
    M.add("pumpkin_light", _bridge(M.bm, bt, bi))
    # buckle on the band face that looks most to the front (-Y)
    M.bm.normal_update()
    fr = min(band_wall, key=lambda f: f.normal.y)
    nrm = fr.normal.copy()
    ctr = fr.calc_center_median()
    xax = nrm.cross(Vector((0, 0, 1))).normalized()  # right-handed (x, nrm, z) basis
    zax = xax.cross(nrm).normalized()
    m = _basis(xax, nrm, zax, ctr + nrm * 0.006)
    bw, bh, bt_, dep = 0.105, 0.085, 0.032, 0.04
    for cx, cz, sx, sz in ((0, bh, bw * 2, bt_), (0, -bh, bw * 2, bt_),
                           (bw - bt_ / 2, 0, bt_, bh * 2), (-bw + bt_ / 2, 0, bt_, bh * 2)):
        _box(M, "gold", Vector((cx, 0, cz)), (sx, dep, sz), mat=None)
        verts = list(M.bm.verts)[-8:]
        bmesh.ops.transform(M.bm, matrix=m, verts=verts)
    _scale_bm(M.bm, k)
    hat = M.finish(name, default_key="charcoal")
    _place(hat, location, rotation_z)
    return [hat]


# --------------------------------------------------------------------------
# Spider web
# --------------------------------------------------------------------------
_CORNER_SIGN = {"TL": (1, -1), "TR": (-1, -1), "BL": (1, 1), "BR": (-1, 1)}


def spider_web_anchor_offset(size=2.5, corner="TL"):
    """Approximate local offset (x, y, z) from the web's origin (bottom centre
    of its bbox) to its anchor (the corner point, or the hub of a 'full' web).
    For exact placement pass at_corner=True to make_spider_web instead."""
    if corner == "full":
        return Vector((0.0, 0.0, size))
    sx, sz = _CORNER_SIGN[corner]
    return Vector((-sx * size / 2, 0.0, size if sz < 0 else 0.0))


def _spider(M, center, sc, rnd):
    c = Vector(center)
    _, af = _ico(M, None, c + Vector((0, -0.02, 0.05)) * sc, 0.11 * sc, scale=(1.0, 0.75, 1.2), jitter=0.006 * sc,
                 seed=rnd.randrange(1000))
    for f in af:
        f.normal_update()
        M.add("pumpkin" if (f.normal.y < -0.55 and f.calc_center_median().z > c.z + 0.05 * sc) else "charcoal", f)
    _ico(M, "black", c + Vector((0, -0.03, -0.11)) * sc, 0.065 * sc, scale=(1, 0.9, 1))
    for sx in (-1, 1):
        for j, zl in enumerate((0.03, -0.01, -0.06, -0.1)):
            spread = (0.55, 0.15, -0.2, -0.6)[j]
            p0 = c + Vector((sx * 0.05, -0.04, zl)) * sc
            p1 = c + Vector((sx * 0.17, -0.1, zl + 0.05 + 0.03 * spread)) * sc
            p2 = c + Vector((sx * 0.25, -0.005, zl + spread * 0.16)) * sc
            _tube(M, "black", [p0, p1, p2], [0.018 * sc, 0.016 * sc, 0.012 * sc], 3, cap_start=False)


def make_spider_web(name, location=(0, 0, 0), rotation_z=0.0, size=2.5, corner="TL", seed=0,
                    spider=True, at_corner=False, key="bone"):
    """Flat low-poly spider web (bone threads) in the local XZ plane."""
    rnd = common.rng(seed * 29 + 13)
    M = _Mesh()
    tr = max(0.026 * size / 2.5, 0.018)
    Y = Vector((0, 1, 0))
    full = corner == "full"
    if full:
        n_sp = 8
        angs = [math.radians(360.0 * k / n_sp + rnd.uniform(-9, 9)) for k in range(n_sp)]
        lens = [size * rnd.uniform(0.86, 1.0) for _ in angs]
        fr = (0.16, 0.36, 0.56, 0.76, 0.93)
        sx, sz = 1, 1
    else:
        sx, sz = _CORNER_SIGN[corner]
        base = (-4, -24, -45, -66, -86)
        angs = [math.radians(b + (rnd.uniform(-5, 5) if 0 < i < 4 else 0)) for i, b in enumerate(base)]
        lens = [size * (1.0 if i in (0, 4) else rnd.uniform(0.86, 0.97)) for i in range(5)]
        fr = (0.22, 0.42, 0.62, 0.82)
        n_sp = 5

    def P(a, d):
        return Vector((sx * math.cos(a) * d, 0.0, -sz * math.sin(a) * d if not full else math.sin(a) * d))

    hub = Vector((0, 0, 0))
    # spokes
    for a, L in zip(angs, lens):
        _tube(M, key, [hub, P(a, L)], [tr * 1.15, tr], 3, phase=-math.pi / 2, squash=0.6, ref=Y)
    # spiral threads, sagging towards the hub
    pairs = list(zip(range(n_sp), list(range(1, n_sp)) + ([0] if full else [])))
    for ri, f in enumerate(fr):
        jit = [rnd.uniform(-0.03, 0.03) for _ in range(n_sp)]
        for i, j in pairs:
            p1 = P(angs[i], lens[i] * (f + jit[i]))
            p2 = P(angs[j], lens[j] * (f + jit[j]))
            if full:  # straight threads keep the round web cheap
                _tube(M, key, [p1, p2], [tr * 0.9] * 2, 3, phase=-math.pi / 2, squash=0.6, ref=Y,
                      cap_start=False, cap_end=False)
                continue
            mid = (p1 + p2) / 2 * (1.0 - 0.11 - 0.03 * rnd.random())
            _tube(M, key, [p1, mid, p2], [tr * 0.9] * 3, 3, phase=-math.pi / 2, squash=0.6, ref=Y,
                  cap_start=False, cap_end=False)
    if full:
        _ico(M, key, hub, tr * 2.2, scale=(1, 0.5, 1))
    if spider:
        i = 2 if not full else 1
        f = fr[1] if not full else fr[2]
        pa = P(angs[i], lens[i] * f)
        pb = P(angs[i + 1 if not full else i + 1], lens[i + 1] * f)
        _spider(M, (pa + pb) / 2 + Vector((0, -0.03, 0)), size / 2.5 * 1.25, rnd)
    obj = M.finish(name, default_key=key)
    if at_corner:  # design origin == anchor -> anchor lands on ``location``
        _place(obj, location, rotation_z)
    else:  # origin (bottom centre of the bbox) lands on ``location``
        obj.matrix_world = Matrix.Identity(4)
        common.origin_to_base(obj)
        obj.matrix_world = Matrix.Translation(Vector(location)) @ Matrix.Rotation(rotation_z, 4, "Z")
    return [obj]


# --------------------------------------------------------------------------
# Demo / catalogue
# --------------------------------------------------------------------------
def _island_volumes(obj):
    """Signed volume per closed connected island (negative -> inside-out)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    seen = set()
    vols = []
    for f0 in bm.faces:
        if f0.index in seen:
            continue
        stack, isl = [f0], []
        seen.add(f0.index)
        while stack:
            f = stack.pop()
            isl.append(f)
            for e in f.edges:
                for g in e.link_faces:
                    if g.index not in seen:
                        seen.add(g.index)
                        stack.append(g)
        closed = all(len(e.link_faces) == 2 for f in isl for e in f.edges)
        if not closed:  # signed volume of an open shell is meaningless
            continue
        c0 = sum((f.calc_center_median() for f in isl), Vector()) / len(isl)
        v = 0.0
        for f in isl:
            co = [l.vert.co - c0 for l in f.loops]
            for i in range(1, len(co) - 1):
                v += co[0].dot(co[i].cross(co[i + 1])) / 6.0
        vols.append(v)
    bm.free()
    return vols


def _report(objs):
    total = 0
    for o in objs:
        t = common.tri_count(o)
        total += t
        probs = common.validate_object(o)
        vols = _island_volumes(o)
        neg = [round(v, 4) for v in vols if v < -1e-5]
        dims = tuple(round(d, 2) for d in o.dimensions)
        degen = sum(1 for poly in o.data.polygons if poly.area < 1e-7)
        if degen:
            probs = probs + [f"{degen} degenerate faces"]
        print(f"  {o.name:36s} {t:5d} tris  dims={dims}  closed={len(vols)}"
              f"{'  NEG ' + str(neg) if neg else ''}  {'; '.join(probs)}")
    print(f"  total {total} tris in {len(objs)} objects")
    return total


def _tight_focus(objs, pad=1.0):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    c = (lo + hi) / 2
    return (c, max((hi - lo).length / 2 / 1.45 * pad, 1.0))


def demo(views=("front", "three_quarter"), only=None, samples=32):
    common.reset_scene()
    big, small = [], []
    big += make_brazier("Brazier_L", (-8.0, 0, 0), 0.0, side="L", seed=1)
    big += make_lantern_post("LanternPost_01", (-3.6, 0.6, 0), 0.0, seed=2)
    big += make_cauldron("Cauldron_01", (1.6, 0, 0), 0.25, size=3.0, seed=3)
    big += make_brazier("Brazier_R", (6.4, 0, 0), 0.0, side="R", seed=2)

    X = 30.0  # small props live away from the big ones
    small += make_candles("Candles_A", (X - 2.6, 0.0, 0), 0.0, count=3, seed=1)
    small += make_candles("Candles_B", (X - 0.9, -0.4, 0), 0.6, count=5, seed=2, height=1.1, wax="black")
    try:
        import pumpkins  # demo only: show the hat sitting on a pumpkin
        pk = pumpkins.make_pumpkin("Pumpkin_HatDemo_01", (X + 1.2, 0.0, 0), 0.4, size=2.0,
                                   variant="round", seed=3)
        ptop = max((pk[0].matrix_world @ v.co).z for v in pk[0].data.vertices
                   if math.hypot(*((pk[0].matrix_world @ v.co) - Vector((X + 1.2, 0, 0))).xy) > 0.35)
        small += pk
        small += make_witch_hat("WitchHat_01", (X + 1.2, 0.0, ptop - 0.08), 0.3, size=1.5, seed=1)
    except Exception as exc:  # pragma: no cover - other module may be mid-edit
        print("[lights] pumpkins demo skipped:", exc)
        small += make_witch_hat("WitchHat_01", (X + 1.2, 0.0, 0.0), 0.3, size=1.5, seed=1)
    small += make_witch_hat("WitchHat_02", (X + 3.2, 0.4, 0.0), -0.5, size=1.2, seed=4)
    # corner web in a little demo frame + a round web
    fx, fz = X + 4.6, 3.2
    frame = []
    for nm, c, sz in (("DemoFrame_Post", (fx, 0.05, fz / 2), (0.25, 0.25, fz)),
                      ("DemoFrame_Rail", (fx + 1.6, 0.05, fz - 0.12), (3.2, 0.2, 0.2))):
        Mf = _Mesh()
        _box(Mf, "wood_dark", c, sz)
        frame.append(Mf.finish(nm))
    small += frame
    small += make_spider_web("SpiderWeb_01", (fx + 0.12, 0.0, fz - 0.22), 0.0, size=2.0, corner="TL",
                             seed=1, at_corner=True)
    small += make_spider_web("SpiderWeb_02", (X - 4.6, 0.6, 0.6), 0.0, size=1.1, corner="full", seed=2)

    img = bpy.data.images.get("palette.png")
    if img is not None and img.packed_file is None:
        img.pack()  # preview only: immune to other processes rewriting the PNG
    print("=== lights demo: big ===")
    _report(big)
    print("=== lights demo: small ===")
    _report([o for o in small if not o.name.startswith(("DemoFrame", "Pumpkin_HatDemo"))])

    if only in (None, "big"):
        for o in small:
            o.hide_render = True
        preview.render("previews/_wip/lights_big.png", views=list(views), objects=big,
                       res=(960, 600), samples=samples, focus=_tight_focus(big, 0.92))
        for o in small:
            o.hide_render = False
    if only in (None, "small"):
        for o in big:
            o.hide_render = True
        preview.render("previews/_wip/lights_small.png", views=list(views), objects=small,
                       res=(960, 600), samples=samples, focus=_tight_focus(small, 0.95))
        for o in big:
            o.hide_render = False


if __name__ == "__main__":
    _views = ("front", "three_quarter")
    _only = None
    _samples = 32
    for arg in sys.argv[1:]:
        if arg.startswith("--views="):
            _views = tuple(arg.split("=", 1)[1].split(","))
        elif arg.startswith("--only="):
            _only = arg.split("=", 1)[1]
        elif arg.startswith("--samples="):
            _samples = int(arg.split("=", 1)[1])
        elif arg == "--norender":
            _only = "none"
    demo(_views, _only, _samples)
