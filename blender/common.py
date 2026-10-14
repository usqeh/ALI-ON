"""Shared conventions for the Halloween portal asset (Blender 5.2, bpy).

Every part module imports this file. It fixes the things that must be the same
across the whole asset so the exported FBX is consistent:

* Scale: 1 Blender unit == 1 Roblox stud. A Roblox character is ~5 studs tall.
* Orientation: Z is up, the portal faces -Y (the camera/player stands at -Y).
* Origins: every prop has its origin at the bottom centre of its footprint,
  so it sits on the ground when placed at z = 0.
* Colour: low-poly "palette texture" workflow. All solid colours live in one
  small PNG (export/textures/palette.png); each face's UVs are dropped onto a
  swatch of that PNG. One texture serves every mesh in every engine.
* Glow: emissive meshes are separate objects named ``GLOW_<key>_<Desc>`` where
  <key> is one of GLOW_KEYS. The Roblox script turns them into Neon parts.
* Materials: only M_Palette (solid), M_Glow (emissive) and M_Swirl (portal
  vortex texture) exist. Never create other materials.
"""

import math
import os
import random

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(ROOT, "export")
TEXTURE_DIR = os.path.join(EXPORT_DIR, "textures")
PREVIEW_DIR = os.path.join(ROOT, "previews")
WIP_DIR = os.path.join(PREVIEW_DIR, "_wip")

# --------------------------------------------------------------------------
# Palette (sRGB hex). Black + orange Halloween, with neutral stone/wood/bone.
# Keys are stable identifiers; add new ones only at the end of a row.
# --------------------------------------------------------------------------
PALETTE_ROWS = [
    # row 0: blacks and stone
    [("black", "0b0a0d"), ("charcoal", "1a181f"), ("ash", "2b2830"),
     ("stone_dark", "3b3742"), ("stone", "54505c"), ("stone_light", "777280"),
     ("moss_dark", "2f3a22"), ("bone", "e6dcc2")],
    # row 1: oranges / pumpkin
    [("pumpkin_dark", "a83c08"), ("pumpkin", "e8620f"), ("pumpkin_light", "ff8a2b"),
     ("pumpkin_rib", "c94f0b"), ("amber", "f5a623"), ("candle_wax", "f2e6c9"),
     ("rust", "7a3410"), ("purple_dark", "3a1d4f")],
    # row 2: wood / plants / metal
    [("wood_dark", "2a1a10"), ("wood", "4a2e1a"), ("wood_light", "6e4526"),
     ("stem_green", "4c6b22"), ("stem_dark", "2f4416"), ("iron", "24222a"),
     ("iron_light", "403d47"), ("gold", "c99a2e")],
    # row 3: glow colours (used by GLOW_ objects; see GLOW_KEYS)
    [("glow_fire", "ff6a00"), ("glow_ember", "ff3d00"), ("glow_amber", "ffa21a"),
     ("glow_flame", "ffd166"), ("glow_void", "1a0b06"), ("glow_white", "fff1d6"),
     ("glow_purple", "a24bff"), ("unused", "ff00ff")],
]

# Glow keys usable in GLOW_<key>_<Desc> object names -> palette entry.
GLOW_KEYS = {
    "fire": "glow_fire",      # main portal orange
    "ember": "glow_ember",    # hot red-orange
    "amber": "glow_amber",    # warm yellow-orange
    "flame": "glow_flame",    # pale flame core / candle
    "white": "glow_white",    # sparing highlight only
    "purple": "glow_purple",  # sparing accent only (user wants black/orange)
}

SWATCH_PX = 32
PALETTE_COLS = 8
PALETTE_W = SWATCH_PX * PALETTE_COLS
PALETTE_H = SWATCH_PX * len(PALETTE_ROWS)

PALETTE = {}  # key -> (col, row, (r, g, b) sRGB 0..1)
for _row, _entries in enumerate(PALETTE_ROWS):
    for _col, (_key, _hex) in enumerate(_entries):
        PALETTE[_key] = (_col, _row, tuple(int(_hex[i:i + 2], 16) / 255 for i in (0, 2, 4)))

STUD = 1.0
CHARACTER_HEIGHT = 5.0


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def color(key):
    """Linear RGBA for a palette key (for viewport colours / lights)."""
    r, g, b = PALETTE[key][2]
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1.0)


def swatch_uv(key):
    """UV centre of a palette swatch. Row 0 is at the TOP of the PNG."""
    col, row, _ = PALETTE[key]
    u = (col + 0.5) / PALETTE_COLS
    v = 1.0 - (row + 0.5) / len(PALETTE_ROWS)
    return u, v


# --------------------------------------------------------------------------
# Scene helpers
# --------------------------------------------------------------------------
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    _MAT_CACHE.clear()
    return scene


def ensure_collection(name, parent=None):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def link(obj, collection=None):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def object_from_bmesh(name, bm, collection=None):
    mesh = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    link(obj, collection)
    return obj


def apply_transforms(obj, location=False, rotation=True, scale=True):
    """Bake rotation/scale (and optionally location) into the mesh data."""
    loc, rot, sca = obj.matrix_world.decompose()
    m = Matrix.Identity(4)
    if location:
        m = Matrix.Translation(loc) @ m
    if rotation:
        m = m @ rot.to_matrix().to_4x4()
    if scale:
        m = m @ Matrix.Diagonal(sca).to_4x4()
    obj.data.transform(m)
    obj.matrix_world = Matrix.LocRotScale(
        Vector((0, 0, 0)) if location else loc,
        None if rotation else rot,
        None if scale else sca,
    )
    return obj


def apply_modifiers(obj):
    """Apply all modifiers using the evaluated mesh (no bpy.ops, no context)."""
    deps = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(deps)
    mesh = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=True, depsgraph=deps)
    old = obj.data
    obj.modifiers.clear()
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return obj


def origin_to_base(obj):
    """Move the origin to the bottom centre of the mesh bounding box, keeping
    the mesh where it is in the world."""
    me = obj.data
    if not me.vertices:
        return obj
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    offset = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
    me.transform(Matrix.Translation(-offset))
    obj.matrix_world = obj.matrix_world @ Matrix.Translation(offset)
    return obj


def shade_flat(obj):
    for p in obj.data.polygons:
        p.use_smooth = False
    return obj


def join(objects, name):
    """Join several mesh objects into one (keeps UVs/materials), returns it."""
    objects = [o for o in objects if o is not None]
    target = objects[0]
    bm = bmesh.new()
    for o in objects:
        me = o.data.copy()
        me.transform(o.matrix_world)
        # Re-map material indices into the target's material list.
        tmp = bmesh.new()
        tmp.from_mesh(me)
        for f in tmp.faces:
            mat = o.data.materials[f.material_index] if o.data.materials else None
            f.material_index = _material_slot(target, mat)
        tmp.to_mesh(me)
        tmp.free()
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    for o in objects[1:]:
        bpy.data.objects.remove(o)
    new_me = bpy.data.meshes.new(name)
    bm.to_mesh(new_me)
    bm.free()
    old = target.data
    mats = list(old.materials)
    target.data = new_me
    for m in mats:
        new_me.materials.append(m)
    if old.users == 0:
        bpy.data.meshes.remove(old)
    target.matrix_world = Matrix.Identity(4)
    target.name = name
    target.data.name = name
    return target


def _material_slot(obj, mat):
    if mat is None:
        mat = mat_palette()
    for i, m in enumerate(obj.data.materials):
        if m == mat:
            return i
    obj.data.materials.append(mat)
    return len(obj.data.materials) - 1


def tri_count(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def scene_tri_count(objects=None):
    objs = objects if objects is not None else [o for o in bpy.context.scene.objects if o.type == "MESH"]
    return sum(tri_count(o) for o in objs if o.type == "MESH")


# --------------------------------------------------------------------------
# Textures and materials
# --------------------------------------------------------------------------
_MAT_CACHE = {}


def palette_image():
    """Create (and save to export/textures/palette.png) the palette texture."""
    img = bpy.data.images.get("palette.png")
    if img is not None:
        return img
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    img = bpy.data.images.new("palette.png", PALETTE_W, PALETTE_H, alpha=False)
    img.colorspace_settings.name = "sRGB"
    px = [0.0] * (PALETTE_W * PALETTE_H * 4)
    for key, (col, row, (r, g, b)) in PALETTE.items():
        # Blender pixel rows start at the bottom of the image.
        y0 = PALETTE_H - (row + 1) * SWATCH_PX
        x0 = col * SWATCH_PX
        for y in range(y0, y0 + SWATCH_PX):
            base = (y * PALETTE_W + x0) * 4
            for x in range(SWATCH_PX):
                i = base + x * 4
                px[i:i + 4] = (r, g, b, 1.0)
    img.pixels.foreach_set(px)
    path = os.path.join(TEXTURE_DIR, "palette.png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    img.filepath = path
    return img


def _principled(mat):
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return bsdf


def _new_material(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    return mat


def _image_node(mat, image, location=(-400, 0)):
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Closest" if image.name == "palette.png" else "Linear"
    tex.location = location
    return tex


def mat_palette():
    """Solid low-poly material: base colour from the palette texture."""
    if "M_Palette" in _MAT_CACHE:
        return _MAT_CACHE["M_Palette"]
    mat = _new_material("M_Palette")
    bsdf = _principled(mat)
    tex = _image_node(mat, palette_image())
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.85
    bsdf.inputs["Metallic"].default_value = 0.0
    _MAT_CACHE["M_Palette"] = mat
    return mat


def mat_glow(strength=2.0):
    """Emissive material: base + emission colour from the palette texture."""
    if "M_Glow" in _MAT_CACHE:
        return _MAT_CACHE["M_Glow"]
    mat = _new_material("M_Glow")
    bsdf = _principled(mat)
    tex = _image_node(mat, palette_image())
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = strength
    bsdf.inputs["Roughness"].default_value = 0.5
    _MAT_CACHE["M_Glow"] = mat
    return mat


def mat_swirl(image, strength=1.6):
    """Portal vortex material using a dedicated texture (see parts/vfx.py)."""
    if "M_Swirl" in _MAT_CACHE:
        return _MAT_CACHE["M_Swirl"]
    mat = _new_material("M_Swirl")
    bsdf = _principled(mat)
    tex = _image_node(mat, image)
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = strength
    bsdf.inputs["Roughness"].default_value = 0.6
    _MAT_CACHE["M_Swirl"] = mat
    return mat


def save_image(img, filename):
    """Save a generated bpy image as PNG into export/textures/."""
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    path = os.path.join(TEXTURE_DIR, filename)
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    img.filepath = path
    return path


# --------------------------------------------------------------------------
# Painting faces with palette swatches
# --------------------------------------------------------------------------
def _ensure_uv(bm):
    uv = bm.loops.layers.uv.get("UVMap")
    if uv is None:
        uv = bm.loops.layers.uv.new("UVMap")
    return uv


def paint_bmesh_faces(bm, faces, key):
    """Drop the UVs of ``faces`` onto palette swatch ``key`` (bmesh level).

    Loops are spread on a small circle inside the swatch so UV triangles keep
    a non-zero area (avoids degenerate tangents in game engines)."""
    uv = _ensure_uv(bm)
    cu, cv = swatch_uv(key)
    r = 0.22 / PALETTE_COLS * 0.5  # well inside one swatch
    for f in faces:
        n = len(f.loops)
        for i, loop in enumerate(f.loops):
            a = 2 * math.pi * i / n
            loop[uv].uv = (cu + r * math.cos(a), cv + r * math.sin(a) * PALETTE_COLS / len(PALETTE_ROWS))


def paint(obj, key, faces=None, glow=False):
    """Paint whole object (or polygon indices ``faces``) with palette ``key``
    and assign M_Palette or M_Glow. Returns obj."""
    mat = mat_glow() if glow else mat_palette()
    slot = _material_slot(obj, mat)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    sel = bm.faces if faces is None else [bm.faces[i] for i in faces]
    paint_bmesh_faces(bm, sel, key)
    for f in sel:
        f.material_index = slot
    bm.to_mesh(obj.data)
    bm.free()
    return obj


def paint_by(obj, fn, glow=False):
    """Paint each polygon with ``fn(center: Vector, normal: Vector, index) -> key``."""
    mat = mat_glow() if glow else mat_palette()
    slot = _material_slot(obj, mat)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    groups = {}
    for f in bm.faces:
        key = fn(f.calc_center_median(), f.normal.copy(), f.index)
        groups.setdefault(key, []).append(f)
        f.material_index = slot
    for key, faces in groups.items():
        paint_bmesh_faces(bm, faces, key)
    bm.to_mesh(obj.data)
    bm.free()
    return obj


def make_glow(obj, key, desc=None):
    """Turn ``obj`` into a glow object: GLOW_<key>_<Desc> name + M_Glow."""
    if key not in GLOW_KEYS:
        raise ValueError(f"unknown glow key {key!r}; use one of {sorted(GLOW_KEYS)}")
    obj.data.materials.clear()
    paint(obj, GLOW_KEYS[key], glow=True)
    desc = desc or obj.name
    obj.name = f"GLOW_{key}_{desc}"
    obj.data.name = obj.name
    return obj


# --------------------------------------------------------------------------
# Small geometry helpers
# --------------------------------------------------------------------------
def rng(seed):
    return random.Random(seed)


def jitter_bmesh(bm, amount, seed=0, verts=None):
    """Random vertex offset for a hand-made low-poly look."""
    r = random.Random(seed)
    for v in (verts if verts is not None else bm.verts):
        v.co += Vector((r.uniform(-amount, amount), r.uniform(-amount, amount), r.uniform(-amount, amount)))


def validate_object(obj, max_tris=20000):
    """Return a list of problems for a finished game-asset object."""
    problems = []
    if obj.type != "MESH":
        return problems
    if any(abs(s - 1.0) > 1e-4 for s in obj.scale):
        problems.append(f"{obj.name}: unapplied scale {tuple(obj.scale)}")
    if tri_count(obj) > max_tris:
        problems.append(f"{obj.name}: {tri_count(obj)} tris > {max_tris}")
    if not obj.data.uv_layers:
        problems.append(f"{obj.name}: no UV map")
    allowed = {"M_Palette", "M_Glow", "M_Swirl"}
    for m in obj.data.materials:
        if m is None or m.name not in allowed:
            problems.append(f"{obj.name}: material {m.name if m else None} not allowed")
    if "." in obj.name:
        problems.append(f"{obj.name}: name contains '.' (auto-suffix?) - name it explicitly")
    if obj.name.startswith("GLOW_"):
        parts = obj.name.split("_")
        if len(parts) < 3 or parts[1] not in GLOW_KEYS:
            problems.append(f"{obj.name}: bad GLOW_<key>_<Desc> name")
    return problems
