from __future__ import annotations

import shutil

import pytest

from pummelmcp.pmh.duplication_writer import (
    LIGHT_SHAPE,
    PLAYER_SPAWN_SHAPE,
    STANDARD_TEXT_FIELDS,
    TEXT_SHAPE,
    duplicate_leaf,
    plan_leaf_duplication,
)
from pummelmcp.pmh.reader import read_pmh


@pytest.mark.parametrize(
    ("identifier", "shape", "safety_class"),
    (
        (
            "d4b76d25-9d72-4772-9364-554c6c159404",
            PLAYER_SPAWN_SHAPE,
            "SAFE_PLAYERSPAWN_LEAF_DUPLICATION",
        ),
        (
            "cf70476f-7eb5-4a5e-bf31-ddc9d0cf3e64",
            LIGHT_SHAPE,
            "SAFE_LIGHT_LEAF_DUPLICATION",
        ),
    ),
)
def test_known_value_candidate_duplicate_is_structurally_safe(
    main_scene_path, tmp_path, identifier, shape, safety_class
):
    scene_path = tmp_path / "MainScene.scene"
    shutil.copyfile(main_scene_path, scene_path)
    before = read_pmh(scene_path)
    source = next(obj for obj in before.walk() if obj.guid == identifier)
    source_payloads = [
        (component.type_name, component.enabled, [(field.name, field.raw) for field in component.fields if field.name != "guid"])
        for component in source.components
    ]

    plan = plan_leaf_duplication(scene_path, identifier)
    assert plan.safety_class == safety_class
    assert tuple(item.type for item in plan.source.components) == shape
    report = duplicate_leaf(scene_path, identifier, backup=False)

    after = read_pmh(scene_path)
    duplicate = next(obj for obj in after.walk() if obj.guid == report.duplicate_guid)
    duplicate_payloads = [
        (component.type_name, component.enabled, [(field.name, field.raw) for field in component.fields if field.name != "guid"])
        for component in duplicate.components
    ]
    assert duplicate_payloads == source_payloads
    assert duplicate.parent.guid == source.parent.guid
    assert duplicate.sibling_index == len(source.parent.children)
    assert report.validation.passed


def test_text_candidate_has_exact_audited_field_surface(main_scene_path):
    scene = read_pmh(main_scene_path)
    text = next(obj for obj in scene.walk() if tuple(c.type_name for c in obj.components) == TEXT_SHAPE)
    assert tuple(field.name for field in text.components[1].fields) == STANDARD_TEXT_FIELDS
    assert isinstance(text.components[1].get_field("Text").value, str)
