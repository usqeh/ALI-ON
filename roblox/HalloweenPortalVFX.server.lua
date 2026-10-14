--!nonstrict
--[[
	HalloweenPortalVFX  (Luau Script)
	=================================

	Runtime VFX for the imported Halloween portal FBX (Model of MeshParts).

	SETUP
	  1. Set this Script's RunContext property to "Client" (Properties > Behavior > RunContext).
	     Every player then animates the portal locally: smooth, no network traffic, no LocalScript needed.
	     Rojo: HalloweenPortalVFX.meta.json next to this file already does it ({"properties":{"RunContext":"Client"}}).
	  2. Put the Script directly inside the imported portal Model (Workspace > HalloweenPortal > this Script).
	     A Folder in Workspace that holds only the portal Model works too (everything in it is scanned by name).
	     Placed directly in Workspace (or in ServerScriptService, see Fallback) it uses the nearest Model that
	     owns "Portal_Frame" (or the Model/Folder named CONFIG.RootName) and waits until that has streamed in.
	     It never uses Workspace or a whole map as its root, never touches a character or NPC rig (any Model
	     with a Humanoid), and AnchorAll only anchors parts inside the Model that owns Portal_Frame.
	     A client copy outside Workspace (e.g. a template in ReplicatedStorage) does nothing, and only one copy
	     drives a portal.
	  3. Import with Anchored = On (or anchor the Model in Studio). The script anchors parts too, but on a
	     client that cannot stop the server's physics; it warns once if it finds unanchored parts.
	  4. Recommended in Studio: Model.ModelStreamingMode = Atomic (the portal streams in as one piece) and
	     Portal_Swirl.CanCollide = false (so the server and NPCs agree with the client). Streaming can drop
	     local-only property changes when a part streams out and back in, so the script re-applies its look
	     (Neon, colours, textures, collision, shadows) whenever a part or the whole Model comes back.
	  Fallback: as a plain server Script (RunContext Legacy/Server, also from ServerScriptService, where it
	  searches Workspace for the Model owning "Portal_Frame" or CONFIG.RootName) it still works, but every
	  CFrame/light/colour change replicates to all clients (stutter, bandwidth). It warns once and lowers
	  FlickerHz to ServerFlickerHz. Use one or the other: a server copy marks the Model, client copies back off.

	WHAT IT DOES (by part name, see EXPORT_NOTES.md "name contract")
	  GLOW_<key>_*                Neon + exact palette colour, TextureID cleared, no shadow, anchored. No
	                              collision/touch/query if small, animated, or a rim/tendril/frame-crack part.
	  *PortalSpin_A / _B          spins around the portal normal (normal = thinnest axis of Portal_Swirl).
	  *Float*                     bobs up/down (+ gentle yaw); parts sharing a base name move together.
	  Bat_*                       orbits the portal centre, bobs, banks and faces its flight direction. All bats
	                              share one angular speed (they never catch up with each other); orbits are
	                              pushed out past the frame and past floating props at the bat's height.
	  *Flame*                     fire particles + flickering PointLight (Brazier* flames also get smoke;
	                              candle flames closer than CandleLightSpacing share one light).
	  JackOLantern*_Face          warm flickering PointLight.
	  *LanternGlow*               warm flickering PointLight.
	  *CauldronLiquid*            rising bubbles + glow light.
	  *MagicCircle*               turns slowly about world up + colour pulse.
	  Portal_Swirl                ember sparks drifting out, dark/purple wisps, pulsing orange lights (one lights
	                              the vortex face, a SurfaceLight throws light out of the opening), emissive
	                              strength on a SurfaceAppearance if there is one, crackling arcs around the opening.
	  *PortalRim* / *PortalCracks*  slow colour pulse;  *PortalTendrils*  electric flicker.

	Every setting in CONFIG can be overridden without editing code by adding an Attribute with the same
	name (and same type) to this Script or to the root Model, e.g. a number attribute "SpinSpeedA" = 1.5.
	Only built-in rbxasset:// textures are used, no uploaded asset ids.
]]

local RunService = game:GetService("RunService")

------------------------------------------------------------------------------------------------------
-- CONFIG
------------------------------------------------------------------------------------------------------
local CONFIG = {
	Enabled = true,
	RootName = "", -- optional: name of the portal Model/Folder in Workspace (when the script is not inside it)
	AnchorAll = true, -- anchor every BasePart of the Model owning Portal_Frame (the importer leaves them unanchored)

	-- glow parts
	GlowNeon = true,
	GlowNoCollideBelow = 6, -- GLOW_ parts whose largest size (studs) is below this lose collision
	-- (rim, tendrils and frame cracks never collide whatever their size, see NO_COLLIDE_GLOW below)

	-- portal swirl (the importer ignores FBX emission: Portal_Swirl arrives as a lit, textured MeshPart)
	SwirlCanCollide = false, -- false: players can walk into the opening (Touched still fires)
	SwirlSparks = true,
	SwirlWisps = true,
	SwirlLights = true, -- PointLight 3 studs in front (lights the vortex face itself) + a weaker one behind
	SwirlLightRange = 26,
	SwirlLightBrightness = 3,
	SwirlSurfaceLight = true, -- SurfaceLight on the swirl's front face: light pouring out of the opening
	SwirlSurfaceLightRange = 18,
	SwirlSurfaceLightBrightness = 2,
	SwirlSurfaceLightAngle = 80,
	SwirlEmissive = 1.5, -- EmissiveStrength set on a SurfaceAppearance under Portal_Swirl, if any (0 = leave)
	FlipFront = false, -- set true if the front light/sparks end up behind the portal

	-- spinning vortex arms
	SpinSpeedA = 0.9, -- radians / second (sign = direction)
	SpinSpeedB = -0.55,
	SpinCenter = "bounds", -- "bounds" (MeshPart centre) | "pivot" (imported origin) | "swirl" (swirl centre)

	-- rim / tendrils / cracks
	RimPulse = true,
	TendrilFlicker = true,
	CrackPulse = true,
	RimArcs = true,
	ArcCount = 3,

	-- floating things
	FloatAmplitude = 0.35, -- studs
	FloatSpeed = 1.6, -- radians / second
	FloatSwayDegrees = 7,
	FloatCubeSpin = 0.8, -- radians / second for *Cube* floaters
	JoinGroupsByKey = true, -- "GLOW_fire_JackOLantern_03_Face" follows "JackOLantern_03_Float"

	-- bats
	BatSpeed = 7, -- studs / second along an orbit of BatRefRadius studs
	BatRefRadius = 11, -- every bat turns at BatSpeed / BatRefRadius rad/s, so their spacing never changes
	BatDirection = 1, -- 1 or -1 = everyone the same way, 0 = random per bat (bats may then cross each other)
	BatBobAmplitude = 0.8,
	BatBobSpeed = 1.3,
	BatFlutter = 0.12,
	BatBankDegrees = 14,
	BatAutoClearance = true, -- push each orbit out past the frame and past Float groups at the bat's height
	BatMinRadius = 0, -- extra floor for every orbit radius (studs), applied on top of the auto clearance
	BatLocalForward = Vector3.new(0, 0, -1), -- nose direction in the imported pivot's axes (-Z = Blender -Y)

	-- magic circle (flat glowing decal in front of the portal)
	MagicCircleSpin = 0.25, -- radians / second about world up (0 = still)

	-- lights / particles
	FlameParticles = true,
	FlameMaxSize = 1.1, -- studs: cap on the fire particle size (big flame meshes would emit huge blobs)
	FlameLights = true,
	BrazierSmoke = true,
	CandleLights = true,
	CandleLightSpacing = 2, -- studs: a candle flame this close to an existing candle light shares it
	JackOLanternLights = true,
	LanternLights = true,
	CauldronBubbles = true,
	LightShadows = false,
	MaxLights = 28, -- hard cap on lights (PointLight + SurfaceLight) this script creates
	FlickerHz = 30, -- light/colour update rate (animation itself runs every frame); only Brightness flickers
	ServerFlickerHz = 10, -- FlickerHz is capped to this when the script runs on the server (replicated)
}

-- glow parts that never collide, whatever their size: the frame mesh already gives the real collision,
-- and the spiky tendrils / inner rim would otherwise add bumps around the frame and narrow the opening
local NO_COLLIDE_GLOW = { "PortalTendrils", "PortalRim", "PortalCracks_Frame" }

local PALETTE = {
	fire = Color3.fromHex("ff6a00"),
	ember = Color3.fromHex("ff3d00"),
	amber = Color3.fromHex("ffa21a"),
	flame = Color3.fromHex("ffd166"),
	white = Color3.fromHex("fff1d6"),
	purple = Color3.fromHex("a24bff"),
}
local COL_CHARCOAL = Color3.fromHex("1a181f")
local COL_BLACK = Color3.fromHex("0b0a0d")
local COL_VOID = Color3.fromHex("1a0b06")

local TEX_FIRE = "rbxasset://textures/particles/fire_main.dds"
local TEX_SMOKE = "rbxasset://textures/particles/smoke_main.dds"
local TEX_SPARKLE = "rbxasset://textures/particles/sparkles_main.dds"

local TAG = "HVFX_" -- prefix for everything this script creates (makes the script idempotent)
local UP = Vector3.new(0, 1, 0)

------------------------------------------------------------------------------------------------------
-- small helpers
------------------------------------------------------------------------------------------------------
local function has(s: string, sub: string): boolean
	return string.find(s, sub, 1, true) ~= nil
end

local function hashString(s: string): number
	local h = 5381
	for i = 1, #s do
		h = (h * 33 + string.byte(s, i)) % 2147483647
	end
	return h
end

-- "GLOW_fire_Pumpkin_Float_01_Face" -> "Pumpkin_Float_01_Face", "fire"
local function stripGlow(name: string): (string, string?)
	local key, rest = string.match(name, "^GLOW_(%w+)_(.+)$")
	if key then
		return rest, string.lower(key)
	end
	return name, nil
end

-- Parts that belong together share the base name up to their last number:
-- "Pumpkin_Float_01_Stem" -> "Pumpkin_Float_01", "Bat_03_Eyes" -> "Bat_03".
local function groupKey(stripped: string): string
	stripped = string.gsub(stripped, "%.%d+$", "") -- tolerate Blender ".001" suffixes
	return string.match(stripped, "^(.*%d)") or stripped
end

-- world-space half extents of an oriented box
local function halfExtents(cf: CFrame, size: Vector3): Vector3
	local r, u, l = cf.RightVector, cf.UpVector, cf.LookVector
	local hx, hy, hz = size.X * 0.5, size.Y * 0.5, size.Z * 0.5
	return Vector3.new(
		math.abs(r.X) * hx + math.abs(u.X) * hy + math.abs(l.X) * hz,
		math.abs(r.Y) * hx + math.abs(u.Y) * hy + math.abs(l.Y) * hz,
		math.abs(r.Z) * hx + math.abs(u.Z) * hy + math.abs(l.Z) * hz
	)
end

-- world direction of the thinnest local axis (the normal of a flat part)
local function thinnestAxis(part: BasePart): Vector3
	local s, cf = part.Size, part.CFrame
	if s.X <= s.Y and s.X <= s.Z then
		return cf.RightVector
	elseif s.Y <= s.Z then
		return cf.UpVector
	end
	return cf.LookVector
end

-- the part face (NormalId) whose outward normal is closest to a world direction
local function normalIdFor(part: BasePart, dir: Vector3): Enum.NormalId
	local cf = part.CFrame
	local dx, dy, dz = cf.RightVector:Dot(dir), cf.UpVector:Dot(dir), -cf.LookVector:Dot(dir)
	local ax, ay, az = math.abs(dx), math.abs(dy), math.abs(dz)
	if ax >= ay and ax >= az then
		return if dx >= 0 then Enum.NormalId.Right else Enum.NormalId.Left
	elseif ay >= az then
		return if dy >= 0 then Enum.NormalId.Top else Enum.NormalId.Bottom
	end
	return if dz >= 0 then Enum.NormalId.Back else Enum.NormalId.Front
end

-- yaw of a horizontal vector, consistent with CFrame.Angles(0, yaw, 0) applied to -Z
local function yawOf(v: Vector3): number
	return math.atan2(-v.X, -v.Z)
end

local function seq(points: { { number } }): NumberSequence
	local kps = {}
	for _, p in points do
		table.insert(kps, NumberSequenceKeypoint.new(p[1], p[2]))
	end
	return NumberSequence.new(kps)
end

local function colorSeq(points: { { any } }): ColorSequence
	local kps = {}
	for _, p in points do
		table.insert(kps, ColorSequenceKeypoint.new(p[1], p[2]))
	end
	return ColorSequence.new(kps)
end

-- find-or-create a tagged child so the script never duplicates effects
local function child(parent: Instance, className: string, name: string): (Instance, boolean)
	local existing = parent:FindFirstChild(TAG .. name)
	if existing and existing.ClassName == className then
		return existing, false
	end
	local inst = Instance.new(className)
	inst.Name = TAG .. name
	return inst, true
end

-- attachment at a world position with world-aligned axes
local function attachmentAt(part: BasePart, name: string, worldPos: Vector3): Attachment
	local att = child(part, "Attachment", name) :: Attachment
	att.CFrame = part.CFrame:ToObjectSpace(CFrame.new(worldPos))
	att.Parent = part
	return att
end

------------------------------------------------------------------------------------------------------
-- config overrides from attributes, root lookup
------------------------------------------------------------------------------------------------------
-- true for anything inside a character or NPC rig (a Model with a Humanoid): never anchored, animated or lit
local function inCharacter(x: Instance): boolean
	local p: Instance? = x
	while p and p ~= workspace and p ~= game do
		if p:IsA("Model") and p:FindFirstChildWhichIsA("Humanoid") then
			return true
		end
		p = p.Parent
	end
	return false
end

local function isFramePart(x: Instance): boolean
	return x:IsA("BasePart") and has(x.Name, "Portal_Frame") and not inCharacter(x)
end

-- the nearest container that owns a part: its nearest Model, else its Folder. Never Workspace itself
-- (Workspace is a Model too, so FindFirstAncestorWhichIsA("Model") can return it).
local function ownerOf(x: Instance): Instance?
	local m = x:FindFirstAncestorWhichIsA("Model")
	if m and m ~= workspace then
		return m
	end
	local p = x.Parent
	if p and p ~= workspace and p:IsDescendantOf(workspace) then
		return p
	end
	return nil
end

local function isNamedRoot(x: Instance): boolean
	return CONFIG.RootName ~= "" and x.Name == CONFIG.RootName and (x:IsA("Model") or x:IsA("Folder"))
		and x ~= workspace and not inCharacter(x)
end

-- one pass over Workspace: the RootName container, else the owner of Portal_Frame (false = frame has no owner)
local function scanForRoot(): Instance | false | nil
	for _, d in workspace:GetDescendants() do
		if CONFIG.RootName ~= "" then
			if isNamedRoot(d) then
				return d
			end
		elseif isFramePart(d) then
			return ownerOf(d) or false
		end
	end
	return nil
end

local function findRoot(): Instance?
	local parent = script.Parent
	if parent and parent:IsA("BasePart") then
		parent = ownerOf(parent) -- dropped onto a part in Explorer: use the Model around it
	end
	if CONFIG.RootName ~= "" then
		local named = scanForRoot()
		if named then
			return named
		end
	end
	if parent and parent ~= workspace and parent:IsDescendantOf(workspace) and not inCharacter(parent) then
		return parent
	end
	-- Directly in Workspace, in ServerScriptService or in a character: never fall back to the script's own
	-- parent (Workspace would make every part in the place, characters included, part of the portal).
	local found = scanForRoot()
	if found == nil then
		-- not streamed in / not inserted yet: wait for it (no WaitForChild: the frame can sit at any depth)
		local conn = workspace.DescendantAdded:Connect(function(x)
			if found == nil then
				if CONFIG.RootName ~= "" then
					if isNamedRoot(x) then
						found = x
					end
				elseif isFramePart(x) then
					found = ownerOf(x) or false
				end
			end
		end)
		if found == nil then
			found = scanForRoot() -- it may have arrived between the first scan and Connect
		end
		local waited, warned = 0, false
		while found == nil do
			waited += task.wait(0.5)
			if waited >= 30 and not warned then
				warned = true
				warn("[HalloweenPortalVFX] still waiting for "
					.. (if CONFIG.RootName ~= "" then "a Model named " .. CONFIG.RootName else "a part named Portal_Frame")
					.. " in Workspace. Put this Script inside the portal Model to skip the search.")
			end
		end
		conn:Disconnect()
	end
	if found == false then
		warn("[HalloweenPortalVFX] Portal_Frame sits directly in Workspace: group the portal parts into a Model.")
		return nil
	end
	return found
end

local function applyAttributeOverrides(from: Instance?)
	if not from then
		return
	end
	for key, default in CONFIG do
		local value = from:GetAttribute(key)
		if value ~= nil and typeof(value) == typeof(default) then
			CONFIG[key] = value
		end
	end
end

local IS_CLIENT = RunService:IsClient()
if IS_CLIENT and not script:IsDescendantOf(workspace) then
	-- RunContext=Client scripts also run in ReplicatedStorage etc.: a template copy there must not
	-- go looking for (and animate a second time) the live portal in Workspace
	return
end

applyAttributeOverrides(script) -- early, so a RootName attribute on the script is seen by findRoot
local root = findRoot()
if root == workspace or (root and root:IsA("BasePart")) then
	warn("[HalloweenPortalVFX] refusing to use " .. root:GetFullName() .. " as the portal root. Put this Script"
		.. " inside the portal Model.")
	return
end
applyAttributeOverrides(root)
applyAttributeOverrides(script) -- attributes on the script win
if not CONFIG.Enabled or not root then
	return
end

-- Per-machine registry shared by every copy of this script (and by a re-run of the same copy): run ids and
-- the rest pose of every animated part, so a copy that takes over never captures a mid-animation CFrame.
local SHARED: any = shared
local REG = SHARED.HalloweenPortalVFX
if type(REG) ~= "table" then
	REG = { runs = 0, rest = {} }
	SHARED.HalloweenPortalVFX = REG
end
REG.runs += 1
local RUN_ID = REG.runs

-- one driver per portal and per machine. The marker remembers which Script (and which run of it) claimed the
-- portal, so a copy that was destroyed (streaming, re-parenting) does not block its replacement, and a copy
-- that is replaced while still running retires (see stillOwner below) instead of animating twice.
local function claimRoot(markerName: string): ObjectValue?
	local marker = root:FindFirstChild(markerName)
	if marker and marker:IsA("ObjectValue") then
		local owner = marker.Value
		if owner and owner ~= script and owner.Parent ~= nil and owner:IsDescendantOf(game) then
			return nil
		end
	else
		marker = Instance.new("ObjectValue")
		marker.Name = markerName
	end
	local m = marker :: ObjectValue
	m.Value = script
	m:SetAttribute("HVFX_Run", RUN_ID)
	m.Parent = root
	return m
end

local ownerMarker: ObjectValue?
if IS_CLIENT then
	if root:GetAttribute("HVFX_ServerOwned") then
		return -- a server copy already drives this portal
	end
	ownerMarker = claimRoot(TAG .. "ClientOwner")
	if not ownerMarker then
		return -- another client copy already drives it (the marker is client-made, it stays local)
	end
	-- (unanchored parts are reported once in register: anchoring on a client cannot stop server physics)
else
	ownerMarker = claimRoot(TAG .. "ServerOwner")
	if not ownerMarker then
		return
	end
	root:SetAttribute("HVFX_ServerOwned", true)
	warn("[HalloweenPortalVFX] running on the server: every animated CFrame, light and colour change is"
		.. " replicated to all clients (stutter, bandwidth). Set this Script's RunContext to Client for smooth"
		.. " local animation.")
	CONFIG.FlickerHz = math.min(CONFIG.FlickerHz, CONFIG.ServerFlickerHz)
end

-- false once another copy (or a newer run of this one, or a server copy) has taken the portal over. Only
-- positive evidence counts: a marker value that is merely missing never stops the animation.
local function stillOwner(): boolean
	local m = ownerMarker :: ObjectValue
	local owner = m.Value
	if owner ~= nil and owner ~= script then
		return false
	end
	local run = m:GetAttribute("HVFX_Run")
	if run ~= nil and run ~= RUN_ID then
		return false
	end
	local current = root:FindFirstChild(m.Name)
	if current and current ~= m then
		return false
	end
	return not (IS_CLIENT and root:GetAttribute("HVFX_ServerOwned"))
end

------------------------------------------------------------------------------------------------------
-- state
------------------------------------------------------------------------------------------------------
local registered: { [BasePart]: boolean } = {}
-- per-part facts from the first pass; applyLook re-applies the property changes from them (see applyLook)
local looks = {} -- [part] = { glowKey, animated, isSpin, isMember, isCircle, isSwirl }
local anchorScope: Instance? = nil -- AnchorAll only applies inside this (the Model that owns Portal_Frame)
local connections: { RBXScriptConnection } = {}
local spinners = {} -- { part, base, speed, ownAxis, axis?, fixedCenter? }
local groups = {} -- [key] = { kind = "float"|"bat", key, members = { {part, base} }, dirty, ... }
local partsByKey: { [string]: { BasePart } } = {}
local flickers = {} -- { light, brightness, speed, amount, seed }  (Brightness only: Range changes are costly)
local pulsers = {} -- { part, base, mode, seed, nextT, on }
local swirlLights = {} -- { light, brightness }
local swirlEmissive = nil :: SurfaceAppearance?
local candleLights = {} -- { light, pos } one light per candle cluster
local arcs = {} -- { beam, a0, a1, untilT, nextT }
local lightCount = 0

local portal = {
	swirl = nil :: BasePart?,
	frame = nil :: BasePart?,
	center = nil :: Vector3?,
	axis = nil :: Vector3?, -- unit normal, points to the front
}

local now = 0

-- rest pose of a part, shared by every run on this machine (a copy taking over from a destroyed or replaced
-- one would otherwise capture whatever mid-animation CFrame the parts were left in)
local function restCFrame(part: BasePart): CFrame
	local cf = REG.rest[part]
	if cf == nil then
		cf = part.CFrame
		REG.rest[part] = cf
	end
	return cf
end

------------------------------------------------------------------------------------------------------
-- lights
------------------------------------------------------------------------------------------------------
local function addLight(parent: Instance, name: string, color: Color3, range: number, brightness: number,
	flickerSpeed: number, flickerAmount: number, className: string?): Light?
	local light, created = child(parent, className or "PointLight", name)
	if created then
		if lightCount >= CONFIG.MaxLights then
			light:Destroy()
			return nil
		end
		lightCount += 1
	end
	local l = light :: any
	l.Color = color
	l.Range = math.clamp(range, 1, 60)
	l.Brightness = brightness
	l.Shadows = CONFIG.LightShadows
	l.Parent = parent
	if flickerAmount > 0 then
		table.insert(flickers, {
			light = l,
			brightness = brightness,
			speed = flickerSpeed,
			amount = flickerAmount,
			seed = (hashString(parent:GetFullName() .. name) % 1000) / 7.31,
		})
	end
	return l
end

------------------------------------------------------------------------------------------------------
-- part setup
------------------------------------------------------------------------------------------------------
local function setupGlow(part: BasePart, key: string, animated: boolean)
	local color = PALETTE[key] or PALETTE.fire
	if CONFIG.GlowNeon then
		part.Material = Enum.Material.Neon
	end
	part.Color = color
	if part:IsA("MeshPart") then
		pcall(function()
			(part :: MeshPart).TextureID = ""
		end)
	end
	for _, c in part:GetChildren() do
		if c:IsA("SurfaceAppearance") then
			c:Destroy() -- would hide the Neon colour
		end
	end
	part.CastShadow = false
	part.Anchored = true
	local s = part.Size
	local noCollide = animated or math.max(s.X, s.Y, s.Z) < CONFIG.GlowNoCollideBelow
	for _, word in NO_COLLIDE_GLOW do
		if has(part.Name, word) then
			noCollide = true -- (PortalCracks_Dais stays solid: it is flush with the dais top)
			break
		end
	end
	if noCollide then
		part.CanCollide = false
		part.CanTouch = false
		part.CanQuery = false -- decorative: raycasts, mouse and camera ignore it too
	end
end

local function worldSize(part: BasePart): Vector3
	return halfExtents(part.CFrame, part.Size) * 2
end

local function setupFlame(part: BasePart, name: string, key: string?)
	local ws = worldSize(part)
	local w = math.max(math.max(ws.X, ws.Z), 0.08)
	local h = math.max(ws.Y, 0.1)
	local isBrazier = has(name, "Brazier")
	local isCandle = has(name, "Candle")
	local isCauldron = has(name, "Cauldron") -- a ring of flames under the pot: small, low, dense fire
	local upFace = normalIdFor(part, UP)
	if isCauldron then
		w = math.min(w, 0.8)
	end
	local fw = math.min(w * 0.7, CONFIG.FlameMaxSize) -- particle size, capped for wide flame meshes
	local speedMul = if isCauldron then 0.5 else 1

	if CONFIG.FlameParticles then
		local fire = child(part, "ParticleEmitter", "Fire") :: ParticleEmitter
		fire.Texture = TEX_FIRE
		fire.Color = colorSeq({ { 0, PALETTE.flame }, { 0.35, PALETTE.fire }, { 1, PALETTE.ember } })
		fire.LightEmission = 1
		fire.LightInfluence = 0
		fire.Size = seq({ { 0, fw }, { 0.6, fw * 0.72 }, { 1, fw * 0.17 } })
		fire.Transparency = seq({ { 0, 0.3 }, { 0.7, 0.55 }, { 1, 1 } })
		fire.Lifetime = NumberRange.new(0.35, 0.75)
		fire.Speed = NumberRange.new(h * 1.1 * speedMul, h * 2.0 * speedMul)
		fire.Acceleration = UP * (h * speedMul)
		fire.Rotation = NumberRange.new(0, 360)
		fire.RotSpeed = NumberRange.new(-90, 90)
		fire.SpreadAngle = Vector2.new(8, 8)
		fire.Rate = if isCandle then 10 elseif isBrazier then 40 elseif isCauldron then 30 else 24
		fire.EmissionDirection = upFace
		fire.Shape = Enum.ParticleEmitterShape.Box
		fire.ShapeStyle = Enum.ParticleEmitterShapeStyle.Volume
		fire.ZOffset = 0.1
		fire.Parent = part

		if not isCandle then
			local sparks = child(part, "ParticleEmitter", "FireSparks") :: ParticleEmitter
			sparks.Texture = TEX_SPARKLE
			sparks.Color = colorSeq({ { 0, PALETTE.flame }, { 1, PALETTE.ember } })
			sparks.LightEmission = 1
			sparks.LightInfluence = 0
			sparks.Size = seq({ { 0, math.clamp(w * 0.12, 0.08, 0.4) }, { 1, 0 } })
			sparks.Lifetime = NumberRange.new(0.8, 1.6)
			sparks.Speed = NumberRange.new(h * 1.5 * speedMul, h * 3 * speedMul)
			sparks.SpreadAngle = Vector2.new(25, 25)
			sparks.Rate = if isBrazier then 8 else 4
			sparks.EmissionDirection = upFace
			sparks.Parent = part
		end
	end

	if isBrazier and CONFIG.BrazierSmoke then
		local att = attachmentAt(part, "SmokeAtt", part.Position + UP * (h * 0.5))
		local smoke = child(att, "ParticleEmitter", "Smoke") :: ParticleEmitter
		smoke.Texture = TEX_SMOKE
		smoke.Color = colorSeq({ { 0, COL_CHARCOAL }, { 1, COL_BLACK } })
		smoke.LightEmission = 0
		smoke.LightInfluence = 1
		smoke.Size = seq({ { 0, w * 0.35 }, { 1, w * 1.4 } })
		smoke.Transparency = seq({ { 0, 1 }, { 0.15, 0.5 }, { 1, 1 } })
		smoke.Lifetime = NumberRange.new(2.5, 4)
		smoke.Speed = NumberRange.new(2, 3.5)
		smoke.Rotation = NumberRange.new(0, 360)
		smoke.RotSpeed = NumberRange.new(-25, 25)
		smoke.SpreadAngle = Vector2.new(10, 10)
		smoke.Rate = 5
		smoke.EmissionDirection = Enum.NormalId.Top
		smoke.Parent = att
	end

	local wantLight = CONFIG.FlameLights and (not isCandle or CONFIG.CandleLights)
	if wantLight and isCandle then
		-- one light per candle cluster: skip flames next to a candle light that is still alive
		for _, c in candleLights do
			if c.light:IsDescendantOf(workspace) and (c.pos - part.Position).Magnitude < CONFIG.CandleLightSpacing then
				wantLight = false
				break
			end
		end
	end
	if wantLight then
		local color = if key then (PALETTE[key] or PALETTE.fire) else PALETTE.fire
		if isCandle then
			local l = addLight(part, "Light", PALETTE.flame, 6, 1.2, 9, 0.3)
			if l then
				table.insert(candleLights, { light = l, pos = part.Position })
			end
		elseif isBrazier then
			addLight(part, "Light", color, 18, 3, 7, 0.28)
		else
			addLight(part, "Light", color, 12, 2, 8, 0.3)
		end
	end
end

local function setupCauldron(part: BasePart, key: string?)
	local col = if key then (PALETTE[key] or PALETTE.fire) else PALETTE.fire
	local ws = worldSize(part)
	local w = math.max(ws.X, ws.Z)
	local upFace = normalIdFor(part, UP)
	if CONFIG.CauldronBubbles then
		local bubbles = child(part, "ParticleEmitter", "Bubbles") :: ParticleEmitter
		bubbles.Texture = TEX_SMOKE
		bubbles.Color = colorSeq({ { 0, col }, { 1, col:Lerp(PALETTE.white, 0.4) } })
		bubbles.LightEmission = 0.8
		bubbles.LightInfluence = 0
		bubbles.Size = seq({ { 0, 0.15 }, { 0.5, 0.4 }, { 1, 0.05 } })
		bubbles.Transparency = seq({ { 0, 0.2 }, { 0.8, 0.4 }, { 1, 1 } })
		bubbles.Lifetime = NumberRange.new(0.6, 1.3)
		bubbles.Speed = NumberRange.new(0.6, 1.6)
		bubbles.SpreadAngle = Vector2.new(15, 15)
		bubbles.Rate = math.clamp(w * 3, 4, 14)
		bubbles.EmissionDirection = upFace
		bubbles.Parent = part

		local steam = child(part, "ParticleEmitter", "Steam") :: ParticleEmitter
		steam.Texture = TEX_SMOKE
		steam.Color = colorSeq({ { 0, col:Lerp(COL_CHARCOAL, 0.3) }, { 1, COL_CHARCOAL } })
		steam.LightEmission = 0.3
		steam.LightInfluence = 0.5
		steam.Size = seq({ { 0, w * 0.25 }, { 1, w * 0.8 } })
		steam.Transparency = seq({ { 0, 1 }, { 0.2, 0.7 }, { 1, 1 } })
		steam.Lifetime = NumberRange.new(2, 3.5)
		steam.Speed = NumberRange.new(1, 2)
		steam.RotSpeed = NumberRange.new(-20, 20)
		steam.Rotation = NumberRange.new(0, 360)
		steam.Rate = 3
		steam.EmissionDirection = upFace
		steam.Parent = part
	end
	addLight(part, "Light", col, 10, 1.8, 3, 0.2)
end

-- portal axis / centre (needs Portal_Swirl or Portal_Frame)
local function updatePortalFrame()
	local ref = portal.swirl or portal.frame
	if not ref then
		return
	end
	portal.center = ref.Position
	local axis = thinnestAxis(portal.swirl or ref)
	-- front = model pivot LookVector (the importer puts the pivot at the file origin, axes = file axes,
	-- and Blender -Y exports to -Z with the recommended FBX settings)
	local model = ref:FindFirstAncestorWhichIsA("Model")
	if model then
		local look = model:GetPivot().LookVector
		if axis:Dot(look) < 0 then
			axis = -axis
		end
	end
	if CONFIG.FlipFront then
		axis = -axis
	end
	portal.axis = axis.Unit
end

-- the swirl's own properties (re-applied by applyLook after streaming, see there)
local function applySwirlLook(part: BasePart)
	part.CastShadow = false
	part.Anchored = true
	part.CanCollide = CONFIG.SwirlCanCollide
	if CONFIG.SwirlEmissive > 0 then
		-- FBX emission is not imported. If Portal_Swirl got (or was given) a SurfaceAppearance, make it
		-- glow: emissive = colour map x tint x strength x mask. Without a mask, try the colour map as mask.
		local sa = part:FindFirstChildWhichIsA("SurfaceAppearance")
		if sa then
			local ok = pcall(function()
				sa.EmissiveStrength = CONFIG.SwirlEmissive
				sa.EmissiveTint = Color3.new(1, 1, 1)
			end)
			if ok then
				swirlEmissive = sa
				pcall(function()
					local mask = sa.EmissiveMaskContent
					if mask == nil or mask.Uri == nil or mask.Uri == "" then
						local cm = sa.ColorMapContent
						local uri = (cm and cm.Uri) or sa.ColorMap
						if uri and uri ~= "" then
							sa.EmissiveMaskContent = Content.fromUri(uri)
						end
					end
				end)
			end
		end
	end
end

-- the swirl's effects (created once; they are children of the part and come back with it)
local function setupSwirl(part: BasePart)
	updatePortalFrame()
	local axis = portal.axis or thinnestAxis(part)
	local front, back = normalIdFor(part, axis), normalIdFor(part, -axis)
	local ws = worldSize(part)
	local span = math.max(ws.X, ws.Y, ws.Z)

	if CONFIG.SwirlSparks then
		for _, info in { { "SparksFront", front, 18 }, { "SparksBack", back, 7 } } do
			local e = child(part, "ParticleEmitter", info[1]) :: ParticleEmitter
			e.Texture = TEX_SPARKLE
			e.Color = colorSeq({ { 0, PALETTE.flame }, { 0.4, PALETTE.fire }, { 1, PALETTE.ember } })
			e.LightEmission = 1
			e.LightInfluence = 0
			e.Size = seq({ { 0, 0.35 }, { 0.7, 0.25 }, { 1, 0 } })
			e.Transparency = seq({ { 0, 0 }, { 0.8, 0.2 }, { 1, 1 } })
			e.Lifetime = NumberRange.new(1.2, 2.4)
			e.Speed = NumberRange.new(1.5, 4.5)
			e.Drag = 1.2
			e.Acceleration = UP * 1.5
			e.SpreadAngle = Vector2.new(35, 35)
			e.Rotation = NumberRange.new(0, 360)
			e.RotSpeed = NumberRange.new(-120, 120)
			e.Rate = info[3]
			e.EmissionDirection = info[2]
			e.Shape = Enum.ParticleEmitterShape.Box
			e.ShapeStyle = Enum.ParticleEmitterShapeStyle.Volume
			e.Parent = part
		end
	end

	if CONFIG.SwirlWisps then
		local e = child(part, "ParticleEmitter", "Wisps") :: ParticleEmitter
		e.Texture = TEX_SMOKE
		e.Color = colorSeq({ { 0, PALETTE.purple }, { 0.5, COL_VOID }, { 1, COL_BLACK } })
		e.LightEmission = 0.25
		e.LightInfluence = 0.3
		e.Size = seq({ { 0, span * 0.12 }, { 1, span * 0.35 } })
		e.Transparency = seq({ { 0, 1 }, { 0.3, 0.8 }, { 1, 1 } })
		e.Lifetime = NumberRange.new(3, 5)
		e.Speed = NumberRange.new(0.4, 1.2)
		e.Rotation = NumberRange.new(0, 360)
		e.RotSpeed = NumberRange.new(-20, 20)
		e.Acceleration = UP * 0.3
		e.Rate = 3
		e.EmissionDirection = front
		e.Parent = part
	end

	if CONFIG.SwirlLights then
		local c = part.Position
		local a1 = attachmentAt(part, "LightFront", c + axis * 3)
		local a2 = attachmentAt(part, "LightBack", c - axis * 3)
		local l1 = addLight(a1, "Light", PALETTE.fire, CONFIG.SwirlLightRange, CONFIG.SwirlLightBrightness, 0, 0)
		local l2 = addLight(a2, "Light", PALETTE.fire, CONFIG.SwirlLightRange * 0.65,
			CONFIG.SwirlLightBrightness * 0.5, 0, 0)
		if l1 then
			table.insert(swirlLights, { light = l1, brightness = CONFIG.SwirlLightBrightness })
		end
		if l2 then
			table.insert(swirlLights, { light = l2, brightness = CONFIG.SwirlLightBrightness * 0.5 })
		end
	end

	if CONFIG.SwirlSurfaceLight then
		-- a SurfaceLight lights what is in front of the face (dais, ground, players), not the swirl itself
		local sl = addLight(part, "FrontGlow", PALETTE.fire, CONFIG.SwirlSurfaceLightRange,
			CONFIG.SwirlSurfaceLightBrightness, 0, 0, "SurfaceLight")
		if sl then
			local s = sl :: SurfaceLight
			s.Face = front
			s.Angle = math.clamp(CONFIG.SwirlSurfaceLightAngle, 0, 180)
			table.insert(swirlLights, { light = s, brightness = CONFIG.SwirlSurfaceLightBrightness })
		end
	end

	if CONFIG.RimArcs and #arcs == 0 then
		for i = 1, CONFIG.ArcCount do
			local a0 = child(part, "Attachment", "Arc" .. i .. "A") :: Attachment
			local a1 = child(part, "Attachment", "Arc" .. i .. "B") :: Attachment
			a0.Parent = part
			a1.Parent = part
			local beam = child(part, "Beam", "Arc" .. i) :: Beam
			beam.Attachment0 = a0
			beam.Attachment1 = a1
			beam.Color = colorSeq({ { 0, PALETTE.fire }, { 0.5, PALETTE.flame }, { 1, PALETTE.fire } })
			beam.LightEmission = 1
			beam.LightInfluence = 0
			beam.Transparency = seq({ { 0, 0.6 }, { 0.5, 0 }, { 1, 0.6 } })
			beam.Width0 = 0.22
			beam.Width1 = 0.22
			beam.FaceCamera = true
			beam.Segments = 12
			beam.Texture = ""
			beam.Enabled = false
			beam.Parent = part
			table.insert(arcs, { beam = beam, a0 = a0, a1 = a1, untilT = 0, nextT = i * 0.37 })
		end
	end
end

------------------------------------------------------------------------------------------------------
-- groups (float / bat)
------------------------------------------------------------------------------------------------------
local function getGroup(kind: string, key: string)
	local g = groups[key]
	if not g then
		local rng = Random.new(hashString(key))
		g = { kind = kind, key = key, members = {}, memberSet = {}, dirty = true, rng = rng }
		g.phase = rng:NextNumber(0, math.pi * 2)
		g.phase2 = rng:NextNumber(0, math.pi * 2)
		g.speedMul = rng:NextNumber(0.75, 1.3)
		g.ampMul = rng:NextNumber(0.75, 1.25)
		g.spinSign = if rng:NextInteger(0, 1) == 0 then -1 else 1
		groups[key] = g
	elseif kind == "bat" then
		g.kind = "bat" -- bat wins over float
	end
	return g
end

local function addMember(g, part: BasePart)
	if g.memberSet[part] then
		return
	end
	g.memberSet[part] = true
	table.insert(g.members, { part = part, base = restCFrame(part) })
	local look = looks[part]
	if look then
		look.isMember = true -- (also for parts adopted through JoinGroupsByKey) so applyLook keeps it up
	end
	part.Anchored = true
	part.CanCollide = false
	part.CanTouch = false
	g.dirty = true
	if g.kind == "float" then
		for _, o in groups do
			if o.kind == "bat" then
				o.dirty = true -- bat orbits keep clear of floating props
			end
		end
	end
end

-- world AABB of a group at rest (from the captured base CFrames)
local function groupBounds(g): (Vector3?, Vector3?)
	local lo, hi = nil, nil
	for _, m in g.members do
		local e = halfExtents(m.base, m.part.Size)
		local p = m.base.Position
		lo = if lo then lo:Min(p - e) else p - e
		hi = if hi then hi:Max(p + e) else p + e
	end
	return lo, hi
end

local function horizontal(v: Vector3): Vector3
	return Vector3.new(v.X, 0, v.Z)
end

-- smallest orbit radius >= r0 whose ring (+- span) clears the frame and the Float groups at bat height
local function clearOrbitRadius(r0: number, pc: Vector3, span: number, yLo: number, yHi: number): number
	local r = math.max(r0, CONFIG.BatMinRadius)
	if not CONFIG.BatAutoClearance then
		return r
	end
	local margin = 0.5
	local obstacles = {} -- { inner, outer } radial ranges around the portal centre
	if portal.frame then
		local fe = halfExtents(portal.frame.CFrame, portal.frame.Size)
		local fc = portal.frame.Position
		if yLo < fc.Y + fe.Y + margin then
			-- the frame is solid across the centre: the orbit must pass outside its corners
			local reach = horizontal(fc - pc).Magnitude + horizontal(fe).Magnitude
			r = math.max(r, reach + span + margin)
		end
	end
	local amp = CONFIG.FloatAmplitude * 1.25 -- largest bob (ampMul <= 1.25)
	for _, o in groups do
		if o.kind == "float" then
			local lo, hi = groupBounds(o)
			if lo and hi and hi.Y + amp > yLo and lo.Y - amp < yHi then
				local d = horizontal((lo + hi) * 0.5 - pc).Magnitude
				local reach = horizontal((hi - lo) * 0.5).Magnitude -- floats sway/spin about their centre
				table.insert(obstacles, { d - reach, d + reach })
			end
		end
	end
	table.sort(obstacles, function(a, b)
		return a[1] < b[1]
	end)
	for _, ob in obstacles do
		if ob[1] < r + span + margin and ob[2] > r - span - margin then
			r = ob[2] + span + margin -- ring would touch it: move the orbit outside it
		end
	end
	return r
end

local function recomputeGroup(g)
	-- optionally adopt parts with the same base name that lack the keyword
	local sameKey = partsByKey[g.key]
	if CONFIG.JoinGroupsByKey and sameKey then
		for _, p in sameKey do
			if not g.memberSet[p] then
				addMember(g, p)
			end
		end
	end
	local lo, hi = groupBounds(g)
	local main, mainVol = nil, -1
	for _, m in g.members do
		local vol = m.part.Size.X * m.part.Size.Y * m.part.Size.Z
		local stripped = stripGlow(m.part.Name)
		if stripped == g.key then
			vol = math.huge -- the part named exactly like the group is the "body"
		end
		if vol > mainVol then
			main, mainVol = m, vol
		end
	end
	if not lo or not hi or not main then
		return
	end
	g.center = (lo + hi) * 0.5
	g.dirty = false
	g.isCube = has(g.key, "Cube")

	if g.kind == "bat" then
		local pc = portal.center or g.center
		local d = g.center - pc
		g.theta0 = math.atan2(d.Z, d.X)
		g.height = d.Y
		-- clearance: horizontal reach in any heading, vertical travel incl. bob, flutter and bank
		local half = (hi - lo) * 0.5
		local span = horizontal(half).Magnitude
		local travel = CONFIG.BatBobAmplitude * 1.25 + CONFIG.BatFlutter
			+ span * math.sin(math.rad(math.abs(CONFIG.BatBankDegrees)))
		g.radius = clearOrbitRadius(math.max(horizontal(d).Magnitude, 0.001), pc, span,
			lo.Y - travel, hi.Y + travel)
		local dir = CONFIG.BatDirection
		if dir == 0 then
			dir = if g.rng:NextInteger(0, 1) == 0 then -1 else 1
		end
		g.dir = if dir < 0 then -1 else 1
		-- one angular speed for every bat (speedMul only varies bob/flutter): the angular spacing the bats
		-- start with never changes, so a faster bat can never catch up with and fly through a slower one
		g.omega = g.dir * CONFIG.BatSpeed / math.max(CONFIG.BatRefRadius, 1)
		-- constant yaw so that the mesh nose points along the flight direction. The nose is read in the
		-- imported pivot's axes: they carry the FBX node rotation whether Studio keeps it on the part's
		-- CFrame or bakes it into the mesh (identity CFrame, rotation only in PivotOffset).
		local t0 = Vector3.new(-math.sin(g.theta0), 0, math.cos(g.theta0)) * g.dir
		local f0 = (main.base * main.part.PivotOffset):VectorToWorldSpace(CONFIG.BatLocalForward)
		local fh = horizontal(f0)
		-- sanity check with the eyes (they sit at the front): if the pivot disagrees by more than 60
		-- degrees (pivot edited/reset in Studio), steer by the body-to-eyes direction instead
		for _, m in g.members do
			if m ~= main and has(m.part.Name, "Eye") then
				local toEyes = horizontal(m.base.Position - main.base.Position)
				if toEyes.Magnitude > 0.05 and (fh.Magnitude < 1e-3 or toEyes.Unit:Dot(fh.Unit) < 0.5) then
					fh = toEyes
				end
				break
			end
		end
		g.yawFix = if fh.Magnitude > 1e-3 then yawOf(t0) - yawOf(fh) else 0
		if g.startT == nil then
			g.startT = now -- only once: a recompute (streamed-in eyes, late swirl) must not snap the bat back
		end
	end
end

------------------------------------------------------------------------------------------------------
-- classification
------------------------------------------------------------------------------------------------------
-- Everything this script sets on a part's own properties. A part that streams out is parented to nil and
-- comes back as the same instance, and local-only property changes can be lost on the way (Roblox docs,
-- "Instance streaming > Stream out"). register() runs once per part, so this is kept idempotent and runs
-- again on every re-entry: from tryRegister for a single part, from the AncestryChanged hook for the Model.
-- Pulsers keep working: setupGlow puts back the palette colour they lerp from.
local function applyLook(part: BasePart)
	local look = looks[part]
	if not look then
		return
	end
	if CONFIG.AnchorAll and anchorScope and part:IsDescendantOf(anchorScope) then
		part.Anchored = true
	end
	if look.glowKey then
		setupGlow(part, look.glowKey, look.animated) -- Neon, palette colour, no texture/SurfaceAppearance/shadow
	end
	if look.isSwirl then
		applySwirlLook(part) -- no shadow, CanCollide = SwirlCanCollide, emissive SurfaceAppearance
	end
	if look.isSpin or look.isMember or look.isCircle then
		part.Anchored = true -- moved by CFrame every frame
		part.CanCollide = false
		part.CanTouch = false
	end
	if look.isSpin then
		part.CastShadow = false
	end
end

-- AnchorAll only applies inside the Model that owns Portal_Frame (never to the rest of a lobby Folder)
local function setAnchorScope(scope: Instance?)
	if scope == nil or scope == anchorScope then
		return
	end
	anchorScope = scope
	if CONFIG.AnchorAll then
		for p in looks do
			if p:IsDescendantOf(scope) then
				p.Anchored = true
			end
		end
	end
end

local warnedUnanchored = false
local function register(part: BasePart)
	if registered[part] then
		return
	end
	if inCharacter(part) then
		return -- defence in depth: a character or NPC rig inside the root is never anchored, animated or lit
	end
	registered[part] = true
	if IS_CLIENT and not warnedUnanchored and not part.Anchored then
		warnedUnanchored = true
		warn("[HalloweenPortalVFX] " .. part:GetFullName() .. " (maybe more) is not anchored on the server."
			.. " A client script cannot anchor it for the server: re-import with Anchored = On or anchor the"
			.. " Model in Studio, otherwise the portal falls apart.")
	end
	local name = part.Name
	local stripped, glowKey = stripGlow(name)
	local key = groupKey(stripped)

	local isSpin = has(name, "PortalSpin")
	local isBat = string.sub(stripped, 1, 4) == "Bat_"
	local isFloat = (not isBat) and has(name, "Float")
	local isCircle = (not isSpin) and (not isBat) and (not isFloat) and has(name, "MagicCircle")
		and CONFIG.MagicCircleSpin ~= 0
	local animated = isSpin or isBat or isFloat or isCircle
	local isSwirl = has(name, "Portal_Swirl") and not portal.swirl
	local isFrame = (not isSwirl) and has(name, "Portal_Frame") and not portal.frame

	looks[part] = {
		glowKey = glowKey,
		animated = animated,
		isSpin = isSpin,
		isMember = isBat or isFloat,
		isCircle = isCircle,
		isSwirl = isSwirl,
	}
	if isFrame then
		portal.frame = part
		local owner = ownerOf(part)
		if owner and (owner == root or owner:IsDescendantOf(root)) then
			setAnchorScope(owner)
		elseif owner and root:IsDescendantOf(owner) then
			setAnchorScope(root) -- the script sits in a Folder inside the portal Model
		end
		if not portal.swirl then
			updatePortalFrame()
		end
	elseif isSwirl then
		portal.swirl = part
	end

	applyLook(part) -- before the pulsers below capture the (palette) colour

	if isSpin then
		local letter = string.match(name, "PortalSpin_?(%a)") or "A"
		local speed = CONFIG.SpinSpeedA
		if letter == "B" then
			speed = CONFIG.SpinSpeedB
		elseif letter ~= "A" then
			speed = CONFIG.SpinSpeedA * 0.7 * (if string.byte(letter) % 2 == 0 then -1 else 1)
		end
		table.insert(spinners, { part = part, base = restCFrame(part), speed = speed, ownAxis = thinnestAxis(part) })
	else
		if not partsByKey[key] then
			partsByKey[key] = {}
		end
		table.insert(partsByKey[key], part)
		if isBat then
			addMember(getGroup("bat", key), part)
		elseif isFloat then
			addMember(getGroup("float", key), part)
		elseif groups[key] then
			groups[key].dirty = true -- may be adopted by JoinGroupsByKey
		end
	end

	if isCircle then
		-- flat decal on the ground: turns about world up through its own (round) bounding-box centre
		table.insert(spinners, { part = part, base = restCFrame(part), speed = CONFIG.MagicCircleSpin, ownAxis = UP,
			axis = UP, fixedCenter = true })
	end

	if isSwirl then
		setupSwirl(part)
		for _, g in groups do
			g.dirty = true -- bats need the real portal centre
		end
	end

	-- effects: created once, they are children of the part and stream out and back in with it
	if has(name, "Flame") then
		setupFlame(part, name, glowKey)
	end
	if has(name, "JackOLantern") and has(name, "Face") and CONFIG.JackOLanternLights then
		addLight(part, "Light", PALETTE.amber, 10, 1.6, 6, 0.25)
	end
	if has(name, "LanternGlow") and CONFIG.LanternLights then
		addLight(part, "Light", PALETTE.amber, 14, 1.8, 4, 0.15)
	end
	if has(name, "CauldronLiquid") then
		setupCauldron(part, glowKey)
	end
	if has(name, "PortalRim") and CONFIG.RimPulse then
		table.insert(pulsers, { part = part, base = part.Color, mode = "rim", seed = hashString(name) % 100 })
	elseif has(name, "PortalTendrils") and CONFIG.TendrilFlicker then
		table.insert(pulsers, { part = part, base = part.Color, mode = "tendril", seed = hashString(name) % 100,
			nextT = 0, on = true })
	elseif (has(name, "PortalCracks") or has(name, "MagicCircle")) and CONFIG.CrackPulse then
		table.insert(pulsers, { part = part, base = part.Color, mode = "crack", seed = hashString(name) % 100 })
	end
end

local function tryRegister(inst: Instance)
	if inst:IsA("BasePart") then
		-- seen before = it streamed back in: only re-apply what streaming may have dropped
		local ok, err = pcall(if registered[inst] then applyLook else register, inst)
		if not ok then
			warn("[HalloweenPortalVFX] could not set up " .. inst:GetFullName() .. ": " .. tostring(err))
		end
	elseif inst:IsA("SurfaceAppearance") then
		-- arrived after its part (streaming order), or came back after setupGlow removed it: apply again
		local p = inst.Parent
		if p and p:IsA("BasePart") and looks[p] then
			pcall(applyLook, p)
		end
	end
end

-- portal parts first so bats/spinners know the portal centre and normal
local descendants = root:GetDescendants()
table.sort(descendants, function(a, b)
	local pa = has(a.Name, "Portal_Swirl") or has(a.Name, "Portal_Frame")
	local pb = has(b.Name, "Portal_Swirl") or has(b.Name, "Portal_Frame")
	if pa ~= pb then
		return pa
	end
	return a.Name < b.Name
end)
for _, d in descendants do
	tryRegister(d)
end
-- StreamingEnabled: parts streaming in for the first time, parts streaming back in, parts added later
table.insert(connections, root.DescendantAdded:Connect(tryRegister))
-- An Atomic Model (or the last part of a Nonatomic one with ModelStreamingBehavior Improved) streams out
-- and back in as a whole: its parts never leave it, so DescendantAdded stays silent. Re-apply on return.
table.insert(connections, root.AncestryChanged:Connect(function()
	if root:IsDescendantOf(workspace) then
		for p in looks do
			if p:IsDescendantOf(root) then
				pcall(applyLook, p)
			end
		end
	end
end))

------------------------------------------------------------------------------------------------------
-- animation (one Heartbeat connection drives everything)
------------------------------------------------------------------------------------------------------
local moveParts: { BasePart } = {}
local moveCFrames: { CFrame } = {}
local flickerAccum = 0

local function queueMove(part: BasePart, cf: CFrame)
	table.insert(moveParts, part)
	table.insert(moveCFrames, cf)
end

local function flushMoves()
	if #moveParts == 0 then
		return
	end
	local ok = pcall(function()
		workspace:BulkMoveTo(moveParts, moveCFrames, Enum.BulkMoveMode.FireCFrameChanged)
	end)
	if not ok then
		for i, p in moveParts do
			p.CFrame = moveCFrames[i]
		end
	end
	table.clear(moveParts)
	table.clear(moveCFrames)
end

local function spinCenter(s): Vector3
	local mode = CONFIG.SpinCenter
	if mode == "pivot" then
		return (s.base * s.part.PivotOffset).Position
	elseif mode == "swirl" and portal.swirl and portal.axis then
		local c = portal.swirl.Position
		return c + portal.axis * portal.axis:Dot(s.base.Position - c)
	end
	return s.base.Position
end

local function placeArc(arc, t: number)
	local swirl, axis = portal.swirl, portal.axis
	if not swirl or not axis then
		return
	end
	-- in-plane axes of the opening
	local side = UP:Cross(axis)
	if side.Magnitude < 1e-3 then
		side = Vector3.new(1, 0, 0)
	end
	side = side.Unit
	local upIn = axis:Cross(side).Unit
	local e = halfExtents(swirl.CFrame, swirl.Size)
	local a = math.abs(side.X) * e.X + math.abs(side.Y) * e.Y + math.abs(side.Z) * e.Z
	local b = math.abs(upIn.X) * e.X + math.abs(upIn.Y) * e.Y + math.abs(upIn.Z) * e.Z
	local depth = 0.6
	if portal.frame then
		local fe = halfExtents(portal.frame.CFrame, portal.frame.Size)
		depth = math.abs(axis.X) * fe.X + math.abs(axis.Y) * fe.Y + math.abs(axis.Z) * fe.Z + 0.15
	end
	local c = swirl.Position + axis * depth
	local rng = Random.new(math.floor(t * 1000))
	local phi = rng:NextNumber(0, math.pi * 2)
	local dphi = rng:NextNumber(0.25, 0.7)
	local r1, r2 = rng:NextNumber(1.02, 1.12), rng:NextNumber(1.02, 1.12)
	local p0 = c + side * (math.cos(phi) * a * r1) + upIn * (math.sin(phi) * b * r1)
	local p1 = c + side * (math.cos(phi + dphi) * a * r2) + upIn * (math.sin(phi + dphi) * b * r2)
	arc.a0.CFrame = swirl.CFrame:ToObjectSpace(CFrame.new(p0))
	arc.a1.CFrame = swirl.CFrame:ToObjectSpace(CFrame.new(p1))
	arc.beam.CurveSize0 = rng:NextNumber(-1.5, 1.5)
	arc.beam.CurveSize1 = rng:NextNumber(-1.5, 1.5)
	arc.beam.Width0 = rng:NextNumber(0.12, 0.3)
	arc.beam.Width1 = rng:NextNumber(0.05, 0.2)
end

local function retire()
	for _, c in connections do
		c:Disconnect()
	end
	table.clear(connections)
end

table.insert(connections, RunService.Heartbeat:Connect(function(dt: number)
	now += dt
	if not root:IsDescendantOf(workspace) then
		return -- the whole Model has streamed out (Atomic) or was removed: nothing to show
	end

	-- spinning vortex arms
	for _, s in spinners do
		if s.part.Parent then
			local axis = s.axis or portal.axis or s.ownAxis
			local c = if s.fixedCenter then s.base.Position else spinCenter(s)
			local rot = CFrame.fromAxisAngle(axis, s.speed * now)
			queueMove(s.part, CFrame.new(c) * rot * CFrame.new(-c) * s.base)
		end
	end

	-- floaters and bats
	for _, g in groups do
		if g.dirty then
			recomputeGroup(g)
		end
		local c0 = g.center
		if c0 then
			local xf
			if g.kind == "bat" then
				local theta = g.theta0 + g.omega * (now - g.startT)
				local pc = portal.center or c0
				local bob = math.sin(now * CONFIG.BatBobSpeed * g.speedMul + g.phase) * CONFIG.BatBobAmplitude * g.ampMul
					+ math.sin(now * 18 + g.phase2) * CONFIG.BatFlutter
				local pos = pc + Vector3.new(math.cos(theta) * g.radius, g.height + bob, math.sin(theta) * g.radius)
				local tangent = Vector3.new(-math.sin(theta), 0, math.cos(theta)) * g.dir
				local bank = CFrame.fromAxisAngle(tangent, math.rad(CONFIG.BatBankDegrees) * g.dir)
				local yaw = CFrame.Angles(0, -(theta - g.theta0) + g.yawFix, 0)
				xf = CFrame.new(pos) * bank * yaw * CFrame.new(-c0)
			else
				local bob = math.sin(now * CONFIG.FloatSpeed * g.speedMul + g.phase) * CONFIG.FloatAmplitude * g.ampMul
				local yawAngle
				if g.isCube then
					yawAngle = now * CONFIG.FloatCubeSpin * g.speedMul * g.spinSign
				else
					yawAngle = math.rad(CONFIG.FloatSwayDegrees)
						* math.sin(now * CONFIG.FloatSpeed * 0.6 * g.speedMul + g.phase2)
				end
				xf = CFrame.new(c0 + Vector3.new(0, bob, 0)) * CFrame.Angles(0, yawAngle, 0) * CFrame.new(-c0)
			end
			for _, m in g.members do
				if m.part.Parent then
					queueMove(m.part, xf * m.base)
				end
			end
		end
	end
	flushMoves()

	-- lights, colour pulses, arcs (throttled)
	flickerAccum += dt
	if flickerAccum < 1 / math.max(CONFIG.FlickerHz, 1) then
		return
	end
	flickerAccum = 0
	if not stillOwner() then
		retire() -- another copy, a newer run of this one, or a server copy has taken over
		return
	end

	for _, f in flickers do
		if f.light.Parent then
			local n = math.clamp(math.noise(now * f.speed, f.seed, 0.5) * 2, -1, 1)
			local n2 = math.clamp(math.noise(now * f.speed * 2.3, f.seed + 11.7, 0.5) * 2, -1, 1)
			f.light.Brightness = f.brightness * (1 + f.amount * (0.7 * n + 0.3 * n2))
			-- (Range is never animated: changing it invalidates the lighting grid, Brightness is cheap)
		end
	end

	local breathe = 0.85 + 0.15 * math.sin(now * 2.2)
	for _, l in swirlLights do
		if l.light.Parent then
			l.light.Brightness = l.brightness * breathe
		end
	end
	if swirlEmissive and swirlEmissive.Parent then
		swirlEmissive.EmissiveStrength = CONFIG.SwirlEmissive * (0.8 + 0.2 * breathe)
	end

	for _, p in pulsers do
		if p.part.Parent then
			if p.mode == "rim" then
				local k = 0.5 + 0.5 * math.sin(now * 2.4 + p.seed)
				p.part.Color = p.base:Lerp(PALETTE.amber, 0.35 * k)
			elseif p.mode == "crack" then
				local k = 0.5 + 0.5 * math.sin(now * 1.3 + p.seed)
				p.part.Color = p.base:Lerp(PALETTE.ember, 0.5 * k)
			elseif now >= p.nextT then -- tendril crackle
				p.on = not p.on
				local rng = Random.new(math.floor(now * 977) + p.seed)
				if p.on then
					p.part.Transparency = 0
					p.part.Color = if rng:NextNumber() < 0.3 then PALETTE.flame else p.base
					p.nextT = now + rng:NextNumber(0.15, 0.9)
				else
					p.part.Transparency = rng:NextNumber(0.35, 0.8)
					p.nextT = now + rng:NextNumber(0.04, 0.14)
				end
			end
		end
	end

	for _, arc in arcs do
		if arc.beam.Parent then
			if arc.beam.Enabled and now >= arc.untilT then
				arc.beam.Enabled = false
			elseif not arc.beam.Enabled and now >= arc.nextT then
				placeArc(arc, now + arc.nextT)
				local rng = Random.new(math.floor(now * 613))
				arc.beam.Enabled = true
				arc.untilT = now + rng:NextNumber(0.07, 0.18)
				arc.nextT = arc.untilT + rng:NextNumber(0.3, 1.4)
			end
		end
	end
end))
