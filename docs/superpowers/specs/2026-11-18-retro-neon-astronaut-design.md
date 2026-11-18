# Retro neon restyle with the astronaut hero

Approved through three interactive mockups on 2026-11-18. Look: 80s synthwave night city (deep purple base,
hot pink / cyan / yellow neon, sunset sky with a banded sun, palm trees), played top-down by default, with the
VYBS-style astronaut as the hero. Original look in that spirit; no characters, logos or maps copied from any game.

## What changes

| Piece | Today | After |
| --- | --- | --- |
| Hero (`build/assets/boxman.glb`) | grey boxman | astronaut: helmet with indigo visor + white and yellow sparkles, lavender rim, chest panel, belt, backpack, puffy pale-cyan suit, shaded boot soles |
| Car (`car.glb`) | sedan | pink wedge sports car: pop-up lights, rear wing, cyan side stripe, violet underglow |
| Helicopter (`heli.glb`) | grey heli | white body with pink and cyan stripes, dark glass, black rotors, cyan skids |
| Plane (`airplane.glb`) | grey plane | white body with sunset stripes, neon wing tips, cyan fin, black prop |
| World (`world.glb`) | grey concrete | same layout, repainted for night: dark purple asphalt, neon kerbs, purple/magenta/teal walls, indigo roofs, glowing roof outlines, palm trees, teal water |
| Sky, light, ocean | daylight | synthwave gradient sky with banded sun, purple fog, pink key light, neon-tinted ocean |
| Camera | third person | top-down by default; **T** switches to third person and back |
| Retro filter | none | chunky pixels + scanlines; **P** toggles it; also in Settings |

## Hard constraints

- The hero keeps the boxman skeleton and all 34 animation clips by name. New body parts are rigid meshes parented
  to bones (no skinning), so every move keeps working.
- Vehicles keep every node that carries glTF extras (`seat`, `entrance_*`, `door_*`, `wheel`, `steering_wheel`,
  `camera`, `collision`, `rotor`, `aileron`, `elevator`, `rudder`) with unchanged names, transforms and extras.
  Only visible mesh data is replaced. New visuals stay within the old collision volumes.
- The world keeps all physics, spawn, path and scenario nodes untouched, and the `ocean` material name.

## Asset conventions (`tools/retro/common.py`)

- One 512x512 atlas (`tools/retro/atlas.png`): 32 palette swatches on top, visor art at the bottom.
- Material names drive the look in Sketchbook's `setupMeshProperties`:
  - `retro` lit (Phong), `neon` unlit (MeshBasic, no fog), `glow` additive halo (MeshBasic, additive,
    no depth write), `visor` lit with alpha cutout.
- Parts are painted by pointing all their UVs at one swatch.
- Each asset has its own Blender script in `tools/retro/` that reads the **original** asset from git
  (`git show <base>:build/assets/<name>.glb`), so any asset can be regenerated or rolled back.

## Code changes

- `FunctionLibrary.setupMeshProperties`: handle `neon` / `glow` / `visor` materials, keep `alphaTest`.
- `CameraOperator`: top-down mode (fixed yaw, ~65° pitch, higher radius, mouse ignored); global **T** toggle.
- `World`: no tone mapping + sRGB output so palette colours show as authored; purple fog; retro filter
  (low pixel ratio, nearest upscale, scanline overlay, FXAA off) with **P** and a Settings checkbox.
- `Sky`: synthwave sky shader, purple hemisphere light, pink sun light, low sunset sun.
- `WaterShader`: teal/indigo sea with pink highlights.

## Out of scope

Pedestrians, a new city layout, visor expressions, menu restyling.

## Testing

Local server: walk, jump, sprint, enter/drive/exit the car, fly the heli and plane, run every scenario, toggle T
and P, no console errors; screenshots of each. Then commit and push; GitHub Pages redeploys.
