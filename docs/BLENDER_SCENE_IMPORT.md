# Blender scene import

PummelMCP can import Blender-authored mesh scenes as Mod-local Pummel Party
assets. The pipeline uses the formats supported by the official editor:

1. Blender exports one triangulated OBJ, MTL and one PNG per material slot.
2. `plan_blender_scene_import` validates every source, target and transform.
3. `build_blender_scene_import` creates official-style `.pmeta` sidecars for the
   Prop, Mesh and Texture assets, creates a `.pmat` whose `_MainTex` points at
   the imported texture, and writes those material references into `ModProp`.
4. The Scene is assembled in a staging file, parsed to exact EOF, structurally
   validated, and published only after all imported asset files are committed.

## Blender export

Run the exporter inside Blender:

```powershell
blender casino.blend --background `
  --python "D:\Pummel Party MCP\tools\blender_export_pummel.py" -- `
  --output "D:\PummelImports\casino" --collection casino
```

Add `--selected-only` to export only selected mesh objects. Modifiers, world
rotation and world scale are baked into each OBJ. World translation is retained
in `pummel_scene.json`. Blender coordinates are converted to the coordinate
system expected after Pummel Party's own OBJ X-axis correction. Split normals,
UVs, negative-scale winding, and only the material slots actually used by faces
are written to the OBJ.

Each material slot must have a Base Color image, or the exporter creates a 1×1
PNG from the material's Base Color. UVs and material-slot order are retained.

## MCP configuration

Set these before starting the MCP server:

```powershell
$env:PUMMELMCP_ALLOWED_ROOT = "D:\GameCache\Rebuilt Games\Pummel Party\WorkshopMods"
$env:PUMMELMCP_IMPORT_ROOT = "D:\PummelImports"
$env:PUMMELMCP_EDITOR_ASSET_ROOT = "D:\Pummel Party MCP\StreamingAssets"
```

Call `plan_blender_scene_import` with the target `.scene` and exported
`pummel_scene.json`. Pass its `plan_sha256` unchanged to
`build_blender_scene_import`.

The builder refuses path traversal, malformed OBJ/image files, material-slot
mismatches, stale plans and existing destination asset names. It creates
`MainScene.scene.bak.blender-import` before publishing the new Scene. A successful
build is marked `BUILT_UNVERIFIED_IN_EDITOR`; open the Mod once in the official
editor so it loads the new assets and visually verify materials, scale and
collision.
