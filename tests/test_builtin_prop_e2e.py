"""Exercise real assets on a temporary Mod copy; source Mods are read-only."""

from __future__ import annotations

import os
import shutil
import struct
import uuid
from collections import Counter
from pathlib import Path

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import read_pmh, validate_scene

E2E_SCENE_ENV = "PUMMELMCP_E2E_MOD_SCENE"
E2E_RESTORE_ENV = "PUMMELMCP_E2E_RESTORE_SCENE"

#: Distinct Viking environment assets and the GUIDs the shipped registry holds
#: for them, one per theme named in the request: trees, glaciers, ice blocks,
#: rocks, grass, and snow piles.
ENVIRONMENT_ASSETS = (
    ("Viking/Environment/Tree Pine 01", "00fc069a-530c-4036-ab72-f2150ce0fc64"),
    ("Viking/Environment/Glacier 01", "4f6a5b52-05be-453f-97ce-eec0929230f2"),
    ("Viking/Environment/IceChunk 01", "79509a07-7434-4575-9732-a32a8396c360"),
    ("Viking/Environment/Rock 01", "5f585b6d-a73d-485a-9595-e54e1d6a8ebe"),
    ("Viking/Environment/Grass 01", "094306a3-a8b1-4bbe-928f-d7694bf3664c"),
    ("Viking/Environment/SnowPile 01", "8add8bc8-dae2-48f3-9661-911d146d860f"),
)


@pytest.fixture(scope="module")
def e2e_scene(tmp_path_factory) -> Path:
    configured = os.environ.get(E2E_SCENE_ENV)
    if not configured:
        pytest.skip(f"set {E2E_SCENE_ENV} for optional real-Mod end-to-end tests")
    candidate = Path(configured)
    if not candidate.is_file():
        pytest.skip(
            "no workshop mod scene is available; set "
            f"{E2E_SCENE_ENV} to run the built-in prop end-to-end test"
        )
    source_mod = candidate.resolve().parents[1]
    copied_mod = tmp_path_factory.mktemp("builtin-prop-e2e") / "Mod"
    shutil.copytree(source_mod, copied_mod, ignore=shutil.ignore_patterns("*.bak*"))
    return copied_mod / candidate.relative_to(candidate.parents[1])


@pytest.fixture(scope="module")
def e2e_mod_root(e2e_scene: Path) -> Path:
    root = e2e_scene.parents[1]
    assert (root / "Assets").is_dir(), f"{root} does not look like a mod root"
    return root


@pytest.fixture(scope="module")
def e2e_server(e2e_scene: Path, e2e_mod_root: Path, editor_asset_root: Path):
    return create_server(e2e_mod_root, editor_asset_root=editor_asset_root)


@pytest.fixture(scope="module")
def e2e_restore_scene(e2e_scene: Path) -> bool:
    """Write the pre-test bytes back afterwards only when explicitly requested."""
    requested = os.environ.get(E2E_RESTORE_ENV, "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }
    if not requested:
        yield False
        return
    original = e2e_scene.read_bytes()
    yield True
    e2e_scene.write_bytes(original)


def _call(server, name: str, arguments: dict):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def _structured(server, name: str, arguments: dict) -> dict:
    result = _call(server, name, arguments)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _raw(game_object, component_type: str, field_name: str) -> bytes:
    return game_object.get_component(component_type).get_field(field_name).raw


def test_editor_asset_tools_see_the_shipped_registry(e2e_server) -> None:
    summary = _structured(e2e_server, "list_editor_assets", {"limit": 1})["catalog"]
    assert summary["asset_count"] > 16000
    assert summary["spawnable_count"] > 15000
    assert summary["writable"] is False

    described = _structured(
        e2e_server, "get_editor_asset", {"asset": "Viking/Environment/Tree Pine 01"}
    )
    assert described["match_count"] == 1
    assert described["resolvable_for_spawn"] is True
    reference = described["assets"][0]["prop_reference"]
    assert reference["byte_length"] == 38
    assert reference["prefix_hex"] == "0124"
    assert reference["verification"] == "REGISTERED_INTERNAL_PROP"


def test_spawn_builtin_environment_assets_into_the_teat_scene(
    e2e_scene: Path, e2e_server, e2e_restore_scene: bool
) -> None:
    before = read_pmh(e2e_scene)
    before_bytes = e2e_scene.read_bytes()
    before_objects = {
        obj.guid: (obj.name, obj.hierarchy_path, tuple(c.type_name for c in obj.components))
        for obj in before.walk()
    }
    before_object_guids = set(before_objects)
    before_component_guids = {
        component.guid for obj in before.walk() for component in obj.components
    }

    created: list[tuple[str, str, dict]] = []
    for index, (asset, expected_guid) in enumerate(ENVIRONMENT_ASSETS):
        report = _structured(
            e2e_server,
            "spawn_builtin_prop",
            {
                "scene_path": str(e2e_scene),
                "asset": asset,
                "position": {"x": float(index * 4), "y": 0.0, "z": -8.0},
                "scale": {"x": 1.5, "y": 1.5, "z": 1.5},
                "tint_color": {"r": 0.8, "g": 0.9, "b": 1.0, "a": 1.0},
                "collision_type": "mesh",
                "shadow_casting_mode": "on",
            },
        )
        created.append((asset, expected_guid, report))

    after = read_pmh(e2e_scene)

    # The scene still parses to exact EOF and passes structural validation.
    assert after.fully_consumed is True
    assert after.remaining_bytes == 0
    assert validate_scene(after).passed is True
    assert after.magic == before.magic and after.version == before.version
    assert after.root_count == before.root_count
    assert after.object_count == before.object_count + len(created)
    assert after.component_count == before.component_count + 2 * len(created)

    objects_by_guid = {obj.guid: obj for obj in after.walk()}
    new_component_guids: set[str] = set()

    for index, (asset, expected_guid, report) in enumerate(created):
        assert report["validation"]["passed"] is True
        assert not [
            name for name, ok in report["validation"]["checks"].items() if not ok
        ]
        assert report["safety_class"] == "SAFE_REGISTERED_BUILTIN_PROP_SPAWN"
        assert report["dry_run"] is False
        assert report["backup_path"] is not None

        # The reference came from the scanned registry, not from the caller.
        assert report["asset"]["asset_id"] == f"Assets/{asset}"
        assert report["asset"]["guid"] == expected_guid
        assert report["asset"]["verification"] == "REGISTERED_INTERNAL_PROP"
        assert report["asset"]["reference_byte_length"] == 38

        object_guid = report["created"]["gameobject_guid"]
        prop_guid = report["created"]["prop_guid"]
        expected_prop_payload = b"\x01\x24" + expected_guid.encode("ascii")
        assert report["asset"]["reference_payload_sha256"] == __import__(
            "hashlib"
        ).sha256(expected_prop_payload).hexdigest()

        # Fresh, non-colliding v4 identities.
        assert object_guid not in before_object_guids
        assert prop_guid not in before_component_guids
        assert object_guid not in new_component_guids
        assert prop_guid not in new_component_guids
        assert uuid.UUID(object_guid).version == 4
        assert uuid.UUID(prop_guid).version == 4
        assert object_guid == object_guid.lower() and len(object_guid) == 36
        new_component_guids.update({object_guid, prop_guid})

        # The object exists exactly once with the approved component shape.
        created_object = objects_by_guid[object_guid]
        assert [
            obj.guid for obj in after.walk() if obj.guid == object_guid
        ] == [object_guid]
        assert [c.type_name for c in created_object.components] == [
            "ModTransform",
            "ModProp",
        ]
        assert created_object.guid == created_object.components[0].guid
        assert created_object.components[1].guid == prop_guid
        assert created_object.parent is not None

        # Hierarchy and destination match the report.
        name = asset.rsplit("/", 1)[-1]
        assert created_object.name == name
        assert created_object.hierarchy_path == report["created"]["hierarchy_path"]
        assert created_object.hierarchy_path.endswith(f"/{name}")
        assert created_object.parent.guid == report["destination"]["parent_guid"]
        assert created_object.sibling_index == report["destination"]["sibling_index"]

        # ModTransform holds the requested values, bit for bit.
        assert _raw(created_object, "ModTransform", "position") == struct.pack(
            "<3f", float(index * 4), 0.0, -8.0
        )
        assert _raw(created_object, "ModTransform", "rotation") == struct.pack(
            "<3f", 0.0, 0.0, 0.0
        )
        assert _raw(created_object, "ModTransform", "scale") == struct.pack(
            "<3f", 1.5, 1.5, 1.5
        )

        # ModProp carries the registry reference and the requested appearance.
        assert _raw(created_object, "ModProp", "prop") == expected_prop_payload
        assert _raw(created_object, "ModProp", "tintColor") == struct.pack(
            "<4f", 0.8, 0.9, 1.0, 1.0
        )
        assert _raw(created_object, "ModProp", "collisionType") == struct.pack("<i", 3)
        assert _raw(created_object, "ModProp", "shadowCastingMode") == struct.pack("<i", 1)
        assert _raw(created_object, "ModProp", "customMaterials") == struct.pack("<I", 0)
        assert report["mod_prop"]["custom_materials"] == "EMPTY"
        assert (
            _raw(created_object, "ModProp", "guid")[1:].decode("ascii") == prop_guid
        )

    # Every pre-existing object is untouched, and the file only grew.
    for guid, snapshot in before_objects.items():
        assert guid in objects_by_guid
        obj = objects_by_guid[guid]
        assert (
            obj.name,
            obj.hierarchy_path,
            tuple(c.type_name for c in obj.components),
        ) == snapshot
    assert len(e2e_scene.read_bytes()) > len(before_bytes)

    # Re-resolving the same assets returns the identical reference.
    again = _structured(
        e2e_server,
        "get_editor_asset",
        {"asset": ENVIRONMENT_ASSETS[0][0]},
    )
    assert again["assets"][0]["guid"] == ENVIRONMENT_ASSETS[0][1]

    # The ModProp objects we created are exactly the ones the scene now carries.
    prop_guids = Counter(
        obj.get_component("ModProp").guid
        for obj in after.walk()
        if any(c.type_name == "ModProp" for c in obj.components)
    )
    for _asset, _guid, report in created:
        assert prop_guids[report["created"]["prop_guid"]] == 1


def test_dry_run_on_the_real_scene_does_not_write(e2e_scene: Path, e2e_server) -> None:
    import hashlib

    before = hashlib.sha256(e2e_scene.read_bytes()).hexdigest()
    report = _structured(
        e2e_server,
        "spawn_builtin_prop",
        {
            "scene_path": str(e2e_scene),
            "asset": "Viking/Environment/Glacier 02",
            "dry_run": True,
        },
    )
    assert report["dry_run"] is True
    assert report["validation"]["passed"] is True
    assert report["backup_path"] is None
    assert hashlib.sha256(e2e_scene.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "asset, expected",
    [
        ("Definitely Not Registered 99", "EDITOR_ASSET_NOT_REGISTERED"),
        ("Rock 01", "AMBIGUOUS_EDITOR_ASSET"),
        ("Prepared/../../etc/passwd", "EDITOR_ASSET_IDENTIFIER_INVALID"),
    ],
)
def test_spawn_rejects_unregistered_ambiguous_and_uncontrolled_assets(
    e2e_scene: Path, e2e_server, asset: str, expected: str
) -> None:
    import hashlib

    before = hashlib.sha256(e2e_scene.read_bytes()).hexdigest()
    with pytest.raises(ToolError, match=expected):
        _call(
            e2e_server,
            "spawn_builtin_prop",
            {"scene_path": str(e2e_scene), "asset": asset},
        )
    assert hashlib.sha256(e2e_scene.read_bytes()).hexdigest() == before


def test_spawn_rejects_a_mod_local_asset_as_cross_library(
    e2e_scene: Path, e2e_server, e2e_mod_root: Path
) -> None:
    """A mod's own .pmeta asset is a different library and must be named as such."""
    import hashlib

    local_assets = sorted(
        path.name[: -len(".pfab.pmeta")]
        for path in (e2e_mod_root / "Assets").rglob("*.pfab.pmeta")
    )
    if not local_assets:
        pytest.skip("this mod ships no local .pfab metadata to test against")

    before = hashlib.sha256(e2e_scene.read_bytes()).hexdigest()
    with pytest.raises(ToolError, match="EDITOR_ASSET_NOT_INTERNAL"):
        _call(
            e2e_server,
            "spawn_builtin_prop",
            {"scene_path": str(e2e_scene), "asset": local_assets[0]},
        )
    assert hashlib.sha256(e2e_scene.read_bytes()).hexdigest() == before


def test_spawn_enforces_the_allowed_root(e2e_scene: Path, e2e_server, tmp_path: Path) -> None:
    outside = tmp_path / "Outside.scene"
    outside.write_bytes(e2e_scene.read_bytes())
    with pytest.raises(ToolError, match="outside PUMMELMCP_ALLOWED_ROOT"):
        _call(
            e2e_server,
            "spawn_builtin_prop",
            {"scene_path": str(outside), "asset": "Viking/Environment/Rock 01"},
        )
