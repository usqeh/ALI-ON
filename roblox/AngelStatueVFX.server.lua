--!nonstrict
--[[
	AngelStatueVFX (Luau Script): runtime VFX for the hooded stone angel statue FBX (one Model of MeshParts).

	SETUP: import the FBX like the portal (EXPORT_NOTES.md section 2: Anchored = On, Merge Meshes = Off,
	Scale Unit = Stud, Set Pivot to Scene Origin = On).
	  1. Put this Script inside the statue Model and set its RunContext to Client (Rojo: AngelStatueVFX.meta.json
	     does it): every player animates the statue locally, smooth and without network traffic. In a Workspace
	     Folder that holds only statue Models it animates every statue in it. Anywhere else (Workspace,
	     ServerScriptService, a LocalScript in StarterPlayerScripts) it drives the first Model it finds that owns
	     a part named "Statue_Angel" or "Statue_Pedestal", waiting until one streams in; never Workspace itself,
	     never a character or NPC rig (a Model with a Humanoid). A client copy in ReplicatedStorage or
	     ReplicatedFirst (a template) does nothing, and only one copy drives a statue.
	  2. Recommended: Model.ModelStreamingMode = Atomic. Streaming can drop local-only property changes, so the
	     look (Neon, colours, collision, shadows) is re-applied whenever a part or the whole Model comes back.
	  The script anchors the parts too, but a client cannot do that for the server's physics: it warns once if one
	  is loose. The orb's rest pose is kept relative to Statue_Angel, so the statue may be moved or rotated at
	  runtime. As a server Script (RunContext Legacy/Server) it still works, but the orb CFrame and every
	  light/colour change replicate to all clients (stutter, bandwidth): it warns once and lowers FlickerHz.

	WHAT IT DOES (by part name)
	  GLOW_<key>_*        Neon + exact palette colour, TextureID cleared, no shadow, anchored; no collision,
	                      touch or query when smaller than GlowNoCollideBelow studs or animated.
	  *StatueOrb*         hovers a little and turns slowly about world up (all orb parts of a statue move
	                      together); pulsing orange PointLight, breathing colour, two emitters circling in front
	                      of it leave sparkles drifting up, star glints twinkle on its surface.
	  *StatueEyes*        slow smoulder (colour pulse), an occasional blink, faint ember light in the hood.
	  *StatueCracks*      slow colour pulse, trailing the orb pulse of the same statue a little.
	  *Flame*             small fire particles + flickering PointLight (candle flames closer than
	                      CandleLightSpacing share one light).
	  *JackOLantern*Face  warm flickering PointLight.

	Every CONFIG entry can be overridden without editing code by an Attribute with the same name and type on
	this Script or the statue Model (e.g. number "OrbSpinSpeed" = 0). Only built-in rbxasset:// textures are used.
]]

local RunService = game:GetService("RunService")

local CONFIG = {
	Enabled = true,
	AnchorAll = true, -- anchor every BasePart of the Model that owns Statue_Angel / Statue_Pedestal
	GlowNeon = true,
	GlowNoCollideBelow = 6, -- studs: smaller GLOW_ parts lose collision/touch/query

	OrbHoverAmplitude = 0.06, -- studs at the authored orb size, scaled with the orb (modelled gap to the hands >= 0.08)
	OrbHoverSpeed = 1.8, -- radians / second
	OrbSpinSpeed = 0.5, -- radians / second about world up (0 = no spin)
	OrbPulseSpeed = 1.6, -- radians / second: orb light, orb colour and pedestal cracks breathe together
	OrbLight = true,
	OrbLightRange = 14,
	OrbLightBrightness = 2.2,
	OrbColorPulse = true,
	OrbSparkles = true,
	OrbSparkleRate = 6, -- per circling emitter (there are two)
	OrbOrbitSpeed = 1.3, -- radians / second of the circling emitters
	OrbTwinkle = true,

	EyesPulse = true,
	EyesBlink = true,
	EyesLight = true,
	EyesLightRange = 3.5,
	EyesLightBrightness = 0.8,
	CrackPulse = true,

	FlameParticles = true,
	FlameMaxSize = 0.7, -- studs: cap on the fire particle size
	FlameLights = true,
	CandleLightSpacing = 2, -- studs: a candle flame this close to an existing candle light shares it
	JackOLanternLights = true,

	LightShadows = false,
	MaxLights = 12, -- hard cap on the live PointLights this script has created
	FlickerHz = 30, -- light/colour/emitter update rate (the orb itself moves every frame)
	ServerFlickerHz = 10, -- FlickerHz cap when running on the server (everything replicates)
}

local PALETTE = {
	fire = Color3.fromHex("ff6a00"),
	ember = Color3.fromHex("ff3d00"),
	amber = Color3.fromHex("ffa21a"),
	flame = Color3.fromHex("ffd166"),
	white = Color3.fromHex("fff1d6"),
	purple = Color3.fromHex("a24bff"),
}
local COL_SMOULDER = Color3.fromHex("5a1204") -- dim end of the eye pulse
local TEX_FIRE = "rbxasset://textures/particles/fire_main.dds"
local TEX_SPARKLE = "rbxasset://textures/particles/sparkles_main.dds"
local KEY_PARTS = { "Statue_Angel", "Statue_Pedestal" }
local ORB_REF_RADIUS = 0.52 -- studs: the authored orb; hover and sparkle orbit scale with the orb's actual size
local TAG = "ASVFX_" -- prefix of everything this script creates (makes it idempotent)
local UP = Vector3.new(0, 1, 0)
local LOG = "[AngelStatueVFX] "

------------------------------------------------------------------------------------------------------
-- helpers
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

local function glowKeyOf(name: string): string? -- "GLOW_fire_StatueOrb" -> "fire"
	local key = string.match(name, "^GLOW_(%w+)_.+$")
	return if key then string.lower(key) else nil
end

local function halfExtents(cf: CFrame, size: Vector3): Vector3 -- world half extents of an oriented box
	local r, u, l = cf.RightVector, cf.UpVector, cf.LookVector
	local hx, hy, hz = size.X * 0.5, size.Y * 0.5, size.Z * 0.5
	return Vector3.new(
		math.abs(r.X) * hx + math.abs(u.X) * hy + math.abs(l.X) * hz,
		math.abs(r.Y) * hx + math.abs(u.Y) * hy + math.abs(l.Y) * hz,
		math.abs(r.Z) * hx + math.abs(u.Z) * hy + math.abs(l.Z) * hz
	)
end

local function seq(points: { { number } }): NumberSequence
	local kps = {}
	for _, p in points do
		table.insert(kps, NumberSequenceKeypoint.new(p[1], p[2]))
	end
	return NumberSequence.new(kps)
end

local function hotColors(): ColorSequence
	return ColorSequence.new({
		ColorSequenceKeypoint.new(0, PALETTE.flame),
		ColorSequenceKeypoint.new(0.4, PALETTE.fire),
		ColorSequenceKeypoint.new(1, PALETTE.ember),
	})
end

-- find-or-create a tagged child, set its properties, parent it: re-runs never duplicate effects
local function make(parent: Instance, className: string, name: string, props: { [string]: any }): (Instance, boolean)
	local found = parent:FindFirstChild(TAG .. name)
	local created = not (found and found.ClassName == className)
	local inst: any = if created then Instance.new(className) else found
	inst.Name = TAG .. name
	for k, v in props do
		inst[k] = v
	end
	inst.Parent = parent
	return inst, created
end

local function inCharacter(x: Instance): boolean -- inside a character or NPC rig (a Model with a Humanoid)
	local p: Instance? = x
	while p and p ~= workspace and p ~= game do
		if p:IsA("Model") and p:FindFirstChildWhichIsA("Humanoid") then
			return true
		end
		p = p.Parent
	end
	return false
end

-- nearest Model that owns x, else its Folder; never Workspace itself (Workspace is a Model too)
local function ownerOf(x: Instance): Instance?
	local m = x:FindFirstAncestorWhichIsA("Model")
	if m and m ~= workspace then
		return m
	end
	local p = x.Parent
	return if p and p ~= workspace and p:IsDescendantOf(workspace) then p else nil
end

local function isKeyPart(x: Instance): boolean
	if x:IsA("BasePart") then
		for _, k in KEY_PARTS do
			if has(x.Name, k) then
				return not inCharacter(x)
			end
		end
	end
	return false
end

------------------------------------------------------------------------------------------------------
-- root lookup, attribute overrides, one driver per statue and machine
------------------------------------------------------------------------------------------------------
local function findRoot(): Instance?
	local parent = script.Parent
	if parent and parent:IsA("BasePart") then
		parent = ownerOf(parent) -- dropped onto a part in Explorer: use the Model around it
	end
	if parent and parent ~= workspace and parent:IsDescendantOf(workspace) and not inCharacter(parent) then
		return parent
	end
	-- elsewhere: the owner of the first key part found (false = no owner). Listen first, then scan, so a part
	-- streaming in between is not missed.
	local found: Instance | false | nil = nil
	local conn = workspace.DescendantAdded:Connect(function(x)
		if found == nil and isKeyPart(x) then
			found = ownerOf(x) or false
		end
	end)
	for _, d in workspace:GetDescendants() do
		if found ~= nil then
			break
		end
		if isKeyPart(d) then
			found = ownerOf(d) or false
		end
	end
	local waited = 0
	while found == nil do
		local before = waited
		waited += task.wait(0.5)
		if before < 30 and waited >= 30 then
			warn(LOG .. "still waiting for a part named Statue_Angel or Statue_Pedestal in Workspace. Put this"
				.. " Script inside the statue Model to skip the search.")
		end
	end
	conn:Disconnect()
	if found == false then
		warn(LOG .. "the statue parts sit directly in Workspace: group them into a Model.")
		return nil
	end
	return found
end

local function applyAttributeOverrides(from: Instance?)
	if from then
		for key, default in CONFIG do
			local value = from:GetAttribute(key)
			if value ~= nil and typeof(value) == typeof(default) then
				CONFIG[key] = value
			end
		end
	end
end

local IS_CLIENT = RunService:IsClient()
if IS_CLIENT and not script:IsDescendantOf(workspace) then
	-- RunContext=Client scripts also run in ReplicatedStorage etc. A template there must not animate the live
	-- statue a second time; a copy under the local Player (PlayerScripts, PlayerGui) searches Workspace instead.
	local player = game:GetService("Players").LocalPlayer
	if not (player and script:IsDescendantOf(player)) then
		if not (script:IsDescendantOf(game:GetService("ReplicatedStorage"))
			or script:IsDescendantOf(game:GetService("ReplicatedFirst"))) then
			warn(LOG .. script:GetFullName() .. " does nothing here: put this Script inside the statue Model in"
				.. " Workspace with RunContext=Client.")
		end
		return
	end
end
local root = findRoot()
if not root or root == workspace then
	return
end
applyAttributeOverrides(root)
applyAttributeOverrides(script) -- attributes on the script win
if not CONFIG.Enabled then
	return
end

-- registry shared by every copy/run on this machine: run ids and orb rest poses (a copy that takes over must
-- not capture a mid-hover orb CFrame as its rest pose)
local SHARED: any = shared
local REG = SHARED.AngelStatueVFX
if type(REG) ~= "table" then
	REG = { runs = 0 }
	SHARED.AngelStatueVFX = REG
end
REG.runs = (tonumber(REG.runs) or 0) + 1
REG.orbRest = REG.orbRest or setmetatable({}, { __mode = "k" })
local RUN_ID = REG.runs

-- One driver per statue and machine. A marker (ObjectValue) in the root and in every statue Model this copy
-- drives remembers the Script and its run: a destroyed copy does not block its replacement, a copy that is
-- replaced while running retires (stillOwner), and a Folder copy and a copy inside one of its statues never
-- both animate that statue.
local markerName = TAG .. (if IS_CLIENT then "ClientOwner" else "ServerOwner")
local claimed: { [Instance]: ObjectValue } = {}

local function claim(container: Instance): boolean
	if claimed[container] then
		return true
	end
	if IS_CLIENT and container:GetAttribute("ASVFX_ServerOwned") then
		return false -- a server copy drives it
	end
	local found = container:FindFirstChild(markerName)
	local marker: ObjectValue
	if found and found:IsA("ObjectValue") then
		local owner = found.Value
		if owner and owner ~= script and owner.Parent ~= nil and owner:IsDescendantOf(game) then
			return false -- another live copy drives it
		end
		marker = found
	else
		marker = Instance.new("ObjectValue")
		marker.Name = markerName
	end
	marker.Value = script
	marker:SetAttribute("ASVFX_Run", RUN_ID)
	marker.Parent = container
	if not IS_CLIENT then
		container:SetAttribute("ASVFX_ServerOwned", true)
	end
	claimed[container] = marker
	return true
end

if not claim(root) then
	return
end
if not IS_CLIENT then
	warn(LOG .. "running on the server: the orb CFrame and every light/colour change replicate to all clients."
		.. " Set this Script's RunContext to Client for smooth local animation.")
	CONFIG.FlickerHz = math.min(CONFIG.FlickerHz, CONFIG.ServerFlickerHz)
end

------------------------------------------------------------------------------------------------------
-- part setup
------------------------------------------------------------------------------------------------------
local registered: { [BasePart]: boolean } = {}
local looks = {} -- [part] = { glowKey, animated }: what applyLook re-applies after streaming
local anchorScopes: { [Instance]: boolean } = {} -- Models owning Statue_Angel / Statue_Pedestal
local connections: { RBXScriptConnection } = {}
local orbGroups = {} -- [owner] = { members, anchor, main, center, radius, current, phase, lights, colors, orbit }
local pulsers = {} -- { part, base, mode = "eyes" | "crack", owner, seed, light, nextBlink, blinkUntil }
local flickers = {} -- { light, brightness, speed, amount, seed }  (Brightness only: Range is never animated)
local candleLights: { { light: PointLight, pos: Vector3 } } = {}
local ownLights: { PointLight } = {}
local now = 0

-- An orb part's rest pose, kept as an offset in Statue_Angel's frame (the script never moves Statue_Angel) so
-- the hover follows the statue when it is moved or rotated at runtime; a world CFrame until the anchor is known.
type Rest = { anchor: BasePart?, offset: CFrame, size: Vector3? }
local function restOf(part: BasePart): Rest
	local r: Rest? = REG.orbRest[part]
	if r then
		return r
	end
	local fresh: Rest = { anchor = nil, offset = part.CFrame }
	REG.orbRest[part] = fresh
	return fresh
end

local function restWorld(r: Rest): CFrame
	local a, s0, o = r.anchor, r.size, r.offset
	if not (a and s0) then
		return o
	end
	local s, p = a.Size, o.Position -- a Model:ScaleTo at runtime scales the offset with Statue_Angel
	return a.CFrame * CFrame.new(p.X * s.X / s0.X - p.X, p.Y * s.Y / s0.Y - p.Y, p.Z * s.Z / s0.Z - p.Z) * o
end

local function rebase(r: Rest, anchor: BasePart)
	if r.anchor ~= anchor then
		local world = restWorld(r)
		r.anchor, r.offset, r.size = anchor, anchor.CFrame:ToObjectSpace(world), anchor.Size
	end
end

local function findAnchor(owner: Instance): BasePart? -- Statue_Angel of the same statue
	for _, d in owner:GetDescendants() do
		if d:IsA("BasePart") and has(d.Name, "Statue_Angel") and ownerOf(d) == owner and not inCharacter(d) then
			return d
		end
	end
	return nil
end

local function setAnchor(g, anchor: BasePart)
	g.anchor = anchor
	for _, m in g.members do
		rebase(m.rest, anchor)
	end
end

local function prune(list: { any }, dead: (any) -> boolean)
	for i = #list, 1, -1 do
		if dead(list[i]) then
			table.remove(list, i)
		end
	end
end

local function addLight(parent: Instance, name: string, color: Color3, range: number, brightness: number,
	flickerSpeed: number, flickerAmount: number): PointLight?
	local existing = parent:FindFirstChild(TAG .. name)
	if not (existing and existing:IsA("PointLight")) then
		prune(ownLights, function(l) -- lights destroyed with their statue free their slot
			return l.Parent == nil
		end)
		if #ownLights >= CONFIG.MaxLights then
			return nil
		end
	end
	local inst, created = make(parent, "PointLight", name, { Color = color, Range = math.clamp(range, 1, 60),
		Brightness = brightness, Shadows = CONFIG.LightShadows })
	local light = inst :: PointLight
	if created then
		table.insert(ownLights, light)
	end
	if flickerAmount > 0 then
		table.insert(flickers, { light = light, brightness = brightness, speed = flickerSpeed, amount = flickerAmount,
			seed = (hashString(parent:GetFullName() .. name) % 1000) / 7.31 })
	end
	return light
end

local function inAnchorScope(part: BasePart): boolean
	local p: Instance? = part.Parent
	while p and p ~= workspace do
		if anchorScopes[p] then
			return true
		end
		p = p.Parent
	end
	return false
end

-- everything set on a part's own properties; runs again whenever the part or the whole Model streams back in
local function applyLook(part: BasePart)
	local look = looks[part]
	if not look then
		return
	end
	if CONFIG.AnchorAll and inAnchorScope(part) then
		part.Anchored = true
	end
	if look.glowKey then
		if CONFIG.GlowNeon then
			part.Material = Enum.Material.Neon
		end
		part.Color = PALETTE[look.glowKey] or PALETTE.fire
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
	end
	local s = part.Size
	if look.animated or (look.glowKey and math.max(s.X, s.Y, s.Z) < CONFIG.GlowNoCollideBelow) then
		part.Anchored = true
		part.CanCollide = false
		part.CanTouch = false
		part.CanQuery = false
	end
end

local function setupOrbEffects(g, part: BasePart)
	local col = PALETTE[glowKeyOf(part.Name) or "fire"] or PALETTE.fire
	if CONFIG.OrbLight then
		local l = addLight(part, "OrbLight", col, CONFIG.OrbLightRange, CONFIG.OrbLightBrightness, 0, 0)
		if l then
			table.insert(g.lights, { light = l, brightness = CONFIG.OrbLightBrightness })
		end
	end
	if CONFIG.OrbSparkles then
		g.orbit = {}
		for i = 1, 2 do -- moved around the orb in the Heartbeat: sparkles left behind spiral up
			local att = make(part, "Attachment", "Orbit" .. i, {}) :: Attachment
			make(att, "ParticleEmitter", "Sparkles", {
				Texture = TEX_SPARKLE, Color = hotColors(), LightEmission = 1, LightInfluence = 0,
				Size = seq({ { 0, 0.28 }, { 0.6, 0.18 }, { 1, 0 } }), Transparency = seq({ { 0, 0 }, { 0.7, 0.25 }, { 1, 1 } }),
				Lifetime = NumberRange.new(1.2, 2.2), Speed = NumberRange.new(0.2, 0.6), SpreadAngle = Vector2.new(180, 180),
				Acceleration = UP * 0.9, Drag = 0.6, Rotation = NumberRange.new(0, 360), RotSpeed = NumberRange.new(-120, 120),
				Rate = CONFIG.OrbSparkleRate, EmissionDirection = Enum.NormalId.Top,
			})
			table.insert(g.orbit, att)
		end
	end
	if CONFIG.OrbTwinkle then
		local r = math.max(part.Size.X, part.Size.Y, part.Size.Z) * 0.5
		make(part, "ParticleEmitter", "Twinkle", {
			Texture = TEX_SPARKLE, Color = ColorSequence.new(PALETTE.white, PALETTE.flame), LightEmission = 1,
			LightInfluence = 0, Size = seq({ { 0, 0 }, { 0.5, math.clamp(r * 0.9, 0.2, 0.8) }, { 1, 0 } }),
			Transparency = seq({ { 0, 0.1 }, { 1, 0.4 } }), Lifetime = NumberRange.new(0.5, 0.9),
			Speed = NumberRange.new(0, 0), Rotation = NumberRange.new(0, 360), RotSpeed = NumberRange.new(60, 140),
			Rate = 2.5, LockedToPart = true, Shape = Enum.ParticleEmitterShape.Sphere,
			ShapeStyle = Enum.ParticleEmitterShapeStyle.Surface, ZOffset = 0.2,
		})
	end
end

local function setupFlame(part: BasePart, name: string, key: string?)
	local e = halfExtents(part.CFrame, part.Size)
	local w, h = math.max(math.max(e.X, e.Z) * 2, 0.08), math.max(e.Y * 2, 0.1)
	local isCandle = has(name, "Candle")
	if CONFIG.FlameParticles then
		-- world-aligned attachment at the flame centre: "Top" is world up even if the mesh is rotated
		local att = make(part, "Attachment", "FireAtt", { CFrame = part.CFrame:ToObjectSpace(CFrame.new(part.Position)) })
		local fw = math.min(w * 0.7, CONFIG.FlameMaxSize)
		make(att, "ParticleEmitter", "Fire", {
			Texture = TEX_FIRE, Color = hotColors(), LightEmission = 1, LightInfluence = 0,
			Size = seq({ { 0, fw }, { 0.6, fw * 0.72 }, { 1, fw * 0.17 } }),
			Transparency = seq({ { 0, 0.3 }, { 0.7, 0.55 }, { 1, 1 } }),
			Lifetime = NumberRange.new(0.35, 0.7), Speed = NumberRange.new(h * 1.1, h * 2), Acceleration = UP * h,
			Rotation = NumberRange.new(0, 360), RotSpeed = NumberRange.new(-90, 90), SpreadAngle = Vector2.new(8, 8),
			Rate = if isCandle then 10 else 20, EmissionDirection = Enum.NormalId.Top,
		})
	end
	if not CONFIG.FlameLights then
		return
	end
	if isCandle then
		for _, c in candleLights do -- one light per candle cluster
			if c.light:IsDescendantOf(workspace) and (c.pos - part.Position).Magnitude < CONFIG.CandleLightSpacing then
				return
			end
		end
		local l = addLight(part, "Light", PALETTE.flame, 7, 1.2, 9, 0.3)
		if l then
			table.insert(candleLights, { light = l, pos = part.Position })
		end
	else
		addLight(part, "Light", PALETTE[key or "fire"] or PALETTE.fire, 10, 1.8, 8, 0.3)
	end
end

local warnedUnanchored = false
local function register(part: BasePart)
	if not has(part.Name, "Statue") then
		return -- only statue parts: a shared Folder may also hold the portal's Flame/JackOLantern parts
	end
	local owner = ownerOf(part) or root
	if registered[part] or inCharacter(part) or not claim(owner) then
		return -- NPC rigs are never touched; a statue another copy drives is left to that copy
	end
	registered[part] = true
	if IS_CLIENT and not warnedUnanchored and not part.Anchored then
		warnedUnanchored = true
		warn(LOG .. part:GetFullName() .. " (maybe more) is not anchored on the server. A client script cannot"
			.. " anchor it for the server: re-import with Anchored = On or anchor the Model in Studio.")
	end
	local name = part.Name
	local glowKey = glowKeyOf(name)
	local isOrb = has(name, "StatueOrb")
	looks[part] = { glowKey = glowKey, animated = isOrb }

	if isKeyPart(part) and not anchorScopes[owner] then
		-- AnchorAll scope: the statue Model (or root, when the script sits in a Folder inside that Model)
		local scope = if owner == root or owner:IsDescendantOf(root) then owner else root
		anchorScopes[scope] = true
		for p in looks do
			if p:IsDescendantOf(scope) then
				applyLook(p)
			end
		end
	end
	applyLook(part) -- before the pulsers below capture the (palette) colour

	if isOrb then
		local g = orbGroups[owner]
		if not g then
			local pos = part.Position -- position-based phase: cloned statues do not bob in sync
			g = { members = {}, memberSet = {}, lights = {}, colors = {}, anchor = findAnchor(owner),
				phase = (hashString(string.format("%d_%d", math.floor(pos.X), math.floor(pos.Z))) % 628) / 100 }
			orbGroups[owner] = g
		end
		if not g.memberSet[part] then
			local rest = restOf(part)
			if g.anchor then
				rebase(rest, g.anchor)
			end
			g.memberSet[part] = true
			table.insert(g.members, { part = part, rest = rest })
		end
		if not g.main then
			g.main = part
			setupOrbEffects(g, part)
		end
		if glowKey and CONFIG.OrbColorPulse then
			table.insert(g.colors, { part = part, base = part.Color })
		end
	elseif has(name, "Statue_Angel") then
		local g = orbGroups[owner] -- Statue_Angel streamed in after the orb
		if g and not g.anchor then
			setAnchor(g, part)
		end
	end
	if has(name, "StatueEyes") then
		local light = if CONFIG.EyesLight
			then addLight(part, "EyesLight", PALETTE[glowKey or "ember"] or PALETTE.ember, CONFIG.EyesLightRange,
				CONFIG.EyesLightBrightness, 0, 0)
			else nil
		table.insert(pulsers, { part = part, base = part.Color, mode = "eyes", owner = owner, light = light,
			seed = hashString(name) % 100, nextBlink = 2 + (hashString(name) % 30) / 10, blinkUntil = 0 })
	elseif has(name, "StatueCracks") and CONFIG.CrackPulse then
		table.insert(pulsers, { part = part, base = part.Color, mode = "crack", owner = owner, light = nil,
			seed = (hashString(name) % 100) / 10, nextBlink = 0, blinkUntil = 0 })
	end
	if has(name, "Flame") then
		setupFlame(part, name, glowKey)
	end
	if has(name, "JackOLantern") and has(name, "Face") and CONFIG.JackOLanternLights then
		addLight(part, "Light", PALETTE.amber, 10, 1.6, 6, 0.25)
	end
end

local function tryRegister(inst: Instance)
	if inst:IsA("BasePart") then
		-- seen before = it streamed back in: only re-apply what streaming may have dropped
		local ok, err = pcall(if registered[inst] then applyLook else register, inst)
		if not ok then
			warn(LOG .. "could not set up " .. inst:GetFullName() .. ": " .. tostring(err))
		end
	elseif inst:IsA("SurfaceAppearance") then
		local p = inst.Parent -- arrived after its part (streaming order): strip it from a glow part again
		if p and p:IsA("BasePart") and looks[p] then
			pcall(applyLook, p)
		end
	end
end

local descendants = root:GetDescendants()
table.sort(descendants, function(a, b)
	return a.Name < b.Name -- deterministic: a re-run picks the same orb main part and candle lights
end)
for _, d in descendants do
	tryRegister(d)
end
table.insert(connections, root.DescendantAdded:Connect(tryRegister)) -- streamed in late / streamed back in
-- an Atomic Model streams out and back in as a whole (DescendantAdded stays silent): re-apply on return
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
local function forget(container: Instance) -- a destroyed statue: drop its animation state
	orbGroups[container] = nil
	local function gone(e: any): boolean
		return e.owner == container or (e.light ~= nil and e.light.Parent == nil)
	end
	prune(pulsers, gone)
	prune(flickers, gone)
	prune(candleLights, gone)
end

local function stillOwner(): boolean -- only positive evidence of a takeover counts
	for container, marker in claimed do
		if marker.Parent == nil then
			-- destroyed together with its statue (an Atomic stream-out keeps it inside the Model): not a takeover
			claimed[container] = nil
			if container.Parent == nil then
				forget(container)
			end
			continue
		end
		if (marker.Value ~= nil and marker.Value ~= script) or container:FindFirstChild(markerName) ~= marker then
			return false
		end
		local run = marker:GetAttribute("ASVFX_Run")
		if (run ~= nil and run ~= RUN_ID) or (IS_CLIENT and container:GetAttribute("ASVFX_ServerOwned")) then
			return false
		end
	end
	return true
end

-- rest centre and radius of an orb group from its members' rest poses (rebuilt every frame: follows the statue)
local function orbRest(g)
	local lo: Vector3?, hi: Vector3? = nil, nil
	for _, m in g.members do
		m.base = restWorld(m.rest)
		local e, p = halfExtents(m.base, m.part.Size), m.base.Position
		lo = if lo then lo:Min(p - e) else p - e
		hi = if hi then hi:Max(p + e) else p + e
	end
	if lo and hi then
		local c = (lo + hi) * 0.5
		g.center, g.radius = c, 0
		for _, m in g.members do -- rotation-independent for a round orb
			local s = m.part.Size
			g.radius = math.max(g.radius, (m.base.Position - c).Magnitude + math.max(s.X, s.Y, s.Z) * 0.5)
		end
	end
end

local function breath(phase: number): number
	return 0.5 + 0.5 * math.sin(now * CONFIG.OrbPulseSpeed + phase)
end

local moveParts: { BasePart } = {}
local moveCFrames: { CFrame } = {}
local flickerAccum = 0

table.insert(connections, RunService.Heartbeat:Connect(function(dt: number)
	now += dt
	if not root:IsDescendantOf(workspace) then
		return -- the whole Model has streamed out (Atomic) or was removed
	end

	-- orb: hover + slow turn about world up through its rest centre, every frame
	for _, g in orbGroups do
		orbRest(g)
		local c = g.center
		if c then
			local amp = CONFIG.OrbHoverAmplitude * g.radius / ORB_REF_RADIUS -- keeps the gap to the hands when scaled
			g.current = c + UP * (math.sin(now * CONFIG.OrbHoverSpeed + g.phase) * amp)
			local xf = CFrame.new(g.current) * CFrame.Angles(0, now * CONFIG.OrbSpinSpeed, 0) * CFrame.new(-c)
			for _, m in g.members do
				if m.part:IsDescendantOf(workspace) then -- not while its statue has streamed out
					table.insert(moveParts, m.part)
					table.insert(moveCFrames, xf * m.base)
				end
			end
		end
	end
	if #moveParts > 0 then
		if not pcall(workspace.BulkMoveTo, workspace, moveParts, moveCFrames, Enum.BulkMoveMode.FireCFrameChanged) then
			for i, p in moveParts do
				p.CFrame = moveCFrames[i]
			end
		end
		table.clear(moveParts)
		table.clear(moveCFrames)
	end

	-- lights, colours, emitters (throttled; the remainder carries over so FlickerHz is kept on average)
	local interval = 1 / math.max(CONFIG.FlickerHz, 1)
	flickerAccum += dt
	if flickerAccum < interval then
		return
	end
	flickerAccum = math.min(flickerAccum - interval, interval)
	if not stillOwner() then
		for _, conn in connections do
			conn:Disconnect() -- another copy, a newer run of this one, or a server copy has taken over
		end
		table.clear(connections)
		return
	end

	for _, f in flickers do
		if f.light.Parent then
			local n = math.clamp(math.noise(now * f.speed, f.seed, 0.5) * 2, -1, 1)
			local n2 = math.clamp(math.noise(now * f.speed * 2.3, f.seed + 11.7, 0.5) * 2, -1, 1)
			f.light.Brightness = f.brightness * (1 + f.amount * (0.7 * n + 0.3 * n2))
		end
	end

	for _, g in orbGroups do
		local k = breath(g.phase)
		local shimmer = math.clamp(math.noise(now * 5, g.phase, 0.5) * 2, -1, 1) * 0.06
		for _, l in g.lights do
			if l.light.Parent then
				l.light.Brightness = l.brightness * (0.7 + 0.3 * k + shimmer)
			end
		end
		for _, ci in g.colors do
			if ci.part.Parent then
				ci.part.Color = ci.base:Lerp(PALETTE.flame, 0.3 * k)
			end
		end
		local main = g.main
		if g.orbit and g.current and main and main.Parent then
			-- circle the orb, pushed forward (away from Statue_Angel's centre) so its back arc stays out of the
			-- chest and the hands; measured on the FBX: 0.1% of the path inside stone (14% when centred)
			local s = g.radius / ORB_REF_RADIUS
			local centre, r = g.current, g.radius + 0.15 * s
			local d = if g.anchor then g.center - g.anchor.Position else nil
			local fwd = if d then Vector3.new(d.X, 0, d.Z) else nil
			if fwd and fwd.Magnitude > 0.1 then
				centre += fwd.Unit * (0.15 * s)
				r += 0.1 * s
			end
			local a = now * CONFIG.OrbOrbitSpeed + g.phase
			for i, att in g.orbit do
				local ang = a + (i - 1) * math.pi
				local lift = r * (0.15 + 0.2 * math.sin(ang * 1.7))
				local wp = centre + Vector3.new(math.cos(ang) * r, lift, math.sin(ang) * r)
				att.Position = main.CFrame:PointToObjectSpace(wp)
			end
		end
	end

	for _, p in pulsers do
		local part = p.part
		if part.Parent then
			if p.mode == "crack" then
				local g = orbGroups[p.owner]
				local k = breath(if g then g.phase - 0.6 else p.seed) -- trails the orb pulse a little
				part.Color = p.base:Lerp(PALETTE.ember, 0.5 * (1 - k))
			else -- eyes: smoulder + an occasional blink (both eyes are one part)
				if CONFIG.EyesPulse then
					part.Color = p.base:Lerp(COL_SMOULDER, 0.45 * (0.5 - 0.5 * math.sin(now * 0.9 + p.seed)))
				end
				if CONFIG.EyesBlink and now >= p.nextBlink then
					p.blinkUntil = now + 0.14
					p.nextBlink = now + Random.new(math.floor(now * 1000) + p.seed):NextNumber(3.5, 8)
				end
				local shut = CONFIG.EyesBlink and now < p.blinkUntil
				local transparency = if shut then 1 else 0
				if part.Transparency ~= transparency then
					part.Transparency = transparency
				end
				local light = p.light
				if light and light.Parent then
					light.Brightness = if shut then 0 else CONFIG.EyesLightBrightness * (0.75 + 0.25 * math.sin(now * 0.9 + p.seed))
				end
			end
		end
	end
end))
