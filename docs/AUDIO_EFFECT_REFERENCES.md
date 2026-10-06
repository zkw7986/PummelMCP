# Audio and effect reference evidence

## PlaySoundAction

Sixteen instances across the read-only Workshop corpus share this complete
shape:

```json
{
  "m_type": 544,
  "m_version": 0,
  "m_targetFlags": 1,
  "m_targets": [],
  "m_clip": {
    "m_assetGUID": {"serializedGuid": "10a663ec-e461-4711-8db8-4aadbc86aeb5"},
    "m_asset": {"m_FileID": 107498, "m_PathID": 0}
  },
  "m_volume": 1.0
}
```

The object shape, GUID bytes, `m_FileID=107498`, `m_PathID=0`, and numeric
volume representation are **CONFIRMED**. The same reference occurs in multiple
real Mods, so it is not a one-off corrupt value. No exact `.pmeta` GUID match
was found. Consequently its classification as an audio/built-in reference is
**INFERRED**, while what the GUID identifies and the meanings of FileID and
PathID are **UNKNOWN**. Filename similarity is not resolution evidence.

`m_clip` and `m_volume` remain read-only. There is no audio replacement tool.

## SpawnEffectAction

Twenty-seven instances contain `m_effectType` as an integer. Observed values
are `0`, `1`, `2`, `3`, and `4`. The representation and values are
**CONFIRMED**. Whether the number is an enum, registry id, asset index, or
another discriminator—and every value name—remain **UNKNOWN**. No Effect
Registry and no effect writer are exposed.
