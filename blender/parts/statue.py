"""Halloween stone angel statue: a small hooded stone angel ('aniolek') holding
a glowing orb, standing on a lightly weathered, stepped stone pedestal.

A chunky, cartoony low-poly cherub in a long A-line robe with deep irregular
folds (carried round onto the back of the skirt, a short train) and a rope
belt (knot + two short tasselled cords hugging the robe), a dagged shoulder
capelet with a gentle V drape towards the orb, and a big rounded hood with a
soft drooping cowl tip. The head is bowed towards the orb and the face is lost
in the black hood opening, with two faint glowing eyes. Big half-open wings
(leading-edge bone, scalloped covert rows, a fan of rounded flight feathers;
the outer half sweeps back) frame the figure. Two cupped, mitten-like hands
(one closed shell each: palm + three blunt finger lobes wrapping up round the
orb's lower side, thumbs lying low across its front) hold a glowing orange
faceted orb at chest height.

Weathering: chipped robe hem, a nick in the hood brim, snapped outer primaries
on the right wing (the broken tip lies on the front of the base ledge), a dark
crack rising from the robe hem, chipped plinth corners with a broken base
corner, rubble at the foot, soft moss clumps in the creases (step corners,
round the robe hem, the left shoulder), organic glowing cracks running from
the plinth's edges (one continues round a corner) and a dark one at the back.

Everything is built from bmesh / mesh data (no bpy.ops modelling), coloured
through the palette texture (M_Palette) and the glow material (M_Glow).

Public API
----------
* make_angel_statue(prefix="Statue", location=(0, 0, 0), rotation_z=0.0, seed=0,
                    with_decor=True, glowing_eyes=True) -> list[Object]

  Objects (prefix "Statue"), solid parts first:
      Statue_Angel                       stone figure: robe, belt, capelet, hood,
                                         sleeves, cupped hands, wings (one mesh)
      Statue_Pedestal                    stepped plinth + chips, moss, rubble,
                                         dark back crack, the snapped feather tip
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

Scale (1 unit = 1 stud): plinth 2.62 tall / 4.8 wide, angel ~6.6 tall (feet to
hood top), wingspan ~6.7, whole statue ~9.2 tall. ~6810 tris with decor
(budget 7000; any single mesh is far below Roblox's 20000).

Hygiene (checked by report() and build_statue.validate): outward-facing
shells (the only open edges are hidden: the robe's bottom sunk into the plinth,
the robe / sleeve tops inside the capelet and hood); no zero-area triangles; no near-coplanar overlapping faces between
meshes (build.check_coplanar) nor inside one mesh (self_coplanar): stacked
wing layers keep >= 0.03 studs between their faces along the wing normal,
overlapping tubes are turned against each other, branches of a crack rise
steeply out of the stone.

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
from mathutils.geometry import delaunay_2d_cdt, tessellate_polygon  # noqa: E402
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
HEAD_TILT = math.radians(-5)        # gentle sideways tilt
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

    def ico(self, co, size, rnd=None, jitter=0.0, rot_z=0.0, key=None, flat_bottom=None, align=None):
        """Low icosphere (20 faces) scaled by ``size`` (x, y, z); ``align``:
        turn its local z axis onto this direction (a surface normal)."""
        res = bmesh.ops.create_icosphere(self.bm, subdivisions=1, radius=1.0)
        vs = res["verts"]
        for vert in vs:
            if rnd is not None and jitter:
                vert.co *= 1.0 + rnd.uniform(-jitter, jitter)
            if flat_bottom is not None and vert.co.z < 0:
                vert.co.z *= flat_bottom
        rot = Matrix.Rotation(rot_z, 4, "Z")
        if align is not None:
            rot = Vector((0, 0, 1)).rotation_difference(Vector(align)).to_matrix().to_4x4() @ rot
        m = Matrix.Translation(co) @ rot @ Matrix.Diagonal((size[0], size[1], size[2], 1.0))
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
        # collapse sub-millimetre edges left by close bisect cuts (no zero-area
        # or sliver triangles after triangulation)
        bmesh.ops.dissolve_degenerate(bm, dist=5e-4, edges=bm.edges[:])
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
ROBE_TRAIN = 0.30                   # back hem pools out (short train, visible from behind)
# z, rx, ry, fold envelope. A clear A-line: the skirt flares ~18 % wider towards
# the hem than the upper body, so the figure stands stable on the plinth.
ROBE_PROFILE = [
    (0.00, 1.84, 1.47, 1.00),
    (0.15, 1.88, 1.51, 1.00),
    (0.45, 1.79, 1.44, 0.96),
    (1.00, 1.62, 1.33, 0.82),
    (1.65, 1.44, 1.21, 0.60),
    (2.30, 1.30, 1.09, 0.36),
    (2.90, 1.20, 0.95, 0.15),
    (3.45, 1.14, 0.87, 0.04),       # slimmer chest: no 'pot belly' between capelet and orb
    (3.95, 1.12, 0.83, 0.0),
    (4.30, 1.04, 0.79, 0.0),        # ends (open) inside the capelet: nothing above shows
]
ROBE_DEEP = 0.08                    # fold value below this (x envelope) -> dark crease


def _robe_folds(rnd):
    """(angle, amplitude, half-width): ridges (+) and deep valleys (-).
    Angle 0 = front (-Y), positive towards +X. The two side ridges and the
    two ridges either side of the back crease are the strongest: they kick
    the hem out and carry the folds round onto the back of the skirt."""
    folds = []
    for a, k in ((-168, 1.30), (-130, 1.25), (-86, 1.30), (-54, 1.0), (-12, 0.9), (14, 0.9),
                 (54, 1.0), (90, 1.25), (128, 1.25), (162, 1.30)):
        folds.append((math.radians(a + rnd.uniform(-5, 5)), 0.13 * k * rnd.uniform(0.9, 1.1),
                      math.radians(rnd.uniform(14, 19))))
    # deep creases between ridges: front-left, front-right, both sides, back
    for a, amp, w in ((-33, -0.19, 11), (33, -0.17, 11), (-108, -0.15, 10), (109, -0.14, 10),
                      (-148, -0.15, 10), (146, -0.14, 10), (180, -0.16, 10)):
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
    ry = ry + ROBE_TRAIN * back ** 1.6 * max(0.0, 1.0 - z / 1.2) ** 1.6
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
        m = (Matrix.Translation((0.34 * side, -1.50, 0.0))
             @ Matrix.Rotation(-0.25 * side, 4, "Z")
             @ Matrix.Diagonal((0.24, 0.34, 0.20, 1.0)))
        bmesh.ops.transform(P.bm, matrix=m, verts=res["verts"])
        for vert in P.bm.verts:            # flatten the bottom onto the plinth
            vert.co.z = max(vert.co.z, -0.02)
        parts.append(P)
    return parts


def _vein(P, paths, mapper, out, depth_in, key=None, corner_s=None, max_seg=None):
    """Organic crack strips (closed thin prisms) on a surface.

    paths: [(pts2d, w0, w1, root)] in the surface's 2D chart; widths taper
    from w0 to w1 along the path. root=True makes a branch: its first point
    starts below the surface and rises out of it, so it never lies flat on
    the strip it forks from (no coplanar overlap at the junction).
    mapper(q2d) -> (surface point, outward unit normal): each outline point
    is projected onto the real (jittered) surface, then pushed ``out`` along
    the normal (front) and ``depth_in`` into it (back).
    corner_s: chart u-values of convex corners; a vertex is inserted where a
    path crosses one so the strip never cuts through the corner.
    max_seg: split longer segments, so the strip follows a curved / folded
    surface instead of cutting chords through its ridges.
    Explicit quads (front, back, sides, end caps): no long concave n-gon."""
    for pts2d, w0, w1, root in paths:
        pts = [Vector(q) for q in pts2d]
        if root:     # rise out of the stone steeply (never near-parallel to it)
            pts.insert(1, pts[0].lerp(pts[1], min(0.5, 0.04 / max((pts[1] - pts[0]).length, 1e-6))))
        if corner_s:
            ins = [pts[0]]
            for qa, qb in zip(pts, pts[1:]):
                lo, hi = sorted((qa.x, qb.x))
                for c in sorted(corner_s, key=lambda c: (c - qa.x) * (1 if qb.x >= qa.x else -1)):
                    if lo + 1e-4 < c < hi - 1e-4:
                        ins.append(qa.lerp(qb, (c - qa.x) / (qb.x - qa.x)))
                ins.append(qb)
            pts = ins
        if max_seg:
            ins = [pts[0]]
            for qa, qb in zip(pts, pts[1:]):
                k = int((qb - qa).length / max_seg)
                ins += [qa.lerp(qb, (j + 1) / (k + 1)) for j in range(k)] + [qb]
            pts = ins
        n = len(pts)
        cum = [0.0]
        for qa, qb in zip(pts, pts[1:]):
            cum.append(cum[-1] + (qb - qa).length)
        rails = []
        for i in range(n):
            t = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized()
            sd = Vector((-t.y, t.x))
            f = cum[i] / max(cum[-1], 1e-9)
            w = _lerp(w0, w1, f) * 0.5
            o = out if not (root and i == 0) else -0.012
            row = []
            for q in (pts[i] + sd * w, pts[i] - sd * w):
                loc, nrm = mapper(q)
                row.append((P.v(loc + nrm * o), P.v(loc - nrm * depth_in)))
            rails.append(row)
        L = [r[0] for r in rails]
        R = [r[1] for r in rails]
        for i in range(n - 1):
            P.face((L[i][0], R[i][0], R[i + 1][0], L[i + 1][0]), key)       # front
            P.face((L[i][1], L[i + 1][1], R[i + 1][1], R[i][1]), key)       # back
            P.face((L[i][0], L[i + 1][0], L[i + 1][1], L[i][1]), key)       # left side
            P.face((R[i][0], R[i][1], R[i + 1][1], R[i + 1][0]), key)       # right side
        P.face((L[0][0], L[0][1], R[0][1], R[0][0]), key)
        P.face((L[-1][0], R[-1][0], R[-1][1], L[-1][1]), key)


ROBE_CHART_R = 1.70                 # robe chart: u = angle * ROBE_CHART_R (studs), z = design z


def _robe_mapper(robe_bvh):
    """Chart (u, z) on the robe -> point on the real lofted (jittered) robe
    surface and its face normal, by a horizontal ray towards the robe axis."""
    def mapper(q):
        a = q[0] / ROBE_CHART_R
        z = _skirt_z(q[1])
        d = Vector((math.sin(a), -math.cos(a), 0.0))
        hit = robe_bvh.ray_cast(d * 3.5 + Vector((0, 0, z)), -d, 3.5)
        if hit[0] is None:
            loc = robe_bvh.find_nearest(d * 1.6 + Vector((0, 0, z)))[0]
            return loc, d
        return hit[0], hit[1]
    return mapper


def _build_robe_crack(robe_bvh):
    """Dark hairline crack in the front-left of the skirt: it starts at the hem
    and wanders up and a little sideways (uneven steps, small drifting jogs,
    one bigger kink), with one short branch."""
    P = _Part("angel")
    R = ROBE_CHART_R
    main = [(-0.98, 0.10), (-0.97, 0.24), (-0.93, 0.45), (-0.92, 0.57), (-0.85, 0.80), (-0.83, 0.97),
            (-0.79, 1.12), (-0.76, 1.36), (-0.74, 1.47)]
    branch = [(-0.92, 0.57), (-1.00, 0.67), (-1.05, 0.74), (-1.11, 0.79)]
    paths = [([(a * R, z) for a, z in main], 0.065, 0.03, False),
             ([(a * R, z) for a, z in branch], 0.045, 0.02, True)]
    _vein(P, paths, _robe_mapper(robe_bvh), 0.04, 0.07, "charcoal", max_seg=0.16)
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
    # two short, thick cords hanging close to the robe from the knot, each
    # ending in a knob and a short flared tassel
    for ci, (da, zd_end, lean) in enumerate(((-0.09, 1.50, -0.02), (0.08, 1.70, 0.02))):
        cp = []
        for t in (0.0, 0.35, 0.7, 1.0):
            zd = _lerp(BELT_Z - 0.06, zd_end, t)
            p = _robe_outer(ak + da * t + lean * t * t, zd, folds, 0.055 + 0.01 * t, 0.25)
            p.z = _skirt_z(zd)
            cp.append(p)
        tang = Vector((math.cos(ak), math.sin(ak), 0.0))      # along the robe at the knot
        cp[0] = kc + tang * (0.09 if da > 0 else -0.09) - Vector((0, 0, 0.11))   # out of the knot's bottom
        # cord, knob and flared tassel as ONE closed tube (no overlapping pieces)
        end = cp[-1]
        out = Vector((end.x, end.y, 0.0)).normalized()
        path = cp + [end + out * 0.012 - Vector((0, 0, z)) for z in (0.05, 0.13, 0.17, 0.27, 0.37)]
        radii = [0.085, 0.08, 0.08, 0.08, 0.115, 0.11, 0.07, 0.11, 0.13]
        _tube(P, path, radii, 5 + ci, phase=0.3 + 0.5 * ci)
    return P


# --------------------------------------------------------------------------
# Capelet (short shoulder cape under the hood) with a dagged hem: six rounded
# dags and a small point at the back
# --------------------------------------------------------------------------
def _dag(a):
    # six rounded dags with one pointing down at the front centre, plus a
    # gentle V drape towards the front: the hem is no straight ledge across
    # the chest and leads the eye down to the orb
    scal = 0.5 + 0.5 * math.cos(6 * a)
    front = max(0.0, math.cos(a)) ** 2
    back = max(0.0, -math.cos(a)) ** 14         # small point at the back
    return 0.11 * scal + 0.10 * front + 0.05 * back


def _build_capelet(rnd):
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
        ring(4.74, 0.98, 0.80, 0.02),
        ring(4.42, 1.22, 0.96, 0.05, 0.25),
        ring(4.12, 1.35, 0.99, 0.08, 0.8),
        ring(4.00, 1.38, 1.00, 0.09, 1.0),   # hem edge
        ring(4.05, 1.24, 0.89, 0.07, 1.0),   # underside
        ring(4.22, 1.05, 0.77),
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
HOOD_TIP_SWING = math.radians(30)


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
    # soft cowl point: the fabric gathers into a short drooping tip at the back
    # of the crown (the rings converge on its end, not on a flat back pole)
    tip = Vector((0.0, math.cos(HOOD_TIP_SWING), math.sin(HOOD_TIP_SWING)))
    k = max(0.0, d.dot(tip) - 0.80) / 0.20
    p += Vector((0.0, 0.88, -0.48)).normalized() * 0.30 * k ** 2.0 * r
    # seam ridge from the brim over the top and down the back, plus two soft
    # fabric folds fanning out from the cowl tip over the back of the hood
    # (both fade out before the cowl tip, so the rings converge smoothly there:
    # no star-shaped pinch and no crease down the back)
    fade = 1.0 - min(1.0, max(0.0, (phi - math.radians(132)) / math.radians(28)))
    if phi > math.radians(56):
        seam = max(0.0, math.cos(psi - math.pi / 2)) ** 24
        p += d * 0.08 * seam * fade * r
    if phi > math.radians(115):
        fold = sum(max(0.0, math.cos(psi - math.radians(a))) ** 30 for a in (52, 128))
        p += d * 0.06 * fold * min(1.0, (phi - math.radians(115)) / math.radians(30)) * fade * r
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
    return [Vector((0.68 * s, 0.05, 4.22)),
            Vector((1.07 * s, 0.02, 3.94)),
            Vector((1.27 * s, -0.27, 3.46)),
            Vector((1.27 * s, -0.72, 3.10)),
            Vector((1.14 * s, -1.06, 2.98)),
            Vector((0.95 * s, -1.19, 3.01))]


SLEEVE_RU = [0.36, 0.39, 0.41, 0.40, 0.35, 0.30]   # upper half: tightens round the wrist
SLEEVE_RL = [0.36, 0.39, 0.42, 0.43, 0.44, 0.43]   # lower half: the hanging drape
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


def _build_sleeve(side):
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


HAND_R = (HAND_IN + HAND_OUT) / 2
# Hand outline on the orb, (psi, theta) in degrees for the right hand (psi
# mirrored for the left one; psi -90 = front, theta 180 = bottom): the palm
# hugs the orb's lower outer side, three blunt finger lobes rise up round its
# lower side (staggered: the front one longest), notches between them.
_HAND_OUTLINE = [(-64, 168), (-67, 150), (-64, 133), (-60, 119), (-55, 112), (-48, 109), (-41, 111),
                 (-37, 118), (-35, 127), (-31, 119), (-26, 115), (-20, 116), (-16, 121), (-14, 130),
                 (-10, 124), (-5, 121), (1, 123), (5, 129), (12, 135), (26, 135), (38, 137), (40, 152),
                 (36, 167), (0, 172)]
# finger centre lines (base -> tip): a knuckle ridge runs along each
_FINGERS = [((-59, 152), (-48, 113)), ((-37, 152), (-25, 118)), ((-15, 152), (-4, 124))]


def _hand_chart(p, t):
    t = math.radians(t)
    return (math.radians(p) * HAND_R * math.sin(t), -t * HAND_R)


def _hand_unchart(x, y):
    t = -y / HAND_R
    return math.degrees(x / (HAND_R * math.sin(t))), math.degrees(t)


def _seg_dist(q, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (q - a).dot(ab) / max(ab.length_squared, 1e-12)))
    return (q - (a + ab * t)).length


def _build_hand(side):
    """One cupped hand as a single closed shell (no overlapping parts inside
    the hand): a thick plate hugging the orb's lower outer side whose top
    edge forms three blunt, mitten-like finger lobes wrapping up round the
    orb's side, with a knuckle ridge along each finger; a thumb lying low
    across the front of the orb; a thick wrist filling the sleeve cuff."""
    parts = []
    P = _Part("angel")
    fingers = [(Vector(_hand_chart(*a)), Vector(_hand_chart(*b))) for a, b in _FINGERS]

    def to3d(x, y, w):
        p, t = _hand_unchart(x, y)
        r = HAND_R + w
        if w > 0:                                    # knuckle ridges on the back of the hand
            q = Vector((x, y))
            r += max(0.045 * max(0.0, 1.0 - _seg_dist(q, a, b) / 0.13) ** 1.5 for a, b in fingers)
        return _orb_pt(t, p, side, r)

    outline = _ccw([_hand_chart(p, t) for p, t in _HAND_OUTLINE])
    extra = [a.lerp(b, f) for a, b in fingers for f in (0.25, 0.55, 0.82)]
    _slab(P, outline, to3d, HAND_OUT - HAND_IN, 0.04, extra=extra)
    parts.append(P)

    # thumb lying low across the front of the orb, tip pointing to the middle
    T = _Part("angel")
    rt = HAND_OUT + 0.035
    th_pts = [_orb_pt(151, -44, side, rt), _orb_pt(148, -60, side, rt),
              _orb_pt(145, -74, side, rt - 0.015), _orb_pt(144, -79, side, rt - 0.045)]
    _tube(T, th_pts, [0.12, 0.115, 0.1, 0.055], 6, phase=0.2)
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
WING_ROOT = (0.36, 0.82, 4.62)
WING_YAW = math.radians(-13)        # the inner half points slightly forward of the X axis: the
                                    # far wing still shows beside the body in three-quarter views
WING_SCALE = 0.86
WING_SPREAD = 0.90                  # extra horizontal scale (wingspan ~6.8)
WING_CUP = 0.04                     # the whole wing curls back a little
WING_SWEEP = 0.20                   # ... and its outer half sweeps clearly back (+Y): from the
WING_SWEEP_U0 = 1.10                # side the wing trails behind the back instead of a flat slab
# Layer depths along the wing normal (w, + = front). Overlapping layers keep
# their front faces and their back faces >= 0.03 apart (no coplanar faces =
# no depth flicker in Roblox): top coverts [-0.15, 0.21], middle coverts
# [-0.09, 0.13], flight feathers stepping back by 0.045 from [-0.215, -0.045]
# (the margins cover the chord error of flat triangles on the curved wing).
W_TOP = (0.03, 0.36)                # (centre, thickness)
W_MID = (0.02, 0.22)
W_FEATHER0, W_FEATHER_STEP, W_FEATHER_T = -0.13, 0.045, 0.17

_WING_BONE = [(0.02, 0.18), (0.34, 0.88), (0.92, 1.52), (1.66, 1.92), (2.45, 2.02), (3.05, 1.82),
              (3.42, 1.42)]
_WING_BONE_R = [0.17, 0.165, 0.155, 0.145, 0.13, 0.11, 0.085]
_COVERT_TOP_EDGE = [(3.45, 1.30), (3.08, 1.72), (2.45, 1.92), (1.66, 1.82), (0.92, 1.42), (0.36, 0.80),
                    (0.02, 0.12)]
_COVERT_MID_EDGE = [(3.62, 0.72), (3.30, 1.25), (2.50, 1.55), (1.50, 1.42), (0.65, 0.96), (-0.18, 0.20)]
# flight feather tips (wing u, v) from the body outwards, and their bases
_FEATHER_TIPS = [(0.20, -1.55), (0.76, -1.96), (1.36, -2.12), (1.98, -2.08), (2.56, -1.84),
                 (3.06, -1.44), (3.46, -0.88), (3.72, -0.22)]
_FEATHER_BASE = ((0.42, 0.12), (3.02, 1.10))


def _wing_mapper(side):
    root = Vector((WING_ROOT[0] * side, WING_ROOT[1], WING_ROOT[2]))
    U = Vector((math.cos(WING_YAW) * side, math.sin(WING_YAW), 0.0))
    V = Vector((0.14 * side, 0.24, 1.0)).normalized()          # leans out and clearly back
    W = U.cross(V).normalized() * side       # towards the front (-Y)

    def surf(u, v):
        u *= WING_SCALE * WING_SPREAD
        v *= WING_SCALE
        # cup the wing: the outer half sweeps back, the top rolls forward a bit
        w_off = (-WING_CUP * u * u - WING_SWEEP * max(0.0, u - WING_SWEEP_U0) ** 2
                 + 0.05 * max(v, 0.0))
        return root + U * u + V * v + W * w_off

    def to3d(u, v, w):
        """Wing chart point (u, v) pushed w along the curved wing surface's
        own normal, so stacked layers keep their spacing where the wing
        sweeps back (an offset along a fixed axis would shrink there)."""
        h = 0.01
        n = (surf(u + h, v) - surf(u - h, v)).cross(surf(u, v + h) - surf(u, v - h)).normalized()
        if n.dot(W) < 0:
            n = -n
        return surf(u, v) + n * w
    return to3d


def _slab_tris(inner, stretch=3.0, extra=()):
    """Triangles (counter-clockwise in the chart) filling a concave 2D outline
    (+ optional interior points): a constrained Delaunay triangulation with u
    stretched, so the triangles stay narrow along u. The wing only curves
    along u (sweep), so narrow triangles keep their chords close to the curved
    surface and clear of the neighbouring feather layers. Returns
    (points, triangles) with the outline points first."""
    n = len(inner)
    pts = [Vector(q) for q in inner] + [Vector(q) for q in extra]
    cdt = delaunay_2d_cdt([Vector((q.x * stretch, q.y)) for q in pts], [], [list(range(n))], 1, 1e-7)
    vmap = [o[0] if len(o) else None for o in cdt[3]]
    tris = []
    for t in cdt[2]:
        idx = [vmap[i] for i in t]
        if None in idx or len(idx) != 3:          # should not happen: fall back
            pts = pts[:n]
            tris = [tuple(t) for t in tessellate_polygon([[Vector((q.x, q.y, 0.0)) for q in pts]])]
            break
        tris.append(tuple(idx))
    out = []
    for a, b, c in tris:
        if (pts[b] - pts[a]).cross(pts[c] - pts[a]) < 0:
            a, b = b, a
        out.append((a, b, c))
    return pts, out


def _ccw(poly):
    area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly)))
    return list(poly) if area > 0 else list(reversed(poly))


def _slab(P, outline, to3d, t, bevel, w0=0.0, key=None, extra=()):
    """Thick bevelled plate from a CCW chart outline: front (w0 + t/2) and back
    (w0 - t/2) on the inset outline, the outline itself at w0. ``extra``:
    interior chart points used by the front triangulation only (relief)."""
    inner = _offset_poly(outline, bevel)
    front = [P.v(to3d(u, v, w0 + t / 2)) for u, v in inner]
    mid = [P.v(to3d(u, v, w0)) for u, v in outline]
    back = [P.v(to3d(u, v, w0 - t / 2)) for u, v in inner]
    P.bridge(front, mid, key)
    P.bridge(mid, back, key)
    # front and back are triangulated in the flat 2D outline, not in 3D (on
    # the swept, curved wing a 3D n-gon triangulation can fold triangles over)
    pts, tris = _slab_tris(inner, extra=extra)
    for q in pts[len(inner):]:
        front.append(P.v(to3d(q.x, q.y, w0 + t / 2)))
    for a, b, c in tris:
        P.face((front[a], front[b], front[c]), key)
    if extra:
        pts, tris = _slab_tris(inner)
    for a, b, c in tris:
        P.face((back[c], back[b], back[a]), key)


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
    the tip more pointed (primaries). 7 points (cheap: 16 of them per angel)."""
    h = Wd / 2
    b = 0.66 - 0.30 * point
    return [(h * 0.78, 0.0), (h * 0.98, 0.60 * L), (h * b, 0.88 * L), (0.0, L),
            (-h * b, 0.88 * L), (-h * 0.98, 0.60 * L), (-h * 0.78, 0.0)]


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
    _slab(A, outline, to3d, W_TOP[1], 0.12, w0=W_TOP[0])
    parts.append(A)
    # middle coverts: bigger scallops overlapping the feather bases
    B = _Part("wing")
    outline = _scallops((-0.08, -0.42), (3.50, 0.30), 5, 0.23, rnd) + _COVERT_MID_EDGE
    _slab(B, outline, to3d, W_MID[1], 0.11, w0=W_MID[0])
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
        w0 = W_FEATHER0 - W_FEATHER_STEP * k
        _slab(F, fo, to3d, W_FEATHER_T, 0.05, w0=w0)
        if snapped and k >= n - 4:
            # the four outer primaries snapped off (35-50 % of their length
            # gone): a jagged, stepped break that clearly shortens this wing's
            # outer edge; the raw facets keep the wing's normal shading so they
            # read as broken stone, not as holes
            dirv = d.normalized()
            frac, t1, t2 = {n - 1: (0.50, 0.60, -0.50), n - 2: (0.60, -0.55, 0.45),
                            n - 3: (0.55, 0.50, -0.60), n - 4: (0.66, -0.45, 0.55)}[k]
            cut = base + d * frac
            for tilt, off in ((t1, 0.0), (t2, 0.08)):
                cdir = _rot2(dirv, tilt)
                c2 = cut + dirv * off
                co = to3d(c2.x, c2.y, w0)
                co_n = to3d(c2.x + cdir.x, c2.y + cdir.y, w0) - co
                F.chop(co, co_n, key="auto")
        parts.append(F)
    return parts


def _shoulder_moss(parts, rnd):
    """A small moss patch on the left shoulder, in the crease where the hood's
    flare meets the capelet: cushions placed on the real surface (rays from
    the side) and aligned with it."""
    trees = [BVHTree.FromBMesh(Q.bm) for Q in parts]
    P = _Part("angel")
    for (y, z), sz in (((-0.08, 4.44), 0.18), ((0.14, 4.52), 0.15), ((-0.26, 4.66), 0.13)):
        o = Vector((-3.0, y, z))
        hits = [t.ray_cast(o, Vector((1, 0, 0)), 3.0) for t in trees]
        hits = [h for h in hits if h[0] is not None]
        if hits:
            loc, nrm = min(hits, key=lambda h: h[3])[:2]
            if nrm.x > 0:                      # unfinished part: face winding may point inwards
                nrm = -nrm
            _moss_lump(P, loc - nrm * 0.01, sz, rnd, flat=0.55, normal=nrm)
    return P


def _snapped_fragment():
    """The broken primary tip lying on the front of the plinth's base ledge."""
    P = _Part("ped")
    L, Wd = 0.56, 0.26
    out = _feather_outline(L, Wd, 0.8)[1:-1]
    out = [(x, y - 0.12 * L) for x, y in out]
    out.append((0.0, 0.0))
    co = Vector((0.36, -2.09, 0.50 * PED_ZS))

    def to3d(u, v, w):
        p = _rot2((u, v), math.radians(-84))
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


def _moss_lump(P, co, size, rnd, flat=0.45, normal=None, turn=None):
    """Soft, lumpy moss cushion (moss_dark) with lighter tufts (stem_dark) on
    top so it stays readable at night; its flattened bottom sinks into the
    stone it grows on (``normal``: the stone's surface normal, default up)."""
    up = Vector(normal).normalized() if normal is not None else Vector((0, 0, 1))
    rz = rnd.uniform(0, TAU)
    faces = P.ico(co, (size, size * rnd.uniform(0.7, 0.9), size * flat), rnd=rnd, jitter=0.16,
                  rot_z=rz if turn is None else turn + rnd.uniform(-0.3, 0.3), key="moss_dark",
                  flat_bottom=0.7, align=None if normal is None else up)
    for f in faces:
        c = f.calc_center_median()
        if (c - Vector(co)).dot(up) > size * flat * 0.35 and rnd.random() < 0.55:
            f[P.pk] = _KID["stem_dark"]


def _moss_clump(P, pts, size, rnd, flat=0.45, normal=None):
    """A clump of 2-4 overlapping cushions laid along a crease or edge. The
    cushions differ in size, flatness and turn (golden-angle steps), so two of
    them do not end up with coincident faces."""
    for i, co in enumerate(pts):
        _moss_lump(P, Vector(co), size * (1.0 - 0.16 * i) * rnd.uniform(0.92, 1.08),
                   rnd, flat * (1.0, 0.8, 1.18, 0.9)[i % 4], normal=normal, turn=2.4 * i)


def _rubble(P, co, sz, rnd, key=None):
    """A broken-off chunk resting at height co.z (the part is cut flat at that
    height afterwards with P.chop)."""
    return P.ico(Vector(co) + Vector((0, 0, sz * 0.35)), (sz * rnd.uniform(0.85, 1.25),
                 sz * rnd.uniform(0.75, 1.1), sz * rnd.uniform(0.55, 0.8)),
                 rnd=rnd, jitter=0.2, rot_z=rnd.uniform(0, TAU), key=key)


# Shaft chart for the cracks: u = distance along the shaft's chamfered-square
# perimeter (0 = front centre, increasing towards +X, i.e. front -> right ->
# back -> left), measured on a reference half width; z = design height. A
# crack can therefore run round a corner onto the next face.
SHAFT_REF = 1.78
CHAM = 0.16                         # _sq_ring chamfer fraction


def _shaft_hw(z):
    """Half width of the shaft's flat panel at design height z."""
    return _lerp(1.82, 1.74, min(max((z - 0.69) / 1.11, 0.0), 1.0))


def _octagon(hw):
    c = hw * CHAM
    pts = [(0.0, -hw), (hw - c, -hw), (hw, -hw + c), (hw, hw - c), (hw - c, hw), (-hw + c, hw),
           (-hw, hw - c), (-hw, -hw + c), (-hw + c, -hw), (0.0, -hw)]
    edges, s0 = [], 0.0
    for p, q in zip(pts, pts[1:]):
        p, q = Vector(p), Vector(q)
        L = (q - p).length
        d = (q - p) / L
        edges.append((s0, L, p, d, Vector((d.y, -d.x))))   # outward normal (CCW from above)
        s0 += L
    return edges, s0


def _shaft_corners(span=2):
    edges, per = _octagon(SHAFT_REF)
    return [e[0] + k * per for e in edges[1:] for k in range(-span, span + 1)]


def _shaft_mapper(bvh):
    """Chart (u, z) -> point on the real (jittered, chipped) shaft and the
    outward normal of the ideal face there (corner bisector on a corner)."""
    def mapper(q):
        z = q[1]
        hw = _shaft_hw(z)
        edges, per = _octagon(hw)
        u = (q[0] * hw / SHAFT_REF) % per
        for i, (s0, L, p0, d, nrm) in enumerate(edges):
            if u <= s0 + L + 1e-9 or i == len(edges) - 1:
                t = u - s0
                p = p0 + d * t
                n = nrm
                if t < 2e-3 and i > 0:
                    n = (nrm + edges[i - 1][4]).normalized() / math.cos(math.radians(22.5))
                elif L - t < 2e-3 and i < len(edges) - 1:
                    n = (nrm + edges[i + 1][4]).normalized() / math.cos(math.radians(22.5))
                break
        N = Vector((n.x, n.y, 0.0))
        base = Vector((p.x, p.y, z * PED_ZS))
        nd = N.normalized()
        hit = bvh.ray_cast(base + nd * 0.6, -nd, 1.2)
        if hit[0] is None or hit[1].dot(nd) < 0.75:          # missed / hit a chip
            return bvh.find_nearest(base)[0], N
        return hit[0], N
    return mapper


def _build_pedestal(rnd):
    base = _tier(rnd, [(2.40, 0.0), (2.40, 0.36), (2.26, 0.50)])
    shaft = _tier(rnd, [(1.95, 0.44), (1.95, 0.60), (1.82, 0.69), (1.74, 1.80),
                        (1.88, 1.88), (1.95, 1.98)])
    cap = _tier(rnd, [(2.12, 1.93), (2.22, 2.02), (2.22, 2.33), (2.10, PED_DESIGN)])

    # chips: flat facets cutting corners and edges
    base.chop((-2.40, -1.95, 0.50), (-0.55, -0.62, 0.56))        # broken front-left corner
    base.chop((-2.18, -2.40, 0.46), (-0.15, -0.72, 0.68))
    base.chop((2.40, 1.70, 0.30), (0.62, 0.55, 0.30))           # back-right corner
    base.chop((1.10, -2.40, 0.47), (0.0, -0.55, 0.83))          # worn front edge
    cap.chop((1.98, -2.22, 2.45), (0.50, -0.55, 0.67))          # front-right top corner
    cap.chop((-2.22, 1.93, 2.40), (-0.62, 0.48, 0.62))          # back-left top corner
    cap.chop((-0.45, -2.22, 2.06), (0.0, -0.70, -0.71))         # worn edge under the front
    shaft.chop((1.74, -1.62, 1.20), (0.70, -0.70, 0.12))        # front-right shaft edge

    # moss: a few soft clumps in the creases (step corners where the ledge
    # meets the shaft, round the robe hem on the plinth top, at the foot)
    ledge = 0.50
    moss = _Part("ped")
    for pts, sz in (([(-1.48, -1.99, ledge), (-1.22, -2.03, ledge), (-1.00, -1.98, ledge)], 0.25),
                    ([(1.99, 1.00, ledge), (2.02, 1.30, ledge)], 0.25),
                    ([(0.62, 1.99, ledge), (0.90, 2.02, ledge)], 0.22),
                    ([(-2.00, -0.20, ledge)], 0.22),
                    ([(-2.05, -2.16, 0.02), (-2.32, -1.92, 0.02)], 0.28),
                    ([(2.46, 0.35, 0.02)], 0.25)):
        _moss_clump(moss, pts, sz, rnd)
    # moss growing where the robe meets the plinth top
    for a_deg, sz, n in ((210, 0.28, 2), (240, 0.22, 1), (122, 0.25, 2), (60, 0.18, 1)):
        pts = []
        for j in range(n):
            a = math.radians(a_deg + 11 * j + rnd.uniform(-3, 3))
            r = 1.0 / math.sqrt((math.sin(a) / 1.90) ** 2 + (math.cos(a) / 1.52) ** 2)
            if math.cos(a) < 0:
                r += 0.10                                    # clear of the robe's back train
            pts.append((math.sin(a) * r, -math.cos(a) * r, PED_DESIGN))
        _moss_clump(moss, pts, sz, rnd, flat=0.5)
    moss.chop((0, 0, 0), (0, 0, -1), key="moss_dark")     # ground clumps sit flat on z = 0
    for P in (base, shaft, cap, moss):
        P.transform(Matrix.Diagonal((1.0, 1.0, PED_ZS, 1.0)))
    shaft_bvh = BVHTree.FromBMesh(shaft.bm)
    # dark hairline crack on the back of the shaft: from under the cap, down
    # towards the back-left corner, with one short branch
    dark = _Part("ped")
    _vein(dark, [([(7.78, 1.79), (7.82, 1.66), (7.93, 1.55), (7.97, 1.40), (8.10, 1.28), (8.14, 1.12),
                   (8.22, 1.03), (8.25, 0.90)], 0.06, 0.025, False),
                 ([(7.97, 1.40), (7.88, 1.31), (7.84, 1.20), (7.77, 1.12)], 0.035, 0.015, True)],
          _shaft_mapper(shaft_bvh), 0.04, 0.06, "charcoal", corner_s=_shaft_corners())
    # chunks broken off the plinth at its foot (laid out point-symmetrically so the
    # bbox, and with it the origin, stays centred on the plinth axis)
    rubble = _Part("ped")
    for co, sz in (((-2.62, -2.28, 0.0), 0.21), ((-2.86, -1.72, 0.0), 0.13),
                   ((2.62, 2.28, 0.0), 0.19), ((2.84, 1.62, 0.0), 0.12)):
        _rubble(rubble, co, sz, rnd)
    rubble.chop((0, 0, 0), (0, 0, -1), key="ash")          # flat bottoms on the ground
    # pebbles on the base ledge (sunk a hair into it)
    pebbles = _Part("ped")
    zl = 0.50 * PED_ZS - 0.01
    for co, sz in (((-2.14, -1.05, zl), 0.09), ((-0.30, -2.12, zl), 0.08)):
        _rubble(pebbles, co, sz, rnd, key="stone_light")
    pebbles.chop((0, 0, zl), (0, 0, -1), key="ash")
    return [base, shaft, cap, moss, dark, rubble, pebbles, _snapped_fragment()], shaft_bvh


def _build_cracks(shaft_bvh):
    """Glowing cracks in the shaft, anchored to the stone: one runs from under
    the cap's front edge down towards the lower-left corner (two short
    branches); one rises from the plinth band near the front-right corner
    and continues round the corner onto the right face up to the cap; one
    rises from the band on the left face near its front corner."""
    P = _Part("angel")
    paths = [
        ([(-0.50, 1.79), (-0.53, 1.68), (-0.61, 1.52), (-0.63, 1.44), (-0.78, 1.30), (-0.84, 1.07),
          (-0.97, 0.96), (-1.03, 0.86), (-1.18, 0.78), (-1.26, 0.71)], 0.095, 0.035, False),
        ([(-0.78, 1.30), (-0.70, 1.19), (-0.67, 1.05), (-0.59, 0.95)], 0.055, 0.02, True),
        ([(-0.61, 1.52), (-0.74, 1.49), (-0.85, 1.51), (-0.93, 1.46)], 0.045, 0.018, True),
        ([(1.02, 0.71), (1.10, 0.84), (1.16, 0.98), (1.30, 1.15), (1.42, 1.25), (1.70, 1.33),
          (1.98, 1.42), (2.12, 1.55), (2.33, 1.63), (2.45, 1.79)], 0.085, 0.03, False),
        ([(1.30, 1.15), (1.22, 1.29), (1.18, 1.40), (1.09, 1.48)], 0.05, 0.018, True),
        ([(-2.05, 0.71), (-2.12, 0.84), (-2.30, 0.93), (-2.38, 1.06), (-2.56, 1.19), (-2.64, 1.27),
          (-2.84, 1.35), (-2.98, 1.50)], 0.08, 0.025, False),
        ([(-2.56, 1.19), (-2.68, 1.11), (-2.77, 1.07), (-2.86, 0.98)], 0.045, 0.018, True),
    ]
    _vein(P, paths, _shaft_mapper(shaft_bvh), CRACK_PROUD, 0.09, corner_s=_shaft_corners())
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
    robe.chop((1.10, -1.30, 0.10), (0.55, -0.70, -0.45))   # chipped hem, front-right
    robe.chop((-1.62, 0.60, 0.12), (-0.80, 0.35, -0.50))   # chipped hem, left side
    robe_bvh = BVHTree.FromBMesh(robe.bm)
    lower = [robe] + _build_toes() + [_build_robe_crack(robe_bvh), _build_belt(rnd, folds)]
    upper = [_build_capelet(rnd)]
    hood = _build_hood(rnd)
    hood.chop(HOOD_C + Vector((0.78, -0.70, 0.30)), (0.62, -0.62, 0.48))  # nick in the brim
    hood.transform(head)
    upper.append(hood)
    for side in (-1, 1):
        upper.append(_build_sleeve(side))
        upper += _build_hand(side)
    for side in (-1, 1):
        upper += _build_wing(side, rnd, snapped=(side > 0))
    upper.append(_shoulder_moss([upper[0], hood], rnd))
    for P in upper:
        P.transform(drop)
    parts = lower + upper
    for P in parts:
        P.transform(lift)
    angel = _finish(_unique(f"{prefix}_Angel"), parts)

    # ---- pedestal ----------------------------------------------------------
    ped_parts, shaft_bvh = _build_pedestal(rnd)
    pedestal = _finish(_unique(f"{prefix}_Pedestal"), ped_parts)

    # ---- glow parts --------------------------------------------------------
    orb_p = _build_orb(rnd)
    orb_p.transform(lift @ drop)
    orb = _finish(_unique(f"GLOW_{ORB_KEY}_{prefix}Orb"), [orb_p], glow_key=ORB_KEY)
    _keep_orb_clear(orb, [angel, pedestal])
    out = [angel, pedestal, orb]
    if glowing_eyes:
        eyes_p = _build_eyes()
        eyes_p.transform(lift @ drop @ head)
        out.append(_finish(_unique(f"GLOW_ember_{prefix}Eyes"), [eyes_p], glow_key="ember"))
    cracks = _build_cracks(shaft_bvh)
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
    objs += candles((-1.66, -1.62, PED_TOP), 0.4, 2, seed + 1, 0.85)
    objs += candles((1.70, -1.60, PED_TOP), -0.9, 1, seed + 2, 0.62)
    # a jack-o'-lantern at the foot of the plinth
    j = _free_index(dp + "_JackOLantern_{:02d}")
    objs += pumpkins.make_jack_o_lantern(f"{dp}_JackOLantern_{j:02d}", (1.72, -3.24, 0.0), 0.30,
                                         size=1.55, face="happy", seed=seed + 11, lid=False)
    return objs


# --------------------------------------------------------------------------
# Checks (also used by build_statue.py)
# --------------------------------------------------------------------------
def find(objs, sub):
    return next((o for o in objs if sub in o.name), None)


def _clearance(orb, stones):
    """(gap, unit push direction): smallest distance between the orb and the
    stone objects, measured both ways (orb vertices -> stone surface and stone
    vertices -> orb surface, so facets never hide a closer contact), and the
    direction that moves the orb away from that contact."""
    t_orb = _world_bvh(orb)
    mo = orb.matrix_world
    opts = [mo @ v.co for v in orb.data.vertices]
    centre = sum(opts, Vector()) / len(opts)
    reach = max((p - centre).length for p in opts) + 0.6
    best, push = 1e9, Vector((0, 0, 1))
    for o in stones:
        t = _world_bvh(o)
        for p in opts:
            hit = t.find_nearest(p)
            if hit[0] is not None and hit[3] < best:
                best, push = hit[3], p - hit[0]
        ms = o.matrix_world
        for v in o.data.vertices:
            p = ms @ v.co
            if (p - centre).length > reach:
                continue
            hit = t_orb.find_nearest(p)
            if hit[0] is not None and hit[3] < best:
                best, push = hit[3], hit[0] - p
    if push.length < 1e-9:
        push = Vector((0, 0, 1))
    return best, push.normalized()


def _stones(objs, orb):
    return [o for o in objs if o is not orb and not o.name.startswith("GLOW_") and "Decor" not in o.name]


def _keep_orb_clear(orb, stones, target=CLEARANCE_TARGET):
    """Safety net (a different seed or edited hands): nudge the orb away from
    the closest stone until the two-way gap reaches ``target``."""
    for _ in range(20):
        d, away = _clearance(orb, stones)
        if d >= target:
            return d
        orb.data.transform(Matrix.Translation(away * (target + 0.01 - d)))
    return _clearance(orb, stones)[0]


def orb_clearance(objs):
    """Smallest gap between the orb and any stone of the statue (Angel and
    Pedestal), measured both ways."""
    orb = next(o for o in objs if o.name.startswith("GLOW_") and o.name.endswith("Orb"))
    return _clearance(orb, _stones(objs, orb))[0]


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
        # neighbouring faces facing (almost) opposite ways = a folded-over
        # triangle: it shows its back face (Roblox culls it -> a hole)
        folds=sum(1 for e in bm.edges if len(e.link_faces) == 2
                  and e.link_faces[0].normal.dot(e.link_faces[1].normal) < -0.9),
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


def coplanar_pairs(objs):
    """build.check_coplanar (same-facing faces of two meshes closer than
    build.COPLANAR_GAP = depth-flicker risk in Roblox) on these objects."""
    try:
        import build  # noqa: E402  (blender/ is on sys.path)
    except ImportError:
        return []
    bpy.context.view_layer.update()        # check_coplanar reads (possibly stale) bound boxes
    return build.check_coplanar([o for o in objs if o.type == "MESH"])


def _face_keys(obj):
    """Palette key of every polygon (from the first loop's UV cell)."""
    me = obj.data
    if not me.uv_layers:
        return ["?"] * len(me.polygons)
    cells = {(c, r): k for k, (c, r, _rgb) in common.PALETTE.items()}
    rows = len(common.PALETTE_ROWS)
    uv = me.uv_layers[0].data
    out = []
    for p in me.polygons:
        u, v = uv[p.loop_start].uv
        out.append(cells.get((int(u * common.PALETTE_COLS), int((1.0 - v) * rows)), "?"))
    return out


def self_coplanar(objs, gap=0.03, min_area=2e-3, skip_decor=True):
    """Near-coplanar, overlapping, same-facing faces INSIDE one mesh
    (build.check_coplanar only compares different meshes): two faces that
    share no vertex, whose normals agree (dot > 0.95), where a sample point of
    one projects straight onto the other (lateral offset < 1e-4) closer than
    ``gap`` along the normal. Pairs are skipped when the outer face is buried
    inside a closed solid (not exposed) or when both are the same glow colour
    (Neon on Neon of one colour cannot flicker visibly). Samples: the centroid
    and three points between it and the corners. Decor (shared factories) is
    skipped by default. Returns [(name, pairs, min_gap, (x, y, z), keys)]."""
    meshes = [o for o in objs if o.type == "MESH" and not (skip_decor and "Decor" in o.name)]
    allv, allp = [], []
    for o in meshes:
        mw = o.matrix_world
        base = len(allv)
        allv += [mw @ v.co for v in o.data.vertices]
        allp += [[base + i for i in p.vertices] for p in o.data.polygons]
    world = BVHTree.FromPolygons(allv, allp)
    ground = min(v.z for v in allv) if allv else 0.0
    out = []
    for o in meshes:
        mw = o.matrix_world
        n3 = mw.to_3x3()
        me = o.data
        verts = [mw @ v.co for v in me.vertices]
        polys = list(me.polygons)
        tree = BVHTree.FromPolygons(verts, [list(p.vertices) for p in polys])
        normals = [(n3 @ p.normal).normalized() for p in polys]
        vsets = [set(p.vertices) for p in polys]
        keys = _face_keys(o)
        glow = o.name.startswith("GLOW_")
        pairs = {}
        for p in polys:
            if p.area < min_area:
                continue
            i0 = p.index
            n = normals[i0]
            c = mw @ p.center
            samples = [c] + [c.lerp(verts[vi], 0.6) for vi in list(p.vertices)[:3]]
            for s in samples:
                for loc, nrm, idx, _d in tree.find_nearest_range(s, gap):
                    if idx is None or idx == i0 or (vsets[idx] & vsets[i0]):
                        continue
                    if normals[idx].dot(n) <= 0.95:
                        continue
                    v = loc - s
                    if (v - nrm * v.dot(nrm)).length > 1e-4:     # not straight above / below
                        continue
                    along = v.dot(n)
                    if abs(along) >= gap:
                        continue
                    if glow and keys[idx] == keys[i0]:
                        continue
                    outer = loc if along > 0 else s
                    if n.z < -0.7 and outer.z < ground + 0.03:   # bottoms resting on the ground
                        continue
                    hit = world.ray_cast(outer + n * 2e-4, n, 100.0)
                    if hit[0] is not None and hit[1].dot(n) > 0:   # inside a closed solid
                        continue
                    k = (min(i0, idx), max(i0, idx))
                    if k not in pairs or abs(along) < pairs[k][0]:
                        pairs[k] = (abs(along), outer.copy(), (keys[i0], keys[idx]))
        if pairs:
            g, loc, ks = min(pairs.values(), key=lambda t: t[0])
            out.append((o.name, len(pairs), g, tuple(round(x, 2) for x in loc), ks,
                        sorted({tuple(round(x, 2) for x in t[1]) for t in pairs.values()})))
    return out


def degenerate_faces(objs, area=1e-7, altitude=1e-4):
    """(zero-area polygons, sliver triangles) per mesh: polygons with an area
    below ``area`` and triangles whose smallest altitude is below ``altitude``
    (run after triangulation). Returns {name: (n_zero, n_sliver, first_loc)}."""
    out = {}
    for o in objs:
        if o.type != "MESH":
            continue
        mw = o.matrix_world
        vs = [mw @ v.co for v in o.data.vertices]
        zero = sliver = 0
        first = None
        for p in o.data.polygons:
            pts = [vs[i] for i in p.vertices]
            a = p.area
            if a < area:
                zero += 1
                first = first or tuple(round(x, 3) for x in pts[0])
                continue
            if len(pts) == 3:
                longest = max((pts[i] - pts[(i + 1) % 3]).length for i in range(3))
                if 2 * a / max(longest, 1e-12) < altitude:
                    sliver += 1
                    first = first or tuple(round(x, 3) for x in pts[0])
        if zero or sliver:
            out[o.name] = (zero, sliver, first)
    return out


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
        if st["folds"] and "Decor" not in o.name:
            p.append(f"{o.name}: {st['folds']} folded-over edges")
        if st["wire"] or st["loose"]:
            p.append(f"{o.name}: {st['wire']} wire edges, {st['loose']} loose verts")
        if "Decor" not in o.name and st["nonmanifold"]:
            p.append(f"{o.name}: {st['nonmanifold']} non-manifold edges")
        probs += p
        if verbose:
            print(f"  {o.name:44s} {t:5d} tris  open={st['open_edges']:3d} isl={st['islands']:2d} "
                  f"closed={st['closed_islands']:2d} inv={st['inside_out']} folds={st['folds']}  {'; '.join(p)}")
    lo, hi = world_bounds(objs)
    clr = orb_clearance(objs)
    if total > TRI_BUDGET:
        probs.append(f"total {total} tris > {TRI_BUDGET}")
    if clr < CLEARANCE_MIN:
        probs.append(f"orb clearance {clr:.3f} < {CLEARANCE_MIN}")
    probs += support_problems(objs)
    cop = coplanar_pairs(objs)
    probs += [f"near-coplanar faces {a} <-> {b}: {n} faces, closest {g * 1000:.1f} mm" for a, b, n, g in cop]
    for name, n, g, loc, ks, _locs in self_coplanar(objs):
        probs.append(f"near-coplanar faces inside {name}: {n} pairs, closest {g * 1000:.2f} mm at {loc} {ks}")
    for name, (z, s, loc) in degenerate_faces(objs).items():
        if z:
            probs.append(f"{name}: {z} zero-area faces (first at {loc})")
    if verbose:
        print(f"  total {total} tris in {len(objs)} objects (budget {TRI_BUDGET}); bounds "
              f"{tuple(round(x, 2) for x in lo)} .. {tuple(round(x, 2) for x in hi)}")
        print(f"  orb clearance {clr:.3f} (min {CLEARANCE_MIN}, target {CLEARANCE_TARGET})")
    return total, probs


def focus(objs, pad=1.04):
    """Frame the statue itself (preview.render's default frames the bbox diagonal)."""
    lo, hi = world_bounds(objs)
    size = hi - lo
    return (lo + hi) / 2, max(size.z * 0.5, size.x * 0.34, size.y * 0.34) * pad


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
