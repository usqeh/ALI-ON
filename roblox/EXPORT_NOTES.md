# Halloween Portal: Blender to Roblox Studio export notes

How to export the asset from Blender 5.2 (1 Blender unit = 1 stud, Z up, portal facing -Y) so the
Roblox Studio 3D Importer gets the right scale, orientation, pivots and names. It also covers how
`HalloweenPortalVFX.server.lua` attaches to the result.

Sources: official Roblox Creator Docs. create.roblox.com could not be reached from the build
sandbox, so the pages were read from the docs' source repository
(github.com/Roblox/creator-docs, `content/en-us/...`), which holds the same text. The Blender side
was checked by exporting test files with bpy 5.2.2 and parsing the FBX
(`io_scene_fbx/parse_fbx.py`).

## 1. Recommended export call (bpy)

```python
# Before export: triangulate every mesh in place (bmesh, no bpy.ops). Concave pumpkin n-gons then
# triangulate the same way in Roblox as in Blender, and the FBX geometry keeps the object names
# (use_triangles=True would make temp meshes called "Name.001").
for ob in [o for o in bpy.context.scene.objects if o.type == 'MESH']:
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method='BEAUTY', ngon_method='BEAUTY')
    bm.to_mesh(ob.data); bm.free()

bpy.ops.export_scene.fbx(
    filepath=os.path.join(common.EXPORT_DIR, "HalloweenPortal.fbx"),
    check_existing=False,
    use_selection=False,              # scene must contain only the asset meshes (preview.render removes its helpers)
    use_visible=False,
    use_active_collection=False,
    object_types={'MESH'},            # no cameras, lights or empties
    global_scale=1.0,
    apply_unit_scale=True,
    apply_scale_options='FBX_SCALE_UNITS',   # "Apply Scalings: FBX Unit Scale" (what Roblox recommends)
    axis_forward='Z',                 # Roblox doc: "Forward: Z Forward"
    axis_up='Y',                      # Roblox doc: "Up: Y Up"
    use_space_transform=True,
    bake_space_transform=True,        # axis change goes into the vertices: MeshParts import with 0,0,0 orientation
    use_mesh_modifiers=True,
    mesh_smooth_type='FACE',          # normals are always written; flat shading survives
    use_triangles=False,              # already triangulated above (True also works; see above)
    use_tspace=False,
    colors_type='NONE',               # no vertex colours: Roblox would multiply them into the texture
    prioritize_active_color=False,
    use_custom_props=False,
    add_leaf_bones=False,             # Roblox doc: disable Add Leaf Bones
    bake_anim=False,                  # Roblox doc: disable Bake Animation (static asset)
    path_mode='COPY',                 # Roblox doc: Path Mode = Copy
    embed_textures=True,              #             + Embed Textures
    batch_mode='OFF',
    use_metadata=True,
)
```

### What this writes (checked by parsing the exported FBX)

| Item | Result |
|---|---|
| Vertex and translation values | 1:1 with Blender units (a 13 x 14 frame is 13 x 14 in the file) |
| `UnitScaleFactor` | 100 (FBX "1 unit = 1 m", the same as Blender's default metric scene) |
| Axis mapping | Blender `(x, y, z)` becomes file/Studio `(-x, z, y)`. This is Z-up to Y-up plus a 180 degree turn about the vertical axis, so nothing is mirrored (checked: `GLOW_fire_MagicCircle_01` at Blender `(0, -9.05, 0.04)` is at file `(0, 0.04, -9.05)`; `Bat_Portal_01` at Blender `(9.84, -0.77, 12.15)` is at file `(-9.84, 12.15, -0.77)`) |
| Facing | The portal faces Blender -Y, which becomes Studio -Z. That is the imported Model's `LookVector` (front) |
| Node rotation | Identity for unrotated objects. A placed instance with Blender `rotation_z = +30 deg` gets file rotation `(0, 30, 0)` |
| Origin | Each object's origin (bottom centre, `common.origin_to_base`) is the node translation. Studio uses it as the MeshPart pivot |
| Textures | `palette.png` (and `portal_swirl.png` when used) are embedded in the .fbx |

Doc-listed alternative that gives the same file coordinates: `apply_scale_options='FBX_SCALE_NONE'`,
`global_scale=0.01` (only `UnitScaleFactor` changes, to 1). Do not use the Blender defaults
(`FBX_SCALE_NONE`, scale 1.0, `-Z` forward). They write 100x vertices and turn the portal to face
Studio +Z (the back).

## 2. Studio 3D Importer settings (File > Import > HalloweenPortal.fbx)

| Section | Setting | Value | Why |
|---|---|---|---|
| File General | Import Only As Model | **On** (default) | One Model with all MeshParts |
| File General | Anchored | **On** (default is off!) | Otherwise the portal falls apart in Play. The VFX script runs on the client (section 4), and a client cannot anchor parts for the server's physics; it only warns |
| File General | Set Pivot to Scene Origin | **On** (default) | Model pivot = Blender origin (dais centre on the ground, axes = file axes; the script reads "front" from it) |
| File General | Insert Using Scene Position | Your choice (default off = drop at camera) | |
| File General | Upload to Roblox / Add to Workspace | Defaults | |
| File Transform | World Forward / World Up | **Front / Top** (defaults) | Roblox doc for Blender files |
| File Geometry | Scale Unit | **Stud** (default) | Roblox doc: keeps the Blender scale |
| File Geometry | Merge Meshes | **Off** (default) | The VFX script needs the separate, named MeshParts |
| File Geometry | Invert Negative Faces | Off | |
| Object General | Use Imported Pivot | On (default) | Pivot = Blender origin |
| Object Geometry | Make Double Sided | Only for a paper-thin mesh, if any (costs performance) | |
| Object Geometry | Ignore Vertex Colors | Off (there are none) | |

**After import, in Studio Properties:**

| Object | Property | Value | Why |
|---|---|---|---|
| The imported Model (`HalloweenPortal`) | `ModelStreamingMode` | **Atomic** (default: Nonatomic) | With StreamingEnabled (on by default) the whole portal streams in and out as one piece: frame, swirl, spin arms, bat bodies with their eyes, jack-o'-lantern shells with their faces. The client never sees half a portal. Roblox recommends Atomic when "a script needs access to all of the parts within a model". Do not use Persistent: Roblox reserves it for rare cases and it keeps the parts in memory for good |
| `Portal_Swirl` | `CanCollide` | **false** (keep `CanTouch` on) | The client script sets it too, but in client mode that change is local: the server, NPCs and server physics would still treat the vortex as a wall. With it off in Studio, everyone agrees and `Touched` still fires for a teleport script |
| `Portal_Frame` | `CollisionFidelity` | PreciseConvexDecomposition (or Default) | See the Collision bullet in section 3. Never Box or Hull |

**Scale check after import:** `Portal_Frame` should measure about 13 x 14 x 2 studs and a 5-stud
character should reach roughly a third of the frame's height. If the whole Model is 100x too big or
too small, re-import with the other Scale Unit. Or fix it in place from the command bar with
`workspace.HalloweenPortal:ScaleTo(0.01)` (or `ScaleTo(100)`); `Model:ScaleTo` scales the parts,
offsets, lights and particles together. Then re-export with the alternative above for next time.

## 3. Research conclusions

- **Units and scale.** Roblox's Blender page says: set Blender units to "None" (or keep metric with
  Unit Scale 1.0; the FBX exporter treats both the same, factor 100). Export with *Apply Scalings =
  FBX Unit Scale* and every other scale at 1.0, then import with *Scale Unit = Stud* for "the same
  scale". It also lists Transform Scale 0.01 and scene Unit Scale 0.01 as alternatives. All three
  give the same raw file coordinates as above. Since Roblox says all three import at the expected
  size, Studio in Stud mode reads raw file units as studs. A third-party guide mentions a "June 2026
  unit conversion update" and extra Scale Unit choices (meter, cm, ...); this could not be confirmed
  in the official docs. That is why the post-import size check above is there.
- **Triangle limit.** "Individual meshes can not exceed 20,000 triangles." The specs also ask for
  watertight meshes with real volume (no zero-thickness geometry) and quads or tris instead of
  n-gons. So flat parts (`PortalSpin_A/B`, `Portal_Swirl`, glow outlines) should be thin closed slabs
  (0.04 to 0.1 studs). A single plane is one-sided in Roblox unless *Make Double Sided* is enabled.
- **Materials.** "Mesh objects can only have one material assigned." Keep exactly one of
  M_Palette / M_Glow / M_Swirl per object; never join glow and solid parts. Emission is not mapped
  to glow by the importer (no doc says so; FBX emission is ignored as far as known). Glow is done
  with Neon by the script.
- **The vortex (`Portal_Swirl`) is not a GLOW part.** It arrives as a lit, textured MeshPart
  (TextureID, or a SurfaceAppearance because the FBX also links an emission texture to M_Swirl).
  At night it will look much darker than in the Blender previews, where M_Swirl has emission 1.6.
  The script helps in three ways: a non-shadowing PointLight 3 studs in front of the swirl lights
  the vortex face (`SwirlLightBrightness`/`SwirlLightRange`); a SurfaceLight on the swirl's front
  face throws orange light out of the opening onto the dais and players (`SwirlSurfaceLight*`); and
  if `Portal_Swirl` has a SurfaceAppearance, it sets `EmissiveStrength = SwirlEmissive` (1.5,
  breathing with the lights), `EmissiveTint` white, and, if no emissive mask is set, uses the
  colour map as the mask. For a fully self-lit vortex, add a SurfaceAppearance to `Portal_Swirl` in
  Studio yourself: `ColorMap` = the imported swirl texture id, `EmissiveMaskContent` = the same id,
  `EmissiveStrength` 1.5 to 3. **Not verifiable in the build sandbox**: tune it in Studio at night
  (`Lighting.Technology = Future`, low `ClockTime`, a `BloomEffect`).
- **Textures.** Basic textures (Principled BSDF Base Color image) import natively. The docs say the
  imported Model has "the appropriate textures applied as a SurfaceAppearance or
  MeshPart.TextureID": one colour map gives TextureID, PBR maps give SurfaceAppearance. Texture
  limits: uploads up to 4096 px, texture maps up to 1024x1024, one UV set, UVs inside 0..1.
  palette.png is 256x128 and the swirl texture must stay at 1024 px or below.
- **Multiple objects.** .fbx supports "multiple mesh objects and hierarchies". With *Import Only As
  Model* you get one Model with one MeshPart per mesh object, named after the object (editable in
  the importer). No maximum mesh count is documented. Keep the hierarchy flat (no Blender
  parenting) so the Model is a flat list of MeshParts. The script scans all descendants, so nesting
  would still work.
- **Reserved name suffixes.** The importer turns objects ending in `_Att` into **Attachments**, and
  objects ending in `_OuterCage` / `_InnerCage` into WrapTarget / WrapLayer cages. Never end an
  object name with these.
- **Pivots.** Each MeshPart's CFrame is its bounding-box centre. The Blender origin becomes the
  pivot (`PivotOffset`, *Use Imported Pivot*). The Model pivot goes to the file origin (*Set Pivot
  to Scene Origin*).
- **Vertex colours** are supported by the importer (FBX/glTF) but unused here (`colors_type='NONE'`).
- **Collision.** `CollisionFidelity` cannot be changed from a running script; set it in Studio
  Properties after import.
  - `Portal_Frame` must stay **Default** or **PreciseConvexDecomposition** (recommended:
    PreciseConvexDecomposition, the closest fit around the opening). **Never Box or Hull**: both fill
    the opening, and nobody can walk into the portal any more.
  - `Portal_Dais`: Hull is fine (round, low). Small props (pumpkins, candles, gravestones, candy
    corn): Box is fine and cheap.
  - The glow outline parts do not collide at all: the script turns off `CanCollide`, `CanTouch`
    and `CanQuery` on `*PortalRim*`, `*PortalTendrils*` and `*PortalCracks_Frame*`, whatever their
    size (so their fidelity does not matter). Otherwise the spiky tendrils (17.5 studs wide) would add collision bumps
    about 2 studs past the frame, and the inner rim ring (it sticks 0.19 into the opening at
    y = +-0.9) would narrow the opening on top of the frame's 0.25 lip. It does the same on every
    GLOW part smaller than 6 studs and on everything it animates. `GLOW_ember_PortalCracks_Dais`
    stays solid: it is flush with the dais top. `Portal_Swirl` gets `CanCollide = false`
    (`SwirlCanCollide`), so players walk in and `Touched` still fires for a teleport script.
  - In client mode (section 4) all of these collision changes are local to each player. Each
    player's own character is simulated by that player's client, so walking into the portal works.
    The server, NPCs and unanchored server physics still see the imported values. That is why
    `Portal_Swirl.CanCollide = false` should also be set in Studio (section 2). The same applies to
    the rim and tendrils if NPCs walk near the portal.
- **Streaming.** The streaming docs (`workspace/streaming`, "Stream out") say a streamed-out
  instance "is parented to `nil`", comes back as the same instance, and "local-only changes to
  instance properties ... can be lost". A client script's Neon, colour, texture, shadow and
  collision changes are exactly such local changes, so the script re-applies them on every
  re-entry (section 4). Set the Model to Atomic (section 2) so the portal streams as one piece.

## 4. VFX script: `HalloweenPortalVFX.server.lua`

1. **Set the Script's `RunContext` to `Client`** (Properties > Behavior). Every player then
   animates the portal locally: smooth, no network traffic, no LocalScript needed. With Rojo,
   `roblox/HalloweenPortalVFX.meta.json` (`{"properties":{"RunContext":"Client"}}`) already does
   this for `HalloweenPortalVFX.server.lua`.
2. **Put the Script inside the imported Model in Workspace.** That Model is then the script's
   root: it scans everything under it by name. Other places, and what root each one gives:
   - A Folder in Workspace that holds the portal Model: the Folder (keep only the portal in it,
     because every part in it is matched by name).
   - Directly in Workspace, inside a character, or in ServerScriptService (server fallback below):
     the nearest Model that owns `Portal_Frame`, or the Model/Folder named `RootName`. If it has
     not streamed in or been inserted yet, the script waits for it (and warns once after 30 s).
     It never uses Workspace itself or a whole map as its root.
   - A client copy outside Workspace, such as a template kept in ReplicatedStorage, does nothing;
     otherwise it would find and animate the live portal a second time.

   Whatever the root, the script never touches a character or NPC rig (a part inside any Model
   with a Humanoid is skipped), and `AnchorAll` only anchors parts inside the Model that owns
   `Portal_Frame`.
   Only one copy drives a portal. The first one leaves an `HVFX_ClientOwner` ObjectValue in the
   Model (local on a client) holding the Script and its run number. A later copy backs off unless
   that owner Script has been destroyed or removed. If a copy does take over (or the same Script
   runs a second time), the older one notices within a frame or two and stops. The new one starts
   from the rest poses the first run recorded (in `shared`), not from mid-animation CFrames.
3. **Import with Anchored = On** (section 2). In client mode the script warns once if it finds an
   unanchored part, because anchoring on the client cannot stop the server's physics.
4. **Streaming (StreamingEnabled is on by default).** Set the Model's `ModelStreamingMode` to
   Atomic (section 2). The script copes either way:
   - Parts that stream in late are set up when they arrive (`DescendantAdded`).
   - A part that streams out and back in comes back as the same instance and may have lost the
     script's local property changes. The script re-applies them on every re-entry: Anchored
     (AnchorAll), Neon, palette colour, TextureID cleared, SurfaceAppearance removed again,
     CastShadow, CanCollide/CanTouch/CanQuery on glow parts and movers, and `Portal_Swirl`
     CastShadow plus `CanCollide = SwirlCanCollide` and its emissive SurfaceAppearance. Rim and
     crack pulses and tendril flicker carry on, because they start again from the palette colour.
   - An Atomic Model streams out and back in as a whole. Its parts never leave it, so
     `DescendantAdded` stays silent. The script listens for the Model's `AncestryChanged`
     instead, re-applies everything when the Model is back in Workspace, and skips all animation
     work while it is out.
   - Particle emitters, lights, attachments and beams are created once. They are children of
     the parts and stream out and back in with them, so they are never duplicated.
   - A SurfaceAppearance that reaches a part after the part was set up (streaming order) is
     handled too: removed from glow parts, made emissive on `Portal_Swirl`.

Fallback: as a plain server Script (RunContext Legacy/Server, also from ServerScriptService, where
it uses the nearest Model that owns `Portal_Frame`, as above) it still works. But every frame it
then writes about 50 part CFrames, plus light brightness, colours and beams, and all of that
replicates to every client. Anchored-part CFrames are not interpolated on clients, so the spin arms,
bats and floaters stutter. It prints a warning once and caps `FlickerHz` at `ServerFlickerHz` (10).
A server copy marks the Model (`HVFX_ServerOwned`). Client copies then back off, and a client copy
that was already running stops.

Settings live in the `CONFIG` table at the top. Any of them can be overridden with an Attribute of
the same name on the Script or the Model.

### Name contract (substring match; a `.001` suffix is tolerated)

| Name | What the script does |
|---|---|
| `GLOW_<key>_*` (key: fire ff6a00, ember ff3d00, amber ffa21a, flame ffd166, white fff1d6, purple a24bff) | Neon, exact colour, TextureID cleared, SurfaceAppearance removed, no shadow, anchored. No collision, touch or query if smaller than 6 studs, animated, or a `PortalRim` / `PortalTendrils` / `PortalCracks_Frame` part |
| `Portal_Swirl` | Normal = thinnest Size axis, turned toward the Model pivot's LookVector. Gets spark emitters (front and back), purple/black wisps, two pulsing orange PointLights (the front one lights the vortex face), a SurfaceLight on the front face, emissive strength on its SurfaceAppearance if it has one, short crackling arcs (Beams) around the opening. `CanCollide=false`, so players can walk in (Touched still fires); re-applied whenever it streams back in. Also set it in Studio (section 2) |
| `Portal_Frame`, `Portal_Dais` | Reference only (frame depth/extent for the arcs and the bat clearance) |
| `*PortalSpin_A` / `*PortalSpin_B` | Rotate about the portal normal through their **own bounding-box centre**: A at +0.9 rad/s, B at -0.55 rad/s |
| `*PortalRim*`, `*PortalCracks*` | Slow colour pulse. `*PortalTendrils*`: crackling on/off flicker |
| `*MagicCircle*` | Turns about world up through its bounding-box centre at 0.25 rad/s (`MagicCircleSpin`) and pulses like the cracks. Must be round (it is) |
| `*Float*` | Bobs (about 0.35 studs, random but deterministic phase). Cubes (`*Cube*`) spin; other floaters sway gently |
| `Bat_*` (also `GLOW_<key>_Bat_*`) | Orbits the portal centre about world up, bobs, flutters, banks, and turns its nose along the flight path. All bats turn at the same angular speed (`BatSpeed / BatRefRadius`), so the spacing they start with never changes and they never fly through each other. Each orbit is pushed out until it clears the frame and every `Float` group whose height range overlaps the bat's (`BatAutoClearance`; `BatMinRadius` adds a floor). The nose is read in the imported pivot's axes (`PivotOffset`), so it is right whether Studio keeps the node rotation on the part or bakes it; if the pivot disagrees with the body-to-`Eyes` direction by more than 60 degrees, the eyes win |
| `*Flame*` | Fire particles (fire_main.dds, size capped at `FlameMaxSize` = 1.1 studs) + sparks + flickering PointLight (brightness only). `*Brazier*` flames also get dark smoke (smoke_main.dds). `*Candle*` flames get a small fire; candle flames within `CandleLightSpacing` (2 studs) of an existing candle light share it, so there is one light per candle cluster. `*Cauldron*` flames get small (0.56 stud), slow, dense fire that stays under the pot rim |
| `JackOLantern*_Face` | Warm flickering PointLight |
| `*LanternGlow*` | Warm flickering PointLight |
| `*CauldronLiquid*` | Rising bubbles + steam + coloured light |

### Rules the modellers and assembler must follow for the script to look right

1. **Spin arms must be point-symmetric** (2 or 4 arms, or a symmetric spiral), so the
   bounding-box centre is the hub. Otherwise they wobble. If that is impossible, set
   `CONFIG.SpinCenter = "swirl"` (spins about the swirl centre). They must also fit inside the
   circle inscribed in the opening.
2. **Multi-part movers share a base name up to their last number.** `Pumpkin_Float_01` +
   `Pumpkin_Float_01_Stem` + `GLOW_fire_Pumpkin_Float_01_Face` move as one rigid group, and so do
   `Bat_03` + `GLOW_ember_Bat_03_Eyes`. A part without the keyword but with the same base name as a
   Float group also follows it (`JoinGroupsByKey`). So never give a ground prop the same base name
   as a floating one: use `Pumpkin_Float_01`, not `Pumpkin_01` + `Pumpkin_01_Float`.
3. **Bats are modelled with the nose pointing -Y in Blender** and their eyes in front of the body
   centre. With the export above, the nose is local -Z of the imported pivot, which is
   `CONFIG.BatLocalForward`. Place them 10 or more studs high or well away from props: they orbit
   around world up through the portal centre, and the script pushes an orbit outward when it would
   touch the frame or a floating prop at the bat's height. Start the bats at least 45 degrees apart
   around the portal: they keep that spacing forever.
4. Keep `bake_space_transform=True`. Without it, every MeshPart carries an axis rotation, and
   `BatLocalForward` would have to be changed (the rest of the script copes with either).
5. The keywords are case-sensitive substrings and are reserved: do not put `Flame`, `Float`,
   `PortalSpin`, `LanternGlow`, `CauldronLiquid`, `MagicCircle`, `Portal_Swirl`, `PortalRim`,
   `PortalTendrils`, `PortalCracks` in names of unrelated props
   (e.g. call a brazier bowl `Brazier_Bowl_L`, not `Brazier_FlameBowl_L`). The glow key itself is
   lowercase (`GLOW_flame_...`), so it never triggers the `Flame` rule.
6. Neon glows best with `Lighting.Technology = Future`, a night ClockTime and a `BloomEffect` in
   Lighting. The script does not touch Lighting.
7. Light budget with the current scene: 16 PointLights + 1 SurfaceLight (`MaxLights` = 28 caps
   it). Only `Brightness` flickers; `Range` is never animated, because changing it invalidates the
   lighting grid. On weak mobile targets set `CandleLights = false` (minus 4 lights).

### Verification done

- `luau-compile` (latest luau-lang/luau GitHub release, Oct 2026): the script parses and compiles.
- `luau-lsp analyze` (luau-lsp 1.70.1) `--platform=roblox` with the current Roblox API definitions
  (`globalTypes.d.luau`): **0 errors in nonstrict mode**, so every property, method and enum used
  exists. Strict mode reports only inference noise from mixed tables.
- Ran the whole script in standalone Luau against a mock Roblox API (Instances, CFrame math,
  Heartbeat stepping), for 10 s of frames in 4 layouts: baked orientations, unbaked (-90 degree X
  part rotations), the Model rotated 40 degrees inside a Folder, and a RunContext=Client run.
  132 checks pass in each (133 in the client run). They cover: Neon/colour/texture clearing;
  rim and tendrils lose collision/touch/query, the frame keeps collision; the expected emitters
  and lights on every part, incl. the swirl SurfaceLight on its front face and the emissive
  SurfaceAppearance; spin arms keep their centre, turn at the configured rate, and A/B turn
  opposite ways; the magic circle stays centred and flat and turns at 0.25 rad/s; bats keep a
  constant orbit radius, face their flight direction and carry their eyes rigidly; float groups
  stay rigid; the late-added jack-o'-lantern face joins its floating shell; ground props and the
  frame stay still; arcs flash; Range is never animated; re-added parts (streaming) get no
  duplicate effects; late-added flames get set up; a second copy backs off; a client template
  copy in ReplicatedStorage does nothing; a client copy takes over when the owning copy was
  destroyed.
- Ran it for 40 s against the real part list of `export/HalloweenPortal.fbx` (names, node
  translations and Y rotations, bounding boxes), in both importer behaviours (node rotation kept
  on the MeshPart CFrame, or baked into the mesh with identity CFrame and the rotation only in
  `PivotOffset`), on the server and on a client. Bat heading error is at most 0.4 degrees in both.
  Bat bounding boxes overlap nothing (no other bat, no floating prop, no static part) in any frame.
  Orbit radii are 10.4 to 13.4 studs. Bat_Portal_03's eyes added 10 s late (streaming) cause no
  jump (before the fix the bat snapped 10 studs back to its start). 4 candle lights (CandleFlame
  01, 03, 05, 06), 17 lights in total. Fire particles are capped at 1.1 studs, cauldron fire at
  0.56. Only `GLOW_ember_PortalCracks_Dais` keeps collision among the GLOW parts. Range is written
  only at setup. Brightness and colour writes drop to about a third on the server (10 Hz).
- Root lookup and streaming (mock extended with working `Disconnect`/`Connected`,
  `AncestryChanged`, `Humanoid`, `task.wait`, `shared`; 50 checks in 7 scenarios, all pass; the
  same tests fail 35 times on the previous version of the script, so they do catch the bugs):
  - Server Script in ServerScriptService with the portal at `Workspace.Map.Lobby.HalloweenPortal`:
    the root is `HalloweenPortal`, not `Map`. An unanchored crate, an unrelated `Balloon_Float_01`,
    a `WallTorch_Flame` and an NPC rig in the map stay untouched.
  - Client copy directly in Workspace before anything has streamed in: it waits instead of
    taking Workspace as its root, and the character stays unanchored. The parts then stream in
    in random order with the frame arriving midway. The root becomes the portal Model, every glow
    part is set up (including parts that arrive after the frame), and a respawned character is
    not touched.
  - Folder root holding the portal, a crate and an NPC, plus an NPC (Humanoid) placed inside the
    portal Model: AnchorAll leaves the crate alone, and neither NPC is anchored or animated.
  - Parts stream out, are reset to plain imported values (Plastic, grey, TextureID, shadows,
    collision, the SurfaceAppearance back), and stream back in. Every property is re-applied, no
    effect is duplicated, the spin arm still turns about its centre, the rim keeps pulsing and the
    tendrils keep flickering. A SurfaceAppearance added to `Portal_Swirl` later is made emissive
    and breathes.
  - Atomic Model out and back in as a whole: no CFrame writes while it is out, everything
    re-applied when it returns, animation resumes.
  - The same Script runs again mid-animation: the first run retires, and the floater bobs about
    its true rest height (this check fails without the rest pose registry). A removed copy retires
    when a replacement takes over, and a client copy stops when `HVFX_ServerOwned` appears.
- Not verified: the real look in Studio (particle sizes, light ranges, how dark the imported swirl
  is at night, whether an empty emissive mask can be replaced by the colour map at runtime); tune
  those in CONFIG and Studio.

### Built-in textures used

`rbxasset://textures/particles/fire_main.dds`, `.../smoke_main.dds`, `.../sparkles_main.dds`.
These are the stock textures behind the legacy Fire, Smoke and Sparkles objects and the default
ParticleEmitter. No uploaded asset ids are used.

## 5. References

- Blender for Roblox: units, import and export settings, "Adjust scale (FBX)":
  https://create.roblox.com/docs/art/blender
- Export settings (Path Mode Copy, Embed Textures, FBX Unit Scale, no leaf bones, no bake animation):
  https://create.roblox.com/docs/art/modeling/export-requirements
- General mesh specifications (20,000 tris per mesh, watertight, volume, no n-gons):
  https://create.roblox.com/docs/art/modeling/specifications
- Importer and all import settings: https://create.roblox.com/docs/studio/importer
- Meshes (`_Att` / `_OuterCage` / `_InnerCage` conventions, TextureID vs SurfaceAppearance):
  https://create.roblox.com/docs/parts/meshes
- Texture specifications (one material per mesh, 1024 px maps, single UV set):
  https://create.roblox.com/docs/art/modeling/texture-specifications
- Assign textures in Blender (Principled BSDF Base Color image):
  https://create.roblox.com/docs/art/modeling/assign-textures
- Import result "SurfaceAppearance or MeshPart.TextureID":
  https://create.roblox.com/docs/avatar/rigid-accessories/import
- Particle emitters (part vs attachment emission, EmissionDirection, shapes):
  https://create.roblox.com/docs/effects/particle-emitters
- Roblox units (1 stud = 28 cm, physics only): https://create.roblox.com/docs/physics/units
- Doc source mirror used for reading: https://github.com/Roblox/creator-docs
- Background on the historic 100x FBX issue:
  https://devforum.roblox.com/t/modelling-units-versus-roblox-studs/2392679
- Blender exporter scale logic: `io_scene_fbx/export_fbx_bin.py` (`FBX_SCALE_UNITS` keeps the
  vertices 1:1 and writes UnitScaleFactor = 100 * scale_length).
