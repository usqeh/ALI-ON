"""Build, validate, export and render the Halloween stone angel statue.

Run (headless, bpy 5.2):

    <venv>/bin/python blender/build_statue.py            # full pipeline
    <venv>/bin/python blender/build_statue.py --quick    # build + validate + fast WIP renders
    <venv>/bin/python blender/build_statue.py --quick --views=front,side
    options: --no-export --no-render --no-verify --no-decor

Pipeline (mirrors blender/build.py and reuses its export / check helpers):
  1. common.reset_scene(), build the statue at the origin with
     parts.statue.make_angel_statue() (collection "Statue").
  2. Validate every mesh: common.validate_object, unique names, triangle
     budgets (statue <= 7000, any mesh <= 20000), exactly one material,
     M_Glow only on GLOW_ parts, one UV map inside 0..1 with every face on a
     single palette swatch, origins at the bottom centre of each bbox, resting
     heights (ground / plinth top), topology (no inside-out islands, no folded
     faces, no wire edges / loose verts), no zero-area triangles after the
     export triangulation, orb clearance, decor support, near-coplanar faces
     between meshes (build.check_coplanar) AND inside one mesh
     (statue.self_coplanar: overlapping feather rows, cords, moss...), bad
     overlaps, and the name contract exactly as
     roblox/AngelStatueVFX.server.lua matches parts.
  3. Triangulate in place (build.triangulate_all) and export
     export/HalloweenAngelStatue.fbx with build.export_fbx (the Roblox kwargs of
     roblox/EXPORT_NOTES.md: meshes only, no animation, embedded textures,
     relative texture paths).
  4. Pack images and save export/HalloweenAngelStatue.blend (build.save_blend).
  5. Render previews/angel_statue_<view>.png (960x600, 48 samples: the CPU is
     shared) and previews/_wip/angel_statue_scale_<view>.png (960x600, 32
     samples) with a grey 5-stud capsule next to the statue (the capsule is
     never exported).
  6. Re-import the FBX into a fresh scene and compare names, materials,
     embedded texture, triangle counts, world bounds and key dimensions.

Exits with status 1 if validation or the FBX round trip found problems.
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
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

import build  # noqa: E402  (portal pipeline: export kwargs + shared checks)
import common  # noqa: E402
import preview  # noqa: E402
from parts import statue  # noqa: E402

FBX_PATH = os.path.join(common.EXPORT_DIR, "HalloweenAngelStatue.fbx")
BLEND_PATH = os.path.join(common.EXPORT_DIR, "HalloweenAngelStatue.blend")
PREFIX = "Statue"
FINAL_VIEWS = ("front", "three_quarter", "three_quarter_r", "side", "back", "low_hero")
TRI_BUDGET = statue.TRI_BUDGET          # whole statue incl. pedestal and decor
TRI_LIMIT_MESH = build.TRI_LIMIT_MESH   # Roblox hard limit per mesh
GROUND_TOL = build.GROUND_TOL
ORIGIN_TOL = 1e-3
FINAL_RES, FINAL_SAMPLES = (960, 600), 48
ZERO_AREA = 1e-7                        # a triangle smaller than this is degenerate
# keywords the portal VFX script reacts to: never on statue parts (both scripts may
# share a place). 'Flame' is allowed only on the candle flames.
PORTAL_KEYWORDS = ("Float", "Bat_", "PortalSpin", "PortalRim", "PortalTendrils", "PortalCracks",
                   "Portal_Swirl", "MagicCircle", "LanternGlow", "CauldronLiquid", "FlameCore")


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def build_scene(with_decor=True):
    common.reset_scene()
    coll = common.ensure_collection("Statue")
    objs = statue.make_angel_statue(prefix=PREFIX, location=(0, 0, 0), rotation_z=0.0, seed=0,
                                    with_decor=with_decor, glowing_eyes=True)
    for o in objs:
        common.link(o, coll)
        o.data.name = o.name
    return objs


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
_SWATCH = {(col, row): key for key, (col, row, _rgb) in common.PALETTE.items()}
_ROWS = len(common.PALETTE_ROWS)


def swatch_problems(o):
    """Every face's UVs must lie well inside ONE palette swatch of a real key;
    glow swatches only on GLOW_ parts. Returns (problems, set of keys)."""
    uv = o.data.uv_layers[0].data
    bad_cell, mixed = 0, 0
    keys = set()
    for p in o.data.polygons:
        cells = set()
        for li in p.loop_indices:
            u, v = uv[li].uv
            x, y = u * common.PALETTE_COLS, (1.0 - v) * _ROWS
            col, row = int(x), int(y)
            if not (0.1 < x - col < 0.9 and 0.1 < y - row < 0.9):
                bad_cell += 1
            cells.add((col, row))
        if len(cells) != 1:
            mixed += 1
            continue
        keys.add(_SWATCH.get(cells.pop(), "?"))
    probs = []
    if bad_cell:
        probs.append(f"{o.name}: {bad_cell} UVs near a swatch border")
    if mixed:
        probs.append(f"{o.name}: {mixed} faces span several swatches")
    if "?" in keys or "unused" in keys:
        probs.append(f"{o.name}: UVs on a non-palette swatch")
    glow = o.name.startswith("GLOW_")
    if glow and any(not k.startswith("glow_") for k in keys):
        probs.append(f"{o.name}: GLOW part uses solid swatches {sorted(k for k in keys if not k.startswith('glow_'))}")
    if not glow and any(k.startswith("glow_") for k in keys):
        probs.append(f"{o.name}: solid part uses glow swatches")
    return probs, keys


def check_name_contract(meshes):
    """Mirror of how roblox/AngelStatueVFX.server.lua classifies parts
    (plain substring matches + the GLOW_<key>_ prefix)."""
    names = [o.name for o in meshes]
    probs = []

    def hits(sub):
        return [n for n in names if sub in n]

    for sub in (f"{PREFIX}_Angel", f"{PREFIX}_Pedestal", f"{PREFIX}Orb"):
        h = hits(sub)
        if len(h) != 1:
            probs.append(f"contract: {len(h)} parts contain '{sub}' (need exactly 1): {h}")
    for sub, keys, need in ((f"{PREFIX}Orb", ("fire", "amber"), True),
                            (f"{PREFIX}Eyes", ("ember", "fire", "amber"), False),
                            (f"{PREFIX}Cracks", ("fire", "ember", "amber"), False)):
        for n in hits(sub):
            m = build._GLOW_RE.match(n)
            if not m or m.group(1) not in keys:
                probs.append(f"contract: {n} must be GLOW_<{'/'.join(keys)}>_...")
        if len(hits(sub)) > 1:
            probs.append(f"contract: several '{sub}' parts: {hits(sub)}")
        if need and not hits(sub):
            probs.append(f"contract: no '{sub}' part")
    core = {f"{PREFIX}_Angel", f"{PREFIX}_Pedestal", f"{PREFIX}_Wings"}
    for n in names:
        stripped, key = build.strip_glow(n)
        if key is not None and key not in common.GLOW_KEYS:
            probs.append(f"contract: {n}: unknown glow key '{key}'")
        if n in core or stripped in (f"{PREFIX}Orb", f"{PREFIX}Eyes", f"{PREFIX}Cracks"):
            continue
        if not stripped.startswith(f"{PREFIX}Decor_"):
            probs.append(f"contract: {n} is neither a statue part nor '{PREFIX}Decor_*'")
        if "Flame" in n and not re.match(rf"^GLOW_flame_{PREFIX}Decor_CandleFlame_\d\d$", n):
            probs.append(f"contract: flame {n} is not GLOW_flame_{PREFIX}Decor_CandleFlame_##")
        if "JackOLantern" in n and "Face" in n and not n.startswith("GLOW_"):
            probs.append(f"contract: jack-o'-lantern face {n} is not a GLOW part")
        for kw in PORTAL_KEYWORDS:
            if kw in n:
                probs.append(f"contract: {n} contains the portal-script keyword '{kw}'")
    summary = {
        "key parts": hits(f"{PREFIX}_Angel") + hits(f"{PREFIX}_Pedestal"),
        "orb (hover + spin + light + sparkles)": hits(f"{PREFIX}Orb"),
        "eyes (smoulder + blink)": hits(f"{PREFIX}Eyes"),
        "cracks (pulse with the orb)": hits(f"{PREFIX}Cracks"),
        "flames (fire + light)": sorted(n for n in names if "Flame" in n),
        "jack-o'-lantern faces (light)": sorted(n for n in names if "JackOLantern" in n and "Face" in n),
        "GLOW parts -> Neon": sum(1 for n in names if n.startswith("GLOW_")),
    }
    return probs, summary


def _decor_group(name):
    """Group key of a decor part (candle body + flames, jack shell + face)."""
    stripped, _k = build.strip_glow(name)
    if "CandleFlame" in stripped or "Candles" in stripped:
        return "candles"
    m = re.match(r"^(.*?_\d\d)", stripped)
    return m.group(1) if m else stripped


def check_overlaps(meshes):
    """Intersections between parts. Designed contacts are allowed: the robe
    sunk 2 cm into the plinth, glowing cracks inlaid in it, eyes inside the
    hood, decor resting on the plinth, parts of one decor prop. The orb must
    touch nothing; decor must not cut into the angel."""
    trees = {o.name: build.bvh_world(o) for o in meshes}
    angel, ped = f"{PREFIX}_Angel", f"{PREFIX}_Pedestal"
    allowed_pairs = {frozenset((angel, ped)), frozenset((ped, f"GLOW_fire_{PREFIX}Cracks")),
                     frozenset((angel, f"GLOW_ember_{PREFIX}Eyes"))}
    info, probs = [], []
    names = [o.name for o in meshes]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            n = len(trees[a].overlap(trees[b]))
            if not n:
                continue
            info.append((a, b, n))
            pair = frozenset((a, b))
            if pair in allowed_pairs:
                continue
            da, db = "Decor" in a, "Decor" in b
            if da and db and _decor_group(a) == _decor_group(b):
                continue
            if (da and b == ped) or (db and a == ped):
                continue
            probs.append(f"overlap {a} <-> {b}: {n} tri pairs")
    return info, probs


def degenerate_after_triangulation(meshes):
    """{name: (zero-area triangles, slivers, first location)} for the meshes
    as the export will triangulate them (bmesh BEAUTY, like
    build.triangulate_all), measured on an in-memory copy."""
    out = {}
    for o in meshes:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
        bm.transform(o.matrix_world)
        zero = sliver = 0
        first = None
        for f in bm.faces:
            a = f.calc_area()
            pts = [v.co for v in f.verts]
            longest = max((pts[i] - pts[(i + 1) % 3]).length for i in range(3))
            if a < ZERO_AREA:
                zero += 1
            elif 2 * a / max(longest, 1e-12) < 1e-4:
                sliver += 1
            else:
                continue
            first = first or tuple(round(c, 3) for c in pts[0])
        bm.free()
        if zero or sliver:
            out[o.name] = (zero, sliver, first)
    return out


def validate(objs):
    bpy.context.view_layer.update()        # build.check_coplanar / bvh helpers read bound boxes
    problems = []
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    names = [o.name for o in meshes]
    if len(set(names)) != len(names):
        problems.append("duplicate object names")
    if set(names) != {o.name for o in objs}:
        problems.append(f"stray objects in the scene: {sorted(set(names) - {o.name for o in objs})}")
    total = 0
    rows = []
    for o in meshes:
        t = common.tri_count(o)
        total += t
        problems += common.validate_object(o, max_tris=TRI_LIMIT_MESH)
        me = o.data
        if not me.vertices or not me.polygons:
            problems.append(f"{o.name}: empty mesh")
        if len(me.materials) != 1:
            problems.append(f"{o.name}: {len(me.materials)} materials (need exactly 1)")
        mat = me.materials[0].name if me.materials else None
        if o.name.startswith("GLOW_") != (mat == "M_Glow"):
            problems.append(f"{o.name}: material {mat} does not match the GLOW_ prefix")
        if mat == "M_Swirl":
            problems.append(f"{o.name}: M_Swirl is reserved for the portal vortex")
        if o.name.endswith(build.RESERVED_SUFFIXES):
            problems.append(f"{o.name}: reserved importer suffix")
        if o.parent is not None:
            problems.append(f"{o.name}: parented")
        if abs(o.rotation_euler.x) > 1e-6 or abs(o.rotation_euler.y) > 1e-6:
            problems.append(f"{o.name}: X/Y object rotation")
        if me.name != o.name:
            problems.append(f"{o.name}: mesh datablock is named {me.name}")
        if len(me.uv_layers) != 1:
            problems.append(f"{o.name}: {len(me.uv_layers)} UV maps")
            keys = set()
        else:
            vals = [0.0] * (len(me.uv_layers[0].data) * 2)
            me.uv_layers[0].data.foreach_get("uv", vals)
            if vals and (min(vals) < -1e-6 or max(vals) > 1 + 1e-6):
                problems.append(f"{o.name}: UVs outside 0..1")
            sp, keys = swatch_problems(o)
            problems += sp
        # origin = bottom centre of the object's own (local) bbox, as
        # common.origin_to_base leaves it (rotated decor keeps its yaw)
        cos = [v.co for v in me.vertices]
        llo = Vector([min(c[i] for c in cos) for i in range(3)])
        lhi = Vector([max(c[i] for c in cos) for i in range(3)])
        off = Vector(((llo.x + lhi.x) / 2, (llo.y + lhi.y) / 2, llo.z)).length
        if off > ORIGIN_TOL:
            problems.append(f"{o.name}: origin {off:.4f} from its bbox bottom centre")
        lo, hi = statue.world_bounds([o])
        st = statue.mesh_stats(o)
        decor = "Decor" in o.name
        if st["inside_out"]:
            problems.append(f"{o.name}: {st['inside_out']} inside-out closed islands")
        if st["folds"] and not decor:
            problems.append(f"{o.name}: {st['folds']} folded-over faces")
        if st["wire"] or st["loose"]:
            problems.append(f"{o.name}: {st['wire']} wire edges, {st['loose']} loose verts")
        if st["nonmanifold"] and not decor:
            problems.append(f"{o.name}: {st['nonmanifold']} non-manifold edges")
        rows.append((o.name, t, mat, lo, hi, st, sorted(keys)))
    if total > TRI_BUDGET:
        problems.append(f"statue has {total} tris > {TRI_BUDGET}")

    # resting heights: the whole statue on the ground, plinth-top decor on the plinth
    alo, ahi = statue.world_bounds(meshes)
    if abs(alo.z) > GROUND_TOL:
        problems.append(f"lowest point z={alo.z:.4f} (the statue must rest on z=0)")
    ped = bpy.data.objects.get(f"{PREFIX}_Pedestal")
    angel = bpy.data.objects.get(f"{PREFIX}_Angel")
    plo, phi = statue.world_bounds([ped])
    if abs(plo.z) > GROUND_TOL:
        problems.append(f"{ped.name}: bottom at z={plo.z:.4f}")
    for o in meshes:
        if "Decor" not in o.name or o.name.startswith("GLOW_"):
            continue
        lo, _hi = statue.world_bounds([o])
        on_top = lo.z > 1.0
        want = statue.PED_TOP if on_top else 0.0
        if abs(lo.z - want) > GROUND_TOL:
            problems.append(f"{o.name}: bottom z={lo.z:.4f} (expected {want:.3f})")
    clearance = statue.orb_clearance(meshes)
    if clearance < statue.CLEARANCE_MIN:
        problems.append(f"orb clearance {clearance:.3f} < {statue.CLEARANCE_MIN}")
    problems += statue.support_problems(meshes)
    coplanar = build.check_coplanar(meshes)
    for a, b, n, g in coplanar:
        problems.append(f"near-coplanar faces {a} <-> {b}: {n} faces, closest {g * 1000:.1f} mm")
    # same check INSIDE each mesh (build.check_coplanar only compares two meshes)
    self_cop = statue.self_coplanar(meshes, gap=build.COPLANAR_GAP)
    for name, n, g, loc, keys, _locs in self_cop:
        problems.append(f"near-coplanar faces inside {name}: {n} exposed pairs, closest {g * 1000:.2f} mm "
                        f"at {loc} {keys}")
    degenerate = degenerate_after_triangulation(meshes)
    for name, (z, sl, loc) in degenerate.items():
        if z:
            problems.append(f"{name}: {z} zero-area triangles after triangulation (first at {loc})")
    overlaps, oprobs = check_overlaps(meshes)
    problems += oprobs
    cprobs, contract = check_name_contract(meshes)
    problems += cprobs

    # key dimensions
    glo, ghi = statue.world_bounds([angel])
    feet = ghi.z - (statue.PED_TOP - 0.02)
    dims = dict(
        statue=(ahi - alo), statue_nodecor=None,
        angel_height=feet, wingspan=ghi.x - glo.x,
        pedestal=(phi - plo), pedestal_top=statue.PED_TOP,
    )
    core = [o for o in meshes if "Decor" not in o.name]
    clo, chi = statue.world_bounds(core)
    dims["statue_nodecor"] = chi - clo
    biggest = max(meshes, key=common.tri_count)
    return dict(problems=problems, total=total, rows=rows, count=len(meshes), bounds=(alo, ahi),
                overlaps=overlaps, coplanar=coplanar, self_coplanar=self_cop, degenerate=degenerate,
                contract=contract, clearance=clearance, dims=dims,
                biggest=(biggest.name, common.tri_count(biggest)))


def _v(v, nd=2):
    return "(" + ", ".join(f"{c:.{nd}f}" for c in v) + ")"


def print_validation(rep):
    print("\n================ VALIDATION (angel statue) ================")
    print(f"mesh objects: {rep['count']}   total tris: {rep['total']} (budget {TRI_BUDGET})   "
          f"largest mesh: {rep['biggest'][0]} {rep['biggest'][1]} tris (limit {TRI_LIMIT_MESH})")
    for name, t, mat, lo, hi, st, keys in rep["rows"]:
        print(f"  {name:44s} {t:5d} tris  {mat:9s} z [{lo.z:5.2f},{hi.z:5.2f}]  open={st['open_edges']:3d} "
              f"islands={st['islands']:2d} inv={st['inside_out']} folds={st['folds']}  {','.join(keys)}")
    alo, ahi = rep["bounds"]
    d = rep["dims"]
    print(f"bounds x [{alo.x:.2f}, {ahi.x:.2f}]  y [{alo.y:.2f}, {ahi.y:.2f}]  z [{alo.z:.2f}, {ahi.z:.2f}]")
    print(f"size with decor {_v(d['statue'])}, statue alone {_v(d['statue_nodecor'])} (x, y, z studs)")
    print(f"angel {d['angel_height']:.2f} tall (feet to hood top), wingspan {d['wingspan']:.2f}; "
          f"pedestal {_v(d['pedestal'])}, top at z={d['pedestal_top']:.2f}")
    print(f"orb clearance to the stone (both ways): {rep['clearance']:.3f} (min {statue.CLEARANCE_MIN})")
    orb = bpy.data.objects.get(f"GLOW_{statue.ORB_KEY}_{PREFIX}Orb")
    if orb is not None:
        lo, hi = statue.world_bounds([orb])
        print(f"orb pivot (origin) {_v(orb.matrix_world.translation)} = bbox BOTTOM; orb centre "
              f"{_v((lo + hi) / 2)} (Roblox: animate MeshPart.Position/CFrame = bbox centre)")
    print("\n---------------- NAME CONTRACT (as AngelStatueVFX.server.lua sees it) ----------------")
    for k, v in rep["contract"].items():
        print(f"  {k}: {v}")
    print(f"intersecting part pairs: {len(rep['overlaps'])} "
          f"({', '.join(f'{a}x{b}' for a, b, _n in rep['overlaps'])})")
    print(f"near-coplanar same-facing face pairs (< {build.COPLANAR_GAP * 1000:.0f} mm): between meshes "
          f"{len(rep['coplanar'])}, inside one mesh {sum(r[1] for r in rep['self_coplanar'])}")
    slivers = {n: v[1] for n, v in rep["degenerate"].items() if v[1]}
    print(f"zero-area triangles after triangulation: {sum(v[0] for v in rep['degenerate'].values())}; "
          f"slivers (altitude < 0.1 mm, info): {slivers or 0}")
    print(f"problems: {len(rep['problems'])}")
    for p in rep["problems"]:
        print("  PROBLEM:", p)


# --------------------------------------------------------------------------
# Export (build.export_fbx / build.save_blend, pointed at the statue files)
# --------------------------------------------------------------------------
class _StatuePaths:
    """build.export_fbx() and build.save_blend() write to build.FBX_PATH /
    build.BLEND_PATH; point those at the statue files while they run."""

    def __enter__(self):
        self.saved = (build.FBX_PATH, build.BLEND_PATH)
        build.FBX_PATH, build.BLEND_PATH = FBX_PATH, BLEND_PATH
        return self

    def __exit__(self, *exc):
        build.FBX_PATH, build.BLEND_PATH = self.saved
        return False


def export_and_save():
    with _StatuePaths():
        build.export_fbx()
        build.save_blend()


# --------------------------------------------------------------------------
# Renders
# --------------------------------------------------------------------------
def _focus(objs, view):
    c, r = statue.focus(objs)
    c = Vector(c)
    if view == "low_hero":
        c.z += 0.1
    elif view == "back":
        c.y += 0.3
    elif view == "three_quarter_r":      # the jack-o'-lantern is nearest the camera here
        c.z -= 0.45
        r *= 1.06
    return c, r


def render_views(path, views, res, samples, objs):
    out = []
    for v in views:
        out += preview.render(path, views=[v], res=res, samples=samples, focus=_focus(objs, v))
    return out


def _capsule(name, x, y, height=common.CHARACTER_HEIGHT):
    bm = bmesh.new()
    r, segs, rings = 0.9, 12, 4
    prof = [(r * math.cos(-math.pi / 2 + math.pi / 2 * k / rings), r + r * math.sin(-math.pi / 2 + math.pi / 2 * k / rings))
            for k in range(rings + 1)]
    prof += [(r * math.cos(math.pi / 2 * k / rings), height - r + r * math.sin(math.pi / 2 * k / rings))
             for k in range(rings + 1)]
    vs = []
    for rr, z in prof:
        vs.append([bm.verts.new((max(rr, 1e-4) * math.cos(math.tau * s / segs),
                                 max(rr, 1e-4) * math.sin(math.tau * s / segs), z)) for s in range(segs)])
    for i in range(len(vs) - 1):
        for s in range(segs):
            bm.faces.new((vs[i][s], vs[i][(s + 1) % segs], vs[i + 1][(s + 1) % segs], vs[i + 1][s]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-3)
    obj = common.object_from_bmesh(name, bm)
    obj.data.materials.append(common.mat_palette())
    common.paint(obj, "stone_light")
    obj.location = (x, y, 0.0)
    return obj


def scale_check_render(objs, res=(960, 600), samples=32):
    """Grey 5-stud capsule (a Roblox character) beside the statue - preview
    only, removed again before anything else happens."""
    cap = _capsule("ScaleRef_Capsule", 4.3, -1.6)
    try:
        lo, hi = statue.world_bounds(objs + [cap])
        c = (lo + hi) / 2
        r = max(hi.z - lo.z, (hi.x - lo.x) * 0.62) * 0.52
        return preview.render("previews/_wip/angel_statue_scale.png", views=["front", "three_quarter"],
                              res=res, samples=samples, focus=(c, r))
    finally:
        me = cap.data
        bpy.data.objects.remove(cap)
        bpy.data.meshes.remove(me)


# --------------------------------------------------------------------------
# FBX round trip
# --------------------------------------------------------------------------
def verify_fbx(snap, dims):
    print("\n================ FBX RE-IMPORT CHECK ================")
    data = open(FBX_PATH, "rb").read()
    png_sigs = data.count(b"\x89PNG\r\n\x1a\n")
    tex_paths, contents = build.fbx_texture_paths(FBX_PATH)
    leaks = [p for _k, p in tex_paths if os.path.isabs(p) or ".fbm" in p]
    root_leak = common.ROOT.encode() in data
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=FBX_PATH)
    bpy.context.view_layer.update()
    objs = list(bpy.context.scene.objects)
    meshes = [o for o in objs if o.type == "MESH"]
    others = [o for o in objs if o.type != "MESH"]
    issues = []
    names = {o.name for o in meshes}
    if sorted(set(snap) - names):
        issues.append(f"missing after import: {sorted(set(snap) - names)}")
    if sorted(names - set(snap)):
        issues.append(f"unexpected after import: {sorted(names - set(snap))}")
    if others:
        issues.append(f"non-mesh objects imported: {[o.name for o in others]}")
    if bpy.data.actions:
        issues.append(f"animation data imported: {[a.name for a in bpy.data.actions]}")
    max_err, tri_total, mats = 0.0, 0, set()
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
        if len(o.data.uv_layers) != 1:
            issues.append(f"{o.name}: {len(o.data.uv_layers)} UV maps after import")
        lo, hi = build.world_bounds([o])          # same measure as build.snapshot()
        err = max((lo - ref["lo"]).length, (hi - ref["hi"]).length)
        max_err = max(max_err, err)
        if err > 0.01:
            issues.append(f"{o.name}: world bounds moved by {err:.4f}")
    images = {img.name: (tuple(img.size), img.packed_file is not None) for img in bpy.data.images}
    lo, hi = statue.world_bounds(meshes) if meshes else (Vector(), Vector())
    angel = bpy.data.objects.get(f"{PREFIX}_Angel")
    glo, ghi = statue.world_bounds([angel]) if angel else (Vector(), Vector())
    size = hi - lo
    print(f"imported objects: {len(objs)} (meshes {len(meshes)}, expected {len(snap)}), tris {tri_total}")
    print(f"materials: {sorted(mats)}")
    print(f"images: {images}")
    print(f"embedded PNG streams in the .fbx: {png_sigs} (Content sizes {contents} bytes)")
    print(f"texture paths in the .fbx: {sorted(set(p for _k, p in tex_paths))}")
    print(f"imported size (studs): {_v(size)}; {PREFIX}_Angel {_v(ghi - glo)} "
          f"(wingspan {ghi.x - glo.x:.2f}, top z {ghi.z:.2f})")
    print(f"max world-bounds deviation vs built scene: {max_err:.5f} studs")
    if leaks or root_leak:
        issues.append(f"absolute / .fbm texture paths in the FBX: {sorted(set(leaks))} (build root in file: {root_leak})")
    if png_sigs < 1:
        issues.append("palette texture is not embedded in the FBX")
    if mats != {"M_Palette", "M_Glow"}:
        issues.append(f"materials {sorted(mats)} != ['M_Glow', 'M_Palette']")
    if "palette.png" not in images:
        issues.append("palette.png missing after import")
    want = dims["statue"]
    if (size - want).length > 0.01:
        issues.append(f"statue size {_v(size)} != built {_v(want)}")
    print(f"issues: {len(issues)}")
    for i in issues:
        print("  ISSUE:", i)
    return issues


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
    objs = build_scene(with_decor="--no-decor" not in argv)
    rep = validate(objs)
    print_validation(rep)
    print(f"[statue] built and validated in {time.time() - t0:.1f}s")
    failed = bool(rep["problems"])

    if quick:
        img = bpy.data.images.get("palette.png")
        if img is not None and img.packed_file is None:
            img.pack()
        for f in render_views("previews/_wip/angel_statue_build.png", views or ["front", "three_quarter"],
                              (800, 500), 16, objs):
            print("[statue] wrote", f)
        if "--scale" in argv:
            scale_check_render(objs, (800, 500), 16)
        return 1 if failed else 0

    snap = None
    if "--no-export" not in argv:
        build.triangulate_all()
        bpy.context.view_layer.update()        # fresh bound boxes for build.snapshot()
        zero = {n: v for n, v in statue.degenerate_faces(objs, area=ZERO_AREA).items() if v[0]}
        if zero:
            print(f"PROBLEM: zero-area triangles in the exported meshes: {zero}")
            failed = True
        snap = build.snapshot()
        export_and_save()
    if "--no-render" not in argv:
        for f in render_views("previews/angel_statue.png", views or FINAL_VIEWS, FINAL_RES, FINAL_SAMPLES, objs):
            print("[statue] wrote", f)
        for f in scale_check_render(objs):
            print("[statue] wrote", f)
    if snap is not None and "--no-verify" not in argv:
        if verify_fbx(snap, rep["dims"]):
            failed = True
    print(f"[statue] done in {time.time() - t0:.1f}s - {'PROBLEMS FOUND' if failed else 'all checks passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
