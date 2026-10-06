"""Tests for spawning a built-in prop object into a PMH scene.

These use a synthetically built :class:`VerifiedPropReference` so the writer is
exercised without depending on the shipped game data; registry provenance is
covered end to end in ``test_builtin_prop_e2e.py`` and the catalog decoder in
``test_editor_assets.py``.
"""

from __future__ import annotations

import hashlib
import shutil
import struct
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    ConcurrentModificationError,
    PMHReader,
    PMHScene,
    UnsafeBuiltinPropSpawnError,
    VerifiedPropReference,
    WriterValidationError,
    plan_builtin_prop_spawn,
    read_pmh,
    spawn_builtin_prop,
    validate_scene,
)

PROP_GUID = "00fc069a-530c-4036-ab72-f2150ce0fc64"
REFERENCE_PAYLOAD = b"\x01\x24" + PROP_GUID.encode("ascii")


@pytest.fixture
def reference() -> VerifiedPropReference:
    return VerifiedPropReference(
        guid=PROP_GUID,
        payload=REFERENCE_PAYLOAD,
        payload_sha256=hashlib.sha256(REFERENCE_PAYLOAD).hexdigest(),
        asset_id="Assets/Viking/Environment/Tree Pine 01",
        asset_name="Tree Pine 01",
        asset_folder="Viking/Environment/",
        catalog_sha256="b" * 64,
    )


@pytest.fixture
def scene(tmp_path: Path, main_scene_path: Path) -> Path:
    target = tmp_path / "Data" / "MainScene.scene"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(main_scene_path, target)
    return target


def _component_raw(game_object, component_type: str, field_name: str) -> bytes:
    return game_object.get_component(component_type).get_field(field_name).raw


def _field(game_object, component_type: str, field_name: str):
    return game_object.get_component(component_type).get_field(field_name)


def test_spawn_creates_one_prop_object_and_preserves_everything_else(
    scene: Path, reference: VerifiedPropReference
) -> None:
    before = read_pmh(scene)
    before_bytes = scene.read_bytes()
    before_objects = {
        obj.guid: (obj.name, obj.hierarchy_path, len(obj.components))
        for obj in before.walk()
    }

    report = spawn_builtin_prop(scene, reference)

    assert report["dry_run"] is False
    assert report["validation"]["passed"] is True
    assert not [name for name, ok in report["validation"]["checks"].items() if not ok]
    assert report["created"]["component_shape"] == ["ModTransform", "ModProp"]
    assert report["created"]["gameobject_guid"] == report["created"]["transform_guid"]
    assert report["created"]["prop_guid"] != report["created"]["gameobject_guid"]
    assert report["mod_prop"]["custom_materials"] == "EMPTY"
    assert report["asset"]["reference_payload_sha256"] == reference.payload_sha256

    after = read_pmh(scene)
    assert after.fully_consumed is True
    assert validate_scene(after).passed is True
    assert after.object_count == before.object_count + 1
    assert after.component_count == before.component_count + 2
    assert scene.stat().st_size == len(before_bytes) + report["bytes_inserted"]
    assert hashlib.sha256(scene.read_bytes()).hexdigest() == report[
        "transaction_after_sha256"
    ]

    for obj in after.walk():
        if obj.guid == report["created"]["gameobject_guid"]:
            continue
        assert (obj.name, obj.hierarchy_path, len(obj.components)) == before_objects[
            obj.guid
        ]


def test_spawn_writes_the_registered_reference_envelope(
    scene: Path, reference: VerifiedPropReference
) -> None:
    report = spawn_builtin_prop(scene, reference)
    created = [
        obj
        for obj in read_pmh(scene).walk()
        if obj.guid == report["created"]["gameobject_guid"]
    ][0]

    assert _component_raw(created, "ModProp", "prop") == REFERENCE_PAYLOAD
    assert _component_raw(created, "ModProp", "prop") == b"\x01\x24" + PROP_GUID.encode()
    # The GameObject and its ModTransform share one identity GUID.
    assert created.guid == created.get_component("ModTransform").guid
    assert (
        _field(created, "ModTransform", "guid").raw[1:].decode("ascii") == created.guid
    )
    assert (
        _field(created, "ModProp", "guid").raw[1:].decode("ascii")
        == report["created"]["prop_guid"]
    )
    # ModProp keeps an empty material override list.
    assert _component_raw(created, "ModProp", "customMaterials") == struct.pack("<I", 0)


def test_spawn_applies_position_rotation_scale_tint_and_collision(
    scene: Path, reference: VerifiedPropReference
) -> None:
    report = spawn_builtin_prop(
        scene,
        reference,
        position={"x": 4.0, "y": 0.5, "z": -6.0},
        rotation={"y": 0.7853982},
        scale={"x": 2.0, "y": 3.0, "z": 4.0},
        tint_color={"r": 0.25, "g": 0.5, "b": 0.75, "a": 1.0},
        collision_type="sphere",
        shadow_casting_mode="off",
    )
    created = [
        obj
        for obj in read_pmh(scene).walk()
        if obj.guid == report["created"]["gameobject_guid"]
    ][0]

    assert _component_raw(created, "ModTransform", "position") == struct.pack(
        "<3f", 4.0, 0.5, -6.0
    )
    assert _component_raw(created, "ModTransform", "rotation") == struct.pack(
        "<3f", 0.0, 0.7853982, 0.0
    )
    assert _component_raw(created, "ModTransform", "scale") == struct.pack(
        "<3f", 2.0, 3.0, 4.0
    )
    assert _component_raw(created, "ModProp", "tintColor") == struct.pack(
        "<4f", 0.25, 0.5, 0.75, 1.0
    )
    assert _component_raw(created, "ModProp", "collisionType") == struct.pack("<i", 2)
    assert _component_raw(created, "ModProp", "shadowCastingMode") == struct.pack("<i", 0)
    assert report["mod_prop"]["collision_type"]["name"] == "sphere"
    assert report["mod_prop"]["shadow_casting_mode"]["name"] == "off"
    # Omitting an axis leaves it at the documented default.
    assert report["transform"]["rotation"]["x"] == 0.0
    assert report["transform"]["rotation"]["z"] == 0.0
    assert report["transform"]["rotation"]["y"] == pytest.approx(0.7853982)


def test_spawn_defaults_match_the_game_component_defaults(
    scene: Path, reference: VerifiedPropReference
) -> None:
    report = spawn_builtin_prop(scene, reference)
    assert report["transform"]["position"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert report["transform"]["rotation"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert report["transform"]["scale"] == {"x": 1.0, "y": 1.0, "z": 1.0}
    assert report["mod_prop"]["collision_type"] == {"name": "mesh", "raw_value": 3}
    assert report["mod_prop"]["shadow_casting_mode"] == {"name": "on", "raw_value": 1}
    assert report["created"]["name"] == reference.asset_name


def test_spawn_places_the_object_as_the_last_child_at_the_right_preorder_slot(
    scene: Path, reference: VerifiedPropReference
) -> None:
    report = spawn_builtin_prop(scene, reference)
    after = read_pmh(scene)
    objects = list(after.walk())
    index = report["destination"]["preorder_index"]

    assert objects[index].guid == report["created"]["gameobject_guid"]
    assert objects[index].hierarchy_path == report["created"]["hierarchy_path"]
    assert objects[index].sibling_index == report["destination"]["sibling_index"]
    assert objects[index].parent is not None
    assert objects[index].parent.guid == report["destination"]["parent_guid"]
    assert objects[index].parent.children[-1] is objects[index]


def test_spawn_accepts_an_explicit_parent_and_name(
    scene: Path, reference: VerifiedPropReference
) -> None:
    before = read_pmh(scene)
    report = spawn_builtin_prop(scene, reference, parent="/Colliders", name="My Tree")
    created = [
        obj
        for obj in read_pmh(scene).walk()
        if obj.guid == report["created"]["gameobject_guid"]
    ][0]

    assert created.name == "My Tree"
    assert created.hierarchy_path == "/Colliders/My Tree"
    assert created.parent.name == "Colliders"
    assert created.parent.hierarchy_path == "/Colliders"
    assert created.layer == before.find_game_object("Colliders").layer
    assert created.tag == before.find_game_object("Colliders").tag
    # The new record is appended after the parent's existing subtree.
    assert created.sibling_index == len(created.parent.children) - 1


def test_repeated_spawns_stay_consistent_and_allocate_fresh_guids(
    scene: Path, reference: VerifiedPropReference
) -> None:
    seen_objects: set[str] = set()
    seen_components: set[str] = set()
    for step in range(4):
        report = spawn_builtin_prop(
            scene, reference, position={"x": float(step * 3)}
        )
        assert report["validation"]["passed"] is True
        guid = report["created"]["gameobject_guid"]
        prop_guid = report["created"]["prop_guid"]
        assert guid not in seen_objects and prop_guid not in seen_components
        seen_objects.add(guid)
        seen_components.add(prop_guid)

    after = read_pmh(scene)
    assert after.fully_consumed is True
    assert validate_scene(after).passed is True
    world = after.find_game_object("World")
    assert [child.name for child in world.children[-4:]] == [reference.asset_name] * 4
    assert [child.guid for child in world.children[-4:]] == [
        child.guid for child in world.children[-4:]
    ]
    assert len({child.guid for child in world.children}) == len(world.children)


def test_spawn_dry_run_writes_nothing(scene: Path, reference: VerifiedPropReference) -> None:
    before = scene.read_bytes()
    report = spawn_builtin_prop(scene, reference, dry_run=True)

    assert report["dry_run"] is True
    assert report["validation"]["passed"] is True
    assert report["backup_path"] is None
    assert scene.read_bytes() == before
    assert not list(scene.parent.glob("*.bak.*"))


def test_spawn_creates_a_backup_and_reports_it(
    scene: Path, reference: VerifiedPropReference
) -> None:
    before = scene.read_bytes()
    report = spawn_builtin_prop(scene, reference)
    backup = Path(report["backup_path"])

    assert backup.is_file()
    assert backup.read_bytes() == before
    assert backup.name.startswith("MainScene.scene.bak.")


def test_spawn_rejects_a_stale_expected_scene_hash(
    scene: Path, reference: VerifiedPropReference
) -> None:
    before = scene.read_bytes()
    with pytest.raises(ConcurrentModificationError, match="UNSAFE_STALE_SCENE"):
        spawn_builtin_prop(scene, reference, expected_scene_hash="0" * 64)
    assert scene.read_bytes() == before

    digest = hashlib.sha256(before).hexdigest()
    report = spawn_builtin_prop(scene, reference, expected_scene_hash=digest.upper())
    assert report["transaction_before_sha256"] == digest


def test_plan_matches_the_committed_report(
    scene: Path, reference: VerifiedPropReference
) -> None:
    plan = plan_builtin_prop_spawn(scene, reference, parent="/World", name="Planned")
    assert plan.safety_class == "SAFE_REGISTERED_BUILTIN_PROP_SPAWN"
    assert plan.created_name == "Planned"
    assert plan.created_hierarchy_path == "/World/Planned"
    assert plan.mutation_kinds == (
        "UPDATE_PARENT_CHILD_COUNT",
        "INSERT_HIERARCHY_RECORD",
        "INSERT_OBJECT_INDEX_RECORD",
        "INSERT_COMPONENT_PAYLOADS",
    )
    hierarchy_bytes = 1 + len("Planned") + 1 + 4 + 1 + len("Untagged") + 2
    assert plan.inserted_bytes == hierarchy_bytes + 138 + 120 + 197
    assert plan.inserted_bytes == 479
    # Planning alone must not touch the file.
    assert PMHScene.load(scene).scene.object_count == read_pmh(scene).object_count


def test_spawn_rejects_a_reference_that_is_not_a_verified_catalog_reference(
    scene: Path,
) -> None:
    before = scene.read_bytes()
    with pytest.raises(UnsafeBuiltinPropSpawnError, match="VerifiedPropReference"):
        spawn_builtin_prop(scene, "00fc069a-530c-4036-ab72-f2150ce0fc64")  # type: ignore[arg-type]
    with pytest.raises(UnsafeBuiltinPropSpawnError, match="VerifiedPropReference"):
        spawn_builtin_prop(scene, {"guid": PROP_GUID, "payload": REFERENCE_PAYLOAD})  # type: ignore[arg-type]
    assert scene.read_bytes() == before


def test_spawn_rejects_a_reference_without_catalog_provenance(scene: Path) -> None:
    forged = VerifiedPropReference(
        guid=PROP_GUID,
        payload=REFERENCE_PAYLOAD,
        payload_sha256=hashlib.sha256(REFERENCE_PAYLOAD).hexdigest(),
        asset_id="",
        asset_name="Tree Pine 01",
        asset_folder="Viking/Environment/",
        catalog_sha256="",
    )
    with pytest.raises(UnsafeBuiltinPropSpawnError, match="catalog provenance"):
        spawn_builtin_prop(scene, forged)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"collision_type": "banana"}, "collision_type must be one of"),
        ({"shadow_casting_mode": "sometimes"}, "shadow_casting_mode must be one of"),
        ({"position": {"x": float("nan")}}, "must be finite"),
        ({"position": {"x": float("inf")}}, "must be finite"),
        ({"position": [1.0, 2.0]}, "three-element sequence"),
        ({"tint_color": [1.0, 2.0]}, "four-element sequence"),
        ({"position": {"w": 1.0}}, "accepts only x, y, and z"),
        ({"tint_color": {"r": 0.5, "g": 0.5, "b": 0.5, "a": 1.0, "x": 1.0}}, "accepts only"),
        ({"name": ""}, "must not be empty"),
        ({"name": "x" * 300}, "above the 255-byte limit"),
        ({"layer": 999999999999}, "signed 32-bit"),
        ({"active": "yes"}, "active must be a boolean"),
    ],
)
def test_spawn_rejects_invalid_arguments(
    scene: Path, reference: VerifiedPropReference, kwargs: dict, message: str
) -> None:
    before = scene.read_bytes()
    with pytest.raises((UnsafeBuiltinPropSpawnError, WriterValidationError), match=message):
        spawn_builtin_prop(scene, reference, **kwargs)
    assert scene.read_bytes() == before


def test_spawn_rejects_an_unknown_parent(
    scene: Path, reference: VerifiedPropReference
) -> None:
    before = scene.read_bytes()
    with pytest.raises(Exception):
        spawn_builtin_prop(scene, reference, parent="/No/Such/Object")
    assert scene.read_bytes() == before


def test_planned_region_reversal_is_exact_and_load_bearing(
    scene: Path, reference: VerifiedPropReference
) -> None:
    """The commit path's region check must be an exact inverse, not a guess."""
    from pummelmcp.pmh import builtin_prop_writer as writer

    prepared = writer._prepare_spawn(
        scene,
        reference,
        parent=None,
        name=None,
        position=None,
        rotation=None,
        scale=None,
        tint_color=None,
        collision_type=None,
        shadow_casting_mode=None,
        layer=None,
        tag=None,
        active=True,
        expected_scene_hash=None,
        guid_factory=__import__("uuid").uuid4,
        max_guid_attempts=32,
    )
    original = prepared.loaded._original_bytes

    # Reversing the planned edits reproduces the source byte for byte.
    assert (
        writer._undo_planned_operations(original, prepared.patched, prepared.operations)
        == original
    )
    # A single unexpected byte change outside every planned region breaks it.
    tampered = bytearray(prepared.patched)
    tampered[1000] ^= 0xFF
    assert (
        writer._undo_planned_operations(original, bytes(tampered), prepared.operations)
        != original
    )
    # The plan's own mutation list accounts for every inserted byte.
    inserted = sum(len(replacement) for _o, old, replacement in prepared.operations if old == 0)
    assert inserted == prepared.plan.inserted_bytes
