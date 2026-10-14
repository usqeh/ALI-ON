"""Preview renderer for checking the asset visually (not exported).

Usage from a part module or build script:

    import preview
    preview.render("previews/_wip/pumpkins.png", views=["front", "three_quarter"])

Renders every visible mesh in the scene with Cycles (CPU), a dusky Halloween
sky, a temporary ground plane and a bloom pass so GLOW_ objects read as
glowing. Each view is written as <name>_<view>.png. Preview-only objects are
put in a "_preview" collection and removed again afterwards.
"""

import math
import os

import bpy
from mathutils import Vector

import common

VIEWS = {
    # name: (azimuth degrees around Z from -Y, elevation degrees)
    "front": (0, 8),
    "three_quarter": (-35, 18),
    "three_quarter_r": (35, 18),
    "side": (90, 10),
    "top": (0, 70),
    "back": (180, 12),
    "low_hero": (-20, 3),
}


def _bounds(objs):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def _setup_world(scene, sky=(0.10, 0.05, 0.14), strength=1.0):
    world = bpy.data.worlds.get("_PreviewWorld") or bpy.data.worlds.new("_PreviewWorld")
    scene.world = world
    nt = world.node_tree
    if nt is None:
        world.use_nodes = True
        nt = world.node_tree
    bg = nt.nodes.get("Background")
    bg.inputs[0].default_value = (*sky, 1.0)
    bg.inputs[1].default_value = strength


def _setup_compositor(scene, bloom=True):
    ng = bpy.data.node_groups.get("_PreviewComp")
    if ng is None:
        ng = bpy.data.node_groups.new("_PreviewComp", "CompositorNodeTree")
        ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = ng.nodes.new("CompositorNodeRLayers")
        out = ng.nodes.new("NodeGroupOutput")
        glare = ng.nodes.new("CompositorNodeGlare")
        glare.name = "Bloom"
        glare.inputs["Type"].default_value = "Bloom"
        glare.inputs["Quality"].default_value = "High"
        glare.inputs["Threshold"].default_value = 0.9
        glare.inputs["Strength"].default_value = 0.6
        glare.inputs["Size"].default_value = 0.5
        ng.links.new(rl.outputs["Image"], glare.inputs["Image"])
        ng.links.new(glare.outputs["Image"], out.inputs[0])
    ng.nodes["Bloom"].mute = not bloom
    scene.compositing_node_group = ng


def _sun(coll, name, rot, energy, col):
    light = bpy.data.lights.new(name, "SUN")
    light.energy = energy
    light.color = col
    light.angle = math.radians(8)
    obj = bpy.data.objects.new(name, light)
    obj.rotation_euler = [math.radians(a) for a in rot]
    coll.objects.link(obj)
    return obj


def render(path, views=("front", "three_quarter"), objects=None, res=(960, 600),
           samples=48, ground=True, bloom=True, night=True, margin=1.15,
           focus=None):
    """Render preview images. ``path`` like 'previews/_wip/name.png' (relative
    paths resolve against the repo root). Returns list of written files.

    objects: meshes to frame (default: all visible meshes).
    focus:   optional (center Vector, radius) to override automatic framing.
    """
    scene = bpy.context.scene
    if not os.path.isabs(path):
        path = os.path.join(common.ROOT, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stem, ext = os.path.splitext(path)
    ext = ext or ".png"

    targets = objects or [o for o in scene.objects if o.type == "MESH" and o.visible_get()]
    lo, hi = _bounds(targets)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1.0)
    if focus is not None:
        center, radius = Vector(focus[0]), focus[1]

    coll = common.ensure_collection("_preview")
    created = []
    try:
        if ground:
            me = bpy.data.meshes.new("_PreviewGround")
            size = radius * 8
            me.from_pydata([(-size, -size, 0), (size, -size, 0), (size, size, 0), (-size, size, 0)], [], [(0, 1, 2, 3)])
            g = bpy.data.objects.new("_PreviewGround", me)
            gm = bpy.data.materials.get("_PreviewGroundMat") or bpy.data.materials.new("_PreviewGroundMat")
            if gm.node_tree is None:
                gm.use_nodes = True
            gm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.035, 0.05, 0.03, 1)
            gm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
            me.materials.append(gm)
            coll.objects.link(g)
            created.append(g)

        if night:
            created.append(_sun(coll, "_Key", (50, 0, -35), 1.6, (0.75, 0.7, 1.0)))
            created.append(_sun(coll, "_Rim", (60, 0, 150), 1.2, (1.0, 0.55, 0.25)))
            _setup_world(scene, sky=(0.06, 0.035, 0.10), strength=1.0)
        else:
            created.append(_sun(coll, "_Key", (45, 0, -30), 3.5, (1.0, 0.97, 0.92)))
            _setup_world(scene, sky=(0.55, 0.65, 0.9), strength=1.0)

        cam_data = bpy.data.cameras.new("_PreviewCam")
        cam_data.lens = 50
        cam = bpy.data.objects.new("_PreviewCam", cam_data)
        coll.objects.link(cam)
        created.append(cam)
        scene.camera = cam

        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
        scene.cycles.max_bounces = 4
        scene.render.resolution_x, scene.render.resolution_y = res
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        _setup_compositor(scene, bloom)

        fov = 2 * math.atan(cam_data.sensor_width / (2 * cam_data.lens))
        aspect = res[0] / res[1]
        fov_v = 2 * math.atan(math.tan(fov / 2) / aspect) if aspect > 1 else fov
        dist = radius * margin / math.sin(min(fov, fov_v) / 2)

        written = []
        for view in views:
            az, el = VIEWS[view] if isinstance(view, str) else view
            az, el = math.radians(az), math.radians(el)
            d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
            cam.location = center + d * dist
            cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
            label = view if isinstance(view, str) else f"az{int(math.degrees(az))}_el{int(math.degrees(el))}"
            scene.render.filepath = f"{stem}_{label}{ext}"
            bpy.ops.render.render(write_still=True)
            written.append(scene.render.filepath)
        return written
    finally:
        for o in created:
            data = o.data
            bpy.data.objects.remove(o)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Light):
                    bpy.data.lights.remove(data)
                elif isinstance(data, bpy.types.Camera):
                    bpy.data.cameras.remove(data)
