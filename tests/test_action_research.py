from __future__ import annotations

from pathlib import Path

from pummelmcp.cli import EXIT_SUCCESS, main
from pummelmcp.pmh.action_research import diff_action
from pummelmcp.pmh.action_writer import ActionFieldWriter
from pummelmcp.pmh.actions import ACTION_EVENT_PROPERTIES, parse_action_payload
from pummelmcp.pmh.reader import read_pmh


def _target(path: Path):
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in ACTION_EVENT_PROPERTIES:
                    continue
                for action in parse_action_payload(field.raw).actions:
                    if action.type_info and action.type_info.class_name == "SpawnPrefabAction":
                        return obj.guid, component.guid, field.name, action.rid
    raise AssertionError


def test_diff_action_is_read_only_and_exact(main_scene_path: Path, tmp_path: Path) -> None:
    before = tmp_path / "before.scene"
    after = tmp_path / "after.scene"
    before.write_bytes(main_scene_path.read_bytes())
    after.write_bytes(main_scene_path.read_bytes())
    target = _target(after)
    action = next(
        a for a in parse_action_payload(
            next(c for o in read_pmh(after).walk() if o.guid == target[0] for c in o.components if c.guid == target[1]).get_field(target[2]).raw
        ).actions if a.rid == target[3]
    )
    old = action.fields["m_parentToTarget"]
    ActionFieldWriter(after).set_action_field(
        *target, "SpawnPrefabAction", "m_parentToTarget", not old, backup=False
    )
    before_hash = before.read_bytes()
    after_hash = after.read_bytes()
    result = diff_action(before, after, *target[:3], action_rid=target[3])
    assert result["changed_field_count"] == 1
    assert result["changes"][0]["field"] == "m_parentToTarget"
    assert result["identity"]["unchanged"] is True
    assert result["read_only"] is True
    assert before.read_bytes() == before_hash
    assert after.read_bytes() == after_hash


def test_research_diff_action_cli(main_scene_path: Path, capsys) -> None:
    target = _target(main_scene_path)
    code = main([
        "research", "diff-action", str(main_scene_path), str(main_scene_path),
        target[0], target[1], target[2], "--rid", str(target[3]), "--json",
    ])
    assert code == EXIT_SUCCESS
    assert '"changed_field_count": 0' in capsys.readouterr().out
