from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    AmbiguousObjectError,
    ObjectIdentifier,
    ObjectNotFoundError,
    PMHFormatError,
    PMHReader,
    PMHScene,
    TransformNotFoundError,
    WriterValidationError,
    read_pmh,
)


@pytest.fixture
def working_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    target = tmp_path / "MainScene.scene"
    shutil.copyfile(main_scene_path, target)
    return target


def _position_raw(path: Path) -> bytes:
    return (
        read_pmh(path)
        .find_game_object("PlayerSpawn_0")
        .get_component("ModTransform")
        .get_field("position")
        .raw
    )


def _property_raw(path: Path, property_name: str) -> bytes:
    return (
        read_pmh(path)
        .find_game_object("PlayerSpawn_0")
        .get_component("ModTransform")
        .get_field(property_name)
        .raw
    )


def test_set_position_x(working_scene: Path) -> None:
    before = _position_raw(working_scene)

    report = PMHScene.load(working_scene).set_position(
        "PlayerSpawn_0", x=8.0, backup=False
    )

    after = _position_raw(working_scene)
    assert struct.unpack("<f", after[:4])[0] == 8.0
    assert after[4:] == before[4:]
    assert report.changes["x"].before == 5.000002384185791
    assert report.changes["x"].after == 8.0
    assert report.validation.passed


def test_set_position_y(working_scene: Path) -> None:
    before = _position_raw(working_scene)

    PMHScene.load(working_scene).set_position(
        "PlayerSpawn_0", y=-3.25, backup=False
    )

    after = _position_raw(working_scene)
    assert after[:4] == before[:4]
    assert struct.unpack("<f", after[4:8])[0] == -3.25
    assert after[8:] == before[8:]


def test_set_position_z(working_scene: Path) -> None:
    before = _position_raw(working_scene)

    PMHScene.load(working_scene).set_position(
        "PlayerSpawn_0", z=12.5, backup=False
    )

    after = _position_raw(working_scene)
    assert after[:8] == before[:8]
    assert struct.unpack("<f", after[8:])[0] == 12.5


def test_partial_update_preserves_other_axes_raw_bytes(working_scene: Path) -> None:
    before = _position_raw(working_scene)

    PMHScene.load(working_scene).set_position(
        "PlayerSpawn_0", x=8.0, backup=False
    )

    after = _position_raw(working_scene)
    assert after[4:8] == before[4:8]
    assert after[8:12] == before[8:12]


def test_set_rotation(working_scene: Path) -> None:
    before = _property_raw(working_scene, "rotation")

    PMHScene.load(working_scene).set_rotation(
        "PlayerSpawn_0", y=45.5, backup=False
    )

    after = _property_raw(working_scene, "rotation")
    assert after[:4] == before[:4]
    assert struct.unpack("<f", after[4:8])[0] == 45.5
    assert after[8:] == before[8:]


def test_set_scale(working_scene: Path) -> None:
    before = _property_raw(working_scene, "scale")

    PMHScene.load(working_scene).set_scale(
        "PlayerSpawn_0", z=2.25, backup=False
    )

    after = _property_raw(working_scene, "scale")
    assert after[:8] == before[:8]
    assert struct.unpack("<f", after[8:])[0] == 2.25


def test_dry_run_does_not_modify_file(working_scene: Path) -> None:
    before = working_scene.read_bytes()

    report = PMHScene.load(working_scene).set_position(
        "PlayerSpawn_0", x=8.0, dry_run=True
    )

    assert working_scene.read_bytes() == before
    assert report.dry_run
    assert report.backup_path is None
    assert report.validation.passed
    assert not list(working_scene.parent.glob("MainScene.scene.bak.*"))


def test_original_fixture_never_modified(main_scene_path: Path) -> None:
    before = main_scene_path.read_bytes()

    report = PMHScene.load(main_scene_path).set_position(
        "PlayerSpawn_0", x=8.0, dry_run=True
    )

    assert report.validation.passed
    assert main_scene_path.read_bytes() == before


def test_backup_created(working_scene: Path) -> None:
    original = working_scene.read_bytes()

    report = PMHScene.load(working_scene).set_position("PlayerSpawn_0", x=8.0)

    assert report.backup_path is not None
    assert report.backup_path.is_file()
    assert report.backup_path.read_bytes() == original
    assert report.backup_path.parent == working_scene.parent


def test_roundtrip_reparse_success(working_scene: Path) -> None:
    report = PMHScene.load(working_scene).set_position(
        "PlayerSpawn_0", x=8.0, backup=False
    )

    reparsed = read_pmh(working_scene)
    assert reparsed.fully_consumed
    assert reparsed.root_count == 41
    assert reparsed.object_count == 142
    assert reparsed.component_count == 267
    assert report.validation.checks["fully_consumed"]


def test_diff_only_inside_target_payload(working_scene: Path) -> None:
    before = working_scene.read_bytes()
    loaded = PMHScene.load(working_scene)
    field = loaded.get_transform("PlayerSpawn_0").get_field("position")
    assert field.source_span is not None
    allowed = set(range(field.source_span.start, field.source_span.start + 4))

    report = loaded.set_position("PlayerSpawn_0", x=8.0, backup=False)

    after = working_scene.read_bytes()
    actual = {i for i, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}
    assert actual
    assert actual <= allowed
    assert set(report.validation.changed_offsets) == actual


def test_missing_object(working_scene: Path) -> None:
    with pytest.raises(ObjectNotFoundError, match="no GameObject"):
        PMHScene.load(working_scene).set_position(
            "definitely-not-present", x=8.0, dry_run=True
        )


def test_ambiguous_object_name(working_scene: Path) -> None:
    with pytest.raises(AmbiguousObjectError, match="use GUID or hierarchy path"):
        PMHScene.load(working_scene).set_position("LowPolyCube", x=8.0, dry_run=True)


def test_guid_and_hierarchy_path_identifiers(working_scene: Path) -> None:
    loaded = PMHScene.load(working_scene)
    by_guid = ObjectIdentifier.by_guid("d4b76d25-9d72-4772-9364-554c6c159404")
    by_path = ObjectIdentifier.by_path("/Player Spawnpoints/PlayerSpawn_0")

    guid_report = loaded.set_position(by_guid, x=8.0, dry_run=True)
    path_report = loaded.set_position(by_path, x=8.0, dry_run=True)

    assert guid_report.guid == path_report.guid
    assert path_report.hierarchy_path == "/Player Spawnpoints/PlayerSpawn_0"


def _scene_without_components() -> bytes:
    def str8(value: str) -> bytes:
        raw = value.encode("utf-8")
        return bytes([len(raw)]) + raw

    hierarchy = b"".join(
        (str8("Root"), struct.pack("<Bi", 1, 0), str8("Untagged"), b"\x00\x00")
    )
    index = str8("root-guid") + struct.pack("<I", 0)
    return str8("PMH") + struct.pack("<IH", 1, 1) + hierarchy + index


def test_missing_transform(tmp_path: Path) -> None:
    path = tmp_path / "no-transform.scene"
    path.write_bytes(_scene_without_components())

    with pytest.raises(TransformNotFoundError, match="missing"):
        PMHScene.load(path).set_position("Root", x=8.0, dry_run=True)


def test_invalid_or_truncated_temp_does_not_replace_original(
    working_scene: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = working_scene.read_bytes()
    loaded = PMHScene.load(working_scene)

    def reject_temp(_reader: PMHReader, _path: Path):
        raise PMHFormatError("simulated truncated temporary PMH")

    monkeypatch.setattr(PMHReader, "read_path", reject_temp)
    with pytest.raises(PMHFormatError, match="truncated"):
        loaded.set_position("PlayerSpawn_0", x=8.0)

    assert working_scene.read_bytes() == original
    assert not list(working_scene.parent.glob("MainScene.scene.bak.*"))
    assert not list(working_scene.parent.glob(".MainScene.scene.*.tmp"))


def test_validation_failure_does_not_replace_original(
    working_scene: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = working_scene.read_bytes()
    loaded = PMHScene.load(working_scene)

    def reject_validation(*_args, **_kwargs):
        raise WriterValidationError("simulated validation failure")

    monkeypatch.setattr(loaded, "_validate", reject_validation)
    with pytest.raises(WriterValidationError, match="validation failure"):
        loaded.set_position("PlayerSpawn_0", x=8.0)

    assert working_scene.read_bytes() == original
    assert not list(working_scene.parent.glob("MainScene.scene.bak.*"))
