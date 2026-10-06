from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    AmbiguousComponentError,
    ComponentNotFoundError,
    ComponentPropertyNotFoundError,
    ComponentPropertyReadOnlyError,
    ComponentWriteError,
    PMHScene,
    read_pmh,
)
from pummelmcp.service import get_component_property_details


@pytest.fixture
def component_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    target = tmp_path / "components.scene"
    shutil.copyfile(main_scene_path, target)
    return target


@pytest.mark.parametrize(
    ("object_identifier", "component", "property_name", "schema"),
    [
        ("PlayerSpawn_0", "ModPlayerSpawn", "SharedSpawn", "Bool1"),
        ("7f35679a-c62b-43f2-8c16-d33a322853d1", "ModBoxCollider", "size", "Vector3Float32"),
        ("10ea971b-c282-4dc0-bc4e-429467bea492", "ModProp", "tintColor", "Color4Float32"),
        ("cf70476f-7eb5-4a5e-bf31-ddc9d0cf3e64", "ModLight", "intensity", "Float32LE"),
        ("WorldText", "ModText", "Text", "Utf8String"),
    ],
)
def test_read_registered_properties_from_real_fixture(
    main_scene_path: Path,
    object_identifier: str,
    component: str,
    property_name: str,
    schema: str,
) -> None:
    result = get_component_property_details(
        main_scene_path, object_identifier, component, property_name
    )
    assert result["component"]["type"] == component
    assert result["schema"] == schema
    assert result["value"] is not None


def _field(path: Path, object_id: str, component: str, property_name: str):
    loaded = PMHScene.load(path)
    return loaded.get_component(object_id, component).get_field(property_name)


@pytest.mark.parametrize(
    ("object_id", "component", "property_name", "value", "expected"),
    [
        ("PlayerSpawn_0", "ModPlayerSpawn", "SharedSpawn", True, True),
        ("PlayerSpawn_0", "ModPlayerSpawn", "SpawnUsageType", 4, 4),
        ("cf70476f-7eb5-4a5e-bf31-ddc9d0cf3e64", "ModLight", "intensity", 2.25, 2.25),
    ],
)
def test_set_scalar_real_fixture(
    component_scene: Path,
    object_id: str,
    component: str,
    property_name: str,
    value,
    expected,
) -> None:
    report = PMHScene.load(component_scene).set_component_property(
        object_id, component, property_name, value, backup=False
    )
    assert _field(component_scene, object_id, component, property_name).value == expected
    assert report.validation.passed
    assert report.property == property_name


def test_set_vector3_partial_changes_only_selected_member(component_scene: Path) -> None:
    object_id = "7f35679a-c62b-43f2-8c16-d33a322853d1"
    before_file = component_scene.read_bytes()
    before = _field(component_scene, object_id, "ModBoxCollider", "size")
    assert before.source_span is not None

    report = PMHScene.load(component_scene).set_component_property(
        object_id, "ModBoxCollider", "size", {"x": 8.0}, backup=False
    )

    after = _field(component_scene, object_id, "ModBoxCollider", "size")
    assert struct.unpack("<f", after.raw[:4])[0] == 8.0
    assert after.raw[4:] == before.raw[4:]
    differences = {
        i
        for i, (old, new) in enumerate(zip(before_file, component_scene.read_bytes()))
        if old != new
    }
    assert differences <= set(range(before.source_span.start, before.source_span.start + 4))
    assert report.changes["x"].source_start == before.source_span.start


def test_set_vector2_partial(component_scene: Path) -> None:
    before = _field(component_scene, "WorldText", "ModText", "Size")
    PMHScene.load(component_scene).set_component_property(
        "WorldText", "ModText", "Size", {"y": 7.5}, backup=False
    )
    after = _field(component_scene, "WorldText", "ModText", "Size")
    assert after.raw[:4] == before.raw[:4]
    assert struct.unpack("<f", after.raw[4:])[0] == 7.5


def test_set_color_partial_changes_only_selected_channel(component_scene: Path) -> None:
    object_id = "cf70476f-7eb5-4a5e-bf31-ddc9d0cf3e64"
    before = _field(component_scene, object_id, "ModLight", "color")
    PMHScene.load(component_scene).set_component_property(
        object_id, "ModLight", "color", {"r": 0.5}, backup=False
    )
    after = _field(component_scene, object_id, "ModLight", "color")
    assert struct.unpack("<f", after.raw[:4])[0] == 0.5
    assert after.raw[4:] == before.raw[4:]


def test_dry_run_does_not_write_or_backup(component_scene: Path) -> None:
    before = component_scene.read_bytes()
    report = PMHScene.load(component_scene).set_component_property(
        "PlayerSpawn_0", "ModPlayerSpawn", "Radius", 9.0, dry_run=True
    )
    assert component_scene.read_bytes() == before
    assert report.dry_run is True
    assert report.backup_path is None
    assert report.validation.passed


def test_real_write_creates_exact_backup(component_scene: Path) -> None:
    before = component_scene.read_bytes()
    report = PMHScene.load(component_scene).set_component_property(
        "PlayerSpawn_0", "ModPlayerSpawn", "Advanced", True
    )
    assert report.backup_path is not None
    assert report.backup_path.read_bytes() == before


def test_component_guid_identifier(component_scene: Path) -> None:
    loaded = PMHScene.load(component_scene)
    component = loaded.get_component(
        "PlayerSpawn_0", "84ce7c9d-70c1-482a-82d3-ea012902147b"
    )
    assert component.type_name == "ModPlayerSpawn"


def test_invalid_property_read_only_and_invalid_value(component_scene: Path) -> None:
    loaded = PMHScene.load(component_scene)
    with pytest.raises(ComponentPropertyNotFoundError):
        loaded.set_component_property(
            "PlayerSpawn_0", "ModPlayerSpawn", "notAProperty", 1, dry_run=True
        )
    with pytest.raises(ComponentPropertyReadOnlyError):
        loaded.set_component_property(
            "WorldText", "ModText", "Text", "replacement", dry_run=True
        )
    with pytest.raises(ComponentWriteError):
        loaded.set_component_property(
            "PlayerSpawn_0", "ModPlayerSpawn", "Radius", "large", dry_run=True
        )


def test_missing_component(component_scene: Path) -> None:
    with pytest.raises(ComponentNotFoundError):
        PMHScene.load(component_scene).set_component_property(
            "PlayerSpawn_0", "ModLight", "intensity", 2.0, dry_run=True
        )


def _scene_with_duplicate_lights() -> bytes:
    def s8(value: str) -> bytes:
        raw = value.encode()
        return bytes((len(raw),)) + raw

    hierarchy = s8("Root") + struct.pack("<Bi", 1, 0) + s8("Untagged") + b"\0\0"
    component_index = b"".join(
        s8("ModLight") + s8(f"light-{index}") + b"\x01" for index in (1, 2)
    )
    index = s8("root-guid") + struct.pack("<I", 2) + component_index
    payload = struct.pack("<H", 0) * 2
    return s8("PMH") + struct.pack("<IH", 1, 1) + hierarchy + index + payload


def test_ambiguous_component_type_requires_guid(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.scene"
    path.write_bytes(_scene_with_duplicate_lights())
    with pytest.raises(AmbiguousComponentError, match="use component GUID"):
        PMHScene.load(path).get_component("Root", "ModLight")
