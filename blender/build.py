"""Assemble the final Halloween portal diorama, validate it, export it, render it.

Run (headless, bpy 5.2):

    <venv>/bin/python blender/build.py                 # full pipeline
    <venv>/bin/python blender/build.py --quick         # layout iteration: fast WIP renders only
    <venv>/bin/python blender/build.py --quick --views=front,top

Pipeline (default):
  1. common.reset_scene(), build the pumpkin portal (parts.portal) at the origin.
  2. Compose the accessories around it (parts.pumpkins / graveyard / lights / vfx)
     into collections Portal, Pumpkins, Graveyard, Lights, VFX.
  3. Validate every mesh (common.validate_object, unique names, budgets, UV range,
     one material per mesh, resting on the ground, bad overlaps) and the name contract
     exactly as roblox/HalloweenPortalVFX.server.lua classifies parts (stripGlow /
     groupKey / JoinGroupsByKey adoption, spinners centred on the hub, round magic
     circle, no grounded prop pulled into an animated group, no frozen airborne part).
  4. Triangulate in place (bmesh) and export export/HalloweenPortal.fbx with the
     Roblox-friendly settings from roblox/EXPORT_NOTES.md.
  5. Pack images and save export/HalloweenPortal.blend.
  6. Render previews/halloween_portal_<view>.png (+ previews/_wip/scale_check_*.png
     with a 5-stud capsule that is NOT exported).
  7. Re-import the FBX into a fresh scene and compare names, materials, embedded
     textures, triangle counts and world-space bounds with the built scene.

Layout (studs, portal faces -Y, the player approaches from -Y):
  * walk-in lane x in [-3.5, 3.5], y < -1.3 is kept clear (only the flat magic
    circle "welcome mat" lies on the ground at the foot of the dais steps);
  * braziers flank the portal just outside the tendril zone (x = +-10);
  * pumpkin piles with jack-o'-lanterns at the front corners of the dais, a lone
    jack-o'-lantern and small pumpkins beside the dais, candy corn and a skull
    scattered at the front;
  * candles on the dais ledge either side of the lane and on the dais top behind
    the frame (the front corners of the top are covered by the glowing cracks);
  * cauldron (orange brew) at the front-left corner balanced by a big pumpkin wearing
    a witch hat at the front-right corner; a lantern post just outside each brazier
    with its lantern swung towards the player, so brazier, post and lantern read as
    separate silhouettes from the front and both three-quarter views;
  * graveyard behind on both sides (gravestones + mounds, bone pile, shovel),
    an iron fence arc behind the portal with a gap and a spider web on its end
    post, a dead tree with a hanging lantern back-right;
  * VFX: bats circling above, floating cubes hugging the frame, swoosh wisps,
    embers over the braziers and the stem, two floating pumpkins.
Props are dropped at a preferred spot and then nudged (spiral search, optional
yaw) to the nearest spot where their triangles intersect nothing already placed
and their placement rule holds (off the dais, on the ledge, clear of cracks ...).
Candle puddles on the jittered dais top are draped onto the stone (drape_bottom).

Validation also flags near-coplanar same-facing faces between meshes (< 30 mm,
depth-flicker risk at distance) and ground props more than 5 mm off z=0. The FBX
stores texture paths as relative 'textures/<file>' (pixels embedded), never the
build machine's absolute path.
"""

import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bpy  # noqa: E402  (must come before bmesh)
import bmesh  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

import common  # noqa: E402
import preview  # noqa: E402
from parts import graveyard, lights, portal, pumpkins, vfx  # noqa: E402

FBX_PATH = os.path.join(common.EXPORT_DIR, "HalloweenPortal.fbx")
BLEND_PATH = os.path.join(common.EXPORT_DIR, "HalloweenPortal.blend")
COLLECTIONS = ("Portal", "Pumpkins", "Graveyard", "Lights", "VFX")

FINAL_VIEWS = ("front", "three_quarter", "three_quarter_r", "top", "low_hero", "back")
LANE_X = 3.5             # keep |x| < LANE_X clear in front of the portal
TRI_BUDGET_SCENE = 45000
TRI_LIMIT_MESH = 20000
RESERVED_SUFFIXES = ("_Att", "_OuterCage", "_InnerCage")
GROUND_TOL = 0.005       # ground props must touch z=0 within 5 mm (Model bbox bottom = baseplate)
COPLANAR_GAP = 0.03      # same-facing faces of two meshes closer than this = depth-flicker risk
LANTERN_L = (-14.3, -3.0, -math.pi / 2)    # LanternPost_01 foot (x, y) + yaw (lantern hangs towards -Y)
LANTERN_R = (14.2, -3.0, -math.pi / 2)     # LanternPost_02: mirrors LANTERN_L, clear of Brazier_R in all views
DAIS_NAMES = ("Portal_Dais", "GLOW_ember_PortalCracks_Dais")   # what dais-top props may rest on


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def world_verts(obj):
    mw = obj.matrix_world
    return [mw @ v.co for v in obj.data.vertices]


def world_bounds(objs):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def bvh_world(obj):
    verts = world_verts(obj)
    polys = [list(p.vertices) for p in obj.data.polygons]
    return BVHTree.FromPolygons(verts, polys)


def shift(objs, dx=0.0, dy=0.0, dz=0.0):
    """Rigidly translate a group of objects (keeps relative placement)."""
    t = Matrix.Translation(Vector((dx, dy, dz)))
    for o in objs:
        o.matrix_world = t @ o.matrix_world
    return objs


def spin_group(objs, pivot, angle):
    """Rigidly rotate a group about the vertical axis through ``pivot``."""
    if abs(angle) < 1e-9:
        return objs
    m = Matrix.Translation(pivot) @ Matrix.Rotation(angle, 4, "Z") @ Matrix.Translation(-Vector(pivot))
    for o in objs:
        o.matrix_world = m @ o.matrix_world
    return objs


def rot_vec(x, y, rz):
    c, s = math.cos(rz), math.sin(rz)
    return x * c - y * s, x * s + y * c


def face_towards(x, y, tx=0.0, ty=-24.0, amount=1.0):
    """rotation_z that turns a -Y facing prop at (x, y) towards (tx, ty)."""
    return amount * math.atan2(tx - x, -(ty - y))


def rename(obj, new):
    obj.name = new
    obj.data.name = new
    return obj


class Surface:
    """Downward ray casts against support meshes (dais, ...)."""

    def __init__(self, objs):
        self.trees = [bvh_world(o) for o in objs]

    def z(self, x, y, default=0.0):
        best = None
        for t in self.trees:
            hit = t.ray_cast(Vector((x, y, 60.0)), Vector((0, 0, -1)))
            if hit[0] is not None:
                best = hit[0].z if best is None else max(best, hit[0].z)
        return default if best is None else best

    def rest_z(self, x, y, r, default=0.0):
        """Lowest support height under a disc (centre + ring) -> nothing floats."""
        zs = [self.z(x, y, default)]
        for k in range(8):
            a = math.tau * k / 8
            zs.append(self.z(x + r * math.cos(a), y + r * math.sin(a), default))
        return min(zs)


class Cracks:
    """2D segments of the glowing dais cracks / inlay (top faces) so props on the
    dais never sit on them (their tops are nearly coplanar with candle puddles)."""

    def __init__(self, obj):
        self.segs = []
        mw = obj.matrix_world
        for p in obj.data.polygons:
            n = (mw.to_3x3() @ p.normal)
            if n.z < 0.9:
                continue
            vs = [mw @ obj.data.vertices[i].co for i in p.vertices]
            for i in range(len(vs)):
                a, b = vs[i], vs[(i + 1) % len(vs)]
                self.segs.append((a.x, a.y, b.x, b.y))

    def dist(self, x, y):
        best = 1e9
        for ax, ay, bx, by in self.segs:
            vx, vy = bx - ax, by - ay
            L = vx * vx + vy * vy
            u = 0.0 if L == 0 else max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / L))
            best = min(best, math.hypot(ax + u * vx - x, ay + u * vy - y))
        return best


def drape_bottom(obj, surf, embed=0.004, band=0.06):
    """Conform the bottom ``band`` (object-local z) of ``obj`` to an uneven support:
    every such vertex keeps its height above the object's base but measured from the
    support surface right under it, minus ``embed``. Used for wax puddles on the
    jittered dais top. The origin is re-based afterwards (bottom centre)."""
    mw = obj.matrix_world.copy()
    inv = mw.inverted()
    me = obj.data
    for v in me.vertices:
        if v.co.z > band:
            continue
        w = mw @ v.co
        w.z = surf.z(w.x, w.y, default=mw.translation.z) - embed + v.co.z
        v.co = inv @ w
    me.update()
    common.origin_to_base(obj)
    return obj


# --------------------------------------------------------------------------
# Scene assembly
# --------------------------------------------------------------------------
class Diorama:
    """Bookkeeping for the assembled scene + collision-aware placement."""

    def __init__(self):
        self.colls = {n: common.ensure_collection(n) for n in COLLECTIONS}
        self.group_of = {}      # object name -> prop group (for overlap checks)
        self.objs = []
        self.ground = {}        # prop group -> "ground" | "dais" | "pumpkin" | "fence" | "decal" | "air"
        self._bvh = {}
        self._box = {}

    def add(self, coll, group, objs, support="ground"):
        for o in objs:
            common.link(o, self.colls[coll])
            self.group_of[o.name] = group
            self.objs.append(o)
            self._bvh.pop(o.name, None)
            self._box.pop(o.name, None)
        self.ground[group] = support
        return objs

    def _tree(self, o):
        t = self._bvh.get(o.name)
        if t is None:
            t = self._bvh[o.name] = bvh_world(o)
        return t

    def _bounds(self, o):
        b = self._box.get(o.name)
        if b is None:
            b = self._box[o.name] = world_bounds([o])
        return b

    def hits(self, objs, ignore_groups=(), include_air=False, ignore_names=()):
        """Names of placed objects (other groups) whose triangles intersect ``objs``."""
        mine = {o.name for o in objs} | set(ignore_names)
        out = []
        for o in objs:
            lo, hi = world_bounds([o])
            t = None
            for p in self.objs:
                if p.name in mine:
                    continue
                g = self.group_of[p.name]
                if g in ignore_groups or (not include_air and self.ground.get(g) == "air"):
                    continue
                plo, phi = self._bounds(p)
                if lo.x > phi.x or plo.x > hi.x or lo.y > phi.y or plo.y > hi.y or lo.z > phi.z or plo.z > hi.z:
                    continue
                t = t or bvh_world(o)
                if t.overlap(self._tree(p)):
                    out.append(p.name)
        return out

    def place_free(self, objs, pref, ok=None, z_at=None, ignore_groups=(), include_air=False,
                   step=0.2, tries=500, label="", ignore_names=(), spins=(0.0,)):
        """Move the rigid group ``objs`` (anchor = first object's origin) to the free
        spot nearest ``pref`` (x, y): no intersection with placed props, and
        ok(objs) True. z_at(x, y) gives the support height (default: keep z).
        ``spins``: extra yaw angles (radians, about the anchor) tried at each spot."""
        anchor = objs[0].matrix_world.translation.copy()
        cur = Vector((anchor.x, anchor.y, anchor.z))
        spun = 0.0
        x0, y0 = pref
        for k in range(tries):
            if k == 0:
                x, y = x0, y0
            else:
                a = k * 2.39996
                d = step * math.sqrt(k)
                x, y = x0 + d * math.cos(a), y0 + d * math.sin(a)
            z = z_at(x, y) if z_at else cur.z
            shift(objs, x - cur.x, y - cur.y, z - cur.z)
            cur = Vector((x, y, z))
            for sp in spins:
                spin_group(objs, cur, sp - spun)
                spun = sp
                if (ok is None or ok(objs)) and not self.hits(objs, ignore_groups, include_air, ignore_names):
                    if k or sp:
                        print(f"[build] {label or objs[0].name}: moved {math.hypot(x - x0, y - y0):.2f} "
                              f"from {pref}, spun {math.degrees(sp):.0f} deg")
                    return True
        spin_group(objs, cur, -spun)
        print(f"[build] WARNING: no free spot for {label or objs[0].name} near {pref}")
        return False


def pumpkin_top_z(obj, axis_xy, min_r):
    """Height of a pumpkin's body rim (ignores the stem / dimple)."""
    best = 0.0
    for v in world_verts(obj):
        if math.hypot(v.x - axis_xy[0], v.y - axis_xy[1]) > min_r:
            best = max(best, v.z)
    return best


def lane_clear(objs, margin=0.1):
    lo, hi = world_bounds(objs)
    return not (lo.x < LANE_X + margin and hi.x > -LANE_X - margin and lo.y < -1.3)


def build_scene():
    common.reset_scene()
    D = Diorama()

    # ------------------------------------------------------------------ portal
    pobjs = portal.build_portal(D.colls["Portal"])
    D.add("Portal", "portal", pobjs)
    info = portal.PORTAL_INFO
    dais = bpy.data.objects["Portal_Dais"]
    surf = Surface([dais])
    cracks = Cracks(bpy.data.objects["GLOW_ember_PortalCracks_Dais"])
    top_r = info["dais_top_radius"]

    def foot(objs, dz=0.18):
        """xy points of the lowest part of a group (its footprint)."""
        vs = [v for o in objs for v in world_verts(o)]
        z0 = min(v.z for v in vs)
        return [v for v in vs if v.z < z0 + dz]

    def on_dais_top(objs):
        pts = foot(objs)
        lo, hi = world_bounds(objs)
        return (all(math.hypot(v.x, v.y) < top_r - 0.12 for v in pts) and hi.y < info["front_y"] - 0.3
                and all(cracks.dist(v.x, v.y) > 0.07 for v in pts[::2]) and lane_clear(objs))

    def off_dais(objs, gap=0.15):
        return all(math.hypot(v.x, v.y) > info["dais_radius"] + gap for v in foot(objs)) and lane_clear(objs)

    def dais_z(x, y):
        return surf.rest_z(x, y, 0.3) - 0.015      # provisional: drape_bottom() conforms the puddles afterwards

    # ------------------------------------------------------------------ portal-side lights
    # Braziers flank the portal just outside the tendril zone (tendril tips reach |x| ~ 8.9).
    for side, x, seed in (("L", -10.0, 1), ("R", 10.0, 2)):
        objs = lights.make_brazier(f"Brazier_{side}", (x, -4.2, 0.0), 0.0, height=4.5, side=side, seed=seed)
        for o in objs:   # pale core: no second fire emitter / light / smoke column in Roblox
            if o.name.startswith("GLOW_flame_BrazierFlameCore_"):
                rename(o, o.name.replace("BrazierFlameCore", "BrazierCore"))
        D.add("Lights", f"Brazier_{side}", objs)

    # candles: small clusters on the flat dais ledge either side of the lane, one cluster on
    # the crack-free dais top behind the frame (the front corners of the top are all cracks)
    # (the ledge is only 0.68 wide: wax puddles may tuck into the upper step's wall, never overhang the bevel)
    ledge_lo, ledge_hi = info["dais_top_radius"] - 0.02, info["dais_radius"] - 0.1

    def on_ledge(objs):
        return all(ledge_lo < math.hypot(v.x, v.y) < ledge_hi for v in foot(objs)) and lane_clear(objs)

    def ledge_z(x, y):      # the ledge is flat: puddle tops 31-45 mm above it, rims tucked 4 mm in
        return info["dais_ledge_z"] - 0.004

    def behind_on_top(objs):
        pts = foot(objs)
        lo, hi = world_bounds(objs)
        return (all(math.hypot(v.x, v.y) < top_r - 0.12 for v in pts) and lo.y > info["back_y"] + 0.3
                and all(cracks.dist(v.x, v.y) > 0.07 for v in pts[::2]))

    def ledge_xy(deg):
        r = info["dais_top_radius"] + 0.55
        return r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg))

    candle_specs = (  # name, pref xy, count, wax, seed, height, rotation, predicate, z
        ("Candles_A", ledge_xy(-138), 2, "cream", 1, 1.25, math.radians(-138 + 90), on_ledge, ledge_z),
        ("Candles_B", ledge_xy(-42), 2, "black", 2, 1.15, math.radians(-42 + 90), on_ledge, ledge_z),
        ("Candles_C", ledge_xy(-160), 1, "orange", 3, 0.9, 0.0, on_ledge, ledge_z),
        ("Candles_D", (-3.3, 2.7), 3, "cream", 4, 1.3, 0.4, behind_on_top, dais_z),
    )
    for name, pref, count, wax, seed, h, rz, pred, zf in candle_specs:
        objs = lights.make_candles(name, (pref[0], pref[1], zf(*pref)), rz, count=count, seed=seed,
                                   height=h, wax=wax)
        D.place_free(objs, pref, ok=pred, z_at=zf, ignore_names=DAIS_NAMES, step=0.08, tries=160,
                     spins=[math.radians(a) for a in range(0, 360, 20)])
        if zf is dais_z:
            # the dais top is jittered (+-0.02): conform the wax puddles to it so every puddle
            # top sits >= 0.03 above the stone and every rim is tucked 4 mm in (no hover, no
            # near-coplanar faces); the flames are untouched (only the bottom 0.06 moves)
            drape_bottom(objs[0], surf, embed=0.004, band=0.06)
        D.add("Lights", name, objs, support="dais")

    # ------------------------------------------------------------------ graveyard (static backdrop first)
    fence_l = [(-14.6, 0.6), (-13.0, 5.2), (-9.2, 8.2), (-3.3, 9.0)]
    D.add("Graveyard", "Fence_L", graveyard.make_fence_run("Fence_L", fence_l, seed=1, broken=(1,)))
    D.add("Graveyard", "Fence_R", graveyard.make_fence_run("Fence_R", [(3.3, 9.0), (8.8, 7.8)], seed=2))

    stones = [  # name, (x, y), style, height, seed, mound
        ("Gravestone_Round_01", (-9.8, 2.0), "round", 3.0, 1, "GraveMound_01"),
        ("Gravestone_Cross_01", (-12.4, 3.6), "cross", 3.6, 2, None),
        ("Gravestone_Slab_01", (-8.6, 5.8), "slab", 3.4, 4, None),
        ("Gravestone_Broken_01", (9.4, 1.8), "broken", 3.0, 3, "GraveMound_02"),
        ("Gravestone_Round_02", (12.9, 1.6), "round", 2.6, 5, "GraveMound_03"),
        ("Gravestone_Cross_02", (8.2, 5.6), "cross", 2.9, 6, None),
    ]
    for i, (name, (x, y), style, h, seed, mound) in enumerate(stones):
        rz = face_towards(x, y, amount=0.45) + (0.06 if i % 2 else -0.05)
        D.add("Graveyard", name, graveyard.make_gravestone(name, (x, y, 0.0), rz, style, h, seed=seed))
        if mound:
            mx, my = rot_vec(0.0, -2.3, rz)
            D.add("Graveyard", name, graveyard.make_grave_mound(mound, (x + mx, y + my, 0.0), rz, 2.8, seed=seed))

    tree = graveyard.make_dead_tree("DeadTree_01", (10.6, 4.4, 0.0), math.radians(270), 13.0, seed=3)
    D.add("Graveyard", "DeadTree_01", tree)

    bones = graveyard.make_bone_pile("BonePile_01", (-10.9, 4.9, 0.0), 0.3, seed=1)
    D.place_free(bones, (-10.9, 4.9))
    D.add("Graveyard", "BonePile_01", bones)
    shovel = graveyard.make_shovel("Shovel_01", (14.2, -0.2, 0.0), -1.2, seed=1)
    D.place_free(shovel, (14.2, -0.2))
    D.add("Graveyard", "Shovel_01", shovel)

    # spider web in the inside top corner of the first left fence segment (end post)
    fa, fb = Vector((*fence_l[0], 0)), Vector((*fence_l[1], 0))
    yaw = math.atan2(fb.y - fa.y, fb.x - fa.x)
    ux, uy = math.cos(yaw), math.sin(yaw)
    nx, ny = math.sin(yaw), -math.cos(yaw)           # local -Y (front side of the fence)
    corner = fa + Vector((ux, uy, 0)) * 0.17 + Vector((nx, ny, 0)) * 0.11
    D.add("Graveyard", "SpiderWeb_01", lights.make_spider_web(
        "SpiderWeb_01", (corner.x, corner.y, 2.38), yaw, size=1.5, corner="TL", seed=1, at_corner=True),
        support="fence")

    # ------------------------------------------------------------------ outer lights
    # brew in the portal orange (fire) instead of amber: keeps the glow balance orange, not yellow
    D.add("Lights", "Cauldron_01", lights.make_cauldron("Cauldron_01", (-12.0, -7.4, 0.0), 0.6, size=3.0, seed=3,
                                                        liquid_key="fire"))
    # left lantern post well outside the left brazier, its lantern swung towards the player (-Y)
    # so post, lantern and brazier read as three separate silhouettes in every view
    D.add("Lights", "LanternPost_01", lights.make_lantern_post("LanternPost_01", LANTERN_L[:2] + (0.0,),
                                                               LANTERN_L[2], seed=2))
    lp2 = lights.make_lantern_post("LanternPost_02", LANTERN_R[:2] + (0.0,), LANTERN_R[2], seed=5)
    D.place_free(lp2, LANTERN_R[:2], ok=lambda objs: True, step=0.1, tries=120)
    D.add("Lights", "LanternPost_02", lp2)

    # ------------------------------------------------------------------ pumpkins
    for prefix, pref, rz, seed, count in (("PumpkinPile_L", (-7.4, -6.4), 0.35, 4, 5),
                                          ("PumpkinPile_R", (7.4, -6.4), -0.35, 12, 4)):
        pile = pumpkins.make_pumpkin_pile(prefix, (pref[0], pref[1], 0.0), rz, seed=seed, count=count)
        D.place_free(pile, pref, ok=off_dais, label=prefix)
        D.add("Pumpkins", prefix, pile)

    # big pumpkin wearing a witch hat at the front-right corner (balances the cauldron)
    hx, hy, hs = 12.6, -8.0, 2.8
    hp = pumpkins.make_pumpkin("Pumpkin_Hat_01", (hx, hy, 0.0), 0.5, size=hs, variant="squat", seed=31,
                               color="pumpkin", stem=False, vine=False, leaf=True)
    D.place_free(hp, (hx, hy))
    D.add("Pumpkins", "Pumpkin_Hat_01", hp)
    hx, hy = hp[0].matrix_world.translation.xy
    ptop = pumpkin_top_z(hp[0], (hx, hy), hs * 0.18)
    # sunk 0.15 into the pumpkin top: a scan of sink depths 0.06..0.20 (check_coplanar) gives
    # near-coplanar hat/pumpkin faces down to 0.12 and none from 0.14 on
    D.add("Lights", "WitchHat_01", lights.make_witch_hat("WitchHat_01", (hx, hy, ptop - 0.15), -0.6, size=2.0, seed=1),
          support="pumpkin")

    # lone jack-o'-lantern at the foot of the dais steps, right of the lane
    jx, jy = 7.7, -2.4
    jack = pumpkins.make_jack_o_lantern("JackOLantern_01", (jx, jy, 0.0), face_towards(jx, jy, amount=0.7),
                                        size=2.0, face="happy", seed=11)
    D.place_free(jack, (jx, jy), ok=off_dais)
    D.add("Pumpkins", "JackOLantern_01", jack)

    # small pumpkins + skull on the ground around the dais front
    smalls = (("Pumpkin_Small_01", (-7.6, -2.3), 1.2, "round", "pumpkin", 21),
              ("Pumpkin_Small_02", (-8.0, -3.6), 0.9, "squat", "charcoal", 22),
              ("Pumpkin_Small_03", (4.3, -9.6), 1.0, "tall", "amber", 23))
    for name, pref, size, variant, color, seed in smalls:
        objs = pumpkins.make_pumpkin(name, (pref[0], pref[1], 0.0), seed * 0.7, size=size, variant=variant,
                                     seed=seed, color=color)
        D.place_free(objs, pref, ok=off_dais)
        D.add("Pumpkins", name, objs)
    skull = graveyard.make_skull("Skull_01", (-4.2, -9.4, 0.0), 0.5, 0.8)
    D.place_free(skull, (-4.2, -9.4), ok=off_dais)
    D.add("Graveyard", "Skull_01", skull)

    # candy corn sprinkled at the front
    for i, (x, y, rz, s) in enumerate(((-4.6, -10.2, 0.4, 0.7), (-9.6, -9.0, -0.7, 0.55), (5.2, -10.2, 0.9, 0.65),
                                       (10.2, -8.6, -0.3, 0.6), (-10.0, -2.2, 0.2, 0.6), (11.4, -6.0, -1.1, 0.5))):
        name = f"CandyCorn_{i + 1:02d}"
        objs = pumpkins.make_candy_corn(name, (x, y, 0.0), rz, size=s, seed=i)
        D.place_free(objs, (x, y), ok=off_dais, step=0.15)
        D.add("Pumpkins", name, objs)

    # ------------------------------------------------------------------ VFX
    body = [o for o in pobjs if "Portal_Frame" in o.name or "PortalTendrils" in o.name or "PortalRim" in o.name]
    keep = vfx.keep_out_boxes_from(body, bands=7, margin=0.4)
    c = (0.0, 0.0, (info["dais_top_z"] + info["frame_body_top_z"]) / 2 + 0.6)
    wisps = vfx.make_wisps("Portal", center=c, radius=9.5, count=5, seed=11, keep_out_box=keep)
    D.add("VFX", "wisps", wisps, support="air")
    floaters = []
    for name, loc, size, color, seed in (("Pumpkin_Float_01", (-8.9, -1.6, 11.2), 1.1, "pumpkin", 41),
                                         ("Pumpkin_Float_02", (8.4, -1.2, 11.6), 0.95, "charcoal", 42)):
        objs = pumpkins.make_pumpkin(name, loc, seed * 0.3, size=size, variant="round", seed=seed, color=color,
                                     vine=False, leaf=False)
        D.place_free(objs, loc[:2], include_air=True)
        floaters += objs
        D.add("VFX", name, objs, support="air")
    cubes = vfx.make_floating_cubes("Portal", center=c, radius=9.0, count=14, seed=3, keep_out_box=keep,
                                    avoid=wisps + floaters)
    D.add("VFX", "cubes", cubes, support="air")
    D.add("VFX", "embers_top", vfx.make_embers("PortalTop", center=(0.4, 0.0, info["frame_height"] - 1.0),
                                               radius=2.6, count=6, seed=5, height=3.6), support="air")
    for side in ("L", "R"):
        fl = bpy.data.objects[f"GLOW_fire_BrazierFlame_{side}"]
        lo, hi = world_bounds([fl])
        ctr = (lo + hi) / 2
        D.add("VFX", f"embers_{side}", vfx.make_embers(f"Brazier_{side}", center=(ctr.x, ctr.y, hi.z + 0.15),
                                                       radius=0.8, count=6, seed=7 if side == "L" else 8,
                                                       height=3.2), support="air")
    bats = vfx.make_bat_swarm("Portal", center=(0, 0, 0), radius=11.5, count=5, height=(12.8, 17.5), seed=2,
                              wingspan=(2.4, 3.1))
    D.add("VFX", "bats", bats, support="air")
    D.add("VFX", "MagicCircle_01", vfx.make_magic_circle("MagicCircle_01", (0.0, -9.05, 0.0), radius=1.95, seed=1),
          support="decal")
    return D


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate(D):
    problems = []
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    names = [o.name for o in meshes]
    if len(set(names)) != len(names):
        problems.append("duplicate object names")
    total = 0
    per_coll = {}
    for o in meshes:
        t = common.tri_count(o)
        total += t
        coll = o.users_collection[0].name if o.users_collection else "?"
        per_coll[coll] = per_coll.get(coll, 0) + t
        problems += common.validate_object(o, max_tris=TRI_LIMIT_MESH)
        me = o.data
        if not me.vertices or not me.polygons:
            problems.append(f"{o.name}: empty mesh")
        if len(me.materials) != 1:
            problems.append(f"{o.name}: {len(me.materials)} materials (need exactly 1)")
        mat = me.materials[0].name if me.materials else None
        if o.name.startswith("GLOW_") and mat != "M_Glow":
            problems.append(f"{o.name}: GLOW object uses {mat}")
        if not o.name.startswith("GLOW_") and mat == "M_Glow":
            problems.append(f"{o.name}: M_Glow on a non-GLOW object")
        if (o.name == "Portal_Swirl") != (mat == "M_Swirl"):
            problems.append(f"{o.name}: M_Swirl misuse")
        if o.name.endswith(RESERVED_SUFFIXES):
            problems.append(f"{o.name}: reserved importer suffix")
        if o.parent is not None:
            problems.append(f"{o.name}: parented")
        if abs(o.rotation_euler.x) > 1e-6 or abs(o.rotation_euler.y) > 1e-6:
            problems.append(f"{o.name}: X/Y object rotation")
        if me.name != o.name:
            me.name = o.name
            if me.name != o.name:
                problems.append(f"{o.name}: mesh datablock is named {me.name}")
        if "Flame" in o.name and not o.name.startswith("GLOW_"):
            problems.append(f"{o.name}: 'Flame' keyword on a non-glow part")
        if len(me.uv_layers) != 1:
            problems.append(f"{o.name}: {len(me.uv_layers)} UV maps")
        else:
            uv = me.uv_layers[0].data
            vals = [0.0] * (len(uv) * 2)
            uv.foreach_get("uv", vals)
            if vals and (min(vals) < -1e-6 or max(vals) > 1 + 1e-6):
                problems.append(f"{o.name}: UVs outside 0..1 ({min(vals):.3f}..{max(vals):.3f})")
        if o.name not in D.group_of:
            problems.append(f"{o.name}: not registered in the layout (stray object?)")
    if total > TRI_BUDGET_SCENE:
        problems.append(f"scene has {total} tris > {TRI_BUDGET_SCENE}")
    portal_tris = per_coll.get("Portal", 0)
    if portal_tris > 12000:
        problems.append(f"portal has {portal_tris} tris > 12000")

    # resting on the ground / supports
    groups = {}
    for o in meshes:
        groups.setdefault(D.group_of.get(o.name, "?"), []).append(o)
    rest_report = []
    for g, objs in groups.items():
        sup = D.ground.get(g, "ground")
        lo, hi = world_bounds(objs)
        if sup == "ground" and abs(lo.z) > GROUND_TOL:
            problems.append(f"group {g}: lowest point z={lo.z:.3f} (should rest on the ground)")
        rest_report.append((g, sup, lo.z, lo.copy(), hi.copy()))

    # footprint of everything that stands on the ground
    grounded = [o for o in meshes if D.ground.get(D.group_of.get(o.name), "ground") != "air"]
    flo, fhi = world_bounds(grounded)
    alo, ahi = world_bounds(meshes)

    # lane check: nothing taller than a decal inside the lane in front of the frame
    for o in meshes:
        g = D.group_of.get(o.name, "?")
        if g == "portal" or D.ground.get(g) == "air":
            continue
        lo, hi = world_bounds([o])
        if hi.z > 0.2 and lo.x < LANE_X and hi.x > -LANE_X and lo.y < -1.4 and hi.y > -12:
            problems.append(f"{o.name}: blocks the walk-in lane ({lo.x:.2f}..{hi.x:.2f}, y {lo.y:.2f}..{hi.y:.2f})")

    contract_problems, contract = check_name_contract(D, meshes)
    problems += contract_problems
    overlaps = check_overlaps(D, meshes)
    coplanar = check_coplanar(meshes)
    notes = []
    for a, b, n, g in coplanar:
        msg = f"near-coplanar faces {a} <-> {b}: {n} faces, closest {g * 1000:.1f} mm"
        if D.group_of.get(a) == D.group_of.get(b):     # inside one part-module prop: reported, not blocking
            notes.append(msg + " (same prop, module geometry)")
        else:
            problems.append(msg)
    biggest = max(meshes, key=common.tri_count)
    return dict(problems=problems, total=total, per_coll=per_coll, count=len(meshes), footprint=(flo, fhi),
                all_bounds=(alo, ahi), overlaps=overlaps, coplanar=coplanar, notes=notes, rest=rest_report,
                biggest=(biggest.name, common.tri_count(biggest)), contract=contract)


# pairs of objects allowed to intersect (designed contact: frame in dais, prop resting on a prop)
def _allowed_overlap(a, b, ga, gb, D):
    s = {ga, gb}
    if s == {"portal"}:
        return True
    if "portal" in s:                            # props resting on the dais may touch only the dais
        other = gb if ga == "portal" else ga
        pname = a if ga == "portal" else b
        return D.ground.get(other) == "dais" and pname in DAIS_NAMES
    if s == {"Pumpkin_Hat_01", "WitchHat_01"}:
        return True
    if "SpiderWeb_01" in s and "Fence_L" in s:
        return True
    return False


def check_overlaps(D, meshes):
    info = []
    boxes = {o.name: world_bounds([o]) for o in meshes}
    trees = {}
    names = [o.name for o in meshes]
    for i, a in enumerate(names):
        la, ha = boxes[a]
        for b in names[i + 1:]:
            ga, gb = D.group_of.get(a, a), D.group_of.get(b, b)
            if ga == gb or _allowed_overlap(a, b, ga, gb, D):
                continue
            lb, hb = boxes[b]
            if la.x > hb.x or lb.x > ha.x or la.y > hb.y or lb.y > ha.y or la.z > hb.z or lb.z > ha.z:
                continue
            ta = trees.get(a) or trees.setdefault(a, bvh_world(bpy.data.objects[a]))
            tb = trees.get(b) or trees.setdefault(b, bvh_world(bpy.data.objects[b]))
            n = len(ta.overlap(tb))
            if n:
                info.append((a, b, n))
    return info


def check_coplanar(meshes, gap=COPLANAR_GAP, min_area=2e-3):
    """Same-facing faces of two different meshes closer than ``gap`` along the normal
    (hovering decals, barely-sunk props): they can depth-flicker at distance in Roblox.
    Returns [(a, b, face_count, min_gap)]."""
    boxes = {o.name: world_bounds([o]) for o in meshes}
    trees = {}
    pairs = {}
    for a in meshes:
        la, ha = boxes[a.name]
        cands = []
        for b in meshes:
            if b is a:
                continue
            lb, hb = boxes[b.name]
            if (la.x - gap > hb.x or lb.x - gap > ha.x or la.y - gap > hb.y or lb.y - gap > ha.y
                    or la.z - gap > hb.z or lb.z - gap > ha.z):
                continue
            cands.append(b)
        if not cands:
            continue
        mw = a.matrix_world
        n3 = mw.to_3x3()
        for b in cands:
            tb = trees.get(b.name) or trees.setdefault(b.name, bvh_world(b))
            lb, hb = boxes[b.name]
            for p in a.data.polygons:
                if p.area < min_area:
                    continue
                c = mw @ p.center
                if not (lb.x - gap <= c.x <= hb.x + gap and lb.y - gap <= c.y <= hb.y + gap
                        and lb.z - gap <= c.z <= hb.z + gap):
                    continue
                n = (n3 @ p.normal).normalized()
                loc, nb, _i, dist = tb.ray_cast(c - n * gap, n, 2 * gap)
                if loc is None or nb.dot(n) < 0.95:
                    continue
                d = abs(dist - gap)
                key = tuple(sorted((a.name, b.name)))
                cnt, best = pairs.get(key, (0, 1e9))
                pairs[key] = (cnt + 1, min(best, d))
    return [(a, b, n, g) for (a, b), (n, g) in sorted(pairs.items())]


# --------------------------------------------------------------------------
# Name contract (mirrors roblox/HalloweenPortalVFX.server.lua: stripGlow, groupKey,
# register() classification and JoinGroupsByKey adoption)
# --------------------------------------------------------------------------
_GLOW_RE = re.compile(r"^GLOW_([A-Za-z0-9]+)_(.+)$")     # Lua "^GLOW_(%w+)_(.+)$" (%w has no '_')


def strip_glow(name):
    m = _GLOW_RE.match(name)
    return (m.group(2), m.group(1).lower()) if m else (name, None)


def group_key(stripped):
    stripped = re.sub(r"\.\d+$", "", stripped)
    m = re.match(r"^(.*\d)", stripped)                      # greedy: up to the LAST digit
    return m.group(1) if m else stripped


def check_name_contract(D, meshes):
    """What the Luau VFX script will do with each part, and whether that is what the
    layout intends. Returns (problems, summary dict)."""
    info = portal.PORTAL_INFO
    problems = []
    names = [o.name for o in meshes]
    by_name = {o.name: o for o in meshes}

    def exactly_one(sub):
        hits = [n for n in names if sub in n]
        if len(hits) != 1:
            problems.append(f"contract: {len(hits)} parts contain '{sub}' (need exactly 1): {hits}")
        return hits

    exactly_one("Portal_Frame")
    exactly_one("Portal_Dais")
    exactly_one("Portal_Swirl")
    spins = [n for n in names if "PortalSpin" in n]
    letters = sorted(re.search(r"PortalSpin_?([A-Za-z])", n).group(1) for n in spins)
    if letters != ["A", "B"]:
        problems.append(f"contract: PortalSpin parts {spins} (need one _A and one _B)")

    # spinners rotate about the portal normal through their bbox centre: centre on the hub,
    # thinnest axis = Y, every vertex inside the opening's inscribed circle
    sc = Vector(info["spin_center"])
    for n in spins + ["Portal_Swirl"]:
        o = by_name.get(n)
        if o is None:
            continue
        lo, hi = world_bounds([o])
        size = hi - lo
        if not (size.y < size.x and size.y < size.z):
            problems.append(f"contract: {n} thinnest axis is not Y (size {tuple(round(c, 2) for c in size)})")
        if n in spins:
            ctr = (lo + hi) / 2
            if (ctr - sc).length > 0.02:
                problems.append(f"contract: {n} bbox centre {tuple(round(c, 3) for c in ctr)} != spin_center")
            rmax = max(math.hypot(v.x - sc.x, v.z - sc.z) for v in world_verts(o))
            if rmax > info["opening_inscribed_radius"] + 1e-3:
                problems.append(f"contract: {n} reaches r={rmax:.2f} > inscribed {info['opening_inscribed_radius']:.2f}")

    # classification exactly as register() does it
    kinds = {}
    parts_by_key = {}
    for n in names:
        stripped, _key = strip_glow(n)
        k = group_key(stripped)
        is_spin = "PortalSpin" in n
        is_bat = stripped.startswith("Bat_")
        is_float = (not is_bat) and "Float" in n
        is_circle = not (is_spin or is_bat or is_float) and "MagicCircle" in n
        kinds[n] = "spin" if is_spin else "bat" if is_bat else "float" if is_float else "circle" if is_circle else ""
        if not is_spin:
            parts_by_key.setdefault(k, []).append(n)
    groups = {}
    for n in names:
        if kinds[n] in ("bat", "float"):
            k = group_key(strip_glow(n)[0])
            g = groups.setdefault(k, {"kind": "float", "members": set()})
            if kinds[n] == "bat":
                g["kind"] = "bat"                                  # bat wins over float
    adopted = []
    for k, g in groups.items():
        g["members"] = set(parts_by_key.get(k, []))               # JoinGroupsByKey adopts every same-key part
        for m in sorted(g["members"]):
            if kinds[m] not in ("bat", "float"):
                adopted.append((k, m))
            sup = D.ground.get(D.group_of.get(m), "ground")
            if sup != "air":                                      # a ground prop would start to bob / orbit
                problems.append(f"contract: grounded part {m} joins animated {g['kind']} group '{k}'")
        if g["kind"] == "bat":
            bodies = [m for m in g["members"] if strip_glow(m)[0] == k]
            eyes = [m for m in g["members"] if m.endswith("_Eyes")]
            if len(bodies) != 1 or len(eyes) != 1 or len(g["members"]) != 2:
                problems.append(f"contract: bat group '{k}' members {sorted(g['members'])} (need body + eyes)")

    # airborne layout pieces the script does NOT animate would hang frozen in mid-air
    for n in names:
        if D.ground.get(D.group_of.get(n)) == "air" and not kinds[n] and not any(n in g["members"] for g in groups.values()):
            problems.append(f"contract: airborne part {n} has no Float/Bat_ keyword (it would hang still)")

    # the magic circle turns about world up through its bbox centre: it must be round
    for n in names:
        if kinds[n] != "circle":
            continue
        o = by_name[n]
        lo, hi = world_bounds([o])
        ctr = (lo + hi) / 2
        half = max(hi.x - lo.x, hi.y - lo.y) / 2
        rmax = max(math.hypot(v.x - ctr.x, v.y - ctr.y) for v in world_verts(o))
        if abs((hi.x - lo.x) - (hi.y - lo.y)) > 0.03 * half * 2 or rmax > half * 1.03:
            problems.append(f"contract: {n} is not round (w {hi.x - lo.x:.2f}, d {hi.y - lo.y:.2f}, r {rmax:.2f})")

    # effect keywords must sit on glow parts and nowhere unexpected
    for n in names:
        glow = n.startswith("GLOW_")
        for kw in ("Flame", "LanternGlow", "CauldronLiquid"):
            if kw in n and not glow:
                problems.append(f"contract: '{kw}' on non-glow part {n}")
        if "JackOLantern" in n and "Face" in n and not glow:
            problems.append(f"contract: jack-o'-lantern face {n} is not a GLOW part")
        if "FlameCore" in n:
            problems.append(f"contract: {n} would get a second fire emitter + light ('FlameCore')")

    summary = {
        "spinners": sorted(spins),
        "bat_groups": sorted(k for k, g in groups.items() if g["kind"] == "bat"),
        "float_groups": sorted(k for k, g in groups.items() if g["kind"] == "float"),
        "multi_part_groups": {k: sorted(g["members"]) for k, g in sorted(groups.items()) if len(g["members"]) > 1},
        "adopted": adopted,
        "flames": sorted(n for n in names if "Flame" in n),
        "jack_faces": sorted(n for n in names if "JackOLantern" in n and "Face" in n),
        "lantern_glows": sorted(n for n in names if "LanternGlow" in n),
        "cauldron_liquids": sorted(n for n in names if "CauldronLiquid" in n),
        "circles": sorted(n for n in names if kinds[n] == "circle"),
        "glow_parts": sum(1 for n in names if n.startswith("GLOW_")),
    }
    return problems, summary


def print_contract(summary):
    print("\n---------------- NAME CONTRACT (as the Luau VFX script sees it) ----------------")
    print(f"spinners: {summary['spinners']}")
    print(f"bat groups ({len(summary['bat_groups'])}): {summary['bat_groups']}")
    print(f"float groups: {len(summary['float_groups'])}")
    for k, ms in summary["multi_part_groups"].items():
        print(f"  group {k}: {ms}")
    if summary["adopted"]:
        print(f"adopted by JoinGroupsByKey (no keyword, same base name): {summary['adopted']}")
    print(f"flames ({len(summary['flames'])}): {summary['flames']}")
    print(f"jack-o'-lantern faces: {summary['jack_faces']}")
    print(f"lantern glows: {summary['lantern_glows']}   cauldron liquids: {summary['cauldron_liquids']}")
    print(f"magic circles (spin about world up): {summary['circles']}   GLOW parts -> Neon: {summary['glow_parts']}")


def print_validation(rep):
    print("\n================ VALIDATION ================")
    print(f"mesh objects: {rep['count']}   total tris: {rep['total']} (budget {TRI_BUDGET_SCENE})")
    for k, v in sorted(rep["per_coll"].items()):
        print(f"  {k:10s} {v:6d} tris")
    print(f"largest mesh: {rep['biggest'][0]} {rep['biggest'][1]} tris (limit {TRI_LIMIT_MESH})")
    flo, fhi = rep["footprint"]
    alo, ahi = rep["all_bounds"]
    print(f"ground footprint x [{flo.x:.2f}, {fhi.x:.2f}]  y [{flo.y:.2f}, {fhi.y:.2f}]  z max {fhi.z:.2f}")
    print(f"all bounds       x [{alo.x:.2f}, {ahi.x:.2f}]  y [{alo.y:.2f}, {ahi.y:.2f}]  z [{alo.z:.2f}, {ahi.z:.2f}]")
    if "--groups" in sys.argv:
        for g, sup, z0, lo, hi in sorted(rep["rest"], key=lambda r: r[0]):
            print(f"  group {g:22s} {sup:7s} x [{lo.x:6.2f},{hi.x:6.2f}] y [{lo.y:6.2f},{hi.y:6.2f}] z [{lo.z:5.2f},{hi.z:5.2f}]")
    print_contract(rep["contract"])
    print(f"overlaps between different props: {len(rep['overlaps'])}")
    for a, b, n in rep["overlaps"]:
        print(f"  OVERLAP {a} <-> {b}: {n} tri pairs")
    print(f"near-coplanar same-facing face pairs (< {COPLANAR_GAP * 1000:.0f} mm): {len(rep['coplanar'])}")
    for n in rep["notes"]:
        print("  NOTE:", n)
    print(f"problems: {len(rep['problems'])}")
    for p in rep["problems"]:
        print("  PROBLEM:", p)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def triangulate_all():
    for ob in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
        bm.to_mesh(ob.data)
        bm.free()
        ob.data.name = ob.name
        common.shade_flat(ob)


def snapshot():
    """Reference data for the FBX round-trip check."""
    snap = {}
    for o in bpy.context.scene.objects:
        if o.type != "MESH":
            continue
        lo, hi = world_bounds([o])
        snap[o.name] = dict(tris=common.tri_count(o), mat=o.data.materials[0].name, lo=lo.copy(), hi=hi.copy())
    return snap


class _RelativeTexturePaths:
    """During the FBX export, write texture paths as 'textures/<file>' (relative to the .fbx,
    where export/textures/ really holds them) instead of the exporter's absolute
    '<export dir>/HalloweenPortal.fbm/<file>' (a folder that never exists with embedded
    textures, and a leak of the build machine's path). The pixels stay embedded either way
    (path_mode='COPY' + embed_textures=True). Falls back silently if the exporter changes."""

    def __enter__(self):
        self.mod = self.orig = None
        try:
            import io_scene_fbx.export_fbx_bin as mod
        except ImportError:
            return self
        orig = getattr(mod, "_gen_vid_path", None)
        if orig is None:
            return self

        def rel_vid_path(img, scene_data):
            fname_abs, fname_rel = orig(img, scene_data)
            rel = "textures/" + os.path.basename(fname_rel or fname_abs)
            return rel, rel

        self.mod, self.orig = mod, orig
        mod._gen_vid_path = rel_vid_path
        return self

    def __exit__(self, *exc):
        if self.mod is not None:
            self.mod._gen_vid_path = self.orig
        return False


def export_fbx():
    os.makedirs(common.EXPORT_DIR, exist_ok=True)
    with _RelativeTexturePaths():
        bpy.ops.export_scene.fbx(
            filepath=FBX_PATH, check_existing=False, use_selection=False, use_visible=False,
            use_active_collection=False, object_types={"MESH"}, global_scale=1.0, apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_UNITS", axis_forward="Z", axis_up="Y", use_space_transform=True,
            bake_space_transform=True, use_mesh_modifiers=True, mesh_smooth_type="FACE", use_triangles=False,
            use_tspace=False, colors_type="NONE", prioritize_active_color=False, use_custom_props=False,
            add_leaf_bones=False, bake_anim=False, path_mode="COPY", embed_textures=True, batch_mode="OFF",
            use_metadata=True)
    print(f"[build] exported {FBX_PATH} ({os.path.getsize(FBX_PATH) / 1024:.0f} KB)")


def save_blend():
    bpy.context.preferences.filepaths.save_version = 0     # no HalloweenPortal.blend1 backups
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
    try:                                   # image paths relative to the .blend (//textures/...)
        bpy.ops.file.make_paths_relative()
        bpy.ops.wm.save_mainfile()
    except RuntimeError as e:
        print(f"[build] note: could not make paths relative ({e})")
    print(f"[build] saved {BLEND_PATH} ({os.path.getsize(BLEND_PATH) / 1024:.0f} KB)")


# --------------------------------------------------------------------------
# Renders
# --------------------------------------------------------------------------
FOCUS = {  # view -> (centre, radius) framing
    "front": ((0.0, -1.0, 5.6), 11.6),
    "three_quarter": ((0.0, -0.5, 5.2), 12.0),
    "three_quarter_r": ((0.0, -0.5, 5.2), 12.0),
    "top": ((0.0, -0.8, 0.0), 12.4),
    "low_hero": ((0.0, -2.0, 6.4), 10.2),
    "back": ((0.0, 0.5, 5.6), 12.0),
    "side": ((0.0, 0.0, 5.0), 12.0),
}


def render_views(prefix, views, res, samples):
    out = []
    for v in views:
        out += preview.render(prefix, views=[v], res=res, samples=samples, focus=FOCUS.get(v))
    return out


def scale_check_render(res=(960, 600), samples=32):
    """Grey 5-stud capsule beside the entrance (preview only, never exported)."""
    bm = bmesh.new()
    r, h, segs, rings = 0.9, 5.0, 12, 4
    verts = []
    prof = []
    for k in range(rings + 1):                      # bottom hemisphere
        a = -math.pi / 2 + (math.pi / 2) * k / rings
        prof.append((r * math.cos(a), r + r * math.sin(a)))
    for k in range(rings + 1):                      # top hemisphere
        a = (math.pi / 2) * k / rings
        prof.append((r * math.cos(a), h - r + r * math.sin(a)))
    for rr, z in prof:
        ring = []
        for s in range(segs):
            a = math.tau * s / segs
            ring.append(bm.verts.new((max(rr, 1e-4) * math.cos(a), max(rr, 1e-4) * math.sin(a), z)))
        verts.append(ring)
    for i in range(len(verts) - 1):
        for s in range(segs):
            bm.faces.new((verts[i][s], verts[i][(s + 1) % segs], verts[i + 1][(s + 1) % segs], verts[i + 1][s]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-3)
    obj = common.object_from_bmesh("ScaleRef_Capsule", bm)
    obj.data.materials.append(common.mat_palette())
    common.paint(obj, "stone_light")
    obj.location = (2.2, -9.0, 0.0)
    try:
        return preview.render("previews/_wip/scale_check.png", views=["three_quarter", "front"], res=res,
                              samples=samples, focus=((0.0, -2.5, 4.5), 9.0))
    finally:
        me = obj.data
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(me)


# --------------------------------------------------------------------------
# FBX round-trip verification
# --------------------------------------------------------------------------
def fbx_texture_paths(path):
    """(element, string) pairs of every FileName / RelativeFilename / Filename / Path
    stored in the FBX's Texture and Video nodes, plus the embedded Content sizes."""
    try:
        from io_scene_fbx import parse_fbx
    except ImportError:
        return [], []
    root, _ver = parse_fbx.parse(path)
    found, contents = [], []

    def walk(elem):
        for e in elem.elems:
            if e.id == b"Definitions":      # property templates: the empty default 'Path' is no texture
                continue
            if e.id in (b"FileName", b"RelativeFilename", b"Filename") and e.props:
                found.append((e.id.decode(), e.props[0].decode("utf-8", "replace")))
            elif e.id == b"Content" and e.props:
                contents.append(len(e.props[0]))
            elif e.id == b"P" and e.props and e.props[0] == b"Path":
                found.append(("Path", e.props[-1].decode("utf-8", "replace")))
            walk(e)

    walk(root)
    return found, contents


def verify_fbx(snap):
    print("\n================ FBX RE-IMPORT CHECK ================")
    data = open(FBX_PATH, "rb").read()
    png_sigs = data.count(b"\x89PNG\r\n\x1a\n")
    tex_paths, contents = fbx_texture_paths(FBX_PATH)
    leaks = [p for _k, p in tex_paths if os.path.isabs(p) or ".fbm" in p]
    root_leak = common.ROOT.encode() in data
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=FBX_PATH)
    objs = [o for o in bpy.context.scene.objects]
    meshes = [o for o in objs if o.type == "MESH"]
    others = [o for o in objs if o.type != "MESH"]
    issues = []
    names = {o.name for o in meshes}
    missing = sorted(set(snap) - names)
    extra = sorted(names - set(snap))
    if missing:
        issues.append(f"missing after import: {missing[:10]}{'...' if len(missing) > 10 else ''}")
    if extra:
        issues.append(f"unexpected after import: {extra[:10]}")
    if others:
        issues.append(f"non-mesh objects imported: {[o.name for o in others]}")
    max_pos_err = 0.0
    tri_total = 0
    mats = set()
    for o in meshes:
        tri_total += common.tri_count(o)
        ms = [m.name for m in o.data.materials if m]
        mats.update(ms)
        ref = snap.get(o.name)
        if ref is None:
            continue
        if len(ms) != 1 or ms[0] != ref["mat"]:
            issues.append(f"{o.name}: materials {ms} != {ref['mat']}")
        if common.tri_count(o) != ref["tris"]:
            issues.append(f"{o.name}: {common.tri_count(o)} tris != {ref['tris']}")
        lo, hi = world_bounds([o])
        err = max((lo - ref["lo"]).length, (hi - ref["hi"]).length)
        max_pos_err = max(max_pos_err, err)
        if err > 0.01:
            issues.append(f"{o.name}: world bounds moved by {err:.4f}")
    images = {}
    for img in bpy.data.images:
        images[img.name] = (tuple(img.size), img.packed_file is not None)
    frame = bpy.data.objects.get("Portal_Frame")
    flo, fhi = world_bounds([frame]) if frame else (Vector(), Vector())
    lo, hi = world_bounds(meshes) if meshes else (Vector(), Vector())
    print(f"imported objects: {len(objs)} (meshes {len(meshes)}, expected {len(snap)})")
    print(f"imported tris: {tri_total}")
    print(f"materials: {sorted(mats)}")
    print(f"images: {images}")
    print(f"embedded PNG streams in the .fbx: {png_sigs} (Content sizes {contents} bytes)")
    print(f"texture paths in the .fbx: {sorted(set(p for _k, p in tex_paths))}")
    if leaks or root_leak:
        issues.append(f"absolute / .fbm texture paths in the FBX: {sorted(set(leaks))} (build root in file: {root_leak})")
    fd = fhi - flo
    print(f"Portal_Frame world size (BU): {fd.x:.2f} x {fd.y:.2f} x {fd.z:.2f}, top z = {fhi.z:.2f}")
    print(f"scene bounds after import: x [{lo.x:.2f}, {hi.x:.2f}] y [{lo.y:.2f}, {hi.y:.2f}] z [{lo.z:.2f}, {hi.z:.2f}]")
    print(f"max world-bounds deviation vs built scene: {max_pos_err:.5f} BU")
    if png_sigs < 2:
        issues.append(f"only {png_sigs} embedded PNG(s) in the FBX (expected palette + swirl)")
    if not {"M_Palette", "M_Glow", "M_Swirl"} <= mats:
        issues.append(f"materials incomplete: {sorted(mats)}")
    print(f"issues: {len(issues)}")
    for i in issues:
        print("  ISSUE:", i)
    return dict(objects=len(objs), meshes=len(meshes), tris=tri_total, materials=sorted(mats), images=images,
                pngs=png_sigs, tex_paths=sorted(set(p for _k, p in tex_paths)),
                frame_size=tuple(round(c, 2) for c in fd), frame_top=round(fhi.z, 2),
                max_err=max_pos_err, issues=issues)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv):
    quick = "--quick" in argv
    views = None
    for a in argv:
        if a.startswith("--views="):
            views = [v for v in a.split("=", 1)[1].split(",") if v]
    t0 = time.time()
    D = build_scene()
    for o in bpy.context.scene.objects:     # mesh datablocks named after their objects
        if o.type == "MESH":
            o.data.name = o.name
    rep = validate(D)
    print_validation(rep)
    print(f"[build] scene built in {time.time() - t0:.1f}s")

    if quick:
        img = bpy.data.images.get("palette.png")
        if img is not None and img.packed_file is None:
            img.pack()
        res = (800, 500)
        samples = 16
        for f in render_views("previews/_wip/build.png", views or ["three_quarter", "top"], res, samples):
            print("[build] wrote", f)
        if "--scale" in argv:
            scale_check_render(res, samples)
        return

    snap_before = None
    if "--no-export" not in argv:
        triangulate_all()
        snap_before = snapshot()
        export_fbx()
        save_blend()
    if "--no-render" not in argv:
        for f in render_views("previews/halloween_portal.png", views or FINAL_VIEWS, (1280, 800), 64):
            print("[build] wrote", f)
        for f in scale_check_render():
            print("[build] wrote", f)
    if snap_before is not None and "--no-verify" not in argv:
        verify_fbx(snap_before)
    print(f"[build] done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1:])
