# Prefab pack v0.1

`plan_prefab_pack` and `build_prefab_pack` clone an existing Mod into a new
directory while creating Prefab assets from templates already in that Mod.
The source Scene path must be inside `PUMMELMCP_ALLOWED_ROOT` and under the
source Mod's `Data` directory; the allowed root must contain the source Mod
directory. The build requires the exact plan hash.

```json
{
  "version": "0.1",
  "scene": "C:/Mods/Shooter/Data/MainScene.scene",
  "output_mod": "NewShooter",
  "assets": [
    {"source": "Assets/Prefabs/SMG_Visual.pfab", "name": "CoinVisual",
     "root_transform": {"scale": {"x": 1.5, "y": 1.5, "z": 1.5}}},
    {"source": "Assets/Prefabs/SMG_Item.pfab", "name": "CoinItem"}
  ],
  "spawner_bindings": [
    {
      "object_guid": "25fc92b9-651d-4780-87a8-f8ba6e02a315",
      "source_asset": "Assets/Prefabs/SMG_Item.pfab"
    }
  ],
  "pickup_scores": [
    {
      "source_asset": "Assets/Prefabs/SMG_Item.pfab",
      "value": 5,
      "template_scene": "C:/Mods/Aftershock/Data/MainScene.scene"
    }
  ]
}
```

`spawner_bindings` is optional. It works only when a single existing
`ModSpawner` has exactly one reference to the named source Item in both its
`Prefabs` and `PrefabGUIDs` fields. The builder replaces those references with
the new Item GUID, without synthesizing a new Spawner. Multiple existing spawn
points under that Spawner remain intact.

Each source `.pfab` must have a matching `.pfab.pmeta`. The parser requires
the observed PMH v1 Prefab grammar: one root object and complete consumption.
The build generates new asset, GameObject and component GUIDs; renames the
Prefab root and metadata; and remaps references between assets in the same
request. External Prefab dependencies must resolve to metadata in the source
Mod. An existing `ModProp.prop` reference is preserved as a template supplied
built-in asset reference. Unknown external GUID forms are rejected.
The plan hash also binds the bytes of referenced Prefab dependencies that are
left in place.
An optional `root_transform` edits the cloned Prefab's existing `ModTransform`
position, rotation, or positive scale. It accepts complete finite `x/y/z`
vectors only; it does not add a component.

`pickup_scores` is optional. It requires an Item Prefab with an empty
`ModItem.OnPickupTrigger` and an exact `ChangeScoreAction` template in the named
Scene, which must also be under the allowed root. The build writes one `add`
Action with a score of 1–1000 targeting the player who picked up the Item.
The event target follows the decompiled `ModItem` pickup event, where
`Source = player`. The source Item's empty version-one Action list is upgraded
using a source-backed empty version-two trigger frame before adding the Action.

The builder validates every cloned Prefab and the changed Scene before
publishing the whole Mod directory. The source Mod is untouched, and failed
builds leave no output directory. `BUILD_PASS` confirms serialized structure;
it does not confirm an Editor save, runtime spawn, pickup effect, or winner.
