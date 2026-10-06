from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "MainScene.scene"
EXPECTED_FIXTURE_SHA256 = (
    "a0af6631df7e99d4f98d964122eca56ab66cb8beaad53d7217bc54efff89cde8"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EDITOR_ASSET_ROOT_ENV = "PUMMELMCP_EDITOR_ASSET_ROOT"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def main_scene_path() -> Path:
    if not FIXTURE_PATH.is_file():
        pytest.fail(
            "required real-world fixture is missing: tests/fixtures/MainScene.scene",
            pytrace=False,
        )
    assert _digest(FIXTURE_PATH) == EXPECTED_FIXTURE_SHA256, (
        "MainScene.scene does not match the approved Joker 21 regression fixture"
    )
    return FIXTURE_PATH


@pytest.fixture(scope="session", autouse=True)
def preserve_main_scene_fixture(request: pytest.FixtureRequest) -> None:
    """Fail the test session if any test changes the original sample bytes."""
    if not FIXTURE_PATH.is_file():
        return
    original = _digest(FIXTURE_PATH)

    def verify_unchanged() -> None:
        assert FIXTURE_PATH.is_file(), "MainScene.scene fixture was removed"
        assert _digest(FIXTURE_PATH) == original, "MainScene.scene fixture was modified"

    request.addfinalizer(verify_unchanged)


@pytest.fixture(scope="session")
def editor_asset_root() -> Path:
    """The shipped StreamingAssets copy that carries the Asset Browser registry."""
    configured = os.environ.get(EDITOR_ASSET_ROOT_ENV)
    candidate = Path(configured) if configured else REPO_ROOT / "StreamingAssets"
    if not (candidate / "aa").is_dir():
        pytest.skip(
            "no StreamingAssets/aa build is available; set "
            f"{EDITOR_ASSET_ROOT_ENV} to run the editor asset tests"
        )
    return candidate


@pytest.fixture(scope="session")
def editor_asset_catalog(editor_asset_root: Path):
    from pummelmcp.pmh import load_editor_asset_catalog

    return load_editor_asset_catalog(editor_asset_root)
