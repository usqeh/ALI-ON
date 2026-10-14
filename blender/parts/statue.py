"""Halloween stone angel statue: a small hooded stone angel ('aniolek') holding
a glowing orb, standing on a lightly weathered, stepped stone pedestal.

A chunky, cartoony low-poly cherub in a long robe with deep irregular folds and
a rope belt (knot + two tasselled cords), a dagged shoulder capelet and a big
rounded hood with a soft drooping cowl tip. The head is bowed towards the orb
and the face is lost in the black hood opening, with two faint glowing eyes.
Big half-open wings (leading-edge bone, scalloped covert rows, a fan of
rounded flight feathers) frame the figure. Two cupped hands with chunky
fingers hold a glowing orange faceted orb at chest height.

Weathering: chipped robe hem, a nick in the hood brim, snapped outer primaries
on the right wing (the broken tip lies on the plinth ledge), a dark crack down
the skirt, chipped plinth corners with a broken base corner, rubble at the
foot, moss clumps with lighter tufts, faint glowing cracks in the plinth.

Everything is built from bmesh / mesh data (no bpy.ops modelling), coloured
through the palette texture (M_Palette) and the glow material (M_Glow).

Public API
----------
* make_angel_statue(prefix="Statue", location=(0, 0, 0), rotation_z=0.0, seed=0,
                    with_decor=True, glowing_eyes=True) -> list[Object]

  Objects (prefix "Statue"), solid parts first:
      Statue_Angel                       stone figure: robe, belt, capelet, hood,
                                         sleeves, cupped hands, wings (one mesh)
      Statue_Pedestal                    stepped plinth + chips, moss, rubble
      GLOW_fire_StatueOrb                the orb (>= CLEARANCE_MIN studs clear of
                                         the hands and all other stone, measured
                                         both ways: orb -> stone and stone -> orb)
      GLOW_ember_StatueEyes              two eyes in the hood (glowing_eyes=True)
      GLOW_fire_StatueCracks             glowing cracks in the plinth faces
      StatueDecor_*                      candles + a jack-o'-lantern (with_decor)
      GLOW_flame_StatueDecor_CandleFlame_##, GLOW_fire_StatueDecor_JackOLantern_##_Face

  Every object: scale 1, flat shaded, one material, explicit name without dots,
  origin at the bottom centre of its own bounding box (common.origin_to_base).
  NOTE for scripts: the orb's origin (pivot) is therefore at the BOTTOM of the
  orb, ORB_R (0.52) below its centre. In Roblox use the MeshPart's Position /
  CFrame (always the bounding-box centre = orb centre) for pulsing, hovering
  and spinning, not the pivot. The pedestal's origin is the bottom centre of
  its bbox (the rubble is laid out symmetrically, so it lies within a few
  hundredths of a stud of the plinth axis).

  The whole statue is built in one design frame (plinth axis on the ground at
  the origin, facing -Y); that frame is placed at ``location`` with
  ``rotation_z`` stored as object rotation. Use a different ``prefix`` for
  every statue in one scene (the names are a contract with the Luau script).

Scale (1 unit = 1 stud): plinth 2.62 tall / 4.8 wide, angel ~6.5 tall (feet to
hood top), wingspan ~6.4, whole statue ~9.2 tall. Under 7000 tris with decor.

Run ``<venv>/bin/python blender/parts/statue.py`` to build it, print the
checks and render previews/_wip/angel_statue_dev_<view>.png
(options: --views=a,b --samples=N --no-decor --res=WxH).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy, bmesh, common  # noqa: E401,E402

import math  # noqa: E402

from mathutils import Matrix, Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

TAU = 2 * math.pi

# --------------------------------------------------------------------------
# Key dimensions (studs). The angel is designed with its feet at z = 0 and
# moved onto the plinth top at the end.
# --------------------------------------------------------------------------
PED_DESIGN = 2.45                   # plinth profile numbers are written for this height
PED_TOP = 2.62                      # final plinth height (profile scaled in z)
PED_ZS = PED_TOP / PED_DESIGN
SKIRT_TOP = 2.90                    # robe below this height is squashed (stubby cherub)
SKIRT_SQUASH = 0.85
BODY_DROP = SKIRT_TOP * (1.0 - SKIRT_SQUASH)   # everything above the skirt moves down
ORB_C = Vector((0.0, -1.66, 3.40))  # orb centre (angel design frame)
ORB_R = 0.52
ORB_KEY = "fire"                    # "fire" or "amber" (name contract)
HAND_IN = ORB_R + 0.145             # cupped hands inner radius
HAND_OUT = HAND_IN + 0.17
NECK = Vector((0.0, 0.02, 4.86))    # head tilt pivot
HEAD_BOW = math.radians(13)         # bowed forward, looking at the orb
HEAD_TILT = math.radians(-8)        # gentle sideways tilt
CLEARANCE_MIN = 0.08                # contract with the Luau script (orb hover)
CLEARANCE_TARGET = 0.095
TRI_BUDGET = 7000
CRACK_PROUD = 0.042                 # glowing crack fronts stand this far off the stone
BELT_Z = 2.20                       # rope belt height (design z, before the skirt squash)

# Palette key ids stored per face (0 = automatic, by normal; "del" = hidden
# cap, deleted after the closed shell's normals are fixed).
_KEYS = ["auto", "stone", "stone_light", "stone_dark", "ash", "charcoal", "black",
         "moss_dark", "stem_dark", "del"]
_KID = {k: i for i, k in enumerate(_KEYS)}

# Shading schemes for automatic faces: (up-facing, side, down-facing, down threshold).
_SCHEME = {
    "angel": ("stone_light", "stone_light", "stone", -0.45),
    "wing": ("stone_light", "stone_light", "stone", -0.62),
    "ped": ("stone", "stone_dark", "ash", -0.45),
}


# --------------------------------------------------------------------------
# Part container: a closed shell built in its own bmesh, with a per-face
# palette key layer that survives bisect / triangulate.
# --------------------------------------------------------------------------
class _Part:
    def __init__(self, scheme="angel"):
        self.bm = bmesh.new()
        self.pk = self.bm.faces.layers.int.new("pk")
        self.scheme = scheme

    def v(self, co):
        return self.bm.verts.new(co)

    def face(self, verts, key=None):
        f = self.bm.faces.new(list(verts))
        f[self.pk] = _KID[key] if key else 0
        return f

    def bridge(self, A, B, key=None, keyfn=None, closed=True):
        """Quads between two rings of equal length (or a ring and a pole)."""
        faces = []
        if len(A) == 1 or len(B) == 1:
            ring, pole = (B, A[0]) if len(A) == 1 else (A, B[0])
            n = len(ring)
            for i in range(n if closed else n - 1):
                vs = [ring[i], ring[(i + 1) % n], pole]
                if len(A) == 1:
                    vs.reverse()
                faces.append(self.face(vs, keyfn(i) if keyfn else key))
            return faces
        n = len(A)
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            faces.append(self.face((A[i], A[j], B[j], B[i]), keyfn(i) if keyfn else key))
        return faces

    def loft(self, rings, keyfn=None, cap_start=True, cap_end=True, cap_key=None, closed_loop=False):
        """Bridge consecutive rings; n-gon caps on open ends. keyfn(band, i).
        closed_loop: also bridge the last ring back to the first (a torus)."""
        pairs = list(zip(rings[:-1], rings[1:]))
        if closed_loop:
            pairs.append((rings[-1], rings[0]))
            cap_start = cap_end = False
        bands = []
        for b, (r0, r1) in enumerate(pairs):
            bands.append(self.bridge(r0, r1, keyfn=(lambda i, b=b: keyfn(b, i)) if keyfn else None))
        caps = []
        if cap_start and len(rings[0]) > 2:
            caps.append(self.face(reversed(rings[0]), cap_key))
        if cap_end and len(rings[-1]) > 2:
            caps.append(self.face(rings[-1], cap_key))
        big = [f for f in caps if len(f.verts) > 4]
        if big:
            bmesh.ops.triangulate(self.bm, faces=big, quad_method="BEAUTY", ngon_method="BEAUTY")
        return bands

    def ico(self, co, size, rnd=None, jitter=0.0, rot_z=0.0, key=None, flat_bottom=None):
        """Low icosphere (20 faces) scaled by ``size`` (x, y, z)."""
        res = bmesh.ops.create_icosphere(self.bm, subdivisions=1, radius=1.0)
        vs = res["verts"]
        for vert in vs:
            if rnd is not None and jitter:
                vert.co *= 1.0 + rnd.uniform(-jitter, jitter)
            if flat_bottom is not None and vert.co.z < 0:
                vert.co.z *= flat_bottom
        m = (Matrix.Translation(co) @ Matrix.Rotation(rot_z, 4, "Z")
             @ Matrix.Diagonal((size[0], size[1], size[2], 1.0)))
        bmesh.ops.transform(self.bm, matrix=m, verts=vs)
        faces = list({f for vert in vs for f in vert.link_faces})
        for f in faces:
            f[self.pk] = _KID[key] if key else 0
        return faces

    def transform(self, m):
        for vert in self.bm.verts:
            vert.co = m @ vert.co

    def chop(self, co, no, key="stone_light"):
        """Cut away everything on the +no side of a plane and cap the hole
        with a flat 'fresh break' face (a stylised chip)."""
        bm = self.bm
        no = Vector(no).normalized()
        if not any((vert.co - Vector(co)).dot(no) > 1e-4 for vert in bm.verts):
            return []
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-5, plane_co=co, plane_no=no, clear_outer=True)
        edges = [e for e in bm.edges if e.is_boundary]
        if not edges:
            return []
        res = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
        new = res.get("faces", [])
        tri = [f for f in new if len(f.verts) > 4]
        if tri:
            r = bmesh.ops.triangulate(bm, faces=tri, quad_method="BEAUTY", ngon_method="BEAUTY")
            new = [f for f in new if f.is_valid] + list(r["faces"])
        for f in new:
            f[self.pk] = _KID[key]
        return new


def _islands(bm, faces=None):
    """Edge-connected face islands."""
    seen = set()
    out = []
    for f0 in (faces if faces is not None else bm.faces):
        if f0 in seen:
            continue
        stack, isl = [f0], []
        seen.add(f0)
        while stack:
            f = stack.pop()
            isl.append(f)
            for e in f.edges:
                for g in e.link_faces:
                    if g not in seen:
                        seen.add(g)
                        stack.append(g)
        out.append(isl)
    return out


def _signed_volume(faces):
    vol = 0.0
    for f in faces:
        vs = [v.co for v in f.verts]
        for i in range(1, len(vs) - 1):
            vol += vs[0].dot(vs[i].cross(vs[i + 1]))
    return vol / 6.0


def _outward_islands(bm):
    """Every closed island must have a positive signed volume (outward normals:
    Roblox culls back faces). Flip any island that came out inside-out (thin
    zig-zag prisms can confuse recalc_face_normals)."""
    flipped = 0
    for isl in _islands(bm):
        if _signed_volume(isl) < 0:
            bmesh.ops.reverse_faces(bm, faces=isl)
            flipped += 1
    return flipped


def _finish(name, parts, glow_key=None):
    """Merge parts into one object, paint each face by its key (or by normal),
    M_Palette (or M_Glow via common.make_glow), flat shaded. Normals are made
    consistent and outward per closed island BEFORE hidden caps are removed."""
    objs = []
    for idx, P in enumerate(parts):
        bm = P.bm
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        _outward_islands(bm)
        hidden = [f for f in bm.faces if _KEYS[f[P.pk]] == "del"]
        if hidden:
            # FACES context also removes edges / verts only those faces used
            # (no wire edges or loose verts are left behind)
            bmesh.ops.delete(bm, geom=hidden, context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.normal_update()
        up, side, down, dthr = _SCHEME[P.scheme]
        groups = {}
        for f in bm.faces:
            k = _KEYS[f[P.pk]]
            if k == "auto":
                nz = f.normal.z
                k = up if nz > 0.55 else (down if nz < dthr else side)
            groups.setdefault(k, []).append(f)
        for k, faces in groups.items():
            common.paint_bmesh_faces(bm, faces, k)
        bm.faces.layers.int.remove(P.pk)
        o = common.object_from_bmesh(f"_{name}_part{idx}", bm)
        o.data.materials.append(common.mat_palette())
        objs.append(o)
    obj = common.join(objs, name) if len(objs) > 1 else objs[0]
    _set_name(obj, name)
    if glow_key:
        common.make_glow(obj, glow_key, name.split("_", 2)[2] if name.startswith("GLOW_") else name)
        _set_name(obj, name)
    common.shade_flat(obj)
    return obj


# --------------------------------------------------------------------------
# Small math helpers
# --------------------------------------------------------------------------
def _lerp(a, b, t):
    return a + (b - a) * t


def _angdiff(a, b):
    return (a - b + math.pi) % TAU - math.pi


def _interp_profile(profile, z):
    """profile rows (z, *values) sorted by z -> linearly interpolated values."""
    if z <= profile[0][0]:
        return profile[0][1:]
    for r0, r1 in zip(profile, profile[1:]):
        if z <= r1[0]:
            t = (z - r0[0]) / (r1[0] - r0[0])
            return tuple(_lerp(a, b, t) for a, b in zip(r0[1:], r1[1:]))
    return profile[-1][1:]


def _frames(pts, ref=Vector((0, 0, -1))):
    """Parallel-transport frames (tangent, normal ~ 'down', binormal)."""
    n = len(pts)
    tans = []
    for i in range(n):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, n - 1)]
        tans.append((b - a).normalized())
    nrm = (ref - tans[0] * ref.dot(tans[0])).normalized()
    out = []
    for t in tans:
        nrm = (nrm - t * nrm.dot(t)).normalized()
        out.append((t, nrm, t.cross(nrm)))
    return out


def _offset_poly(pts, d):
    """Inset a CCW 2D polygon by d (miter, clamped)."""
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2 = Vector(pts[i - 1]), Vector(pts[i]), Vector(pts[(i + 1) % n])
        e0 = (p1 - p0).normalized()
        e1 = (p2 - p1).normalized()
        n0 = Vector((-e0.y, e0.x))
        n1 = Vector((-e1.y, e1.x))
        b = n0 + n1
        if b.length < 1e-6:
            b = n0
        b.normalize()
        c = max(b.dot(n0), 0.45)
        out.append(p1 + b * (d / c))
    return out


def _rot2(p, a):
    c, s = math.cos(a), math.sin(a)
    return Vector((p[0] * c - p[1] * s, p[0] * s + p[1] * c))


def _tube(P, pts, radii, sides, key=None, phase=0.0, cap_key=None):
    fr = _frames(pts)
    rings = []
    for i, (p, r) in enumerate(zip(pts, radii)):
        t, nrm, bi = fr[i]
        rings.append([P.v(p + (bi * math.cos(TAU * k / sides + phase)
                               + nrm * math.sin(TAU * k / sides + phase)) * r)
                      for k in range(sides)])
    P.loft(rings, keyfn=(lambda b, i: key) if key else None, cap_key=cap_key or key)
    return rings


# --------------------------------------------------------------------------
# Robe: lofted stack of rings with irregular ridge / valley folds that are
# deepest at the hem, a slight twist and a short train at the back
# --------------------------------------------------------------------------
ROBE_N = 32
ROBE_TWIST = math.radians(10)       # fold pattern rotates a little towards the hem
ROBE_TRAIN = 0.24                   # back hem pools out a bit (short train)
# z, rx, ry, fold envelope
ROBE_PROFILE = [
    (0.00, 1.60, 1.42, 1.00),
    (0.15, 1.64, 1.46, 1.00),
    (0.45, 1.58, 1.40, 0.96),
    (1.00, 1.50, 1.31, 0.82),
    (1.65, 1.40, 1.21, 0.60),
    (2.30, 1.30, 1.11, 0.36),
    (2.90, 1.22, 1.02, 0.15),
    (3.45, 1.17, 0.96, 0.04),
    (3.95, 1.15, 0.90, 0.0),
    (4.35, 1.03, 0.80, 0.0),
    (4.65, 0.74, 0.62, 0.0),
    (4.88, 0.42, 0.40, 0.0),
]
ROBE_DEEP = 0.08                    # fold value below this (x envelope) -> dark crease


def _robe_folds(rnd):
    """(angle, amplitude, half-width): ridges (+) and deep valleys (-).
    Angle 0 = front (-Y), positive towards +X."""
    folds = []
    for a in (-168, -130, -86, -54, -12, 14, 54, 90, 128, 162):
        folds.append((math.radians(a + rnd.uniform(-5, 5)), 0.13 * rnd.uniform(0.8, 1.2),
                      math.radians(rnd.uniform(14, 19))))
    # deep creases between ridges: front-left, front-right, both sides, back
    for a, amp, w in ((-33, -0.19, 11), (33, -0.17, 11), (-108, -0.14, 10), (109, -0.13, 10),
                      (-150, -0.10, 9), (180, -0.11, 9)):
        folds.append((math.radians(a + rnd.uniform(-2, 2)), amp, math.radians(w)))
    return folds


def _fold_value(a, folds):
    f = -0.035
    for ak, amp, w in folds:
        d = abs(_angdiff(a, ak))
        if d < w:
            f += amp * math.cos(0.5 * math.pi * d / w) ** 1.6
    return f


def _robe_point(a, z, folds, extra=0.0):
    rx, ry, env = _interp_profile(ROBE_PROFILE, z)
    s, c = math.sin(a), -math.cos(a)
    back = max(0.0, c)
    ry = ry + ROBE_TRAIN * back ** 2 * max(0.0, 1.0 - z / 0.95) ** 2
    re = 1.0 / math.sqrt((s / rx) ** 2 + (c / ry) ** 2)
    f = _fold_value(a + ROBE_TWIST * max(0.0, 1.0 - z / SKIRT_TOP) ** 1.5, folds)
    r = re * (1.0 + env * f) + extra
    return Vector((s * r, c * r, z)), f * env


def _robe_outer(a, z, folds, extra, span):
    """Robe point pushed out to the outermost fold within +-span/2 around a
    (so a coarser ring laid over the robe never dips into a ridge)."""
    best = 0.0
    for j in range(-2, 3):
        p, _ = _robe_point(a + span * j / 4.0, z, folds)
        best = max(best, Vector((p.x, p.y)).length)
    d = Vector((math.sin(a), -math.cos(a)))
    return Vector((d.x * (best + extra), d.y * (best + extra), z))


def _skirt_z(z):
    return z * SKIRT_SQUASH if z < SKIRT_TOP else z - BODY_DROP


def _build_robe(rnd, seed):
    P = _Part("angel")
    folds = _robe_folds(rnd)
    angles = [TAU * i / ROBE_N for i in range(ROBE_N)]
    rings, fvals = [], []
    for zi, row in enumerate(ROBE_PROFILE):
        z = row[0]
        ring, fr = [], []
        for a in angles:
            zz = z
            if zi == 1:  # wavy hem lip
                zz += 0.035 * math.sin(3 * a + seed)
            p, f = _robe_point(a, zz, folds)
            p.z = _skirt_z(p.z)
            if zi > 0:
                p.x += rnd.uniform(-0.012, 0.012)
                p.y += rnd.uniform(-0.012, 0.012)
            ring.append(P.v(p))
            fr.append(f)
        rings.append(ring)
        fvals.append(fr)
    # grime + a little moss creeping up the hem from the plinth
    moss = {(0, 4), (0, 5), (1, 5), (0, 13), (0, 21), (0, 22), (1, 21), (0, 28)}

    def keyfn(b, i):
        f = 0.5 * (fvals[b][i] + fvals[b][(i + 1) % ROBE_N])
        if (b, i) in moss:
            return "moss_dark"
        if b == 0:
            return "stone"
        if b <= 6:
            if f < -ROBE_DEEP:
                return "stone_dark"      # deep fold creases
            if f < -0.05:
                return "stone"
        return None

    P.loft(rings, keyfn=keyfn, cap_key="del")
    return P, folds


def _build_toes():
    """Two chubby stone feet peeking out under the front hem."""
    parts = []
    for side in (-1, 1):
        P = _Part("angel")
        res = bmesh.ops.create_uvsphere(P.bm, u_segments=7, v_segments=4, radius=1.0)
        m = (Matrix.Translation((0.33 * side, -1.42, 0.0))
             @ Matrix.Rotation(-0.25 * side, 4, "Z")
             @ Matrix.Diagonal((0.24, 0.34, 0.20, 1.0)))
        bmesh.ops.transform(P.bm, matrix=m, verts=res["verts"])
        for vert in P.bm.verts:            # flatten the bottom onto the plinth
            vert.co.z = max(vert.co.z, -0.02)
        parts.append(P)
    return parts


def _build_robe_crack(folds):
    """Dark hairline crack down the front-left of the skirt (thin strip proud
    of the surface; its winding is fixed per island in _finish)."""
    P = _Part("angel")
    pts = [(-0.80, 1.80), (-0.70, 1.62), (-0.84, 1.44), (-0.74, 1.24), (-0.90, 1.05), (-0.82, 0.86)]
    w = 0.055
    left, right = [], []
    for i, (a, z) in enumerate(pts):
        p, _ = _robe_point(a, z, folds, extra=0.03)
        p.z = _skirt_z(p.z)
        tz = Vector((math.cos(a), math.sin(a), 0.0))  # tangent around
        side = tz * (w * 0.5)
        nrm = Vector((math.sin(a), -math.cos(a), 0.0))
        left.append((p + side, nrm))
        right.append((p - side, nrm))
    outline = left + list(reversed(right))
    front = [P.v(p) for p, nrm in outline]
    back = [P.v(p - nrm * 0.08) for p, nrm in outline]
    P.bridge(front, back, "charcoal")
    f1 = P.face(front, "charcoal")
    f2 = P.face(list(reversed(back)), "charcoal")
    bmesh.ops.triangulate(P.bm, faces=[f1, f2], quad_method="BEAUTY", ngon_method="BEAUTY")
    return P


def _build_belt(rnd, folds):
    """Rope belt at the waist, a knot at the front-left and two hanging cords
    ending in flared tassels."""
    P = _Part("angel")
    segs = 16
    span = TAU / segs
    rings = []
    for i in range(segs):
        a = TAU * i / segs
        zd = BELT_Z + 0.025 * math.sin(2 * a + 0.7)
        c = _robe_outer(a, zd, folds, 0.0, span)
        zc = _skirt_z(zd)
        d = Vector((c.x, c.y, 0.0)).normalized()
        r = Vector((c.x, c.y)).length
        ring = [P.v(Vector((d.x * (r + 0.04), d.y * (r + 0.04), zc + 0.09))),
                P.v(Vector((d.x * (r + 0.13), d.y * (r + 0.13), zc))),
                P.v(Vector((d.x * (r + 0.04), d.y * (r + 0.04), zc - 0.09))),
                P.v(Vector((d.x * (r - 0.05), d.y * (r - 0.05), zc)))]
        rings.append(ring)
    P.loft(rings, closed_loop=True)
    # knot over a fold at the front-left
    ak = math.radians(-16)
    kc = _robe_outer(ak, BELT_Z, folds, 0.16, 0.3)
    kc.z = _skirt_z(BELT_Z) - 0.02
    P.ico(kc, (0.21, 0.15, 0.18), rnd=rnd, jitter=0.12, rot_z=ak)
    # two cords hanging from the knot, each ending in a tassel
    for da, zd_end, lean in ((-0.10, 1.00, -0.03), (0.08, 1.30, 0.03)):
        cp = []
        for t in (0.0, 0.3, 0.62, 1.0):
            zd = _lerp(BELT_Z - 0.06, zd_end, t)
            p = _robe_outer(ak + da * t + lean * t * t, zd, folds, 0.09 + 0.02 * t, 0.25)
            p.z = _skirt_z(zd)
            cp.append(p)
        cp[0] = kc + (cp[1] - kc) * 0.15
        _tube(P, cp, [0.065, 0.06, 0.06, 0.06], 4, phase=math.pi / 4)
        end = cp[-1]
        out = Vector((end.x, end.y, 0.0)).normalized() * 0.035
        tp = [end + Vector((0, 0, 0.04)), end + out + Vector((0, 0, -0.15)), end + out * 1.6 + Vector((0, 0, -0.34))]
        _tube(P, tp, [0.07, 0.105, 0.115], 5, phase=0.3)
    return P


# --------------------------------------------------------------------------
# Capelet (short shoulder cape under the hood) with a dagged hem: six rounded
# dags and a small point at the back
# --------------------------------------------------------------------------
def _dag(a):
    # six rounded dags (24 verts -> 4 per dag); a dag peak sits at the front so
    # the hem stays clear of the orb held right in front of the chest
    scal = 0.5 - 0.5 * math.cos(6 * a)
    back = max(0.0, -math.cos(a)) ** 14         # small point at the back
    return 0.13 * scal + 0.10 * back


def _build_capelet(rnd, seed):
    P = _Part("angel")
    n = 24
    angles = [TAU * i / n for i in range(n)]
    jit = [rnd.uniform(-0.018, 0.018) for _ in range(n)]
    hem_folds = [(math.radians(a + rnd.uniform(-6, 6)), 1.0, math.radians(20))
                 for a in (0, 45, 90, 135, 180, 225, 270, 315)]

    def ring(z, rx, ry, fold=0.0, dag=0.0):
        out = []
        for i, a in enumerate(angles):
            s, c = math.sin(a), -math.cos(a)
            re = 1.0 / math.sqrt((s / rx) ** 2 + (c / ry) ** 2)
            f = sum(math.cos(0.5 * math.pi * abs(_angdiff(a, ak)) / w) ** 2
                    for ak, _, w in hem_folds if abs(_angdiff(a, ak)) < w)
            r = re * (1 + fold * (f - 0.4))
            out.append(P.v((s * r, c * r, z - dag * (_dag(a) + jit[i]))))
        return out

    rings = [
        ring(5.00, 0.62, 0.56),
        ring(4.74, 0.98, 0.82, 0.02),
        ring(4.42, 1.22, 1.00, 0.05, 0.25),
        ring(4.12, 1.36, 1.11, 0.08, 0.8),
        ring(4.00, 1.39, 1.14, 0.09, 1.0),   # hem edge
        ring(4.05, 1.25, 1.00, 0.07, 1.0),   # underside
        ring(4.22, 1.06, 0.82),
    ]
    P.loft(rings, cap_start=True, cap_end=True, cap_key="del")
    return P


# --------------------------------------------------------------------------
# Hood: rings around the forward axis; deep dark face cavity, rolled brim,
# soft drooping cowl tip at the back, flaring towards the shoulders.
# --------------------------------------------------------------------------
HOOD_C = Vector((0.0, -0.05, 5.78))
HOOD_R = Vector((1.27, 1.15, 1.20))
HOOD_M = 20
HOOD_TIP_SWING = math.radians(24)


def _hood_point(phi, psi, r, brim=0.0):
    d = Vector((math.sin(phi) * math.cos(psi), -math.cos(phi), math.sin(phi) * math.sin(psi)))
    # swing the back hemisphere up so the back pole becomes the cowl tip
    if d.y > 0:
        th = HOOD_TIP_SWING * d.y ** 1.5
        c, sn = math.cos(th), math.sin(th)
        d = Vector((d.x, d.y * c - d.z * sn, d.y * sn + d.z * c))
    p = Vector((d.x * HOOD_R.x, d.y * HOOD_R.y, d.z * HOOD_R.z)) * r
    # overhanging top brim
    p.y -= brim * max(0.0, math.sin(psi)) ** 1.5
    # soft drooping cowl tip (fabric gathered at the back of the crown)
    tip = Vector((0.0, math.cos(HOOD_TIP_SWING), math.sin(HOOD_TIP_SWING)))
    k = max(0.0, d.dot(tip) - 0.74) / 0.26
    p += Vector((0.0, 0.62, -0.78)).normalized() * 0.24 * k ** 1.6 * r
    # seam ridge from the brim over the top and down the back, plus two soft
    # fabric folds fanning out from the cowl tip over the back of the hood
    if phi > math.radians(56):
        seam = max(0.0, math.cos(psi - math.pi / 2)) ** 24
        if phi > math.radians(110):
            seam = max(seam, max(0.0, math.cos(psi + math.pi / 2)) ** 24)
        p += d * 0.08 * seam * r
    if phi > math.radians(115):
        fold = sum(max(0.0, math.cos(psi - math.radians(a))) ** 30 for a in (52, 128))
        p += d * 0.06 * fold * min(1.0, (phi - math.radians(115)) / math.radians(30)) * r
    # the lower back hangs down onto the capelet like fabric
    back = max(0.0, d.y) ** 1.2
    low = max(0.0, min(1.0, (0.10 - d.z) / 0.8))
    p.z -= 0.30 * back * low * r
    # flare the lower part out towards the shoulders
    if p.z < 0:
        t = min(1.0, -p.z / HOOD_R.z)
        p.x *= 1.0 + 0.16 * t * t
        p.y *= 1.0 + 0.10 * t * t
    return p


def _build_hood(rnd):
    P = _Part("angel")
    M = HOOD_M
    psis = [TAU * k / M - math.pi / 2 for k in range(M)]  # vertex at bottom + top
    spec = [  # phi deg, radius scale, brim, key of the band towards the next ring
        (24, 0.40, 0.00, "black"),
        (36, 0.60, 0.02, "black"),
        (43, 0.88, 0.08, "charcoal"),
        (47, 1.05, 0.12, None),       # rolled lip
        (54, 1.01, 0.10, None),       # rim on the outer surface
        (68, 1.0, 0.04, None),
        (84, 1.0, 0.0, None),
        (102, 1.0, 0.0, None),
        (122, 1.0, 0.0, None),
        (143, 1.0, 0.0, None),
        (163, 1.0, 0.0, None),
    ]
    pole_front = P.v(HOOD_C + _hood_point(0.0, 0.0, 0.30))
    rings = [[pole_front]]
    keys = ["black"]
    for phi, r, brim, key in spec:
        ring = []
        for psi in psis:
            p = _hood_point(math.radians(phi), psi, r, brim)
            if phi >= 54:
                p += Vector((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))) * 0.012
            ring.append(P.v(HOOD_C + p))
        rings.append(ring)
        keys.append(key)
    pole_back = P.v(HOOD_C + _hood_point(math.pi, 0.0, 1.0))
    rings.append([pole_back])
    keys.append(None)
    P.loft(rings, keyfn=lambda b, i: keys[b], cap_start=False, cap_end=False)
    return P


def _build_eyes():
    """Two small droopy oval eyes deep inside the hood cavity."""
    P = _Part("angel")
    for side in (-1, 1):
        res = bmesh.ops.create_uvsphere(P.bm, u_segments=6, v_segments=4, radius=1.0)
        m = (Matrix.Translation(HOOD_C + Vector((0.30 * side, -0.45, -0.09)))
             @ Matrix.Rotation(-0.10 * side, 4, "Y")
             @ Matrix.Diagonal((0.13, 0.05, 0.17, 1.0)))
        bmesh.ops.transform(P.bm, matrix=m, verts=res["verts"])
    return P


def _head_matrix():
    return (Matrix.Translation(NECK) @ Matrix.Rotation(HEAD_TILT, 4, "Y")
            @ Matrix.Rotation(HEAD_BOW, 4, "X") @ Matrix.Translation(-NECK))


# --------------------------------------------------------------------------
# Sleeves (bell sleeves whose cuffs close round a thick wrist on top and hang
# as a drape below, turned in towards the orb, so no open cuff loop shows from
# the front) and two cupped hands around the orb
# --------------------------------------------------------------------------
def _sleeve_path(side):
    s = side
    return [Vector((0.70 * s, 0.05, 4.22)),
            Vector((1.15 * s, 0.02, 3.94)),
            Vector((1.40 * s, -0.26, 3.46)),
            Vector((1.37 * s, -0.72, 3.10)),
            Vector((1.21 * s, -1.06, 2.98)),
            Vector((0.98 * s, -1.19, 3.01))]


SLEEVE_RU = [0.40, 0.44, 0.46, 0.44, 0.38, 0.31]   # upper half: tightens round the wrist
SLEEVE_RL = [0.40, 0.44, 0.46, 0.47, 0.48, 0.47]   # lower half: the hanging drape
SLEEVE_DROOP = [0.0, 0.05, 0.12, 0.22, 0.32, 0.38]


def _sleeve_ring(P, p, frame, ru, rl, droop, sides=10, scale=1.0):
    t, nrm, bi = frame
    ring = []
    for k in range(sides):
        th = TAU * k / sides + 0.3
        cs, sn = math.cos(th), math.sin(th)
        r = _lerp(ru, rl, max(0.0, sn)) * scale     # nrm ~ down: sn > 0 is the lower half
        dn = sn * (1.0 + droop) if sn > 0 else sn
        ring.append(P.v(p + (bi * cs + nrm * dn) * r))
    return ring


def _build_sleeve(side, rnd):
    P = _Part("angel")
    pts = _sleeve_path(side)
    fr = _frames(pts)
    rings = [_sleeve_ring(P, p, fr[i], SLEEVE_RU[i], SLEEVE_RL[i], SLEEVE_DROOP[i])
             for i, p in enumerate(pts)]
    # cuff: rolled lip and a short, mid-grey inside (the wrist fills the top)
    t, nrm, bi = fr[-1]
    p = pts[-1]
    rings.append(_sleeve_ring(P, p - t * 0.06, fr[-1], SLEEVE_RU[-1], SLEEVE_RL[-1],
                              SLEEVE_DROOP[-1] * 0.85, scale=0.80))
    rings.append([P.v(p - t * 0.30 + nrm * 0.14)])

    def keyfn(b, i):
        return "stone" if b == len(pts) else None   # inside the cuff

    P.loft(rings, keyfn=keyfn, cap_start=True, cap_end=False, cap_key="del")
    return P


def _sphere_dir(theta, psi):
    """theta from +Z, psi around Z from +X (psi = -90 deg is the front, -Y)."""
    return Vector((math.sin(theta) * math.cos(psi), math.sin(theta) * math.sin(psi), math.cos(theta)))


def _orb_pt(theta_deg, psi_deg, side, r):
    """Point around the orb for the hand on ``side`` (psi mirrored for -X)."""
    psi = math.radians(psi_deg)
    if side < 0:
        psi = math.pi - psi
    return ORB_C + _sphere_dir(math.radians(theta_deg), psi) * r


def _build_hand(side):
    """One cupped hand: a palm hugging the orb's lower outer side (the two
    palms stay apart at the front), three short, fat fingers side by side
    curling up the orb's lower front, a thumb on its flank and a thick wrist
    that fills the top of the sleeve cuff."""
    parts = []
    P = _Part("angel")
    thetas = (124, 139, 153, 167)
    psis = (-76, -46, -16, 14, 40)
    out_r = [[_orb_pt(t, q, side, HAND_OUT) for q in psis] for t in thetas]
    in_r = [[_orb_pt(t, q, side, HAND_IN) for q in psis] for t in thetas]
    outer = [[P.v(c) for c in row] for row in out_r]
    inner = [[P.v(c) for c in row] for row in in_r]
    R, C = len(thetas), len(psis)
    for r in range(R - 1):
        for c in range(C - 1):
            P.face((outer[r][c], outer[r][c + 1], outer[r + 1][c + 1], outer[r + 1][c]))
            P.face((inner[r][c], inner[r + 1][c], inner[r + 1][c + 1], inner[r][c + 1]))
    for c in range(C - 1):
        P.face((outer[0][c], inner[0][c], inner[0][c + 1], outer[0][c + 1]))
        P.face((outer[-1][c], outer[-1][c + 1], inner[-1][c + 1], inner[-1][c]))
    for r in range(R - 1):
        P.face((outer[r][0], outer[r + 1][0], inner[r + 1][0], inner[r][0]))
        P.face((outer[r][-1], inner[r][-1], inner[r + 1][-1], outer[r + 1][-1]))
    parts.append(P)

    # fingers curling up the front of the orb (pinky nearest the centre line);
    # they overlap a little, so the hand reads as one chunky cupped shape
    for psi, t_tip, rad in ((-81, 119, 0.115), (-65, 115, 0.125), (-49, 114, 0.125)):
        F = _Part("angel")
        rf = HAND_IN + rad * 0.95
        pts = [_orb_pt(t, psi, side, rf) for t in (155, 142, 130)]
        pts.append(_orb_pt(t_tip, psi + 1, side, rf - 0.01))
        pts.append(_orb_pt(t_tip - 6, psi + 1, side, rf - 0.035))
        _tube(F, pts, [rad, rad, rad * 0.96, rad * 0.84, rad * 0.42], 5, phase=0.3)
        parts.append(F)

    # thumb lying on the orb's flank, pointing forward / up
    T = _Part("angel")
    th_pts = [_orb_pt(t, q, side, HAND_IN + 0.115) for t, q in ((133, -14), (118, -26), (106, -34))]
    th_pts.append(_orb_pt(101, -37, side, HAND_IN + 0.09))
    _tube(T, th_pts, [0.125, 0.118, 0.095, 0.05], 5)
    parts.append(T)

    # wrist from inside the cuff to the back of the hand
    W = _Part("angel")
    path = _sleeve_path(side)
    fr = _frames(path)
    a = path[-1] - fr[-1][0] * 0.26
    b = _orb_pt(143, 16, side, HAND_OUT + 0.02)
    _tube(W, [a, a.lerp(b, 0.5), b], [0.25, 0.22, 0.18], 7)
    parts.append(W)
    return parts


# --------------------------------------------------------------------------
# Wings: big half-open wings. A leading-edge bone over a thick top-covert
# slab with a scalloped edge, a scalloped middle-covert row, and a fan of
# rounded flight feathers (the outer primaries longer and more pointed).
# Wing 2D frame: u outwards along the wing, v up; the top edge is a broad arc
# that peaks well away from the hood (no 'cat ear' lobe beside it).
# --------------------------------------------------------------------------
WING_ROOT = (0.30, 0.66, 4.55)
WING_YAW = math.radians(-4)         # (slightly forward of) the X axis: the far wing still
                                    # shows beside the body in three-quarter views
WING_SCALE = 0.82
WING_CUP = 0.04                     # the outer part curls back

_WING_BONE = [(0.02, 0.18), (0.34, 0.88), (0.92, 1.52), (1.66, 1.92), (2.45, 2.02), (3.05, 1.82),
              (3.42, 1.42)]
_WING_BONE_R = [0.17, 0.165, 0.155, 0.145, 0.13, 0.11, 0.085]
_COVERT_TOP_EDGE = [(3.45, 1.30), (3.08, 1.72), (2.45, 1.92), (1.66, 1.82), (0.92, 1.42), (0.36, 0.80),
                    (0.02, 0.12)]
_COVERT_MID_EDGE = [(3.62, 0.72), (3.30, 1.25), (2.50, 1.55), (1.50, 1.42), (0.65, 0.96), (0.06, 0.20)]
# flight feather tips (wing u, v) from the body outwards, and their bases
_FEATHER_TIPS = [(0.20, -1.55), (0.76, -1.96), (1.36, -2.12), (1.98, -2.08), (2.56, -1.84),
                 (3.06, -1.44), (3.46, -0.88), (3.72, -0.22)]
_FEATHER_BASE = ((0.42, 0.12), (3.02, 1.10))


def _wing_mapper(side):
    root = Vector((WING_ROOT[0] * side, WING_ROOT[1], WING_ROOT[2]))
    U = Vector((math.cos(WING_YAW) * side, math.sin(WING_YAW), 0.0))
    V = Vector((0.14 * side, 0.15, 1.0)).normalized()          # leans out and back
    W = U.cross(V).normalized() * side       # towards the front (-Y)

    def to3d(u, v, w):
        u *= WING_SCALE
        v *= WING_SCALE
        # cup the wing: the outer part curves back, the top rolls forward a bit
        w_off = -WING_CUP * u * u + 0.05 * max(v, 0.0)
        return root + U * u + V * v + W * (w + w_off)
    return to3d


def _slab(P, outline, to3d, t, bevel, w0=0.0, key=None):
    inner = _offset_poly(outline, bevel)
    front = [P.v(to3d(u, v, w0 + t / 2)) for u, v in inner]
    mid = [P.v(to3d(u, v, w0)) for u, v in outline]
    back = [P.v(to3d(u, v, w0 - t / 2)) for u, v in inner]
    P.bridge(front, mid, key)
    P.bridge(mid, back, key)
    ff = P.face(front, key)
    fb = P.face(list(reversed(back)), key)
    bmesh.ops.triangulate(P.bm, faces=[ff, fb], quad_method="BEAUTY", ngon_method="BEAUTY")


def _scallops(a, b, n, dip, rnd=None):
    """Points from a towards b (b excluded) with n rounded bumps bulging to
    the right of the direction of travel (= outside of a CCW outline)."""
    a, b = Vector(a), Vector(b)
    t = (b - a).normalized()
    out_n = Vector((t.y, -t.x))
    pts = []
    for j in range(n):
        d = dip * (rnd.uniform(0.85, 1.15) if rnd else 1.0)
        for q in (0.0, 0.22, 0.5, 0.78):
            p = a.lerp(b, (j + q) / n)
            pts.append(tuple(p + out_n * (0.12 * d + d * math.sin(math.pi * q) ** 0.7)))
    return pts


def _feather_outline(L, Wd, point=0.0):
    """Rounded feather pointing along +y from (0, 0); ``point`` (0..1) makes
    the tip more pointed (primaries)."""
    h = Wd / 2
    a = 0.92 - 0.18 * point
    b = 0.62 - 0.30 * point
    return [(h * 0.72, 0.0), (h * 0.98, 0.45 * L), (h * a, 0.70 * L), (h * b, 0.89 * L),
            (0.0, L), (-h * b, 0.89 * L), (-h * a, 0.70 * L), (-h * 0.98, 0.45 * L),
            (-h * 0.72, 0.0)]


def _build_wing(side, rnd, snapped=False):
    to3d = _wing_mapper(side)
    parts = []
    # leading-edge bone: a thick rounded ridge along the top arc
    Bn = _Part("wing")
    _tube(Bn, [to3d(u, v, 0.03) for u, v in _WING_BONE], [r * WING_SCALE for r in _WING_BONE_R], 6,
          phase=0.5)
    parts.append(Bn)
    # top coverts: thick, plump slab under the bone, scalloped lower edge
    A = _Part("wing")
    outline = _scallops((0.10, -0.08), (3.30, 1.02), 5, 0.17, rnd) + _COVERT_TOP_EDGE
    _slab(A, outline, to3d, 0.40, 0.12)
    parts.append(A)
    # middle coverts: bigger scallops overlapping the feather bases
    B = _Part("wing")
    outline = _scallops((0.16, -0.70), (3.50, 0.30), 5, 0.23, rnd) + _COVERT_MID_EDGE
    _slab(B, outline, to3d, 0.30, 0.09, w0=-0.02)
    parts.append(B)
    # flight feathers: overlapping rounded fan, outer ones tucked behind
    n = len(_FEATHER_TIPS)
    for k, tip in enumerate(_FEATHER_TIPS):
        t = k / (n - 1)
        base = Vector(_FEATHER_BASE[0]).lerp(Vector(_FEATHER_BASE[1]), t)
        tipv = Vector(tip) + Vector((rnd.uniform(-0.04, 0.04), rnd.uniform(-0.04, 0.04)))
        d = tipv - base
        L = d.length
        ang = math.atan2(d.y, d.x) - math.pi / 2
        prim = max(0.0, (k - 4) / 3.0)
        Wd = 0.68 - 0.06 * prim
        fo = [tuple(base + _rot2(p, ang)) for p in _feather_outline(L, Wd, prim)]
        F = _Part("wing")
        w0 = 0.06 - 0.03 * k
        _slab(F, fo, to3d, 0.17, 0.05, w0=w0)
        if snapped and k >= n - 3:
            # the three outer primaries snapped off: a jagged, pointed break
            dirv = d.normalized()
            frac = {n - 1: 0.34, n - 2: 0.40, n - 3: 0.58}[k]
            cut = base + d * frac
            for tilt, off in ((0.55, 0.0), (-0.45, 0.07)):
                cdir = _rot2(dirv, tilt)
                c2 = cut + dirv * off
                co = to3d(c2.x, c2.y, w0)
                co_n = to3d(c2.x + cdir.x, c2.y + cdir.y, w0) - co
                F.chop(co, co_n)
        parts.append(F)
    return parts


def _snapped_fragment(rnd):
    """The broken primary tip lying on the plinth's base ledge (right side)."""
    P = _Part("ped")
    L, Wd = 0.62, 0.34
    out = _feather_outline(L, Wd, 0.8)[1:-1]
    out = [(x, y - 0.12 * L) for x, y in out]
    out.append((0.0, 0.0))
    co = Vector((2.10, -0.55, 0.50 * PED_ZS))

    def to3d(u, v, w):
        p = _rot2((u, v), math.radians(-12))
        return co + Vector((p.x, -p.y, 0.055 + w))
    _slab(P, out, to3d, 0.12, 0.03, key="stone_light")
    return P


# --------------------------------------------------------------------------
# Pedestal: three stacked, chamfered-square tiers with chips and moss
# --------------------------------------------------------------------------
def _sq_ring(P, hw, z, rnd, cham=0.16, jit=0.018):
    c = hw * cham
    pts = [(hw - c, -hw), (hw, -hw + c), (hw, hw - c), (hw - c, hw),
           (-hw + c, hw), (-hw, hw - c), (-hw, -hw + c), (-hw + c, -hw)]
    return [P.v((x + rnd.uniform(-jit, jit), y + rnd.uniform(-jit, jit), z)) for x, y in pts]


def _tier(rnd, profile):
    P = _Part("ped")
    rings = [_sq_ring(P, hw, z, rnd) for hw, z in profile]
    P.loft(rings, cap_start=True, cap_end=True)
    return P


def _moss_lump(P, co, size, rnd, flat=0.35):
    """Flattened moss clump (moss_dark) with a few lighter tufts (stem_dark)
    on top so it stays readable at night."""
    faces = P.ico(co, (size, size * rnd.uniform(0.6, 0.9), size * flat), rnd=rnd, jitter=0.18,
                  rot_z=rnd.uniform(0, TAU), key="moss_dark", flat_bottom=0.2)
    for f in faces:
        c = f.calc_center_median()
        if c.z - co.z > size * flat * 0.45 and rnd.random() < 0.6:
            f[P.pk] = _KID["stem_dark"]


def _rubble(P, co, sz, rnd, key=None):
    faces = P.ico(Vector(co) + Vector((0, 0, sz * 0.5)), (sz * rnd.uniform(0.85, 1.25),
                  sz * rnd.uniform(0.75, 1.1), sz * rnd.uniform(0.55, 0.8)),
                  rnd=rnd, jitter=0.2, rot_z=rnd.uniform(0, TAU), key=key)
    for f in faces:
        for vert in f.verts:
            vert.co.z = max(vert.co.z, co[2])
    return faces


def _ped_face(z):
    """Half width of the plinth's flat faces at design height z."""
    if z < 0.50:
        return 2.40
    if z < 0.69:
        return 1.82
    if z < 1.80:
        return _lerp(1.82, 1.74, (z - 0.69) / 1.11)
    return 2.13


def _plane_axes(plane):
    """(ray origin fn(u, z), ray direction, outward normal) for a plinth face."""
    if plane == "front":
        return (lambda u, z: Vector((u, -4.0, z))), Vector((0, 1, 0)), Vector((0, -1, 0))
    if plane == "back":
        return (lambda u, z: Vector((u, 4.0, z))), Vector((0, -1, 0)), Vector((0, 1, 0))
    if plane == "left":
        return (lambda u, z: Vector((-4.0, u, z))), Vector((1, 0, 0)), Vector((-1, 0, 0))
    return (lambda u, z: Vector((4.0, u, z))), Vector((-1, 0, 0)), Vector((1, 0, 0))


def _crack_prism(P, pts2d, plane, w0, w1, bvh, out=CRACK_PROUD, depth_in=0.09, key=None):
    """Zig-zag strip on a vertical plinth face. pts2d = [(u, z_design)]; u is
    x for front/back and y for left/right. Every outline point is projected
    onto the real (jittered) stone surface with ``bvh`` and pushed ``out``
    along the face normal, so the strip never sinks into or hovers barely
    above the stone (no depth flicker)."""
    n = len(pts2d)
    left, right = [], []
    for i in range(n):
        a = Vector(pts2d[max(i - 1, 0)])
        b = Vector(pts2d[min(i + 1, n - 1)])
        t = (b - a).normalized()
        sd = Vector((-t.y, t.x))
        w = _lerp(w0, w1, i / max(n - 1, 1)) * 0.5
        p = Vector(pts2d[i])
        left.append(p + sd * w)
        right.append(p - sd * w)
    outline = left + list(reversed(right))
    origin, ray, N = _plane_axes(plane)
    front, back = [], []
    for q in outline:
        z = q[1] * PED_ZS
        hit = bvh.ray_cast(origin(q[0], z), ray, 8.0)
        loc = hit[0]
        nominal = origin(q[0], z) + ray * (4.0 - _ped_face(q[1]))
        if loc is None or (loc - nominal).length > 0.12:
            loc = nominal
        front.append(P.v(loc + N * out))
        back.append(P.v(loc - N * depth_in))
    P.bridge(front, back, key)
    f1 = P.face(front, key)
    f2 = P.face(list(reversed(back)), key)
    bmesh.ops.triangulate(P.bm, faces=[f1, f2], quad_method="BEAUTY", ngon_method="BEAUTY")


def _build_pedestal(rnd, seed):
    base = _tier(rnd, [(2.40, 0.0), (2.40, 0.36), (2.26, 0.50)])
    shaft = _tier(rnd, [(1.95, 0.44), (1.95, 0.60), (1.82, 0.69), (1.74, 1.80),
                        (1.88, 1.88), (1.95, 1.98)])
    cap = _tier(rnd, [(2.04, 1.93), (2.13, 2.02), (2.13, 2.33), (1.99, PED_DESIGN)])

    # chips: flat facets cutting corners and edges
    base.chop((-2.40, -1.95, 0.50), (-0.55, -0.62, 0.56))        # broken front-left corner
    base.chop((-2.18, -2.40, 0.46), (-0.15, -0.72, 0.68))
    base.chop((2.40, 1.70, 0.30), (0.62, 0.55, 0.30))           # back-right corner
    base.chop((1.10, -2.40, 0.47), (0.0, -0.55, 0.83))          # notch in the front edge
    cap.chop((1.90, -2.13, 2.45), (0.50, -0.55, 0.67))          # front-right top corner
    cap.chop((-2.13, 1.85, 2.40), (-0.62, 0.48, 0.62))          # back-left top corner
    cap.chop((-0.45, -2.13, 2.06), (0.0, -0.70, -0.71))         # bite under the front edge
    shaft.chop((1.74, -1.62, 1.20), (0.70, -0.70, 0.12))        # front-right shaft edge

    moss = _Part("ped")
    for co, sz in (((-1.55, -2.15, 0.50), 0.34), ((-2.15, -0.9, 0.50), 0.28),
                   ((1.95, 2.0, 0.50), 0.36), ((2.12, -1.45, 0.50), 0.20),
                   ((-1.75, 1.80, PED_DESIGN), 0.30), ((1.65, 1.2, PED_DESIGN), 0.22),
                   ((-2.05, -2.05, 0.03), 0.30), ((2.3, 0.4, 0.03), 0.26)):
        _moss_lump(moss, Vector(co), sz, rnd)
    # moss growing where the robe meets the plinth top
    for a_deg, sz in ((200, 0.30), (232, 0.22), (118, 0.24), (25, 0.16)):
        a = math.radians(a_deg + rnd.uniform(-6, 6))
        r = 1.0 / math.sqrt((math.sin(a) / 1.66) ** 2 + (math.cos(a) / 1.48) ** 2)
        if math.cos(a) < 0:
            r += 0.12                                        # clear of the robe's back train
        _moss_lump(moss, Vector((math.sin(a) * r, -math.cos(a) * r, PED_DESIGN)), sz, rnd, flat=0.5)
    for P in (base, shaft, cap, moss):
        P.transform(Matrix.Diagonal((1.0, 1.0, PED_ZS, 1.0)))
    # dark hairline crack on the back of the shaft (projected onto the jittered stone)
    dark = _Part("ped")
    _crack_prism(dark, [(0.35, 1.80), (0.48, 1.58), (0.30, 1.40), (0.44, 1.18), (0.28, 0.98)],
                 "back", 0.06, 0.025, BVHTree.FromBMesh(shaft.bm), out=0.03, depth_in=0.06,
                 key="charcoal")
    # chunks broken off the plinth at its foot (laid out point-symmetrically so the
    # bbox, and with it the origin, stays centred on the plinth axis)
    rubble = _Part("ped")
    for co, sz in (((-2.62, -2.28, 0.0), 0.21), ((-2.86, -1.72, 0.0), 0.13), ((0.98, -2.66, 0.0), 0.12),
                   ((2.62, 2.28, 0.0), 0.19), ((2.84, 1.62, 0.0), 0.12), ((-1.02, 2.64, 0.0), 0.11)):
        _rubble(rubble, co, sz, rnd)
    # pebbles on the base ledge
    for co, sz in (((-2.14, -1.05, 0.50 * PED_ZS), 0.09), ((-1.20, -2.12, 0.50 * PED_ZS), 0.08)):
        _rubble(rubble, co, sz, rnd, key="stone_light")
    return [base, shaft, cap, moss, dark, rubble, _snapped_fragment(rnd)]


def _build_cracks(bvh):
    """Glowing veins in the plinth (front, right and left faces of the shaft)."""
    P = _Part("angel")
    # main crack: from under the cap down the front of the shaft, forking
    _crack_prism(P, [(-0.55, 1.79), (-0.42, 1.55), (-0.62, 1.32), (-0.48, 1.08), (-0.70, 0.86),
                     (-0.60, 0.73)], "front", 0.10, 0.04, bvh)
    _crack_prism(P, [(-0.62, 1.32), (-0.88, 1.20), (-1.02, 1.02)], "front", 0.07, 0.03, bvh)
    # small crack at the right of the front face
    _crack_prism(P, [(0.95, 1.79), (1.08, 1.60), (0.98, 1.42), (1.16, 1.25)], "front", 0.08, 0.03, bvh)
    # crack on the right side
    _crack_prism(P, [(-0.30, 1.79), (-0.15, 1.52), (-0.36, 1.30), (-0.22, 1.05)], "right", 0.08, 0.03, bvh)
    # crack on the left side, rising from the plinth band
    _crack_prism(P, [(0.55, 0.73), (0.42, 0.95), (0.60, 1.16), (0.47, 1.38), (0.66, 1.55)],
                 "left", 0.09, 0.03, bvh)
    return P


# --------------------------------------------------------------------------
# Orb
# --------------------------------------------------------------------------
def _build_orb(rnd):
    P = _Part("angel")
    res = bmesh.ops.create_icosphere(P.bm, subdivisions=2, radius=ORB_R)
    for vert in res["verts"]:
        vert.co *= 1.0 + rnd.uniform(-0.015, 0.015)
        vert.co += ORB_C
    return P


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def _unique(name):
    if bpy.data.objects.get(name) is None:
        return name
    raise RuntimeError(f"object name {name!r} already exists (use another prefix)")


def _free_index(fmt, start=1):
    i = start
    while bpy.data.objects.get(fmt.format(i)) is not None:
        i += 1
    return i


def _set_name(obj, name):
    obj.name = name
    obj.data.name = name
    return obj


def world_bounds(objs):
    """World-space bounds from the vertices (bound_box can be stale right
    after mesh-data transforms)."""
    lo = Vector((1e9,) * 3)
    hi = Vector((-1e9,) * 3)
    for o in objs:
        mw = o.matrix_world
        for v in o.data.vertices:
            w = mw @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def _world_bvh(obj):
    mw = obj.matrix_world
    return BVHTree.FromPolygons([mw @ v.co for v in obj.data.vertices],
                                [list(p.vertices) for p in obj.data.polygons])


def make_angel_statue(prefix="Statue", location=(0, 0, 0), rotation_z=0.0, seed=0,
                      with_decor=True, glowing_eyes=True):
    """Build the stone angel statue. Returns [Angel, Pedestal, Orb glow,
    (Eyes glow), Cracks glow, decor...] (see module docstring)."""
    rnd = common.rng(seed * 7 + 3)
    lift = Matrix.Translation((0, 0, PED_TOP - 0.02))
    drop = Matrix.Translation((0, 0, -BODY_DROP))
    head = _head_matrix()

    # ---- angel figure ------------------------------------------------------
    robe, folds = _build_robe(rnd, seed)
    robe.chop((0.95, -1.18, 0.10), (0.55, -0.70, -0.45))   # chipped hem, front-right
    robe.chop((-1.42, 0.55, 0.12), (-0.80, 0.35, -0.50))   # chipped hem, left side
    lower = [robe] + _build_toes() + [_build_robe_crack(folds), _build_belt(rnd, folds)]
    upper = [_build_capelet(rnd, seed)]
    hood = _build_hood(rnd)
    hood.chop(HOOD_C + Vector((0.78, -0.70, 0.30)), (0.62, -0.62, 0.48))  # nick in the brim
    hood.transform(head)
    upper.append(hood)
    for side in (-1, 1):
        upper.append(_build_sleeve(side, rnd))
        upper += _build_hand(side)
    for side in (-1, 1):
        upper += _build_wing(side, rnd, snapped=(side > 0))
    for P in upper:
        P.transform(drop)
    parts = lower + upper
    for P in parts:
        P.transform(lift)
    angel = _finish(_unique(f"{prefix}_Angel"), parts)

    # ---- pedestal ----------------------------------------------------------
    pedestal = _finish(_unique(f"{prefix}_Pedestal"), _build_pedestal(rnd, seed))

    # ---- glow parts --------------------------------------------------------
    orb_p = _build_orb(rnd)
    orb_p.transform(lift @ drop)
    orb = _finish(_unique(f"GLOW_{ORB_KEY}_{prefix}Orb"), [orb_p], glow_key=ORB_KEY)
    out = [angel, pedestal, orb]
    if glowing_eyes:
        eyes_p = _build_eyes()
        eyes_p.transform(lift @ drop @ head)
        out.append(_finish(_unique(f"GLOW_ember_{prefix}Eyes"), [eyes_p], glow_key="ember"))
    cracks = _build_cracks(_world_bvh(pedestal))
    out.append(_finish(_unique(f"GLOW_fire_{prefix}Cracks"), [cracks], glow_key="fire"))

    # ---- decor (built in the same design frame) ---------------------------
    if with_decor:
        out += _build_decor(prefix, seed, _world_bvh(pedestal), _world_bvh(angel))

    # ---- origins + placement of the whole group ---------------------------
    place = Matrix.Translation(Vector(location)) @ Matrix.Rotation(rotation_z, 4, "Z")
    for o in out:
        common.origin_to_base(o)
        o.matrix_world = place @ o.matrix_world
    return out


def _candle_bad(obj, ped_bvh, angel_bvh, margin=0.04):
    """True if a candle cluster overhangs the plinth top or touches the robe."""
    mw = obj.matrix_world
    pts = [mw @ v.co for v in obj.data.vertices]
    z0 = min(p.z for p in pts)
    for p in pts:
        if p.z <= z0 + 0.02:
            hit = ped_bvh.ray_cast(p + Vector((0, 0, 0.05)), Vector((0, 0, -1)), 0.1)
            if hit[0] is None:
                return True
            for d in (Vector((margin, 0, 0)), Vector((-margin, 0, 0)), Vector((0, margin, 0)),
                      Vector((0, -margin, 0))):
                if ped_bvh.ray_cast(p + d + Vector((0, 0, 0.05)), Vector((0, 0, -1)), 0.1)[0] is None:
                    return True
    tb = BVHTree.FromPolygons(pts, [list(p.vertices) for p in obj.data.polygons])
    return bool(tb.overlap(angel_bvh))


def _build_decor(prefix, seed, ped_bvh, angel_bvh):
    from parts import lights, pumpkins  # noqa: E402  (lazy: only when decor is wanted)
    dp = f"{prefix}Decor"
    objs = []

    def candles(loc, rot, count, sd, height, wax="black"):
        """Candle cluster on the plinth top: try a few yaws / nudges until the
        wax puddles rest fully on the stone and stay clear of the robe."""
        i = _free_index(dp + "_Candles_{:02d}")
        name = f"{dp}_Candles_{i:02d}"
        loc = Vector(loc)
        tries = [(rot + dr, loc + Vector((dx, dy, 0.0)))
                 for dx, dy in ((0, 0), (0.06, 0.06), (-0.06, 0.06), (0.06, -0.06))
                 for dr in (0.0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, 2.4, -2.4, 3.0)]
        got = None
        for r, l in tries:
            got = lights.make_candles(name, l, r, count=count, seed=sd, height=height, wax=wax)
            if not _candle_bad(got[0], ped_bvh, angel_bvh):
                break
            for o in got:
                me = o.data
                bpy.data.objects.remove(o)
                bpy.data.meshes.remove(me)
            got = None
        if got is None:                         # keep the preferred spot; report() flags it
            got = lights.make_candles(name, loc, rot, count=count, seed=sd, height=height, wax=wax)
        for o in got[1:]:
            j = _free_index("GLOW_flame_" + dp + "_CandleFlame_{:02d}")
            _set_name(o, f"GLOW_flame_{dp}_CandleFlame_{j:02d}")
        return got

    # black candles on the plinth top, flanking the angel's feet
    objs += candles((-1.42, -1.40, PED_TOP), 0.4, 2, seed + 1, 0.85)
    objs += candles((1.50, -1.40, PED_TOP), -0.9, 1, seed + 2, 0.62)
    # a jack-o'-lantern at the foot of the plinth
    j = _free_index(dp + "_JackOLantern_{:02d}")
    objs += pumpkins.make_jack_o_lantern(f"{dp}_JackOLantern_{j:02d}", (1.72, -3.24, 0.0), 0.30,
                                         size=1.55, face="happy", seed=seed + 11)
    return objs


# --------------------------------------------------------------------------
# Checks (also used by build_statue.py)
# --------------------------------------------------------------------------
def find(objs, sub):
    return next((o for o in objs if sub in o.name), None)


def orb_clearance(objs):
    """Smallest gap between the orb and any stone of the statue (Angel and
    Pedestal), measured both ways: orb vertices -> stone surface and stone
    vertices -> orb surface (facets never hide a closer contact)."""
    orb = next(o for o in objs if o.name.startswith("GLOW_") and o.name.endswith("Orb"))
    t_orb = _world_bvh(orb)
    mo = orb.matrix_world
    opts = [mo @ v.co for v in orb.data.vertices]
    centre = sum(opts, Vector()) / len(opts)
    reach = max((p - centre).length for p in opts) + 0.6
    best = 1e9
    for o in objs:
        if o is orb or o.name.startswith("GLOW_") or "Decor" in o.name:
            continue
        t = _world_bvh(o)
        for p in opts:
            hit = t.find_nearest(p)
            if hit[0] is not None:
                best = min(best, hit[3])
        ms = o.matrix_world
        for v in o.data.vertices:
            p = ms @ v.co
            if (p - centre).length > reach:
                continue
            hit = t_orb.find_nearest(p)
            if hit[0] is not None:
                best = min(best, hit[3])
    return best


def mesh_stats(obj):
    """Topology report: open (boundary) edges, non-manifold edges, wire
    edges, loose verts, islands, closed islands with inward normals."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    bm.normal_update()
    isl = _islands(bm)
    closed = [i for i in isl if all(e.is_manifold for f in i for e in f.edges)]
    st = dict(
        open_edges=sum(1 for e in bm.edges if e.is_boundary),
        nonmanifold=sum(1 for e in bm.edges if len(e.link_faces) > 2),
        wire=sum(1 for e in bm.edges if e.is_wire),
        loose=sum(1 for v in bm.verts if not v.link_faces),
        islands=len(isl),
        closed_islands=len(closed),
        inside_out=sum(1 for i in closed if _signed_volume(i) < -1e-7),
    )
    bm.free()
    return st


def support_problems(objs, tol=0.06):
    """Decor standing on the plinth top must not overhang it: every vertex of
    a candle cluster's bottom (puddle rims) needs stone right under it."""
    ped = find(objs, "_Pedestal")
    if ped is None:
        return []
    t = _world_bvh(ped)
    probs = []
    for o in objs:
        if "Decor" not in o.name or o.name.startswith("GLOW_"):
            continue
        mw = o.matrix_world
        pts = [mw @ v.co for v in o.data.vertices]
        z0 = min(p.z for p in pts)
        if z0 < 0.2:                       # stands on the ground
            continue
        bad = 0
        for p in pts:
            if p.z > z0 + 0.02:
                continue
            hit = t.ray_cast(p + Vector((0, 0, 0.05)), Vector((0, 0, -1)), 0.05 + tol)
            if hit[0] is None:
                bad += 1
        if bad:
            probs.append(f"{o.name}: {bad} bottom vertices overhang the plinth")
    return probs


def report(objs, verbose=True):
    """Print per-object tris / topology and return (total tris, problems)."""
    total = 0
    probs = []
    names = set()
    for o in objs:
        t = common.tri_count(o)
        total += t
        p = common.validate_object(o)
        st = mesh_stats(o)
        if o.name in names:
            p.append(f"duplicate {o.name}")
        names.add(o.name)
        if st["inside_out"]:
            p.append(f"{o.name}: {st['inside_out']} inside-out closed islands")
        if st["wire"] or st["loose"]:
            p.append(f"{o.name}: {st['wire']} wire edges, {st['loose']} loose verts")
        if "Decor" not in o.name and st["nonmanifold"]:
            p.append(f"{o.name}: {st['nonmanifold']} non-manifold edges")
        probs += p
        if verbose:
            print(f"  {o.name:44s} {t:5d} tris  open={st['open_edges']:3d} isl={st['islands']:2d} "
                  f"closed={st['closed_islands']:2d} inv={st['inside_out']}  {'; '.join(p)}")
    lo, hi = world_bounds(objs)
    clr = orb_clearance(objs)
    if total > TRI_BUDGET:
        probs.append(f"total {total} tris > {TRI_BUDGET}")
    if clr < CLEARANCE_MIN:
        probs.append(f"orb clearance {clr:.3f} < {CLEARANCE_MIN}")
    probs += support_problems(objs)
    if verbose:
        print(f"  total {total} tris in {len(objs)} objects (budget {TRI_BUDGET}); bounds "
              f"{tuple(round(x, 2) for x in lo)} .. {tuple(round(x, 2) for x in hi)}")
        print(f"  orb clearance {clr:.3f} (min {CLEARANCE_MIN}, target {CLEARANCE_TARGET})")
    return total, probs


def focus(objs, views_pad=1.04):
    """Frame the statue itself (preview.render's default frames the bbox diagonal)."""
    lo, hi = world_bounds(objs)
    size = hi - lo
    return (lo + hi) / 2, max(size.z * 0.5, size.x * 0.34, size.y * 0.34) * views_pad


def demo(views=("front", "three_quarter", "side", "back"), samples=40, decor=True, res=(960, 600)):
    import preview  # noqa: E402
    common.reset_scene()
    objs = make_angel_statue(with_decor=decor)
    print("=== angel statue ===")
    total, probs = report(objs)
    if probs:
        print("PROBLEMS:", *probs, sep="\n  ")
    if views:
        preview.render("previews/_wip/angel_statue_dev.png", views=list(views), res=res, samples=samples,
                       focus=focus(objs))
    return objs


if __name__ == "__main__":
    _views = ("front", "three_quarter", "side", "back")
    _samples = 40
    _decor = True
    _res = (960, 600)
    for arg in sys.argv[1:]:
        if arg.startswith("--views="):
            _views = tuple(v for v in arg.split("=", 1)[1].split(",") if v)
        elif arg.startswith("--samples="):
            _samples = int(arg.split("=", 1)[1])
        elif arg.startswith("--res="):
            _res = tuple(int(x) for x in arg.split("=", 1)[1].split("x"))
        elif arg == "--no-decor":
            _decor = False
    demo(_views, _samples, _decor, _res)
