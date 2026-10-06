from __future__ import annotations

import hashlib
import json
import shutil
import struct
from io import StringIO
from pathlib import Path

import pytest

from pummelmcp.cli import main
from pummelmcp.pmh import read_pmh


def _run(*arguments: object) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main([str(value) for value in arguments], stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def cli_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    directory = tmp_path / "含 空格 scene"
    directory.mkdir()
    target = directory / "MainScene.scene"
    shutil.copyfile(main_scene_path, target)
    return target


def _transform_raw(path: Path, property_name: str) -> bytes:
    return (
        read_pmh(path)
        .find_game_object("PlayerSpawn_0")
        .get_component("ModTransform")
        .get_field(property_name)
        .raw
    )


def test_cli_summary(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("summary", main_scene_path)

    assert code == 0
    assert stderr == ""
    assert "Magic: PMH" in stdout
    assert "Roots: 41" in stdout
    assert "GameObjects: 142" in stdout
    assert "Components: 267" in stdout
    assert "Valid: PASS" in stdout


def test_cli_objects(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("objects", main_scene_path)

    assert code == 0
    assert stderr == ""
    assert "/Player Spawnpoints/PlayerSpawn_0" in stdout
    assert "d4b76d25-9d72-4772-9364-554c6c159404" in stdout


def test_cli_objects_query(main_scene_path: Path) -> None:
    code, stdout, _ = _run("objects", main_scene_path, "--query", "PlayerSpawn_0")

    assert code == 0
    assert "PlayerSpawn_0" in stdout
    assert "LowPolyCube" not in stdout


def test_cli_object_by_hierarchy_path(main_scene_path: Path) -> None:
    code, stdout, stderr = _run(
        "object", main_scene_path, "/Player Spawnpoints/PlayerSpawn_0"
    )

    assert code == 0
    assert stderr == ""
    assert "Name: PlayerSpawn_0" in stdout
    assert "Parent: Player Spawnpoints" in stdout
    assert "ModTransform" in stdout


def test_cli_object_by_guid_is_case_insensitive(main_scene_path: Path) -> None:
    code, stdout, stderr = _run(
        "object", main_scene_path, "D4B76D25-9D72-4772-9364-554C6C159404"
    )

    assert code == 0
    assert stderr == ""
    assert "Name: PlayerSpawn_0" in stdout


def test_cli_transform(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("transform", main_scene_path, "PlayerSpawn_0")

    assert code == 0
    assert stderr == ""
    assert "Object: PlayerSpawn_0" in stdout
    assert "Position" in stdout
    assert "X: 5.00000238" in stdout
    assert "Rotation" in stdout
    assert "Scale" in stdout


def test_cli_set_position_x(cli_scene: Path) -> None:
    before = _transform_raw(cli_scene, "position")

    code, stdout, stderr = _run("set-position", cli_scene, "PlayerSpawn_0", "--x", 8)

    after = _transform_raw(cli_scene, "position")
    assert code == 0
    assert stderr == ""
    assert "x: 5.00000238 -> 8" in stdout
    assert struct.unpack("<f", after[:4])[0] == 8.0
    assert after[4:] == before[4:]


def test_cli_set_position_partial(cli_scene: Path) -> None:
    before = _transform_raw(cli_scene, "position")

    code, _, _ = _run("set-position", cli_scene, "PlayerSpawn_0", "--y", -2.5)

    after = _transform_raw(cli_scene, "position")
    assert code == 0
    assert after[:4] == before[:4]
    assert struct.unpack("<f", after[4:8])[0] == -2.5
    assert after[8:] == before[8:]


def test_cli_set_position_dry_run(cli_scene: Path) -> None:
    before = cli_scene.read_bytes()

    code, stdout, stderr = _run(
        "set-position", cli_scene, "PlayerSpawn_0", "--x", 8, "--dry-run"
    )

    assert code == 0
    assert stderr == ""
    assert "Dry run: true" in stdout
    assert cli_scene.read_bytes() == before
    assert not list(cli_scene.parent.glob("MainScene.scene.bak.*"))


def test_cli_set_rotation(cli_scene: Path) -> None:
    before = _transform_raw(cli_scene, "rotation")

    code, _, _ = _run("set-rotation", cli_scene, "PlayerSpawn_0", "--z", 90)

    after = _transform_raw(cli_scene, "rotation")
    assert code == 0
    assert after[:8] == before[:8]
    assert struct.unpack("<f", after[8:])[0] == 90.0


def test_cli_set_scale(cli_scene: Path) -> None:
    before = _transform_raw(cli_scene, "scale")

    code, _, _ = _run("set-scale", cli_scene, "PlayerSpawn_0", "--x", 1.5)

    after = _transform_raw(cli_scene, "scale")
    assert code == 0
    assert struct.unpack("<f", after[:4])[0] == 1.5
    assert after[4:] == before[4:]


def test_cli_validate(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("validate", main_scene_path)

    assert code == 0
    assert stderr == ""
    assert "Validation: PASS" in stdout
    assert "fully_consumed: PASS" in stdout
    assert "property_source_spans_valid: PASS" in stdout


def test_cli_json_output(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("summary", main_scene_path, "--json")

    data = json.loads(stdout)
    assert code == 0
    assert stderr == ""
    assert data["magic"] == "PMH"
    assert data["object_count"] == 142
    assert stdout.count("\n") == 1


def test_cli_json_mutation_uses_change_report(cli_scene: Path) -> None:
    code, stdout, stderr = _run(
        "set-position", cli_scene, "PlayerSpawn_0", "--x", 8, "--dry-run", "--json"
    )

    data = json.loads(stdout)
    assert code == 0
    assert stderr == ""
    assert data["object"] == "PlayerSpawn_0"
    assert data["property"] == "position"
    assert data["changes"]["x"]["before"] == 5.000002384185791
    assert data["changes"]["x"]["after"] == 8.0
    assert data["validation"]["passed"] is True


def test_cli_missing_object(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("object", main_scene_path, "missing-object")

    assert code == 3
    assert stdout == ""
    assert "no GameObject" in stderr
    assert "Traceback" not in stderr


def test_cli_ambiguous_object(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("transform", main_scene_path, "LowPolyCube")

    assert code == 3
    assert stdout == ""
    assert "use GUID or hierarchy path" in stderr


def test_cli_no_axis(cli_scene: Path) -> None:
    before = cli_scene.read_bytes()

    code, stdout, stderr = _run("set-position", cli_scene, "PlayerSpawn_0")

    assert code == 2
    assert stdout == ""
    assert "at least one" in stderr
    assert cli_scene.read_bytes() == before


def test_cli_invalid_float(cli_scene: Path) -> None:
    code, stdout, stderr = _run(
        "set-position", cli_scene, "PlayerSpawn_0", "--x", "not-a-float"
    )

    assert code == 2
    assert stdout == ""
    assert "invalid float value" in stderr


def test_cli_invalid_scene(tmp_path: Path) -> None:
    invalid = tmp_path / "截断 scene.scene"
    invalid.write_bytes(b"\x03PMH\x01")

    code, stdout, stderr = _run("validate", invalid)

    assert code == 3
    assert stdout == ""
    assert "unexpected EOF" in stderr
    assert "Traceback" not in stderr


def test_cli_validate_trailing_bytes_returns_nonzero(
    tmp_path: Path, main_scene_path: Path
) -> None:
    invalid = tmp_path / "trailing.scene"
    invalid.write_bytes(main_scene_path.read_bytes() + b"unexpected")

    code, stdout, stderr = _run("validate", invalid)

    assert code == 3
    assert stderr == ""
    assert "Validation: FAIL" in stdout
    assert "fully_consumed: FAIL" in stdout


def test_cli_json_error_is_stable(main_scene_path: Path) -> None:
    code, stdout, stderr = _run("object", main_scene_path, "missing-object", "--json")

    data = json.loads(stderr)
    assert code == 3
    assert stdout == ""
    assert data["error"]["code"] == 3
    assert "no GameObject" in data["error"]["message"]


def test_cli_mutation_uses_tmp_copy(cli_scene: Path, main_scene_path: Path) -> None:
    fixture_before = hashlib.sha256(main_scene_path.read_bytes()).hexdigest()
    copy_before = cli_scene.read_bytes()

    code, _, _ = _run("set-position", cli_scene, "PlayerSpawn_0", "--x", 8)

    assert code == 0
    assert cli_scene.read_bytes() != copy_before
    assert hashlib.sha256(main_scene_path.read_bytes()).hexdigest() == fixture_before


def test_cli_original_fixture_hash_unchanged(main_scene_path: Path) -> None:
    expected = "a0af6631df7e99d4f98d964122eca56ab66cb8beaad53d7217bc54efff89cde8"

    code, _, _ = _run("transform", main_scene_path, "PlayerSpawn_0")

    assert code == 0
    assert hashlib.sha256(main_scene_path.read_bytes()).hexdigest() == expected
