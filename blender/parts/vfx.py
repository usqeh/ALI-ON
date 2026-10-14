"""Geometry VFX for the Halloween portal: floating cubes, embers, bats,
a glowing magic circle and swoosh wisps.

These are plain low-poly meshes, so they read well in a static FBX. The
Roblox script (roblox/HalloweenPortalVFX.server.lua) animates them by name:

* ``*Float*``  bobs up and down (``*Cube*`` floaters also spin, others sway)
* ``Bat_*``    orbits the portal centre, bobs and banks. A bat and its eyes
               share the base name up to the last number (``Bat_Portal_03`` +
               ``GLOW_ember_Bat_Portal_03_Eyes``), so they move as one group.

Every mesh is built with bmesh (no bpy.ops modelling), is a closed slab or
solid (Roblox renders single planes one-sided), is flat shaded, has scale 1,
its origin at the bottom centre of its bounding box and exactly ONE material
(M_Palette or M_Glow; Roblox allows one material per mesh, so glow and solid
parts are never joined).

Public API (every function returns a list of objects already linked to the
scene; random tilts are baked into the mesh, ``rotation_z`` is stored as the
object rotation):

* make_floating_cubes(prefix, center, radius, count, size_range, seed, keep_out_box,
                      ..., hug_gap, avoid, max_z, top_extent)
      GLOW_fire/GLOW_ember/GLOW_amber ``<prefix>_FloatCube_##`` plus at most
      ``max_dark`` (2) dark ``<prefix>_FloatCube_##`` (12 tris each), 0.4-0.9
      studs (largest side of the tilted box), hugging the frame outline
      1-2 studs out, never above ``max_z`` (just over the stem), small near
      the stem. One running number for glow and dark cubes, so no two cubes
      share a group.
* make_embers(prefix, center, radius, count, seed, height)
      ``GLOW_ember_<prefix>_FloatEmber_##`` (some fire/amber) tiny 8-tri
      diamonds rising above ``center`` (brazier mouth, portal top ...).
* make_bat(name, location, rotation_z, wingspan, seed, pose, bank_deg)
      ``Bat_..._##`` charcoal bat (nose -Y, scalloped membrane wings split and
      mitred at the wrist fold, closed body, ears, fangs, <= 260 tris) +
      ``GLOW_ember_Bat_..._##_Eyes`` (16 tris). ``location`` = body centre.
* make_bat_swarm(prefix, center, radius, count, height, seed, ..., avoid)
      bats placed around ``center`` (the portal ground centre), noses along
      the orbit tangent; start positions keep >= 2 studs of air around the
      floating cubes/pumpkins already in the scene (build it last).
* make_magic_circle(name, location, radius, seed)
      ``GLOW_fire_<name>`` flat ring decal (rings, beads, arcs, three vortex
      arms; nothing that reads as text), bottom at location.z + 0.04.
* make_wisps(prefix, center, radius, count, seed, ..., keep_out_box)
      ``GLOW_fire_<prefix>_FloatWisp_##`` thin tapered energy arcs in the
      portal plane, following the outline on the upper shoulders like a
      broken halo (``count`` is an upper bound: as many as fit).
* keep_out_boxes_from(objects, bands, margin) -> [(min_xyz, max_xyz), ...]
      banded keep-out that follows a round silhouette (frame + tendrils).
* keep_out_box_from(objects, margin) -> (min_xyz, max_xyz)
      single world AABB.

Recommended use around the real portal (parts/portal.py), see build_demo_set:
    body = [Portal_Frame, GLOW_*_PortalTendrils, GLOW_*_PortalRim_*]
    keep = keep_out_boxes_from(body, bands=7, margin=0.4)
    c = (0, 0, (dais_top_z + frame_body_top_z) / 2 + 0.6)       # ~6.8
    wisps = make_wisps("Portal", c, 9.5, 5, keep_out_box=keep)
    cubes = make_floating_cubes("Portal", c, 9.0, 14, keep_out_box=keep, avoid=wisps)

``prefix`` may be "" (names then start with the keyword). Prefixes and names
must not contain the reserved script keywords (Flame, Float, PortalSpin,
LanternGlow, CauldronLiquid, Portal_Swirl) - the functions add the keywords.
Existing names are never reused: numbering skips names already in the file.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy, bmesh, common  # noqa: E401,E402

import math  # noqa: E402

from mathutils import Euler, Matrix, Vector  # noqa: E402

RESERVED = ("Flame", "Float", "PortalSpin", "LanternGlow", "CauldronLiquid", "Portal_Swirl",
            "Portal_Frame", "Portal_Dais")


# --------------------------------------------------------------------------
# Naming helpers
# --------------------------------------------------------------------------
def _strip_glow(name):
    """'GLOW_fire_X_01' -> 'X_01' (what the Roblox script groups by)."""
    if name.startswith("GLOW_"):
        parts = name.split("_", 2)
        if len(parts) == 3 and parts[1] in common.GLOW_KEYS:
            return parts[2]
    return name


def _taken():
    names = set()
    for o in bpy.data.objects:
        names.add(o.name)
        names.add(_strip_glow(o.name))
    return names


def _pfx(prefix):
    prefix = (prefix or "").strip("_")
    for word in RESERVED:
        if word in prefix:
            raise ValueError(f"prefix {prefix!r} contains reserved script keyword {word!r}")
    return f"{prefix}_" if prefix else ""


class _Counter:
    """Hands out the next free number for '<base>##' (glow and plain names
    are checked in their stripped form, so 'GLOW_fire_A_01' blocks 'A_01')."""

    def __init__(self, base, start=1):
        self.base = base
        self.i = start
        self.taken = _taken()

    def next(self):
        while f"{self.base}{self.i:02d}" in self.taken:
            self.i += 1
        name = f"{self.base}{self.i:02d}"
        self.taken.add(name)
        self.i += 1
        return name


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
class _Groups(dict):
    """palette key -> list of bmesh faces."""

    def add(self, key, faces):
        if not isinstance(faces, (list, tuple)):
            faces = [faces]
        self.setdefault(key, []).extend(faces)


def _place(obj, location, rotation_z=0.0):
    """Origin -> bottom centre of the mesh, then put the mesh's construction
    frame at ``location`` rotated by ``rotation_z`` (object rotation)."""
    obj.matrix_world = Matrix.Identity(4)
    common.origin_to_base(obj)
    off = obj.matrix_world.translation.copy()
    obj.matrix_world = (Matrix.Translation(Vector(location))
                        @ Matrix.Rotation(rotation_z, 4, "Z")
                        @ Matrix.Translation(off))
    return obj


def _finish_solid(name, bm, groups, location, rotation_z=0.0):
    """Paint face groups with palette swatches, M_Palette, flat, placed."""
    for key, faces in groups.items():
        live = [f for f in faces if f.is_valid]
        if live:
            common.paint_bmesh_faces(bm, live, key)
    for f in bm.faces:
        f.material_index = 0
    obj = common.object_from_bmesh(name, bm)
    obj.name = name
    obj.data.name = name
    obj.data.materials.append(common.mat_palette())
    common.shade_flat(obj)
    return _place(obj, location, rotation_z)


def _finish_glow(stripped_name, key, bm, location, rotation_z=0.0):
    """Single-material glow object named GLOW_<key>_<stripped_name>."""
    obj = common.object_from_bmesh("_vfx_tmp", bm)
    common.make_glow(obj, key, stripped_name)
    common.shade_flat(obj)
    return _place(obj, location, rotation_z)


def _poly_slab(bm, pts, z0, z1, triangulate=False):
    """Closed prism from a CCW 2D outline (XY) between z0 and z1.
    Returns (top_faces, bottom_faces, side_faces, verts)."""
    n = len(pts)
    top = [bm.verts.new((p[0], p[1], z1)) for p in pts]
    bot = [bm.verts.new((p[0], p[1], z0)) for p in pts]
    ft = bm.faces.new(top)
    fb = bm.faces.new(list(reversed(bot)))
    sides = []
    for i in range(n):
        j = (i + 1) % n
        sides.append(bm.faces.new((bot[i], bot[j], top[j], top[i])))
    tops, bots = [ft], [fb]
    if triangulate and n > 4:
        ft.normal_update()   # ear clipping projects along the face normal
        fb.normal_update()
        tops = bmesh.ops.triangulate(bm, faces=[ft], quad_method="BEAUTY", ngon_method="EAR_CLIP")["faces"]
        bots = bmesh.ops.triangulate(bm, faces=[fb], quad_method="BEAUTY", ngon_method="EAR_CLIP")["faces"]
    return list(tops), list(bots), sides, top + bot


def _ribbon(bm, pts, ups, widths, thick, closed=False):
    """Closed slab ribbon along a 3D polyline. ``ups`` = thickness direction
    per point (unit), the width runs along up x tangent. Returns
    (top_faces, bottom_faces, edge_faces, cap_faces)."""
    n = len(pts)
    lt, rt, lb, rb = [], [], [], []
    for i in range(n):
        if closed:
            a, b = pts[(i - 1) % n], pts[(i + 1) % n]
        else:
            a, b = pts[max(i - 1, 0)], pts[min(i + 1, n - 1)]
        t = (b - a).normalized()
        up = ups[i]
        side = up.cross(t).normalized()
        w = max(widths[i], 0.02) / 2
        h = thick / 2
        p = pts[i]
        lt.append(bm.verts.new(p + side * w + up * h))
        rt.append(bm.verts.new(p - side * w + up * h))
        lb.append(bm.verts.new(p + side * w - up * h))
        rb.append(bm.verts.new(p - side * w - up * h))
    tops, bots, edges, caps = [], [], [], []
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        tops.append(bm.faces.new((lt[i], rt[i], rt[j], lt[j])))
        bots.append(bm.faces.new((lb[j], rb[j], rb[i], lb[i])))
        edges.append(bm.faces.new((lt[i], lt[j], lb[j], lb[i])))
        edges.append(bm.faces.new((rt[j], rt[i], rb[i], rb[j])))
    if not closed:
        caps.append(bm.faces.new((lt[0], lb[0], rb[0], rt[0])))
        caps.append(bm.faces.new((lt[-1], rt[-1], rb[-1], lb[-1])))
    return tops, bots, edges, caps


def _loft(bm, rings, sides=6, phase=0.0, tip_start=None, tip_end=None):
    """Lofted solid along Y. rings: [(y, rx, rz, zc)], tips: (x, y, z) or
    None. Returns all faces."""
    loops = []
    for (y, rx, rz, zc) in rings:
        loop = []
        for k in range(sides):
            a = phase + 2 * math.pi * k / sides
            loop.append(bm.verts.new((rx * math.cos(a), y, zc + rz * math.sin(a))))
        loops.append(loop)
    faces = []
    for r in range(len(loops) - 1):
        A, B = loops[r], loops[r + 1]
        for k in range(sides):
            m = (k + 1) % sides
            faces.append(bm.faces.new((A[k], A[m], B[m], B[k])))
    if tip_start is not None:
        v = bm.verts.new(tip_start)
        A = loops[0]
        for k in range(sides):
            faces.append(bm.faces.new((A[(k + 1) % sides], A[k], v)))
    if tip_end is not None:
        v = bm.verts.new(tip_end)
        B = loops[-1]
        for k in range(sides):
            faces.append(bm.faces.new((B[k], B[(k + 1) % sides], v)))
    # winding depends on the loft direction; make the shell consistent/outward
    bmesh.ops.recalc_face_normals(bm, faces=faces)
    return faces


def _tetra(bm, base_center, base_r, apex, phase=0.0, up=Vector((0, 0, 1))):
    """Pointy tetrahedron (ear, fang, claw). Base triangle perpendicular to
    (apex - base_center)."""
    base_center = Vector(base_center)
    apex = Vector(apex)
    axis = (apex - base_center).normalized()
    ref = up if abs(axis.dot(up)) < 0.9 else Vector((1, 0, 0))
    u = axis.cross(ref).normalized()
    v = axis.cross(u).normalized()
    bv = [bm.verts.new(base_center + (u * math.cos(phase + 2 * math.pi * k / 3)
                                      + v * math.sin(phase + 2 * math.pi * k / 3)) * base_r)
          for k in range(3)]
    av = bm.verts.new(apex)
    faces = [bm.faces.new((bv[0], bv[1], bv[2]))]
    for k in range(3):
        faces.append(bm.faces.new((bv[k], av, bv[(k + 1) % 3])))
    bmesh.ops.recalc_face_normals(bm, faces=faces)
    return faces


def _octa(bm, center, rx, ry, rz, rot=None):
    """Diamond: 6 verts / 8 tris, optional rotation matrix."""
    c = Vector(center)
    m = rot if rot is not None else Matrix.Identity(3)
    offs = [(rx, 0, 0), (0, ry, 0), (-rx, 0, 0), (0, -ry, 0), (0, 0, rz), (0, 0, -rz)]
    vs = [bm.verts.new(c + m @ Vector(o)) for o in offs]
    eq, top, bot = vs[:4], vs[4], vs[5]
    faces = []
    for k in range(4):
        a, b = eq[k], eq[(k + 1) % 4]
        faces.append(bm.faces.new((a, b, top)))
        faces.append(bm.faces.new((b, a, bot)))
    return faces


def _check_volume(obj):
    """Signed volume (> 0 means outward normals on a closed mesh)."""
    me = obj.data
    vol = 0.0
    for p in me.polygons:
        vs = [me.vertices[i].co for i in p.vertices]
        for k in range(1, len(vs) - 1):
            vol += vs[0].dot(vs[k].cross(vs[k + 1])) / 6.0
    return vol


# --------------------------------------------------------------------------
# keep-out boxes
# --------------------------------------------------------------------------
def keep_out_box_from(objects, margin=0.3):
    """World-space AABB ((min), (max)) of ``objects`` grown by ``margin``."""
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in objects:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return (tuple(lo - Vector((margin,) * 3)), tuple(hi + Vector((margin,) * 3)))


class _KeepOut(list):
    """A plain list of (min_xyz, max_xyz) boxes that also remembers the dense
    silhouette points it was made from, so the VFX functions can hug the
    real outline (fine bands) instead of the coarse boxes."""

    points = ()
    margin = 0.35


def _band_boxes(pts, bands, margin):
    z0 = min(p.z for p in pts)
    z1 = max(p.z for p in pts)
    y0 = min(p.y for p in pts) - margin
    y1 = max(p.y for p in pts) + margin
    h = (z1 - z0) / bands
    rows = [[1e9, -1e9] for _ in range(bands)]
    for p in pts:
        b = min(int((p.z - z0) / h), bands - 1) if h > 0 else 0
        row = rows[b]
        if p.x < row[0]:
            row[0] = p.x
        if p.x > row[1]:
            row[1] = p.x
    boxes = []
    for b, (xlo, xhi) in enumerate(rows):
        if xlo > xhi:
            continue
        lo, hi = z0 + b * h, z0 + (b + 1) * h
        boxes.append(((xlo - margin, y0, lo - (margin if b == 0 else 0.0)),
                      (xhi + margin, y1, hi + (margin if b == bands - 1 else 0.0))))
    return boxes


def keep_out_boxes_from(objects, bands=6, margin=0.35):
    """Tighter keep-out than one AABB: the objects' world vertices are cut
    into ``bands`` horizontal slices and each slice gets its own box (so a
    round pumpkin frame with tendrils keeps its shape). Returns a list of
    (min_xyz, max_xyz) boxes usable as ``keep_out_box``. The list also
    carries the (edge-densified) silhouette points, which make_floating_cubes
    and make_wisps use to hug the real outline with ~0.5-stud bands."""
    pts = []
    for o in objects:
        if o.type != "MESH":
            continue
        mw = o.matrix_world
        ws = [mw @ v.co for v in o.data.vertices]
        pts += ws
        for e in o.data.edges:            # densify long edges (tendril zig-zags)
            a, b = ws[e.vertices[0]], ws[e.vertices[1]]
            n = int((a - b).length / 0.25)
            pts += [a.lerp(b, i / (n + 1)) for i in range(1, n + 1)]
    out = _KeepOut()
    if not pts:
        return out
    out.extend(_band_boxes(pts, bands, margin))
    out.points = pts
    out.margin = margin
    return out


def _boxes(keep_out_box, band_h=0.5):
    """Accept None, one (min, max) box or a list of boxes. A list made by
    keep_out_boxes_from is re-banded at ``band_h`` studs from its points."""
    if not keep_out_box:
        return []
    pts = getattr(keep_out_box, "points", None)
    if pts:
        z0 = min(p.z for p in pts)
        z1 = max(p.z for p in pts)
        keep_out_box = _band_boxes(pts, max(1, round((z1 - z0) / band_h)), keep_out_box.margin)
    first = keep_out_box[0]
    if len(first) == 3 and not hasattr(first[0], "__len__"):
        keep_out_box = [keep_out_box]
    return [(Vector(a), Vector(b)) for a, b in keep_out_box]


def _inside(p, rad, boxes, use_y):
    for lo, hi in boxes:
        if (lo.x - rad < p.x < hi.x + rad and lo.z - rad < p.z < hi.z + rad
                and (not use_y or lo.y - rad < p.y < hi.y + rad)):
            return True
    return False


def _push_out(p, center, rad, boxes, use_y=False, step=0.2):
    """Move ``p`` radially away from ``center`` (in XZ) until it is clear."""
    if not boxes:
        return p
    d = Vector((p.x - center.x, 0.0, p.z - center.z))
    if d.length < 1e-6:
        d = Vector((0, 0, 1))
    d.normalize()
    q = p.copy()
    for _ in range(200):
        if not _inside(q, rad, boxes, use_y):
            break
        q += d * step
    return q


# --------------------------------------------------------------------------
# Floating cubes
# --------------------------------------------------------------------------
def _cube_bm(extent, rot, seed, jitter=0.05):
    """Tumbled cube whose largest world-axis extent (AABB side) is ``extent``."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    common.jitter_bmesh(bm, jitter, seed=seed)
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=rot)
    m = max(max(v.co[i] for v in bm.verts) - min(v.co[i] for v in bm.verts) for i in range(3))
    bmesh.ops.scale(bm, vec=(extent / m,) * 3, verts=bm.verts)
    bm.normal_update()
    return bm


def _dark_key(n):
    if n.z > 0.45:
        return "stone_dark"
    if n.z < -0.45:
        return "black"
    return "ash" if n.x + 0.3 * n.y > 0 else "charcoal"


def _hug(c, a, y, squash, boxes, rad, gap, use_y, fallback_r):
    """Point at angle ``a`` (portal plane, from +X CCW) around ``c``: just
    outside the keep-out boxes plus ``gap`` when boxes are given, else on the
    ellipse (fallback_r, fallback_r * squash)."""
    d = Vector((math.cos(a), 0.0, math.sin(a) * squash))
    d.normalize()
    if not boxes:
        e = Vector((math.cos(a) * fallback_r, 0.0, math.sin(a) * fallback_r * squash))
        return Vector((c.x + e.x, y, c.z + e.z)), d
    p = Vector((c.x, y, c.z)) + d * 0.3
    p = _push_out(p, c, rad, boxes, use_y, step=0.1)
    return p + d * gap, d


def _avoid_list(avoid):
    """Objects or (point, radius) pairs -> [(Vector, radius)]."""
    out = []
    for a in avoid or []:
        if hasattr(a, "bound_box"):
            ws = [a.matrix_world @ Vector(v) for v in a.bound_box]
            lo = Vector(map(min, *ws))
            hi = Vector(map(max, *ws))
            dims = sorted(hi - lo)
            if a.type == "MESH" and dims[2] > 1.2 and dims[2] > 2.5 * dims[1]:
                # long thin thing (wisp arc): a chain of small spheres along it
                mw = a.matrix_world
                vs = [mw @ v.co for v in a.data.vertices]
                chain = []
                for v in vs:
                    if all((v - q).length > 0.35 for q in chain):
                        chain.append(v)
                out += [(q, 0.2) for q in chain]
            else:
                out.append(((lo + hi) / 2, (hi - lo).length / 2))
        else:
            out.append((Vector(a[0]), float(a[1])))
    return out


def _clamp_top(p, c, rad, boxes, z_top, gap, use_y, drop=0.0):
    """Keep ``p`` (a cube of half size ``rad``) below ``z_top``: lower it
    (by ``drop`` more, so clamped cubes do not line up) and slide it sideways
    (away from the centre line) until it clears the boxes."""
    if p.z + rad <= z_top:
        return p
    q = p.copy()
    q.z = z_top - rad - drop
    sx = 1.0 if q.x >= c.x else -1.0
    for _ in range(300):
        if not _inside(q, rad, boxes, use_y):
            break
        q.x += sx * 0.1
    q.x += sx * gap
    return q


def make_floating_cubes(prefix, center=(0, 0, 8), radius=8.0, count=14, size_range=(0.4, 0.9),
                        seed=0, keep_out_box=None, arc=(6.0, 174.0), dark_ratio=0.12, max_dark=2,
                        depth=(-2.2, 0.9), squash_z=0.92, min_z=1.6, keep_out_y=False,
                        radial_jitter=(0.92, 1.15), hug_gap=(0.15, 1.0), avoid=None,
                        glow_keys=(("fire", 0.6), ("ember", 0.25), ("amber", 0.15)),
                        max_z=None, top_extent=0.5):
    """Small tumbling cubes in a loose arc hugging the portal silhouette.

    center:      centre of the arc (middle of the portal silhouette); the arc
                 lies in the portal plane (XZ), angles in ``arc`` are degrees
                 from +X counter-clockwise (90 = top); the bottom stays open.
    radius:      without keep_out_box: cubes sit on the ellipse
                 (radius, radius * squash_z) * radial_jitter.
    size_range:  (min, max) world size of a cube = largest side of its tilted
                 bounding box; most cubes use the lower 60 %, max is a hard cap.
    keep_out_box:(min_xyz, max_xyz) or a list of them (see
                 keep_out_boxes_from). Cubes then HUG the silhouette: each one
                 sits just outside its X/Z footprint plus a random ``hug_gap``
                 (so they follow the frame and never cover the opening from
                 the front). keep_out_y=True: only boxes that also overlap in Y
                 count.
    max_z:       no cube rises above this z (default: top of the keep-out +
                 0.55, i.e. just over the stem); cubes within 1.6 studs of it
                 are at most ``top_extent`` big.
    dark_ratio / max_dark: share (and hard cap) of dark stone cubes; the rest
                 glow orange.
    avoid:       objects (or (point, radius) pairs) to keep clear of, e.g. the
                 wisps, so cubes do not sit on top of them.
    Returns glow and dark cubes (every name contains 'Float' and 'Cube').
    """
    r = common.rng(seed)
    c = Vector(center)
    boxes = _boxes(keep_out_box)
    others = _avoid_list(avoid)
    base = f"{_pfx(prefix)}FloatCube_"
    names = _Counter(base)
    if max_z is None:
        max_z = (max(b[1].z for b in boxes) + 0.55) if boxes else c.z + radius * squash_z + 1.0
    n_dark = 0 if count < 3 else min(max_dark, max(1, round(count * dark_ratio)))
    # spread the dark cubes evenly through the arc
    dark_slots = set()
    if n_dark:
        off = r.uniform(0, count / n_dark)
        dark_slots = {int(off + k * count / n_dark) % count for k in range(n_dark)}
    a0, a1 = math.radians(arc[0]), math.radians(arc[1])
    # spread the cubes evenly along the (offset, capped) outline, not by angle
    angs = [a0 + (a1 - a0) * j / 120 for j in range(121)]
    path = []
    for a in angs:
        if boxes:
            rr = _radial_clear(c, a, c.y, 0.3, boxes, keep_out_y, step=0.1) + 0.6
            q = Vector((c.x + rr * math.cos(a), 0.0, c.z + rr * math.sin(a)))
        else:
            q = Vector((c.x + radius * math.cos(a), 0.0, c.z + radius * squash_z * math.sin(a)))
        q.z = min(q.z, max_z)
        path.append(q)
    cum = [0.0]
    for j in range(1, len(path)):           # half density along the top (above the stem)
        wgt = 0.5 if min(path[j].z, path[j - 1].z) > max_z - 1.2 else 1.0
        cum.append(cum[-1] + (path[j] - path[j - 1]).length * wgt)

    def angle_at(t):
        target = t * cum[-1]
        for j in range(1, len(cum)):
            if cum[j] >= target:
                f = (target - cum[j - 1]) / max(cum[j] - cum[j - 1], 1e-9)
                return angs[j - 1] + (angs[j] - angs[j - 1]) * f
        return angs[-1]

    lo, hi = size_range
    placed = []
    objs = []
    for i in range(count):
        dark = i in dark_slots
        if dark:
            s = r.uniform(lo + (hi - lo) * 0.3, lo + (hi - lo) * 0.65)
        elif r.random() < 0.15:
            s = r.uniform(lo + (hi - lo) * 0.6, hi)
        else:
            s = r.uniform(lo + (hi - lo) * 0.1, lo + (hi - lo) * 0.6)
        best, best_gap, best_s = None, -1e9, s
        for attempt in range(40):
            w = min(1.0, attempt / 25)             # crowded slot: look along the neighbours' slots too
            a = angle_at(min(max((i + r.uniform(0.1 - w, 0.9 + w)) / count, 0.0), 1.0))
            y = c.y + r.uniform(*depth)
            g = r.uniform(*hug_gap)
            p, d = _hug(c, a, y, squash_z, boxes, s * 0.6, g, keep_out_y,
                        radius * r.uniform(*radial_jitter))
            ps = s
            if p.z + 1.6 > max_z:                 # near the stem: small cubes only
                ps = min(s, top_extent)
            # cubes near the cap are lowered by a random amount and slid out
            # sideways, so the ones over the stem do not form a row
            z_cap = max_z - (r.uniform(0.0, 0.9) if p.z + ps * 0.6 > max_z - 0.9 else 0.0)
            p = _clamp_top(p, c, ps * 0.6, boxes, z_cap, g, keep_out_y)
            p.z = max(p.z, min_z + ps * 0.6)
            gaps = [(p - q).length - (ps + qs) * 0.6 for q, qs in placed]
            gaps += [math.hypot(p.x - q.x, p.z - q.z) - qr - ps * 0.6 for q, qr in others]
            gap = min(gaps or [9.0])
            if gap > best_gap:
                best, best_gap, best_s = p, gap, ps
            if gap > 0.8:
                break
        s = best_s
        placed.append((best, s))
        rot = Euler((r.uniform(0, math.tau), r.uniform(0, math.tau), r.uniform(0, math.tau))).to_matrix()
        bm = _cube_bm(s, rot, seed * 1000 + i)
        name = names.next()
        if dark:
            groups = _Groups()
            for f in bm.faces:
                groups.add(_dark_key(f.normal), f)
            objs.append(_finish_solid(name, bm, groups, best))
        else:
            key = _weighted(r, glow_keys)
            objs.append(_finish_glow(name, key, bm, best))
    return objs


def _weighted(r, pairs):
    x = r.uniform(0, sum(w for _, w in pairs))
    for k, w in pairs:
        x -= w
        if x <= 0:
            return k
    return pairs[-1][0]


# --------------------------------------------------------------------------
# Embers
# --------------------------------------------------------------------------
def make_embers(prefix, center=(0, 0, 13), radius=4.0, count=10, seed=0, height=3.0,
                size_range=(0.12, 0.3), keys=(("ember", 0.6), ("fire", 0.25), ("amber", 0.15))):
    """Tiny glowing diamonds rising above ``center`` (bottom of the cloud):
    denser and bigger near the bottom, sparse and small higher up."""
    r = common.rng(seed)
    c = Vector(center)
    names = _Counter(f"{_pfx(prefix)}FloatEmber_")
    placed = []
    objs = []
    for i in range(count):
        best, best_gap = None, -1e9
        for attempt in range(25):
            h = height * (r.random() ** 1.5)
            spread = radius * (0.55 + 0.45 * h / max(height, 1e-6))  # cloud widens upward
            rr = spread * math.sqrt(r.random())
            a = r.uniform(0, math.tau)
            p = Vector((c.x + rr * math.cos(a), c.y + rr * math.sin(a), c.z + h))
            gap = min([(p - q).length for q in placed] or [9.0])
            if gap > best_gap:
                best, best_gap = p, gap
            if gap > max(0.45, radius * 0.25):
                break
        placed.append(best)
        f = (best.z - c.z) / max(height, 1e-6)
        s = size_range[1] + (size_range[0] - size_range[1]) * f
        s *= r.uniform(0.85, 1.15)
        rot = Euler((r.uniform(-0.6, 0.6), r.uniform(-0.6, 0.6), r.uniform(0, math.tau))).to_matrix()
        bm = bmesh.new()
        _octa(bm, (0, 0, 0), s * 0.42, s * 0.42, s * 0.75, rot)
        bm.normal_update()
        objs.append(_finish_glow(names.next(), _weighted(r, keys), bm, best))
    return objs


# --------------------------------------------------------------------------
# Bats
# --------------------------------------------------------------------------
# Right wing outline (u = outward from the body axis, v = forward, i.e. -Y),
# CCW seen from above; the left wing is mirrored. Wingspan 2 -> u max = 1.
def _wing_outline():
    """Classic Halloween bat wing: leading edge with a wrist bump and thumb
    claw, pointed finger tips joined by curved scallops (pulled toward the
    wrist) on the trailing edge."""
    lead = [(0.08, 0.10), (0.27, 0.20), (0.43, 0.27),   # shoulder -> wrist
            (0.46, 0.34),                               # thumb claw
            (0.51, 0.26), (0.76, 0.22)]
    tips = [(1.00, 0.12), (0.86, -0.34), (0.62, -0.50), (0.36, -0.42), (0.08, -0.24)]
    wrist = (0.45, 0.10)
    depth = [0.30, 0.30, 0.27, 0.16]
    pts = list(lead)
    for i in range(len(tips) - 1):
        a, b = tips[i], tips[i + 1]
        pts.append(a)
        n_mid = 2 if i < 3 else 1
        for m in range(1, n_mid + 1):
            t = m / (n_mid + 1)
            px, py = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            pull = depth[i] * math.sin(math.pi * t)
            pts.append((px + (wrist[0] - px) * pull, py + (wrist[1] - py) * pull))
    pts.append(tips[-1])
    return pts


_WING = _wing_outline()
_WRIST_U = 0.47

POSES = {
    # (inner angle, outer angle) in degrees; positive = up
    "up": (26.0, 46.0),      # upstroke V
    "glide": (12.0, -6.0),   # gull wing
    "down": (2.0, -32.0),    # downstroke, tips low
}


def _fold(u, zoff, a_in, a_out, uw=_WRIST_U, hinge=False):
    """Map flat wing coordinates (u outward, zoff thickness) to (x, z).
    ``hinge`` verts (u == uw) are mitred: offset along the bisector by
    zoff / cos(half fold angle), so they lie on BOTH halves' surfaces."""
    hx, hz = uw * math.cos(a_in), uw * math.sin(a_in)
    if hinge:
        m = (a_in + a_out) / 2
        s = zoff / math.cos((a_out - a_in) / 2)
        return hx - s * math.sin(m), hz + s * math.cos(m)
    if u <= uw:
        ca, sa = math.cos(a_in), math.sin(a_in)
        return u * ca - zoff * sa, u * sa + zoff * ca
    du = u - uw
    ca, sa = math.cos(a_out), math.sin(a_out)
    return hx + du * ca - zoff * sa, hz + du * sa + zoff * ca


def _wing_slab(bm, flat, xw, th):
    """Closed slab from a CCW XY outline, split along the fold line |x| = xw:
    outline verts are inserted where the outline crosses it, the inner and
    outer halves get their own (ear-clipped) top/bottom faces, so no triangle
    spans the fold. Returns (tops, bots, sides, verts, hinge_verts)."""
    pts = []
    hinge_idx = []
    n = len(flat)
    for i in range(n):
        p, q = flat[i], flat[(i + 1) % n]
        pts.append(p)
        dp, dq = abs(p[0]) - xw, abs(q[0]) - xw
        if dp * dq < 0:
            t = dp / (dp - dq)
            hinge_idx.append(len(pts))
            pts.append((math.copysign(xw, p[0]), p[1] + (q[1] - p[1]) * t))
    if len(hinge_idx) != 2:
        raise RuntimeError(f"wing outline crosses the fold line {len(hinge_idx)} times (need 2)")
    h1, h2 = hinge_idx
    top = [bm.verts.new((x, y, th / 2)) for x, y in pts]
    bot = [bm.verts.new((x, y, -th / 2)) for x, y in pts]
    half_a = list(range(h1, h2 + 1))
    half_b = list(range(h2, len(pts))) + list(range(0, h1 + 1))
    tops, bots = [], []
    for half in (half_a, half_b):
        ft = bm.faces.new([top[i] for i in half])
        fb = bm.faces.new([bot[i] for i in reversed(half)])
        ft.normal_update()
        fb.normal_update()
        for f, out in ((ft, tops), (fb, bots)):
            res = bmesh.ops.triangulate(bm, faces=[f], quad_method="BEAUTY", ngon_method="EAR_CLIP")["faces"]
            out.extend(res or [f])
    sides = []
    m = len(pts)
    for i in range(m):
        j = (i + 1) % m
        sides.append(bm.faces.new((bot[i], bot[j], top[j], top[i])))
    hinge = {top[h1], top[h2], bot[h1], bot[h2]}
    return tops, bots, sides, top + bot, hinge


def _bat_body_bm(k, pose, seed):
    """Bat body + head + ears + fangs + wings in one bmesh (palette groups)."""
    r = common.rng(seed)
    bm = bmesh.new()
    g = _Groups()

    # body: plump spindle along Y (tail +Y, nose -Y)
    rings = [(0.30 * k, 0.05 * k, 0.04 * k, 0.03 * k),
             (0.17 * k, 0.12 * k, 0.11 * k, 0.0),
             (0.00 * k, 0.15 * k, 0.15 * k, 0.0),
             (-0.15 * k, 0.12 * k, 0.12 * k, 0.03 * k)]
    # both ends closed; the front tip lies inside the head so the neck is sealed
    body = _loft(bm, rings, sides=6, phase=math.pi / 6,
                 tip_start=(0, 0.38 * k, 0.05 * k), tip_end=(0, -0.25 * k, 0.06 * k))
    for f in body:
        n = f.normal
        g.add("ash" if n.z > 0.5 else ("black" if n.z < -0.4 else "charcoal"), f)

    # head: low uv-sphere, a bit wider than tall
    hc = Vector((0, -0.29 * k, 0.07 * k))
    hr = 0.18 * k
    head = bmesh.ops.create_uvsphere(bm, u_segments=6, v_segments=4, radius=hr,
                                     matrix=Matrix.Translation(hc) @ Matrix.Diagonal((1.12, 0.95, 0.95, 1.0)))
    hverts = head["verts"]
    hfaces = list({f for v in hverts for f in v.link_faces})
    bm.normal_update()
    for f in hfaces:
        n = f.normal
        if n.y < -0.55 and n.z < 0.3:
            g.add("ash", f)          # muzzle / face
        elif n.z > 0.5:
            g.add("ash", f)
        else:
            g.add("charcoal", f)

    # ears: big pointy tetrahedra leaning out, orange inner face
    for sx in (-1, 1):
        base = hc + Vector((sx * 0.10 * k, 0.02 * k, 0.11 * k))
        apex = hc + Vector((sx * 0.22 * k, 0.05 * k, 0.40 * k))
        ears = _tetra(bm, base, 0.075 * k, apex, phase=0.4 * sx)
        bm.normal_update()
        for f in ears:
            g.add("rust" if f.normal.y < -0.55 else "charcoal", f)

    # fangs: two tiny bone teeth under the muzzle
    for sx in (-1, 1):
        base = hc + Vector((sx * 0.055 * k, -0.135 * k, -0.075 * k))
        apex = base + Vector((0, -0.01 * k, -0.09 * k))
        g.add("bone", _tetra(bm, base, 0.028 * k, apex))

    # feet: two little hooks under the tail
    for sx in (-1, 1):
        base = Vector((sx * 0.06 * k, 0.16 * k, -0.08 * k))
        apex = base + Vector((sx * 0.02 * k, 0.07 * k, -0.12 * k))
        g.add("black", _tetra(bm, base, 0.03 * k, apex))

    # wings: closed thin slabs, folded at the wrist
    a_in, a_out = (math.radians(a) for a in POSES[pose])
    a_in += math.radians(r.uniform(-4, 4))
    a_out += math.radians(r.uniform(-5, 5))
    th = 0.045 * k
    for sx in (1, -1):
        pts = [(u * k, v * k) for u, v in _WING]
        if sx < 0:
            pts = [(-u, v) for u, v in reversed(pts)]
        # flat build in XY (v forward = -Y -> y = -v)
        flat = [(x, -y) for x, y in pts]
        # mirror flips winding; the outline must be CCW in XY
        if _area(flat) < 0:
            flat = list(reversed(flat))
        tops, bots, sides, verts, hinge = _wing_slab(bm, flat, _WRIST_U * k, th)
        for v in verts:
            u = abs(v.co.x)
            x, z = _fold(u / k, v.co.z / k, a_in, a_out, hinge=v in hinge)
            v.co.x = math.copysign(x * k, v.co.x)
            v.co.z = z * k + 0.02 * k
            v.co.y += 0.02 * k
        g.add("charcoal", tops)
        g.add("black", bots)
        g.add("ash", sides)
    bm.normal_update()
    return bm, g, hc, hr


def _area(pts):
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                     for i in range(len(pts)))


def _bat_eyes_bm(k, hc, hr):
    """Two slanted diamond eyes on the face (separate glow object)."""
    bm = bmesh.new()
    for sx in (-1, 1):
        c = hc + Vector((sx * 0.072 * k, -hr * 0.90, 0.035 * k))
        rot = Matrix.Rotation(sx * math.radians(-28), 3, "Y")
        _octa(bm, c, 0.05 * k, 0.03 * k, 0.03 * k, rot)
    bm.normal_update()
    return bm


def _bat_name(name):
    name = name.strip("_")
    if name.startswith("GLOW_"):
        name = _strip_glow(name)
    if not name.startswith("Bat_"):
        name = f"Bat_{name}"
    if not name[-1].isdigit():
        name = f"{name}_01"   # the script groups bat + eyes by the trailing number
    for word in RESERVED:
        if word in name:
            raise ValueError(f"bat name {name!r} contains reserved script keyword {word!r}")
    return name


def make_bat(name, location=(0, 0, 12), rotation_z=0.0, wingspan=2.0, seed=0, pose=None,
             bank_deg=0.0, eyes_key="ember", eyes=True):
    """Low-poly bat, nose -Y (before rotation_z), wings along X.

    name:      must start with 'Bat_' and end with a number (fixed up if not).
    location:  world position of the body centre.
    pose:      'up' | 'glide' | 'down' (None = pick from seed).
    bank_deg:  roll about the body axis, baked into the mesh (+ = right wing down).
    eyes:      True -> separate 'GLOW_<eyes_key>_<name>_Eyes' (same script group).
    Returns [bat] or [bat, eyes].
    """
    name = _bat_name(name)
    if name in _taken():
        raise ValueError(f"object name {name!r} already exists")
    k = wingspan / 2.0
    r = common.rng(seed)
    pose = pose or r.choice(sorted(POSES))
    bm, groups, hc, hr = _bat_body_bm(k, pose, seed)
    tilt = Matrix.Rotation(math.radians(bank_deg), 3, "Y") @ Matrix.Rotation(math.radians(r.uniform(-6, 8)), 3, "X")
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=tilt)
    bm.normal_update()
    bat = _finish_solid(name, bm, groups, location, rotation_z)
    out = [bat]
    if eyes:
        ebm = _bat_eyes_bm(k, hc, hr)
        bmesh.ops.rotate(ebm, verts=ebm.verts, cent=(0, 0, 0), matrix=tilt)
        ebm.normal_update()
        out.append(_finish_glow(f"{name}_Eyes", eyes_key, ebm, location, rotation_z))
    return out


def _swarm_avoid(avoid):
    """'auto' -> every Float object in the scene (cubes, wisps, embers,
    floating pumpkins); else objects or (point, radius) pairs. Returns
    [(centre, radius, clearance)]: cubes and floating pumpkins need 2 studs
    of air around them, the thin wisps and tiny embers 1 stud."""
    if avoid == "auto":
        avoid = [o for o in bpy.data.objects if o.type == "MESH" and "Float" in o.name]
    out = []
    for a in avoid or []:
        big = (not hasattr(a, "name")) or "Cube" in a.name or "Pumpkin" in a.name
        for q, qr in _avoid_list([a]):
            out.append((q, qr, 2.0 if big else 1.0))
    return out


def _judge_cams():
    """Approximate camera positions of the preview renders the static export
    pose is judged from (preview.VIEWS front / three_quarter / three_quarter_r
    at the build.py framing: 50 mm lens, 16:10, margin 1.15)."""
    fov = 2 * math.atan(math.tan(math.atan(18 / 50)) / 1.6)
    cams = []
    for (az, el), (ctr, rad) in (((0, 8), ((0, -1.0, 5.6), 11.6)),
                                 ((-35, 18), ((0, -0.5, 5.2), 12.0)),
                                 ((35, 18), ((0, -0.5, 5.2), 12.0))):
        az, el = math.radians(az), math.radians(el)
        d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
        cams.append(Vector(ctr) + d * (rad * 1.15 / math.sin(fov / 2)))
    return cams


_JUDGE_CAMS = _judge_cams()


def _screen_gap(p, rp, q, rq):
    """Smallest on-screen gap (studs at p's depth) between a ball of radius
    ``rp`` at p and one of ``rq`` at q, over the judge cameras (perspective)."""
    worst = 1e9
    for cam in _JUDGE_CAMS:
        a, b = p - cam, q - cam
        la, lb = a.length, b.length
        worst = min(worst, a.angle(b, 0.0) * la - rp - rq * la / lb)
    return worst


def _bat_clearance(p, hs, others, bats):
    """Worst margin (studs, >= 0 is fine) of a bat centred at ``p`` with half
    wingspan ``hs``: 3D air around cubes/wisps/embers, no overlap with them
    or with other bats as seen from the front / three-quarter views (the
    static export pose), room between bats."""
    worst = 9.0
    for q, qr, need in others:
        d3 = (p - q).length
        worst = min(worst, d3 - qr - need, d3 - qr - hs - 0.5,
                    _screen_gap(p, hs * 0.9 + 0.3, q, qr))
    for q, qh in bats:
        worst = min(worst, (p - q).length - (hs + qh + 1.0),
                    _screen_gap(p, hs * 0.8, q, qh * 0.8))
    return worst


def _bvh_hits(objs, skip):
    """True if any of ``objs`` intersects another mesh in the scene."""
    from mathutils.bvhtree import BVHTree

    def tree(o):
        mw = o.matrix_world
        return BVHTree.FromPolygons([mw @ v.co for v in o.data.vertices],
                                    [tuple(p.vertices) for p in o.data.polygons])

    for o in objs:
        lo, hi = keep_out_box_from([o], 0.0)
        t = None
        for other in bpy.data.objects:
            if other.type != "MESH" or other.name in skip:
                continue
            plo, phi = keep_out_box_from([other], 0.0)
            if any(lo[i] > phi[i] or plo[i] > hi[i] for i in range(3)):
                continue
            t = t or tree(o)
            if t.overlap(tree(other)):
                return True
    return False


def make_bat_swarm(prefix, center=(0, 0, 0), radius=11.0, count=5, height=(11.0, 17.0), seed=0,
                   wingspan=(1.8, 2.6), clockwise=False, eyes_key="ember", avoid="auto"):
    """Bats spread around ``center`` (portal ground centre) at ``radius``,
    heights in ``height`` (absolute z offset from center.z), noses along the
    orbit tangent, slightly banked into the turn. Returns all bat objects.

    avoid: 'auto' (default) keeps each bat's start position clear of every
    ``*Float*`` object already in the scene (>= 2 studs of air around cubes
    and floating pumpkins, 1 around wisps/embers, no overlap with them in the
    front view) and of the other bats; candidates that intersect any other
    mesh are skipped. Pass a list of objects / (point, radius) pairs, or None.
    Build the swarm after the cubes so it can see them."""
    r = common.rng(seed)
    c = Vector(center)
    names = _Counter(f"Bat_{_pfx(prefix)}")
    others = _swarm_avoid(avoid)
    objs = []
    bats = []
    off = r.uniform(0, math.tau)
    poses = sorted(POSES)
    sgn = -1.0 if clockwise else 1.0
    for i in range(count):
        th0 = off + math.tau * (i + r.uniform(-0.18, 0.18)) / count
        rr0 = radius * r.uniform(0.85, 1.15)
        hz0 = height[0] + (height[1] - height[0]) * ((i * 0.618 + r.uniform(0, 0.3)) % 1.0)
        span = r.uniform(*wingspan)
        hs = span / 2
        bank = 14.0 * sgn * r.uniform(0.6, 1.2)
        pose = poses[(i + int(off * 10)) % len(poses)]
        # candidate start positions: the planned one first, then wider searches
        cands = []
        for attempt in range(80):
            if attempt == 0:
                th, rr, hz = th0, rr0, hz0
            else:
                f = min(1.0, 0.25 + attempt / 40)
                th = th0 + r.uniform(-0.42, 0.42) * math.tau / count * f
                rr = radius * r.uniform(0.85, 1.0 + 0.25 * f)
                hz = r.uniform(*height)
            p = Vector((c.x + rr * math.cos(th), c.y + rr * math.sin(th), c.z + hz))
            cands.append((_bat_clearance(p, hs, others, bats), attempt, p, th))
        good = [cd for cd in cands if cd[0] >= 0.0]
        ranked = sorted(good, key=lambda cd: cd[1]) + sorted(
            [cd for cd in cands if cd[0] < 0.0], key=lambda cd: -cd[0])
        name = names.next()
        made = None
        for score, _attempt, p, th in ranked[:24]:
            tx, ty = -math.sin(th) * sgn, math.cos(th) * sgn
            rz = math.atan2(tx, -ty)          # local -Y (nose) -> tangent
            made = make_bat(name, p, rz, wingspan=span, seed=seed * 100 + i, pose=pose,
                            bank_deg=-bank, eyes_key=eyes_key)
            if not _bvh_hits(made, {o.name for o in made}):
                break
            for o in made:                    # intersects a prop: try the next spot
                me = o.data
                bpy.data.objects.remove(o)
                bpy.data.meshes.remove(me)
            made = None
        if made is None:                      # nothing clean: fall back to the best score
            score, _attempt, p, th = ranked[0]
            tx, ty = -math.sin(th) * sgn, math.cos(th) * sgn
            made = make_bat(name, p, math.atan2(tx, -ty), wingspan=span, seed=seed * 100 + i,
                            pose=pose, bank_deg=-bank, eyes_key=eyes_key)
        bats.append((p, hs))
        objs += made
    return objs


# --------------------------------------------------------------------------
# Magic circle
# --------------------------------------------------------------------------
def _circle_pts(r, segs, a0=0.0, a1=None, closed=True):
    if closed:
        return [Vector((r * math.cos(a0 + math.tau * i / segs), r * math.sin(a0 + math.tau * i / segs), 0))
                for i in range(segs)]
    return [Vector((r * math.cos(a0 + (a1 - a0) * i / segs), r * math.sin(a0 + (a1 - a0) * i / segs), 0))
            for i in range(segs + 1)]


def _diamond(bm, c, along, length, width, z0, z1):
    """Flat diamond slab centred at c (2D), long axis along unit 'along'."""
    a = Vector((along[0], along[1]))
    p = Vector((-a.y, a.x))
    c = Vector((c[0], c[1]))
    pts = [c + a * length / 2, c + p * width / 2, c - a * length / 2, c - p * width / 2]
    return _poly_slab(bm, [(q.x, q.y) for q in pts], z0, z1)


def make_magic_circle(name, location=(0, 0, 0), radius=5.0, seed=0, key="fire", z_offset=0.04,
                      thickness=0.04, arms=3, beads=12):
    """Flat glowing ring decal: bold outer ring, a band of diamond beads, a
    broken ring of arcs, ``arms`` vortex arms swirling to a small centre ring.
    The mesh bottom sits ``z_offset`` above ``location`` (no z-fighting).
    Returns [GLOW_<key>_<name>]."""
    stripped = _strip_glow(name)
    for word in RESERVED:
        if word in stripped:
            raise ValueError(f"name {stripped!r} contains reserved script keyword {word!r}")
    if f"GLOW_{key}_{stripped}" in _taken():
        raise ValueError(f"object name GLOW_{key}_{stripped} already exists")
    r = common.rng(seed)
    R = radius
    z0, z1 = 0.0, thickness
    up = Vector((0, 0, 1))
    bm = bmesh.new()

    def ring(rad, w, segs, a0=0.0):
        pts = _circle_pts(rad, segs, a0)
        _ribbon(bm, [p + up * (z0 + z1) / 2 for p in pts], [up] * len(pts), [w] * len(pts), z1 - z0, closed=True)

    def arc(rad, w, a0, a1, segs, taper=0.0):
        pts = _circle_pts(rad, segs, a0, a1, closed=False)
        n = len(pts)
        ws = [w * (1 - taper * abs(2 * i / (n - 1) - 1) ** 2) for i in range(n)]
        _ribbon(bm, [p + up * (z0 + z1) / 2 for p in pts], [up] * n, ws, z1 - z0)

    wo = max(0.14, R * 0.035)
    segs_o = max(24, int(R * 6.4))
    ring(R - wo / 2, wo, segs_o)                                   # bold outer ring
    # bead band: alternating long/short diamonds pointing at the centre
    rb = R - wo - max(0.26, R * 0.075)
    phase = r.uniform(0, math.tau / beads)
    for i in range(beads):
        a = phase + math.tau * i / beads
        d = (math.cos(a), math.sin(a))
        big = i % 2 == 0
        L = max(0.34, R * 0.11) * (1.0 if big else 0.55)
        W = max(0.2, R * 0.062) * (1.0 if big else 0.7)
        _diamond(bm, (rb * d[0], rb * d[1]), d, L, W, z0, z1)
    # thin full ring inside the beads
    ri = rb - max(0.28, R * 0.08)
    ring(ri, max(0.06, R * 0.014), max(20, int(R * 5.2)), a0=phase)
    # broken ring of tapered arcs
    ra = ri * 0.78
    n_arcs = 6
    gap = math.radians(14)
    for i in range(n_arcs):
        a0 = phase + math.tau * i / n_arcs + gap / 2
        a1 = phase + math.tau * (i + 1) / n_arcs - gap / 2
        arc(ra, max(0.1, R * 0.03), a0, a1, 4, taper=0.6)
    # vortex arms: spirals from the centre ring out to the arc ring
    rc = max(0.35, R * 0.11)
    for j in range(arms):
        a_start = phase + math.tau * j / arms
        sweep = math.radians(115)
        n = 7
        pts, ws = [], []
        for i in range(n + 1):
            t = i / n
            rad = rc + (ra * 0.80 - rc) * t
            a = a_start + sweep * t
            pts.append(Vector((rad * math.cos(a), rad * math.sin(a), (z0 + z1) / 2)))
            ws.append(max(0.05, R * 0.045) * math.sin(math.pi * min(1.0, 0.15 + t * 0.95)) + 0.03)
        _ribbon(bm, pts, [up] * len(pts), ws, z1 - z0)
    ring(rc, max(0.08, R * 0.018), 10)                             # centre ring
    bm.normal_update()
    loc = Vector(location) + Vector((0, 0, z_offset))
    return [_finish_glow(stripped, key, bm, loc)]


# --------------------------------------------------------------------------
# Wisps
# --------------------------------------------------------------------------
def _ellipse_pt(c, R, squash, a, y):
    return Vector((c.x + R * math.cos(a), y, c.z + R * squash * math.sin(a)))


def _arc_ribbon(bm, pts, width, thick, head=1.35):
    """Thin flat ribbon (flat side facing -Y) along ``pts``, tapering to a
    point at both ends; the widest part sits ahead of the middle (towards
    the last point = direction of travel)."""
    n = len(pts) - 1
    ws = []
    for i in range(n + 1):
        x = (i / n) ** head
        ws.append(width * math.sin(math.pi * min(max(x, 0.001), 0.999)) ** 1.1)
    up = Vector((0.0, -1.0, 0.0))
    _ribbon(bm, pts, [up] * len(pts), ws, thick)


def _radial_clear(c, a, y, rad, boxes, use_y, r0=0.5, step=0.05):
    """Distance from ``c`` along angle ``a`` (portal plane) to the first
    point that clears the keep-out boxes."""
    d = Vector((math.cos(a), 0.0, math.sin(a)))
    rr = r0
    while rr < 40.0 and _inside(Vector((c.x, y, c.z)) + d * rr, rad, boxes, use_y):
        rr += step
    return rr


def _smooth_profile(rs, passes=2):
    """Dilate then average a radius profile, never going below the input."""
    n = len(rs)
    out = list(rs)
    for _ in range(passes):
        dil = [max(out[max(i - 1, 0):i + 2]) for i in range(n)]
        out = [sum(dil[max(i - 1, 0):i + 2]) / len(dil[max(i - 1, 0):i + 2]) for i in range(n)]
    return [max(a, b) for a, b in zip(out, rs)]


def _outline_arc(c, a_mid, length, y, rad, boxes, use_y, gap, n=10):
    """Points of an arc ~``length`` long centred on angle ``a_mid`` that runs
    ``gap`` studs outside the silhouette (smoothed), CCW order."""
    r_mid = _radial_clear(c, a_mid, y, rad, boxes, use_y) + gap
    sweep = length / max(r_mid, 1.0)
    angs = [a_mid - sweep / 2 + sweep * i / n for i in range(n + 1)]
    rs = _smooth_profile([_radial_clear(c, a, y, rad, boxes, use_y) for a in angs])
    return [Vector((c.x + (rr + gap) * math.cos(a), y, c.z + (rr + gap) * math.sin(a)))
            for a, rr in zip(angs, rs)], angs, [rr + gap for rr in rs]


def make_wisps(prefix, center=(0, 0, 7.5), radius=9.0, count=6, seed=0, arc=(24.0, 156.0),
               depth=(-0.75, -0.45), length=(3.6, 4.6), width=(0.16, 0.22), clockwise=False,
               keep_out_box=None, hug_gap=(0.1, 0.35), squash_z=0.88, keep_out_y=False,
               keys=(("fire", 1.0),), skip_arc=(78.0, 102.0), spacing=1.4, thickness=0.07,
               echo=True):
    """Thin tapered energy arcs that follow the portal outline like a broken
    halo, all swirling the same way.

    The arcs lie in the portal plane (flat side facing -Y, y in ``depth``,
    just in front of the tendrils) and run tangentially around ``center``.
    ``arc`` (degrees from +X CCW, 90 = top) minus ``skip_arc`` (kept free
    above the stem) is where they may go; the default keeps them on the upper
    shoulders, clear of the braziers. ``count`` is an upper bound: as many
    arcs as fit with ``spacing`` studs between them are made, split evenly
    between the free sectors. echo=True adds a shorter, thinner parallel
    line just outside each arc's tail (cartoon speed lines, same object).

    With keep_out_box every point of an arc sits ``hug_gap`` outside the
    silhouette (smoothed, so it follows the outline); without it the arcs lie
    on the ellipse (radius, radius * squash_z).
    Returns GLOW_<key>_<prefix>_FloatWisp_## objects (default key: fire)."""
    r = common.rng(seed)
    c = Vector(center)
    boxes = _boxes(keep_out_box)
    names = _Counter(f"{_pfx(prefix)}FloatWisp_")
    sgn = -1.0 if clockwise else 1.0
    # free sectors (radians)
    a0, a1 = math.radians(arc[0]), math.radians(arc[1])
    sectors = [(a0, a1)]
    if skip_arc:
        s0, s1 = math.radians(skip_arc[0]), math.radians(skip_arc[1])
        sectors = [(lo, hi) for lo, hi in ((a0, min(a1, s0)), (max(a0, s1), a1)) if hi - lo > 1e-3]
    # how many arcs fit in each sector
    fits = []
    for lo, hi in sectors:
        R_est = radius
        if boxes:
            R_est = _radial_clear(c, (lo + hi) / 2, c.y, 0.1, boxes, keep_out_y) + sum(hug_gap) / 2
        fits.append(max(0, int((hi - lo) * R_est / (sum(length) / 2 + spacing))))
    if sectors and count > 0 and not any(fits):
        fits[max(range(len(sectors)), key=lambda j: sectors[j][1] - sectors[j][0])] = 1
    want = min(count, sum(fits))
    alloc = [0] * len(sectors)
    order = sorted(range(len(sectors)), key=lambda k: -fits[k])
    k = 0
    while sum(alloc) < want:                 # round-robin, symmetric when possible
        j = order[k % len(order)]
        if alloc[j] < fits[j]:
            alloc[j] += 1
        k += 1
    objs = []
    for (lo, hi), n in zip(sectors, alloc):
        for i in range(n):
            a = lo + (hi - lo) * (i + 0.5 + r.uniform(-0.1, 0.1)) / n
            y = c.y + r.uniform(*depth)
            w = r.uniform(*width)
            L = r.uniform(*length)
            gap = r.uniform(*hug_gap)
            if boxes:
                pts, angs, rads = _outline_arc(c, a, L, y, w / 2 + 0.05, boxes, keep_out_y, gap)
            else:
                R = radius * r.uniform(0.96, 1.05)
                sweep = L / R
                angs = [a - sweep / 2 + sweep * j / 10 for j in range(11)]
                pts = [_ellipse_pt(c, R, squash_z, aa, y) for aa in angs]
                rads = [R] * len(angs)
            if sgn < 0:                       # head leads in the direction of travel
                pts.reverse()
                angs.reverse()
                rads.reverse()
            bm = bmesh.new()
            _arc_ribbon(bm, pts, w, thickness)
            if echo:                          # speed line outside the tail half
                m = len(pts) - 1
                i0, i1 = 1, int(m * 0.6)
                off = w * 1.2 + 0.22
                e_pts = [p + (p - Vector((c.x, y, c.z))).normalized() * off for p in pts[i0:i1 + 1]]
                _arc_ribbon(bm, e_pts, w * 0.55, thickness, head=1.0)
            bm.normal_update()
            # move into a local frame around the arc centre, place there
            mid = pts[len(pts) // 2].copy()
            bmesh.ops.translate(bm, verts=bm.verts, vec=-mid)
            objs.append(_finish_glow(names.next(), _weighted(r, keys), bm, mid))
    return objs


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------
def _placeholder_portal():
    """Dark 13 x 14 disc centred at (0, 0, 7) facing -Y, plus a low dais."""
    objs = []
    bm = bmesh.new()
    n = 28
    pts = [(6.5 * math.cos(math.tau * i / n), 7.0 * math.sin(math.tau * i / n)) for i in range(n)]
    tops, bots, sides, _ = _poly_slab(bm, pts, -0.75, 0.75)
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "X"))
    bm.normal_update()
    g = _Groups()
    g.add("charcoal", tops + bots)
    g.add("ash", sides)
    objs.append(_finish_solid("Placeholder_PortalDisc", bm, g, (0, 0, 7)))
    bm = bmesh.new()
    pts = [(7.0 * math.cos(math.tau * i / 24), 7.0 * math.sin(math.tau * i / 24)) for i in range(24)]
    tops, bots, sides, _ = _poly_slab(bm, pts, 0.0, 0.8)
    g = _Groups()
    g.add("stone", tops)
    g.add("stone_dark", bots + sides)
    objs.append(_finish_solid("Placeholder_Dais", bm, g, (0, 0, 0)))
    return objs


def build_demo_set(real_portal=False):
    """Everything the demo shows; returns (stand_in_objects, vfx_objects)."""
    if real_portal:
        from parts import portal  # optional: the real portal for judging composition
        stand = portal.build_portal()
        info = portal.PORTAL_INFO
        body = [o for o in stand if "Portal_Frame" in o.name or "PortalTendrils" in o.name
                or "PortalRim" in o.name]
        keep = keep_out_boxes_from(body, bands=7, margin=0.4)
        center = (0.0, 0.0, (info["dais_top_z"] + info["frame_body_top_z"]) / 2 + 0.6)
        top_z = info["frame_height"]
    else:
        stand = _placeholder_portal()
        keep = keep_out_boxes_from(stand[:1], bands=7, margin=0.9)   # + room for tendrils
        center = (0.0, 0.0, 7.4)
        top_z = 14.0
    vfx = []
    wisps = make_wisps("Portal", center=center, radius=9.5, count=5, seed=11, keep_out_box=keep)
    vfx += wisps
    vfx += make_floating_cubes("Portal", center=center, radius=9.0, count=14, seed=3, keep_out_box=keep,
                               avoid=wisps)
    vfx += make_embers("PortalTop", center=(0.4, 0, top_z - 1.0), radius=2.6, count=6, seed=5, height=3.6)
    for side, x in (("L", -10.5), ("R", 10.5)):
        vfx += make_embers(f"Brazier_{side}", center=(x, -5.0, 4.6), radius=0.8, count=6,
                           seed=7 if side == "L" else 8, height=3.2)
    vfx += make_bat_swarm("Portal", center=(0, 0, 0), radius=12.5, count=5, height=(11.5, 17.0), seed=2)
    vfx += make_magic_circle("MagicCircle_01", (0, -11.5, 0), radius=3.6, seed=1)
    return stand, vfx


def build_catalog():
    """Close-up line-up of every VFX kind (for detail checks)."""
    objs = []
    for i, pose in enumerate(("up", "glide", "down")):
        objs += make_bat(f"Bat_Cat_{i + 1:02d}", (-3.2 + 3.2 * i, 0, 3.2), 0.0, wingspan=2.4,
                         seed=i, pose=pose)
    objs += make_floating_cubes("Cat", center=(0, 0, 0.6), radius=4.0, count=7, seed=1,
                                arc=(200, 340), squash_z=0.1, depth=(-0.2, 0.2), min_z=0.0)
    objs += make_embers("Cat", center=(5.6, 0, 0.3), radius=0.7, count=6, seed=2, height=2.4)
    objs += make_wisps("Cat", center=(0, 0, 1.2), radius=4.6, count=1, seed=4, arc=(158, 202),
                       depth=(-0.2, 0.2))
    objs += make_magic_circle("MagicCircle_Cat", (0, 6.0, 0), radius=3.0, seed=1)
    return objs


def _report(objs, title):
    print(f"=== {title} ===")
    total = 0
    for o in objs:
        t = common.tri_count(o)
        total += t
        probs = common.validate_object(o)
        vol = _check_volume(o)
        flag = "" if vol > 0 else "  NEGATIVE VOLUME"
        print(f"  {o.name:40s} {t:5d} tris  vol={vol:8.4f}{flag}  {'; '.join(probs)}")
    print(f"  total {total} tris in {len(objs)} objects")
    return total


def demo(views=("front", "three_quarter"), samples=32, real_portal=False, catalog=False):
    import preview
    common.reset_scene()
    if catalog:
        objs = build_catalog()
        _report(objs, "vfx catalog")
        preview.render("previews/_wip/vfx_catalog.png", views=list(views), res=(960, 600),
                       samples=samples, focus=((0, 1.5, 2.2), 6.2))
        return
    stand, vfx = build_demo_set(real_portal)
    _report(vfx, "vfx demo")
    out = "previews/_wip/vfx_real.png" if real_portal else "previews/_wip/vfx.png"
    preview.render(out, views=list(views), res=(960, 600), samples=samples,
                   focus=((0, -1.0, 9.0), 11.5))


if __name__ == "__main__":
    _args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    _views = [a for a in _args if a in ("front", "three_quarter", "three_quarter_r", "side", "top",
                                         "back", "low_hero")] or ["front", "three_quarter"]
    demo(views=_views, real_portal="real" in _args, catalog="catalog" in _args)
