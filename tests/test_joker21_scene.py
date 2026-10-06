from __future__ import annotations

from pathlib import Path

import pytest

from pummelmcp.pmh import DecodeConfidence, Vector3, read_pmh


@pytest.fixture(scope="module")
def joker21_scene(main_scene_path: Path):
    return read_pmh(main_scene_path)


def test_confirmed_scene_totals(joker21_scene) -> None:
    assert joker21_scene.magic == "PMH"
    assert joker21_scene.version == 1
    assert joker21_scene.root_count == 41
    assert joker21_scene.object_count == 142
    assert joker21_scene.component_count == 267
    assert joker21_scene.fully_consumed
    assert joker21_scene.remaining_bytes == 0


def test_player_spawn_transform_is_found_structurally(joker21_scene) -> None:
    game_object = joker21_scene.find_game_object("PlayerSpawn_0")
    transform = game_object.get_component("ModTransform")

    position = transform.get_field("position")
    rotation = transform.get_field("rotation")
    scale = transform.get_field("scale")

    assert position.confidence is DecodeConfidence.CONFIRMED
    # This fixture is the editor-validated experiment in which position.x was
    # changed to 5.0. Its exact decoded float is slightly above 5 because the
    # stored little-endian payload is 05 00 a0 40.
    assert position.value == pytest.approx(
        Vector3(5.000002384185791, 0.4256715178489685, -9.119691848754883)
    )
    assert rotation.value == pytest.approx(Vector3(0.0, 0.0, -0.0))
    assert scale.value == pytest.approx(Vector3(1.0, 1.0, 1.0))
    assert len(position.raw) == len(rotation.raw) == len(scale.raw) == 12
