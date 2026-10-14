"""Graveyard props for the Halloween portal scene.

Factories (all return a list of objects already linked to the scene, placed in
world space at ``location`` with ``rotation_z`` applied as object rotation; the
object origin is the bottom centre of its bounding box, the mesh's design pivot
(stone centre / trunk base / fence centre) lands exactly on ``location``):

* make_gravestone(name, location, rotation_z, style, height, seed)
      style 'round'  arched tablet with a recessed panel
            'cross'  Celtic ring cross on a stepped plinth
            'broken' tablet snapped off, the top leaning against the stump
            'slab'   tall tablet with a gabled top and a cornice ledge
      stone / stone_dark / stone_light, chipped edges, a hairline crack,
      dirt patch + grass tufts at the base. No text, no engraving.
* make_fence(name, location, rotation_z, length, height, seed, broken, end_posts)
      black wrought-iron segment along local X: two posts (at x = +-length/2,
      so segments chain with a shared post via end_posts=(False, True)),
      two rails, spear-tipped bars, a couple of bent bars; broken=True adds a
      gap with snapped stubs, a drooping rail and a fallen bar in front (-Y).
* make_fence_run(prefix, points, height, seed, broken, closed)
      chains make_fence segments through XY points with shared posts
      (names <prefix>_01, _02, ...), e.g. an arc around the portal.
* make_dead_tree(name, location, rotation_z, height, seed, lantern)
      twisted leafless tree; optional hanging lantern whose light is a separate
      GLOW_amber_LanternGlow_## object (the Roblox script adds a PointLight).
* make_skull(name, location, rotation_z, size)
* make_bone_pile(name, location, rotation_z, seed)
* make_grave_mound(name, location, rotation_z, length, seed)
      runs along local Y, head end at +Y (put a gravestone there).
* make_shovel(name, location, rotation_z, seed)
      small extra: a shovel stuck in the ground, leaning.

Run this file directly for a catalogue render in previews/_wip/graveyard_*.png.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy, bmesh, common  # noqa: E401,E402

import math  # noqa: E402

from mathutils import Euler, Matrix, Vector  # noqa: E402

_KEYS = list(common.PALETTE.keys())
_TAG = "pal"


# --------------------------------------------------------------------------
# bmesh building helpers
# --------------------------------------------------------------------------
class Builder:
    """Collects geometry in one bmesh, remembers per-face palette keys (as a
    face int layer, so they survive triangulation) and faces to delete after
    normals have been recalculated on the closed shells."""

    def __init__(self):
        self.bm = bmesh.new()
        self.tag = self.bm.faces.layers.int.new(_TAG)
        self.drop = []

    # ---- tagging -------------------------------------------------------
    def paint(self, faces, key):
        idx = _KEYS.index(key) + 1
        for f in faces:
            f[self.tag] = idx
        return faces

    def verts_of(self, faces):
        out = []
        seen = set()
        for f in faces:
            for v in f.verts:
                if v not in seen:
                    seen.add(v)
                    out.append(v)
        return out

    # ---- primitives ----------------------------------------------------
    def prism_xz(self, prof, y0, y1):
        """Extrude polygon ``prof`` [(x, z)] between y0 (front) and y1 (back)."""
        bm = self.bm
        area = sum(prof[i][0] * prof[(i + 1) % len(prof)][1] - prof[(i + 1) % len(prof)][0] * prof[i][1]
                   for i in range(len(prof)))
        if area < 0:   # make the outline counter-clockwise (x right, z up)
            prof = list(reversed(prof))
            rev = True
        else:
            rev = False
        f = [bm.verts.new((x, y0, z)) for x, z in prof]
        b = [bm.verts.new((x, y1, z)) for x, z in prof]
        front = bm.faces.new(f)                     # normal -Y
        back = bm.faces.new(list(reversed(b)))      # normal +Y
        n = len(prof)
        sides = [bm.faces.new((f[i], b[i], b[(i + 1) % n], f[(i + 1) % n])) for i in range(n)]
        if rev:   # keep sides[i] = edge prof[i] -> prof[i+1] of the caller's order
            sides = [sides[(n - 2 - i) % n] for i in range(n)]
        for fc in [front, back] + sides:
            fc.normal_update()
        return front, back, sides

    def frustum(self, center, z0, z1, r0, r1, sides=4, phase=None, jit=0.0, rnd=None, cap_top=True):
        """Polygonal frustum (box when sides=4) between z0 and z1. r0/r1 are
        (rx, ry) half sizes at bottom/top. Returns (bottom, top, side faces)."""
        bm = self.bm
        cx, cy = center
        if phase is None:
            phase = math.pi / sides
        lo, hi = [], []
        k = math.sqrt(2) if sides == 4 else 1.0   # boxes: r is the half size, not the corner radius
        for i in range(sides):
            a = phase + 2 * math.pi * i / sides
            ca, sa = math.cos(a) * k, math.sin(a) * k
            j0 = (rnd.uniform(-jit, jit), rnd.uniform(-jit, jit)) if rnd else (0, 0)
            j1 = (rnd.uniform(-jit, jit), rnd.uniform(-jit, jit)) if rnd else (0, 0)
            lo.append(bm.verts.new((cx + ca * r0[0] + j0[0], cy + sa * r0[1] + j0[1], z0)))
            hi.append(bm.verts.new((cx + ca * r1[0] + j1[0], cy + sa * r1[1] + j1[1], z1)))
        bottom = bm.faces.new(list(reversed(lo)))
        top = bm.faces.new(hi) if cap_top else None
        sides_f = [bm.faces.new((lo[i], lo[(i + 1) % sides], hi[(i + 1) % sides], hi[i])) for i in range(sides)]
        if not cap_top:
            c = bm.verts.new((cx, cy, z1))
            sides_f += [bm.faces.new((hi[i], hi[(i + 1) % sides], c)) for i in range(sides)]
        return bottom, top, sides_f

    def tube(self, pts, radii, sides=4, phase=0.0, twist=0.0, tip=False, cap_start=True,
             cap_end=True, drop_start=False):
        """Polygonal tube along ``pts``. A radius may be a float or (a, b)
        where a runs along the frame normal and b along the binormal. With
        ``tip`` the last point becomes a sharp point. ``drop_start`` deletes
        the start cap after normals are fixed (hidden inside a parent).
        Returns (faces, rings)."""
        bm = self.bm
        pts = [Vector(p) for p in pts]
        n = len(pts)
        # A list gives one radius per point; a float or an (a, b) tuple is used everywhere.
        if not isinstance(radii, list):
            radii = [radii] * n
        assert len(radii) == n, "tube: need one radius per point"
        tangents = []
        for i in range(n):
            if i == 0:
                t = pts[1] - pts[0]
            elif i == n - 1:
                t = pts[-1] - pts[-2]
            else:
                t = (pts[i + 1] - pts[i]).normalized() + (pts[i] - pts[i - 1]).normalized()
            tangents.append(t.normalized())
        nrm = _perp(tangents[0])
        rings = []
        last = n - 1 if tip else n
        for i in range(last):
            t = tangents[i]
            if i > 0:
                nrm = nrm - t * nrm.dot(t)
                if nrm.length < 1e-6:
                    nrm = _perp(t)
                nrm.normalize()
            b = t.cross(nrm)
            r = radii[i]
            ra, rb = (r, r) if not isinstance(r, (list, tuple)) else r
            ring = []
            for s in range(sides):
                a = phase + twist * i + 2 * math.pi * s / sides
                ring.append(bm.verts.new(pts[i] + ra * math.cos(a) * nrm + rb * math.sin(a) * b))
            rings.append(ring)
        faces = []
        for i in range(len(rings) - 1):
            r0, r1 = rings[i], rings[i + 1]
            for s in range(sides):
                faces.append(bm.faces.new((r0[s], r0[(s + 1) % sides], r1[(s + 1) % sides], r1[s])))
        if tip:
            tv = bm.verts.new(pts[-1])
            r0 = rings[-1]
            for s in range(sides):
                faces.append(bm.faces.new((r0[s], r0[(s + 1) % sides], tv)))
        elif cap_end:
            faces.append(bm.faces.new(list(reversed(rings[-1]))))
        if cap_start:
            cap = bm.faces.new(rings[0])
            faces.append(cap)
            if drop_start:
                self.drop.append(cap)
        return faces, rings

    def ellipsoid(self, c, r, segs=6, lats=(-45.0, 0.0, 45.0), phase=0.0, rot=None, lons=None):
        """Low-poly ellipsoid. Returns (faces, bands) where bands[j][k] is the
        face between latitude ring j-1 and j (band 0 = bottom fan) at
        longitude step k (longitude = phase + 360*k/segs, or ``lons[k]`` in
        degrees when an explicit increasing list is given)."""
        if lons is not None:
            segs = len(lons)
        bm = self.bm
        c = Vector(c)
        m = rot.to_matrix() if rot is not None else Matrix.Identity(3)

        def P(x, y, z):
            return bm.verts.new(c + m @ Vector((x * r[0], y * r[1], z * r[2])))

        bot = P(0, 0, -1)
        top = P(0, 0, 1)
        rings = []
        for lat in lats:
            la = math.radians(lat)
            ring = []
            for k in range(segs):
                lo = math.radians(lons[k]) if lons is not None else math.radians(phase) + 2 * math.pi * k / segs
                ring.append(P(math.cos(lo) * math.cos(la), math.sin(lo) * math.cos(la), math.sin(la)))
            rings.append(ring)
        bands = []
        band = []
        for k in range(segs):
            band.append(bm.faces.new((bot, rings[0][(k + 1) % segs], rings[0][k])))
        bands.append(band)
        for j in range(len(rings) - 1):
            band = []
            for k in range(segs):
                band.append(bm.faces.new((rings[j][k], rings[j][(k + 1) % segs],
                                          rings[j + 1][(k + 1) % segs], rings[j + 1][k])))
            bands.append(band)
        band = []
        for k in range(segs):
            band.append(bm.faces.new((rings[-1][k], rings[-1][(k + 1) % segs], top)))
        bands.append(band)
        faces = [f for b in bands for f in b]
        return faces, bands

    def octa(self, c, r, rot=None):
        faces, _ = self.ellipsoid(c, r, segs=4, lats=(0.0,), phase=45.0, rot=rot)
        return faces

    def blade(self, base, height, lean, width, rnd, key):
        """One grass blade: thin 3-sided pyramid leaning by vector ``lean``."""
        bm = self.bm
        bx, by, bz = base
        a0 = rnd.uniform(0, 2 * math.pi)
        vs = [bm.verts.new((bx + width * math.cos(a0 + i * 2.094), by + width * math.sin(a0 + i * 2.094), bz))
              for i in range(3)]
        tip = bm.verts.new((bx + lean[0], by + lean[1], bz + height))
        fs = [bm.faces.new((vs[i], vs[(i + 1) % 3], tip)) for i in range(3)]
        fs.append(bm.faces.new(list(reversed(vs))))
        self.paint(fs, key)
        return fs

    def tuft(self, cx, cy, rnd, size=1.0, n=4, keys=("moss_dark", "stem_dark")):
        for i in range(n):
            a = rnd.uniform(0, 2 * math.pi)
            d = rnd.uniform(0.0, 0.12) * size
            h = rnd.uniform(0.28, 0.55) * size
            lean = (math.cos(a) * h * rnd.uniform(0.25, 0.6), math.sin(a) * h * rnd.uniform(0.25, 0.6))
            self.blade((cx + math.cos(a) * d, cy + math.sin(a) * d, 0.0), h, lean,
                       0.06 * size, rnd, keys[i % len(keys)])

    # ---- finishing -----------------------------------------------------
    def finish(self, name, default_key="stone", clamp_ground=True, drop_bottom=True,
               glow_key=None, glow_desc=None):
        bm = self.bm
        if clamp_ground:
            for v in bm.verts:
                if v.co.z < 0.0:
                    v.co.z = 0.0
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        kill = set(f for f in self.drop if f.is_valid)
        if drop_bottom:
            for f in bm.faces:
                if f.normal.z < -0.97 and all(v.co.z < 0.02 for v in f.verts):
                    kill.add(f)
        if kill:
            bmesh.ops.delete(bm, geom=list(kill), context="FACES_ONLY")
        # Clean up verts orphaned by deletions.
        loose = [e for e in bm.edges if not e.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="EDGES")
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        ngons = [f for f in bm.faces if len(f.verts) > 4]
        if ngons:
            bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
        groups = {}
        for f in bm.faces:
            idx = f[self.tag]
            key = _KEYS[idx - 1] if idx > 0 else default_key
            groups.setdefault(key, []).append(f)
            f.material_index = 0
        for key, faces in groups.items():
            common.paint_bmesh_faces(bm, faces, key)
        bm.faces.layers.int.remove(self.tag)   # build-time only, keep it out of the FBX
        obj = common.object_from_bmesh(name, bm)
        obj.data.materials.append(common.mat_palette())
        if glow_key:
            common.make_glow(obj, glow_key, glow_desc or name)
        common.shade_flat(obj)
        return obj


def _perp(v):
    a = Vector((0, 0, 1)) if abs(v.z) < 0.9 else Vector((1, 0, 0))
    return v.cross(a).normalized()


def _place(obj, location, rotation_z):
    """Origin to bottom centre, then put the design pivot at ``location``."""
    common.origin_to_base(obj)
    off = obj.matrix_world.translation.copy()
    obj.matrix_world = (Matrix.Translation(Vector(location)) @ Matrix.Rotation(rotation_z, 4, "Z")
                        @ Matrix.Translation(off))
    return obj


def _free_index(prefix):
    i = 1
    while bpy.data.objects.get(f"{prefix}{i:02d}") is not None:
        i += 1
    return i


def _seed(*parts):
    s = 0
    for p in parts:
        if isinstance(p, str):
            p = sum((i + 1) * ord(ch) for i, ch in enumerate(p))
        s = (s * 1000003 + int(p * 1000 if isinstance(p, float) else p)) & 0x7FFFFFFF
    return s


def _rotate_verts(verts, pivot, euler):
    m = euler.to_matrix()
    p = Vector(pivot)
    for v in verts:
        v.co = p + m @ (v.co - p)


def _ribbon_prism(B, pts2d, w0, w1, y_front, y_back):
    """Thin zig-zag strip in the XZ plane, extruded in Y (used for cracks)."""
    n = len(pts2d)
    left, right = [], []
    for i in range(n):
        a = Vector(pts2d[max(i - 1, 0)])
        b = Vector(pts2d[min(i + 1, n - 1)])
        t = (b - a).normalized()
        side = Vector((-t.y, t.x))
        w = (w0 + (w1 - w0) * i / (n - 1)) * 0.5
        p = Vector(pts2d[i])
        left.append(p + side * w)
        right.append(p - side * w)
    outline = [(p.x, p.y) for p in left] + [(p.x, p.y) for p in reversed(right)]
    return B.prism_xz(outline, y_front, y_back)


# --------------------------------------------------------------------------
# Gravestones
# --------------------------------------------------------------------------
def _arch_profile(hw, z0, top, rnd, segs=7, squash=0.9):
    """Tablet with a round top: bottom corners at z0, top at ``top``."""
    rise = hw * squash
    zs = top - rise
    prof = [(-hw, z0), (hw, z0), (hw, zs)]
    for i in range(1, segs):
        a = math.pi * i / segs
        prof.append((hw * math.cos(a), zs + rise * math.sin(a)))
    prof.append((-hw, zs))
    return prof, zs


def _chip(prof, idx, depth):
    """Dent profile point ``idx`` towards the polygon centroid (a chipped edge)."""
    cx = sum(p[0] for p in prof) / len(prof)
    cz = sum(p[1] for p in prof) / len(prof)
    x, z = prof[idx]
    d = Vector((cx - x, cz - z))
    if d.length < 1e-6:
        return
    d.normalize()
    prof[idx] = (x + d.x * depth, z + d.y * depth)


def _jitter_prof(prof, rnd, amt, keep_bottom_z=None):
    out = []
    for x, z in prof:
        jx = rnd.uniform(-amt, amt)
        jz = 0.0 if (keep_bottom_z is not None and abs(z - keep_bottom_z) < 1e-6) else rnd.uniform(-amt, amt)
        out.append((x + jx, z + jz))
    return out


def _base_patch(B, rx, ry, rnd, h=0.13, key="wood_dark"):
    """Low irregular dirt patch around a gravestone base."""
    n = 9
    lo, hi = [], []
    ph = rnd.uniform(0, 1)
    for i in range(n):
        a = 2 * math.pi * (i + ph) / n
        k = rnd.uniform(0.85, 1.12)
        lo.append(B.bm.verts.new((math.cos(a) * rx * k, math.sin(a) * ry * k, 0.0)))
        k2 = rnd.uniform(0.62, 0.74)
        hi.append(B.bm.verts.new((math.cos(a) * rx * k * k2, math.sin(a) * ry * k * k2, h * rnd.uniform(0.8, 1.15))))
    fs = [B.bm.faces.new(list(reversed(lo))), B.bm.faces.new(hi)]
    fs += [B.bm.faces.new((lo[i], lo[(i + 1) % n], hi[(i + 1) % n], hi[i])) for i in range(n)]
    B.paint(fs, key)
    return fs


def make_gravestone(name, location=(0.0, 0.0, 0.0), rotation_z=0.0, style="round", height=3.0,
                    seed=0, base=True):
    """Gravestone facing -Y. ``height`` = total height incl. the plinth.
    ``base`` adds a dirt patch and grass tufts. Returns [obj]."""
    if style not in ("round", "cross", "broken", "slab"):
        raise ValueError(f"unknown gravestone style {style!r}")
    rnd = common.rng(_seed("gravestone", style, seed))
    # Stone tone: most stones mid grey, some darker/older ones.
    if rnd.random() < 0.3:
        K_B, K_D, K_L = "stone_dark", "ash", "stone"
    else:
        K_B, K_D, K_L = "stone", "stone_dark", "stone_light"
    B = Builder()
    H = float(height)
    s = H / 3.0
    body_faces = []      # faces of the tilting upright part

    # ---------------- plinth ----------------
    if style == "cross":
        T = 0.40 * s
        ph0, ph1 = 0.26 * s, 0.44 * s
        bot, top, sides = B.frustum((0, 0), 0.0, ph0, (0.62 * s, 0.48 * s), (0.6 * s, 0.46 * s), jit=0.02 * s, rnd=rnd)
        B.paint([bot, top] + sides, K_D)
        bot2, top2, sides2 = B.frustum((0, 0), ph0 - 0.02, ph1, (0.44 * s, 0.34 * s), (0.4 * s, 0.31 * s), jit=0.015 * s, rnd=rnd)
        B.paint([bot2] + sides2, K_B)
        B.paint([top2], K_L)
        B.drop.append(bot2)              # inside the lower tier
        plinth_top = ph1
        foot_hw, foot_hd = 0.62 * s, 0.48 * s
    else:
        if style == "slab":
            W = H * rnd.uniform(0.46, 0.52)
            T = 0.21 * s
        else:
            W = H * rnd.uniform(0.58, 0.66)
            T = 0.17 * s
        ph = 0.28 * s
        yaw = rnd.uniform(-0.06, 0.06)
        bot, top, sides = B.frustum((0, 0), 0.0, ph, (W / 2 + 0.2 * s, T / 2 + 0.24 * s),
                                    (W / 2 + 0.15 * s, T / 2 + 0.19 * s), jit=0.025 * s, rnd=rnd)
        B.paint([bot] + sides, K_D)
        B.paint([top], K_D)
        _rotate_verts(B.verts_of([bot, top] + sides), (0, 0, 0), Euler((0, 0, yaw)))
        plinth_top = ph
        foot_hw, foot_hd = W / 2 + 0.2 * s, T / 2 + 0.24 * s

    z0 = plinth_top - 0.06 * s   # body sinks a little into the plinth
    y0, y1 = -T / 2, T / 2
    broken_faces = []
    panel = None

    # ---------------- body ----------------
    if style in ("round", "slab", "broken"):
        hw = W / 2
        if style == "slab":
            g = 0.30 * s
            zc = H - g - 0.06 * s
            prof = [(-hw, z0), (hw, z0), (hw, zc), (0.0, H), (-hw, zc)]
            # Extra side points so chips can be cut into the long edges.
            prof = [(-hw, z0), (hw, z0), (hw, z0 + (zc - z0) * 0.55), (hw, zc), (0.0, H), (-hw, zc),
                    (-hw, z0 + (zc - z0) * 0.5)]
            chip_ids = [rnd.choice([3, 5])]
        elif style == "round":
            prof, zs = _arch_profile(hw, z0, H, rnd)
            chip_ids = rnd.sample(range(3, len(prof) - 1), 1)
        else:  # broken: same tablet but snapped at zb
            prof_full, zs = _arch_profile(hw, z0, H, rnd)
            zb = z0 + (H - z0) * rnd.uniform(0.48, 0.58)
            slant = rnd.choice([-1, 1]) * rnd.uniform(0.12, 0.22)
            nz = 6
            zig = []
            for i in range(nz + 1):
                t = i / nz
                x = hw - 2 * hw * t
                off = (0.16 if i % 2 else -0.08) * s * rnd.uniform(0.6, 1.2) if 0 < i < nz else 0.0
                zig.append((x, zb + slant * (x / hw) * s + off))
            prof = [(-hw, z0), (hw, z0)] + zig
            chip_ids = []
        prof = _jitter_prof(prof, rnd, 0.025 * s, keep_bottom_z=z0)
        for ci in chip_ids:
            _chip(prof, ci, rnd.uniform(0.08, 0.13) * s)
        front, back, sides = B.prism_xz(prof, y0, y1)
        B.paint([front, back] + sides, K_B)
        body_faces += [front, back] + sides
        B.drop.append(sides[0])          # bottom, hidden inside the plinth
        if style == "broken":
            # Fresh break surface reads lighter.
            broken_faces = sides[2:-1] if len(sides) > 3 else []
            B.paint(broken_faces, K_L)
        bmesh.ops.recalc_face_normals(B.bm, faces=[front, back] + sides)
        # Recessed front panel (blank; no text).
        res = bmesh.ops.inset_individual(B.bm, faces=[front], thickness=0.15 * s, depth=-0.055 * s)
        B.paint(res["faces"], K_D)
        body_faces += res["faces"]
        panel = front
        B.paint([panel], K_B)
        # Some stones get a lighter weathered top.
        if style == "round":
            tops = [f for f in sides if f.calc_center_median().z > H - 0.35 * s]
            B.paint(tops[: len(tops) // 2 + 1], K_L)
        if style == "slab":
            # Cornice ledge under the gable.
            zc_ = H - 0.30 * s - 0.06 * s
            cb, ct, cs = B.frustum((0, 0), zc_ - 0.2 * s, zc_ + 0.02 * s, (hw + 0.09 * s, T / 2 + 0.07 * s),
                                   (hw + 0.12 * s, T / 2 + 0.09 * s))
            B.paint([cb] + cs, K_D)
            B.paint([ct], K_L)
            body_faces += [cb, ct] + cs
        # Hairline crack across the panel (a thin dark strip proud of the panel).
        if style != "broken" and rnd.random() < 0.85:
            pc = panel.calc_center_median()
            pz = pc.z
            py = pc.y
            sx = rnd.choice([-1, 1])
            x = sx * (hw - 0.2 * s)
            z = pz + rnd.uniform(0.0, 0.35) * s
            pts = [(x + sx * 0.06 * s, z)]
            for i in range(4):
                x -= sx * rnd.uniform(0.12, 0.2) * s
                z -= rnd.uniform(0.1, 0.22) * s
                x += (0.05 if i % 2 else -0.05) * s
                pts.append((x, z))
            cf, cbk, cs = _ribbon_prism(B, pts, 0.07 * s, 0.025 * s, py - 0.02 * s, py + 0.04 * s)
            B.paint([cf, cbk] + cs, "black")
            body_faces += [cf, cbk] + cs
    else:  # cross
        pw = 0.40 * s           # post width
        za = H * 0.6            # arm bottom
        aw = 0.38 * s           # arm height
        span = 1.58 * s
        fl = 0.07 * s           # flare at the ends
        hp, ha = pw / 2, span / 2
        prof = [(-hp, z0), (hp, z0), (hp, za), (ha, za - fl), (ha, za + aw + fl), (hp, za + aw),
                (hp + fl * 0.6, H), (-hp - fl * 0.6, H), (-hp, za + aw), (-ha, za + aw + fl),
                (-ha, za - fl), (-hp, za)]
        prof = _jitter_prof(prof, rnd, 0.025 * s, keep_bottom_z=z0)
        _chip(prof, rnd.choice([3, 4, 9, 10, 6, 7]), rnd.uniform(0.08, 0.13) * s)
        front, back, sides = B.prism_xz(prof, y0, y1)
        B.paint([front, back] + sides, K_B)
        B.paint([f for f in sides if f.normal.z > 0.5], K_L)
        B.drop.append(sides[0])          # bottom, hidden inside the plinth
        body_faces += [front, back] + sides
        # Celtic ring around the crossing.
        zc = za + aw / 2
        R = 0.57 * s
        rw, rd = 0.1 * s, T * 0.38
        segs = 12
        rings = []
        for i in range(segs):
            a = 2 * math.pi * i / segs + math.pi / segs
            radial = Vector((math.cos(a), 0, math.sin(a)))
            c = Vector((0, 0, zc)) + radial * R
            ring = []
            for b in range(4):
                bb = math.pi / 4 + b * math.pi / 2
                ring.append(B.bm.verts.new(c + radial * rw * math.cos(bb) * 1.41 + Vector((0, 1, 0)) * rd * math.sin(bb) * 1.41))
            rings.append(ring)
        rf = []
        for i in range(segs):
            r0, r1 = rings[i], rings[(i + 1) % segs]
            for b in range(4):
                rf.append(B.bm.faces.new((r0[b], r0[(b + 1) % 4], r1[(b + 1) % 4], r1[b])))
        B.paint(rf, K_B)
        body_faces += rf
        # Crack on the post.
        if rnd.random() < 0.8:
            x, z = rnd.uniform(-0.08, 0.08) * s, za - 0.15 * s
            pts = [(x, z)]
            for i in range(3):
                x += (0.08 if i % 2 else -0.08) * s
                z -= rnd.uniform(0.18, 0.26) * s
                pts.append((x, z))
            cf, cbk, cs = _ribbon_prism(B, pts, 0.06 * s, 0.02 * s, y0 - 0.02 * s, y0 + 0.05 * s)
            B.paint([cf, cbk] + cs, "black")
            body_faces += [cf, cbk] + cs

    # ---------------- tilt the upright part ----------------
    tilt = Euler((rnd.uniform(-0.07, 0.05), rnd.uniform(-0.08, 0.08), rnd.uniform(-0.05, 0.05)))
    if style == "cross":
        tilt = Euler((tilt.x * 0.5, tilt.y * 0.5, tilt.z))
    _rotate_verts(B.verts_of(body_faces), (0, 0, plinth_top), tilt)

    # ---------------- fallen top of a broken stone ----------------
    if style == "broken":
        piece = [(x, z + 0.02 * s) for x, z in reversed(zig)]
        # the rest of the arch above the break line
        piece += [(hw, max(zs, zig[0][1] + 0.1 * s))]
        for i in range(1, 7):
            a = math.pi * i / 7
            piece.append((hw * math.cos(a), zs + hw * 0.9 * math.sin(a)))
        piece.append((-hw, max(zs, zig[-1][1] + 0.1 * s)))
        # lose a chunk: shrink a bit around the centroid
        cx = sum(p[0] for p in piece) / len(piece)
        cz = sum(p[1] for p in piece) / len(piece)
        piece = [(cx + (x - cx) * 0.9, cz + (z - cz) * 0.9) for x, z in piece]
        piece = _jitter_prof(piece, rnd, 0.03 * s)
        pf, pb, ps = B.prism_xz(piece, -T / 2 * 0.95, T / 2 * 0.95)
        B.paint([pf, pb] + ps, K_B)
        B.paint(ps[:len(zig) - 1], K_L)
        pv = B.verts_of([pf, pb] + ps)
        # pivot = middle of the jagged bottom edge
        bz = sum(z for _, z in piece[:len(zig)]) / len(zig)
        for v in pv:
            v.co -= Vector((cx, 0, bz))
        # Lean it back against the front of the stump, slightly turned.
        side = rnd.choice([-1, 1])
        lean = math.radians(rnd.uniform(26, 34))
        m = (Matrix.Rotation(side * rnd.uniform(0.25, 0.45), 3, "Z")
             @ Matrix.Rotation(side * rnd.uniform(0.04, 0.09), 3, "Y")
             @ Matrix.Rotation(-lean, 3, "X"))
        for v in pv:
            v.co = m @ v.co
        minz = min(v.co.z for v in pv)
        shift = Vector((side * hw * 0.3, -T / 2 - 0.3 * s - 0.6 * T - (H - zb) * 0.5 * math.sin(lean), -minz))
        for v in pv:
            v.co += shift

    # ---------------- ground dressing ----------------
    if base:
        _base_patch(B, foot_hw + 0.35 * s, foot_hd + 0.45 * s, rnd, h=0.13 * s)
        for i in range(rnd.randint(2, 3)):
            a = rnd.uniform(0, 2 * math.pi)
            B.tuft(math.cos(a) * (foot_hw + 0.12 * s), math.sin(a) * (foot_hd + 0.15 * s), rnd,
                   size=s * rnd.uniform(0.8, 1.15), n=rnd.randint(3, 4))

    obj = B.finish(name, default_key="stone")
    return [_place(obj, location, rotation_z)]


# --------------------------------------------------------------------------
# Wrought-iron fence
# --------------------------------------------------------------------------
def _spear(B, base, d, hw, key_tip="iron_light", length=0.40, wide=0.13):
    """Arrowhead tip continuing a bar from ``base`` along direction ``d``."""
    d = Vector(d).normalized()
    p0 = Vector(base)
    pts = [p0, p0 + d * 0.13, p0 + d * length]
    faces, _ = B.tube(pts, [(hw * 0.9, hw * 0.9), (hw * 0.75, wide), 0.0], sides=4, phase=math.pi / 4, tip=True,
                      cap_start=True, drop_start=True)
    B.paint(faces, key_tip)
    return faces


def make_fence(name, location=(0.0, 0.0, 0.0), rotation_z=0.0, length=6.0, height=3.0, seed=0,
               broken=False, end_posts=(True, True)):
    """Wrought-iron fence segment running along local X, centred on location.
    Posts stand at x = +-length/2 so segments chain end to end; pass
    end_posts=(False, True) on all but the first segment to share posts."""
    rnd = common.rng(_seed("fence", seed, int(broken)))
    B = Builder()
    L, Hh = float(length), float(height)
    s = Hh / 3.0
    hw = 0.045 * s                    # bar half width
    pw = 0.15 * s                     # post half width
    z_r = [0.55 * s, Hh - 0.62 * s]   # rail heights
    rail_hw = (0.07 * s, 0.05 * s)

    # ---- posts ----
    for side, on in zip((-1, 1), end_posts):
        if not on:
            continue
        x = side * L / 2
        fb, ft, fs = B.frustum((x, 0), 0.0, 0.32 * s, (0.34 * s, 0.34 * s), (0.3 * s, 0.3 * s), jit=0.02 * s, rnd=rnd)
        B.paint([fb] + fs, "stone_dark")
        B.paint([ft], "stone")
        pb, pt, ps = B.frustum((x, 0), 0.25 * s, Hh + 0.02 * s, (pw, pw), (pw * 0.9, pw * 0.9))
        B.paint([pb, pt] + ps, "iron")
        B.drop += [pb, pt]               # inside the footing / collar
        cb, ct, cs = B.frustum((x, 0), Hh, Hh + 0.12 * s, (pw * 1.35, pw * 1.35), (pw * 1.35, pw * 1.35))
        B.paint([cb, ct] + cs, "iron_light")
        kb, _, ks = B.frustum((x, 0), Hh + 0.1 * s, Hh + 0.42 * s, (pw * 1.2, pw * 1.2), (0.02, 0.02), cap_top=False)
        B.paint([kb] + ks, "iron")
        B.drop.append(kb)                # inside the collar
        B.paint(B.octa((x, 0, Hh + 0.5 * s), (0.11 * s, 0.11 * s, 0.12 * s)), "iron_light")

    # ---- bars ----
    n = max(3, int(round((L - 0.3) / (0.42 * s))))
    xs = [-L / 2 + (i + 1) * L / (n + 1) for i in range(n)]
    gap = set()
    broken = broken and n >= 5
    if broken:
        g0 = rnd.randint(max(1, n // 2 - 2), n // 2)
        gap = {g0, g0 + 1, g0 + 2} if n >= 7 else {g0, g0 + 1}
    bendable = [i for i in range(n) if i not in gap and min(i, n - 1 - i) >= 1]
    bent_top = set(rnd.sample(bendable, 1)) if bendable else set()
    rest = [i for i in bendable if all(abs(i - j) > 2 for j in bent_top)]
    bowed = set(rnd.sample(rest, 1)) if rest and n >= 6 else set()
    for i, x in enumerate(xs):
        top = Hh - (0.0 if i % 2 == 0 else 0.22 * s) - 0.28 * s
        if i in gap:
            # Snapped stubs: one from the ground, one hanging from the bottom rail.
            h = rnd.uniform(0.35, 0.8) * s
            f, _ = B.tube([(x, 0, 0), (x, 0, h - 0.12 * s), (x + rnd.uniform(-0.05, 0.05), 0, h)],
                          [hw, hw, 0.0], sides=4, phase=math.pi / 4, tip=True)
            B.paint(f, "iron")
            continue
        if i in bent_top:
            a = rnd.choice([-1, 1]) * rnd.uniform(0.45, 0.7)
            zk = z_r[1] + 0.06 * s
            seg = max(Hh - 0.28 * s - zk, 0.3 * s) * 1.25
            d = Vector((math.sin(a), rnd.uniform(-0.25, 0.25), math.cos(a))).normalized()
            end = Vector((x, 0, zk)) + d * seg
            f, _ = B.tube([(x, 0, 0), (x, 0, zk - 0.1 * s), (x, 0, zk), end], hw, sides=4, phase=math.pi / 4)
            B.paint(f, "iron")
            _spear(B, end - d * 0.01, d, hw)
        elif i in bowed:
            bow = rnd.choice([-1, 1]) * rnd.uniform(0.25, 0.4) * s
            zm = (z_r[0] + z_r[1]) / 2
            pts = [(x, 0, 0), (x, 0, z_r[0] + 0.1 * s), (x + 0.05 * s, bow, zm), (x, 0, z_r[1] - 0.1 * s), (x, 0, top)]
            f, _ = B.tube(pts, hw, sides=4, phase=math.pi / 4)
            B.paint(f, "iron")
            _spear(B, (x, 0, top - 0.01), (0, 0, 1), hw)
        else:
            lean = rnd.uniform(-0.04, 0.04) if broken else rnd.uniform(-0.015, 0.015)
            pts = [(x, 0, 0), (x + lean * top, 0, top)]
            f, _ = B.tube(pts, hw, sides=4, phase=math.pi / 4)
            B.paint(f, "iron")
            d = Vector((lean, 0, 1)).normalized()
            _spear(B, Vector(pts[-1]) - d * 0.01, d, hw)

    # ---- rails ----
    def rail(x0, x1, z, sag=0.03):
        xm = (x0 + x1) / 2
        f, _ = B.tube([(x0, 0, z), (xm, 0, z - sag * s), (x1, 0, z)], rail_hw, sides=4, phase=math.pi / 4)
        B.paint(f, "iron")

    x_lo = -L / 2 + (0.0 if end_posts[0] else -0.02)
    x_hi = L / 2 + (0.0 if end_posts[1] else 0.02)
    rail(x_lo, x_hi, z_r[0], rnd.uniform(0.0, 0.04))
    if not broken:
        rail(x_lo, x_hi, z_r[1], rnd.uniform(0.0, 0.05))
    else:
        g = sorted(gap)
        xa = xs[g[0]] - 0.12 * s
        xb = xs[g[-1]] + 0.18 * s
        rail(x_lo, xa, z_r[1], 0.0)
        rail(xb, x_hi, z_r[1], 0.02)
        # The loose end of the top rail bent down to the ground.
        f, _ = B.tube([(xb + 0.15 * s, 0, z_r[1]), (xb - 0.12 * s, -0.05 * s, z_r[1] - 0.45 * s),
                       (xb - 0.6 * s, -0.35 * s, 0.35 * s), (xb - 0.95 * s, -0.55 * s, 0.07 * s)],
                      rail_hw, sides=4, phase=math.pi / 4)
        B.paint(f, "iron")
        # A fallen bar lying in front of the gap.
        xc = (xa + xb) / 2
        a = rnd.uniform(-0.5, 0.5)
        d = Vector((math.cos(a), math.sin(a) * 0.4 - 0.2, 0)).normalized()
        p0 = Vector((xc - d.x * 1.0 * s, -0.9 * s - d.y * 1.0 * s, hw))
        p1 = p0 + d * 1.9 * s
        f, _ = B.tube([p0, p1], hw, sides=4, phase=math.pi / 4)
        B.paint(f, "iron")
        _spear(B, p1 - d * 0.01, d, hw)

    obj = B.finish(name, default_key="iron")
    return [_place(obj, location, rotation_z)]


def make_fence_run(prefix, points, height=3.0, seed=0, broken=(), closed=False):
    """Chain fence segments through XY ``points`` (studs) with shared posts.
    Segment i (named ``<prefix>_<i+1:02d>``) runs from points[i] to
    points[i+1]; indices in ``broken`` get the broken variant. With
    ``closed`` the last point connects back to the first. Returns all objects."""
    pts = [Vector((p[0], p[1], 0.0)) for p in points]
    if closed:
        pts.append(pts[0])
    out = []
    nseg = len(pts) - 1
    for i in range(nseg):
        a, b = pts[i], pts[i + 1]
        d = b - a
        first_post = i == 0
        last_post = not (closed and i == nseg - 1)
        out += make_fence(f"{prefix}_{i + 1:02d}", (a + b) / 2, math.atan2(d.y, d.x), length=d.length,
                          height=height, seed=seed * 101 + i, broken=i in broken,
                          end_posts=(first_post, last_post))
    return out


# --------------------------------------------------------------------------
# Dead tree
# --------------------------------------------------------------------------
def _wiggle(d, rnd, amt, bias=None, bias_w=0.0):
    v = d + Vector((rnd.uniform(-amt, amt), rnd.uniform(-amt, amt), rnd.uniform(-amt, amt)))
    if bias is not None:
        v += bias * bias_w
    return v.normalized()


def _branch_dir(parent, rnd, ang_lo, ang_hi, up=0.25):
    """Direction deviating from ``parent`` by a random angle around a random axis."""
    p = parent.normalized()
    axis = _perp(p)
    axis = Matrix.Rotation(rnd.uniform(0, 2 * math.pi), 3, p) @ axis
    d = Matrix.Rotation(math.radians(rnd.uniform(ang_lo, ang_hi)), 3, axis) @ p
    d.z += up
    return d.normalized()


def make_dead_tree(name, location=(0.0, 0.0, 0.0), rotation_z=0.0, height=12.0, seed=0, lantern=True):
    """Twisted leafless tree, trunk base at ``location``. Returns [tree] or
    [tree, GLOW_amber_LanternGlow_##] when a lantern could be hung."""
    rnd = common.rng(_seed("tree", seed))
    B = Builder()
    H = float(height)
    s = H / 12.0
    bark = ["wood_dark", "wood_dark", "wood_dark", "charcoal", "wood"]
    paths = []   # (pts, radii) of limbs, used for lantern placement

    def paint_bark(faces):
        for f in faces:
            B.paint([f], rnd.choice(bark))

    # ---- trunk ----
    lean_a = rnd.uniform(0, 2 * math.pi)
    lean = Vector((math.cos(lean_a), math.sin(lean_a), 0)) * rnd.uniform(0.5, 0.9) * s
    ph = rnd.uniform(0, 2 * math.pi)
    fork_z = H * rnd.uniform(0.40, 0.46)
    trunk = []
    nseg = 5
    for i in range(nseg + 1):
        t = i / nseg
        z = fork_z * t
        sway = 0.45 * s * math.sin(t * math.pi * 1.6 + ph)
        off = lean * t ** 1.4 + Vector((math.cos(ph + 1.7), math.sin(ph + 1.7), 0)) * sway
        trunk.append(Vector((off.x, off.y, z)))
    tr = [0.92 * s, 0.7 * s, 0.6 * s, 0.54 * s, 0.5 * s, 0.47 * s]
    f, _ = B.tube(trunk, tr, sides=7, phase=rnd.uniform(0, 1), twist=0.32)
    paint_bark(f)
    paths.append((trunk, tr, 0))

    def trunk_at(z):
        """Trunk axis point (with lean + sway) and tube radius at height z."""
        z = min(max(z, 0.0), fork_z)
        t = z / fork_z * nseg
        k = min(int(t), nseg - 1)
        u = t - k
        return trunk[k].lerp(trunk[k + 1], u), tr[k] + (tr[k + 1] - tr[k]) * u

    # ---- roots ----
    # Each root starts on the trunk axis at its own height (lean and sway
    # included) with a ring small enough to stay fully inside the trunk, so
    # the dropped start cap can never open a see-through gap at the junction.
    base = trunk[0]
    nroots = rnd.randint(4, 5)
    for i in range(nroots):
        a = 2 * math.pi * i / nroots + rnd.uniform(-0.3, 0.3)
        dv = Vector((math.cos(a), math.sin(a), 0))
        L = rnd.uniform(1.4, 2.0) * s
        c0, r0 = trunk_at(1.0 * s)
        c1, _ = trunk_at(0.42 * s)
        pts = [c0 + dv * 0.15 * r0,
               Vector((c1.x, c1.y, 0.42 * s)) + dv * 0.75 * s,
               Vector((base.x, base.y, 0.1 * s)) + dv * (0.6 * L + 0.4 * s) + _perp(dv) * rnd.uniform(-0.2, 0.2) * s,
               Vector((base.x, base.y, -0.05)) + dv * (L + 0.5 * s)]
        f, _ = B.tube(pts, [0.55 * r0, 0.36 * s, 0.17 * s, 0.0], sides=5, tip=True, drop_start=True)
        paint_bark(f)

    # ---- limbs ----
    def grow(start, d, length, r0, depth, segs, start_r=None):
        pts = [Vector(start)]
        cur = Vector(d)
        out = Vector((cur.x, cur.y, 0))
        out = out.normalized() if out.length > 1e-4 else Vector((1, 0, 0))
        seg_len = length / (segs - 0.4)
        for i in range(segs):
            # gnarly kinks, a pull outwards and an upward hook at the end
            cur = _wiggle(cur, rnd, {1: 0.42, 2: 0.5}.get(depth, 0.55), out, 0.4)
            if i == segs - 1:
                cur = (cur + Vector((0, 0, rnd.uniform(0.1, 0.5)))).normalized()
            pts.append(pts[-1] + cur * seg_len * (0.6 if i == segs - 1 else 1.0))
        radii = [r0 * (1 - 0.62 * i / segs) for i in range(segs)] + [0.0]
        if start_r is not None:   # thinner open start ring, hidden inside the parent
            radii[0] = start_r
        sides = {1: 6, 2: 5}.get(depth, 4)
        f, _ = B.tube(pts, radii, sides=sides, tip=True, drop_start=True, twist=0.25)
        paint_bark(f)
        paths.append((pts, radii, depth))
        if depth >= 3:
            return
        # children
        n_child = 2 + (rnd.random() < 0.5) if depth == 1 else 1 + (rnd.random() < 0.4)
        idxs = sorted(rnd.sample(range(1, segs), min(n_child, segs - 1)))
        for k, ci in enumerate(idxs):
            p = pts[ci]
            dirp = (pts[ci + 1] - pts[ci]).normalized()
            cd = _branch_dir(dirp, rnd, 32, 58, up=0.2)
            grow(p, cd, length * rnd.uniform(0.45, 0.6), radii[ci] * 0.66, depth + 1, 3 if depth == 1 else 2)

    top = trunk[-1]
    tdir = (trunk[-1] - trunk[-2]).normalized()
    nlimbs = 3
    az0 = rnd.uniform(0, 2 * math.pi)
    for k in range(nlimbs):
        az = az0 + 2 * math.pi * k / nlimbs + rnd.uniform(-0.35, 0.35)
        el = math.radians(rnd.uniform(32, 58) if k else rnd.uniform(62, 75))
        d = Vector((math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el)))
        d = (d + tdir * 0.3).normalized()
        # Start well below the trunk's top cap with a ring narrower than the
        # trunk, so the tilted open start ring stays buried in the trunk.
        start = top - tdir * 0.45 * s
        grow(start, d, H * rnd.uniform(0.36, 0.46) * (1.1 if k == 0 else 1.0), 0.46 * s, 1, 4,
             start_r=0.36 * s)

    # A stubby broken-off branch on the trunk.
    ti = rnd.randint(2, 3)
    a = rnd.uniform(0, 2 * math.pi)
    sd = Vector((math.cos(a), math.sin(a), 0.5)).normalized()
    p0 = trunk[ti]
    f, _ = B.tube([p0, p0 + sd * 0.9 * s, p0 + sd * 1.25 * s], [0.26 * s, 0.2 * s, 0.17 * s], sides=5,
                  drop_start=True)
    paint_bark(f[:-2])
    B.paint(f[-2:-1], "wood_light")   # end cap: broken-off fresh wood

    # ---- hanging lantern ----
    glow_obj = None
    if lantern:
        best = None
        for pts, radii, depth in paths:
            if depth > 2 or depth == 0:
                continue
            for i in range(1, len(pts) - 1):
                p = pts[i]
                seg = (pts[i + 1] - pts[i]).normalized()
                rad = Vector((p.x, p.y)).length
                if not (0.5 * H < p.z < 0.78 * H) or rad < 1.6 * s:
                    continue
                score = -abs(seg.z) * 2 + rad * 0.15 - abs(p.z - 0.62 * H) / H
                # keep the lantern clear of other branches
                lz0, lz1 = p.z - 2.2 * s, p.z - 0.3 * s
                clear = True
                for q, qr, qd in paths:
                    for qq in q:
                        if lz0 < qq.z < lz1 and Vector((qq.x - p.x, qq.y - p.y)).length < 0.7 * s:
                            clear = False
                            break
                    if not clear:
                        break
                if clear and (best is None or score > best[0]):
                    best = (score, p.copy(), radii[i])
        if best is not None:
            _, p, rr = best
            chain = 0.75 * s
            lh = 0.85 * s
            lw = 0.26 * s
            ztop = p.z - rr * 0.6 - chain
            # chain + hook
            f, _ = B.tube([(p.x, p.y, p.z), (p.x, p.y, ztop + 0.05)], 0.035 * s, sides=3)
            B.paint(f, "iron")
            # lantern frame: cap pyramid, top plate, bottom plate, corner bars
            cb, _, cs = B.frustum((p.x, p.y), ztop - 0.02 * s, ztop + 0.2 * s, (lw * 1.25, lw * 1.25), (0.03, 0.03), cap_top=False)
            B.paint([cb] + cs, "iron")
            b1, t1, s1 = B.frustum((p.x, p.y), ztop - 0.1 * s, ztop + 0.03 * s, (lw * 1.15, lw * 1.15), (lw * 1.15, lw * 1.15))
            B.paint([b1, t1] + s1, "iron_light")
            zb = ztop - lh
            b2, t2, s2 = B.frustum((p.x, p.y), zb, zb + 0.1 * s, (lw * 1.1, lw * 1.1), (lw * 1.1, lw * 1.1))
            B.paint([b2, t2] + s2, "iron_light")
            for cx, cy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                f, _ = B.tube([(p.x + cx * lw, p.y + cy * lw, zb + 0.05 * s), (p.x + cx * lw, p.y + cy * lw, ztop - 0.05 * s)],
                              0.035 * s, sides=4, phase=math.pi / 4)
                B.paint(f, "iron")
            # the light itself, separate glow object
            G = Builder()
            gb, gt, gs = G.frustum((p.x, p.y), zb + 0.08 * s, ztop - 0.08 * s, (lw * 0.92, lw * 0.92), (lw * 0.85, lw * 0.85))
            idx = _free_index("GLOW_amber_LanternGlow_")
            glow_obj = G.finish(f"LanternGlow_{idx:02d}", drop_bottom=False, clamp_ground=False,
                                glow_key="amber", glow_desc=f"LanternGlow_{idx:02d}")
    obj = B.finish(name, default_key="wood_dark")
    _place(obj, location, rotation_z)
    out = [obj]
    if glow_obj is not None:
        # built in the same design space as the tree -> same placement
        _place(glow_obj, location, rotation_z)
        out.append(glow_obj)
    return out


# --------------------------------------------------------------------------
# Skull, bones, mound
# --------------------------------------------------------------------------
def _skull_geo(B, center, size, rot=None):
    """Add a low-poly skull (facing -Y before ``rot``) resting with its bottom
    on center.z. Returns the new verts."""
    sz = size
    c = Vector(center)
    m = rot.to_matrix() if rot is not None else Matrix.Identity(3)
    start = len(B.bm.verts)
    cr = (0.42 * sz, 0.44 * sz, 0.37 * sz)
    cc = Vector((0, 0.05 * sz, 0.52 * sz))
    # Uneven longitudes: a nose bridge face at the very front (-90 deg) flanked
    # by one face per eye, the back of the cranium coarser.
    lons = [-103.0, -77.0, -39.0, 12.0, 64.0, 116.0, 168.0, 219.0]
    lats = (-62.0, -30.0, 12.0, 44.0, 72.0)
    cf, bands = B.ellipsoid(cc, cr, lats=lats, lons=lons)
    B.paint(cf, "bone")

    def socket(face, rim, push, squeeze_bottom=0.0):
        # 1st inset keeps a bone rim on the surface, 2nd one becomes the
        # recessed socket (dark walls + black bottom).
        B.bm.normal_update()
        bmesh.ops.inset_individual(B.bm, faces=[face], thickness=rim, depth=0.0)
        vs = list(face.verts)
        if squeeze_bottom:
            low = sorted(vs, key=lambda v: v.co.z)[:2]
            mid = (low[0].co + low[1].co) / 2
            for v in low:
                v.co = mid + (v.co - mid) * squeeze_bottom
        B.bm.normal_update()
        res = bmesh.ops.inset_individual(B.bm, faces=[face], thickness=0.008 * sz, depth=0.0)
        B.paint(res["faces"], "charcoal")
        cen = face.calc_center_median()
        inward = (cc - cen).normalized()
        for v in face.verts:
            v.co += inward * push
        B.paint([face], "black")

    socket(bands[2][1], 0.024 * sz, 0.08 * sz)    # eye (+x)
    socket(bands[2][7], 0.024 * sz, 0.08 * sz)    # eye (-x)
    socket(bands[1][0], 0.02 * sz, 0.05 * sz, squeeze_bottom=0.2)  # nose
    # Upper jaw / teeth block under the face.
    jb, jt, js = B.frustum((0, -0.11 * sz), 0.0, 0.22 * sz, (0.19 * sz, 0.19 * sz), (0.25 * sz, 0.22 * sz))
    B.paint([jb, jt] + js, "bone")
    # Teeth: four dark slits, just proud of the jaw front.
    for tx in (-0.105, -0.035, 0.035, 0.105):
        tb, tt, ts = B.frustum((tx * sz, -0.312 * sz), 0.06 * sz, 0.17 * sz, (0.012 * sz, 0.014 * sz),
                               (0.012 * sz, 0.014 * sz))
        for v in B.verts_of([tb, tt] + ts):   # follow the slanted jaw front
            v.co.y -= (v.co.z / (0.22 * sz)) * 0.025 * sz
        B.paint([tb, tt] + ts, "black")
    B.bm.verts.ensure_lookup_table()
    new_verts = B.bm.verts[start:]
    for v in new_verts:
        v.co = c + m @ v.co
    return new_verts


def make_skull(name, location=(0.0, 0.0, 0.0), rotation_z=0.0, size=0.8):
    """Low-poly skull facing -Y, ~size studs tall. Returns [obj]."""
    B = Builder()
    _skull_geo(B, (0, 0, 0), size)
    obj = B.finish(name, default_key="bone")
    return [_place(obj, location, rotation_z)]


def _bone(B, p0, p1, r=0.06, knob=0.095, rnd=None):
    p0, p1 = Vector(p0), Vector(p1)
    d = (p1 - p0).normalized()
    side = _perp(d)
    up = d.cross(side)
    f, _ = B.tube([p0, p1], r, sides=5, phase=rnd.uniform(0, 1) if rnd else 0.0)
    B.paint(f, "bone")
    rot = d.to_track_quat("Z", "Y").to_euler()
    for p, sgn in ((p0, -1), (p1, 1)):
        for k in (-1, 1):
            q = p + side * k * knob * 0.7 + d * sgn * knob * 0.3
            fk, _ = B.ellipsoid(q, (knob, knob, knob * 1.1), segs=5, lats=(-40.0, 40.0), rot=rot)
            B.paint(fk, "bone")


def make_bone_pile(name, location=(0.0, 0.0, 0.0), rotation_z=0.0, seed=0, skull=True):
    """A little heap of crossed bones (and a skull on top). Returns [obj]."""
    rnd = common.rng(_seed("bones", seed))
    B = Builder()
    r, kb = 0.075, 0.11
    z = kb * 0.9
    # bottom layer: lying flat
    for i in range(3):
        a = rnd.uniform(0, math.pi) + i * 1.05
        ln = rnd.uniform(0.9, 1.35)
        cx, cy = rnd.uniform(-0.35, 0.35), rnd.uniform(-0.3, 0.3)
        d = Vector((math.cos(a), math.sin(a), 0))
        _bone(B, Vector((cx, cy, z)) - d * ln / 2, Vector((cx, cy, z)) + d * ln / 2, r, kb, rnd)
    # upper layer: resting on the bottom ones
    for i in range(2):
        a = rnd.uniform(0, 2 * math.pi)
        ln = rnd.uniform(0.8, 1.2)
        d = Vector((math.cos(a), math.sin(a), 0))
        c = Vector((rnd.uniform(-0.25, 0.25), rnd.uniform(-0.25, 0.25), z + 0.13))
        lift = rnd.uniform(0.05, 0.25)
        _bone(B, c - d * ln / 2 + Vector((0, 0, -lift * 0.5)), c + d * ln / 2 + Vector((0, 0, lift)), r, kb, rnd)
    # a bone leaning on the heap
    a = rnd.uniform(0, 2 * math.pi)
    d = Vector((math.cos(a), math.sin(a), 0))
    _bone(B, Vector((0, 0, z)) + d * 0.95, Vector((0, 0, 0.42)) + d * 0.15, r, kb, rnd)
    if skull:
        _skull_geo(B, (rnd.uniform(-0.1, 0.1), -0.15, 0.2), 0.62,
                   rot=Euler((rnd.uniform(-0.25, -0.1), rnd.uniform(-0.15, 0.15), rnd.uniform(-0.5, 0.5))))
    obj = B.finish(name, default_key="bone")
    return [_place(obj, location, rotation_z)]


def make_grave_mound(name, location=(0.0, 0.0, 0.0), rotation_z=0.0, length=3.0, seed=0, width=None):
    """Elongated dirt mound running along local Y (head end at +Y), with a few
    pebbles, grass tufts and fallen orange leaves. Returns [obj]."""
    rnd = common.rng(_seed("mound", seed))
    B = Builder()
    L = float(length)
    W = width if width is not None else L * 0.47
    h = 0.42 * (L / 3.0) ** 0.5
    n = 12
    # (radius fraction, height fraction); fr[0] is the ridge (only used by surf_z)
    fr = [(0.0, 1.03), (0.42, 0.97), (0.74, 0.72), (0.92, 0.32), (1.0, 0.0)]

    def shape(a, f):
        # superellipse (rounded rectangle) outline
        ca, sa = math.cos(a), math.sin(a)
        e = 2.6
        x = math.copysign(abs(ca) ** (2 / e), ca) * W / 2 * f
        y = math.copysign(abs(sa) ** (2 / e), sa) * L / 2 * f
        return x, y

    # Unjittered ring angles; none lies on the X axis (sin != 0), so every ring
    # vert is cleanly on the +Y or the -Y side of the ridge.
    angles = [2 * math.pi * i / n + math.pi / n for i in range(n)]
    assert all(abs(math.sin(a)) > 1e-3 for a in angles)
    # In-plane jitter shrinks on small mounds so mirrored ring verts never cross.
    jscale = min(1.0, L / 2.8)
    rings = []
    for j, (f, hz) in enumerate(fr[1:]):
        ring = []
        for i, a in enumerate(angles):
            x, y = shape(a, f)
            jj = (0.05 if j < 3 else 0.06) * jscale
            # inner ring: half the height jitter so it stays below the ridge
            zj = rnd.uniform(-0.08, 0.08) * (0.5 if j == 0 else 1.0)
            z = h * hz * (1.0 + zj) if hz > 0 else 0.0
            ring.append(B.bm.verts.new((x + rnd.uniform(-jj, jj), y + rnd.uniform(-jj, jj), z)))
        rings.append(ring)
    # Top ridge: two verts along Y so the top is a rounded ridge, not a point.
    # They sit well inside the inner ring (its tips are at ~+-0.2 L) and above
    # it (inner ring z <= 0.97 * 1.04 h), so the top fan can never fold over.
    ra = B.bm.verts.new((0, L * 0.12, h * 1.04))
    rb = B.bm.verts.new((0, -L * 0.12, h * 1.02))
    top_faces = []
    inner = rings[0]

    def ridge(i):
        return ra if math.sin(angles[i]) > 0 else rb

    # Connect the inner ring to the ridge, following the ring winding (CCW
    # seen from above): +Y edges fan to ra, -Y edges fan to rb, and exactly
    # the two edges whose ends lie on opposite sides of y = 0 (decided from
    # the unjittered angles) become quads (v0, v1, ridge(v1), ridge(v0)).
    for i in range(n):
        k = (i + 1) % n
        v0, v1 = inner[i], inner[k]
        r0, r1 = ridge(i), ridge(k)
        if r0 is r1:
            top_faces.append(B.bm.faces.new((v0, v1, r0)))
        else:
            top_faces.append(B.bm.faces.new((v0, v1, r1, r0)))
    assert sum(len(f.verts) == 4 for f in top_faces) == 2
    for f in top_faces:   # consistent winding: the whole top fan faces up
        f.normal_update()
        assert f.normal.z > 0.0, f"{name}: top fan face folded over"
    side_faces = []
    for j in range(len(rings) - 1):
        for i in range(n):
            side_faces.append(B.bm.faces.new((rings[j][i], rings[j][(i + 1) % n],
                                              rings[j + 1][(i + 1) % n], rings[j + 1][i])))
    bottom = B.bm.faces.new(list(reversed(rings[-1])))
    for f in top_faces + side_faces + [bottom]:
        u = rnd.random()
        B.paint([f], "wood" if u < 0.5 else ("wood_dark" if u < 0.82 else "moss_dark"))

    def surf_z(x, y):
        # approximate mound height at (x, y)
        u = min(1.0, math.sqrt((2 * x / W) ** 2 + (2 * y / L) ** 2))
        for (f0, h0), (f1, h1) in zip(fr, fr[1:]):
            if f0 <= u <= f1:
                t = (u - f0) / (f1 - f0)
                return h * (h0 + (h1 - h0) * t)
        return 0.0

    # pebbles
    for i in range(rnd.randint(3, 4)):
        x, y = rnd.uniform(-0.45, 0.45) * W, rnd.uniform(-0.48, 0.48) * L
        z = surf_z(x, y)
        rr = rnd.uniform(0.07, 0.12)
        B.paint(B.octa((x, y, z), (rr * 1.3, rr, rr * 0.7),
                       rot=Euler((0, 0, rnd.uniform(0, 3)))), rnd.choice(["stone_dark", "stone"]))
    # fallen leaves (orange accents)
    for i in range(rnd.randint(4, 6)):
        x, y = rnd.uniform(-0.38, 0.38) * W, rnd.uniform(-0.42, 0.42) * L
        z = surf_z(x, y) + 0.015
        rr = rnd.uniform(0.12, 0.17)
        B.paint(B.octa((x, y, z), (rr, rr * 0.55, 0.025),
                       rot=Euler((rnd.uniform(-0.2, 0.2), rnd.uniform(-0.2, 0.2), rnd.uniform(0, 3.14)))),
                rnd.choice(["pumpkin_dark", "rust", "pumpkin", "amber"]))
    # tufts around the edge
    for i in range(rnd.randint(2, 3)):
        a = rnd.uniform(0, 2 * math.pi)
        x, y = shape(a, 1.0)
        B.tuft(x * 1.02, y * 1.02, rnd, size=rnd.uniform(0.8, 1.1), n=3)
    obj = B.finish(name, default_key="wood_dark")
    # Only the dropped bottoms (mound, grass blades) may be open: any open
    # edge above the ground means a hole in the mound top.
    me = obj.data
    edge_users = {}
    for p in me.polygons:
        for ek in p.edge_keys:
            edge_users[ek] = edge_users.get(ek, 0) + 1
    holes = [ek for ek, c in edge_users.items()
             if c == 1 and max(me.vertices[ek[0]].co.z, me.vertices[ek[1]].co.z) >= 0.03]
    if holes:
        raise AssertionError(f"{name}: {len(holes)} open edges above the ground (hole in the mound)")
    return [_place(obj, location, rotation_z)]


def make_shovel(name, location=(0.0, 0.0, 0.0), rotation_z=0.0, seed=0):
    """Old shovel stuck in the ground, leaning (~3.4 studs tall). Returns [obj]."""
    rnd = common.rng(_seed("shovel", seed))
    B = Builder()
    tilt = Euler((rnd.uniform(0.2, 0.3), rnd.uniform(-0.15, 0.15), 0))
    # blade: half buried, pointed
    bw, bh, bt = 0.55, 0.75, 0.06
    prof = [(-bw / 2, -0.35), (0, -0.55), (bw / 2, -0.35), (bw / 2, bh - 0.35), (0.09, bh - 0.25),
            (-0.09, bh - 0.25), (-bw / 2, bh - 0.35)]
    f, b, s = B.prism_xz(prof, -bt / 2, bt / 2)
    B.paint([f, b] + s, "iron_light")
    B.paint([sf for sf in s if sf.calc_center_median().z > bh - 0.4], "rust")
    # shaft
    fs, _ = B.tube([(0, 0, bh - 0.3), (0, 0, 2.7)], 0.07, sides=5)
    B.paint(fs, "wood")
    # D-grip
    fg, _ = B.tube([(-0.2, 0, 2.65), (-0.2, 0, 3.0), (0.2, 0, 3.0), (0.2, 0, 2.65)], 0.05, sides=4)
    B.paint(fg, "wood_dark")
    cb, ct, cs = B.frustum((0, 0), 2.6, 2.72, (0.24, 0.07), (0.24, 0.07))
    B.paint([cb, ct] + cs, "wood_dark")
    _rotate_verts(B.bm.verts, (0, 0, 0), tilt)
    # small dirt heap where it is stuck in
    _base_patch(B, 0.5, 0.42, rnd, h=0.16)
    obj = B.finish(name, default_key="wood")
    return [_place(obj, location, rotation_z)]


# --------------------------------------------------------------------------
# Demo / catalogue
# --------------------------------------------------------------------------
BUDGETS = {"Gravestone": 300, "Fence": 1200, "DeadTree": 1500, "Skull": 300, "BonePile": 900,
           "GraveMound": 300, "Shovel": 200}


def demo():
    common.reset_scene()
    objs = []
    # front row: gravestones
    objs += make_gravestone("Gravestone_Round_01", (-7.5, 0, 0), 0.08, "round", 3.0, seed=1)
    objs += make_gravestone("Gravestone_Cross_01", (-4.5, 0.3, 0), -0.1, "cross", 3.6, seed=2)
    objs += make_gravestone("Gravestone_Broken_01", (-1.5, 0, 0), 0.15, "broken", 3.0, seed=3)
    objs += make_gravestone("Gravestone_Slab_01", (1.5, 0.2, 0), -0.05, "slab", 3.4, seed=4)
    objs += make_gravestone("Gravestone_Round_02", (4.3, 0, 0), -0.18, "round", 2.5, seed=5)
    objs += make_gravestone("Gravestone_Cross_02", (7.0, 0.2, 0), 0.2, "cross", 2.8, seed=6)
    # back row: fences + tree
    objs += make_fence_run("Fence", [(-10.0, 6.0), (-4.0, 6.0), (2.0, 6.0)], seed=1, broken=(1,))
    objs += make_dead_tree("DeadTree_01", (6.5, 6.5, 0), 0.4, 12.0, seed=1)
    # ground clutter
    objs += make_grave_mound("GraveMound_01", (1.5, -2.3, 0), -0.05, 3.0, seed=1)
    objs += make_grave_mound("GraveMound_02", (-7.5, -2.1, 0), 0.08, 2.6, seed=2)
    objs += make_skull("Skull_01", (-1.0, -2.8, 0), 0.12, 0.8)
    objs += make_bone_pile("BonePile_01", (-4.5, -2.8, 0), 0.0, seed=1)
    objs += make_shovel("Shovel_01", (3.3, -2.0, 0), -0.5, seed=1)

    total = 0
    problems = []
    for o in objs:
        tc = common.tri_count(o)
        total += tc
        cat = o.name.split("_")[0]
        lim = BUDGETS.get(cat)
        flag = "" if lim is None or tc <= lim else f"  OVER BUDGET ({lim})"
        problems += common.validate_object(o)
        print(f"  {o.name:34s} {tc:6d} tris{flag}")
    print(f"GRAVEYARD TOTAL: {total} tris, {len(objs)} objects")
    for p in problems:
        print("  PROBLEM:", p)
    import preview
    views = os.environ.get("GY_VIEWS", "front,three_quarter").split(",")
    samples = int(os.environ.get("GY_SAMPLES", "32"))
    preview.render("previews/_wip/graveyard.png", views=views, res=(960, 600), samples=samples,
                   focus=((0.0, 2.0, 4.4), 6.8))


if __name__ == "__main__":
    demo()
