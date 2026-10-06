"""Bundled complete Mod templates, independent of local game installation."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).with_name("templates")


def list_mod_templates() -> dict:
    catalog = json.loads((TEMPLATE_ROOT / "catalog.json").read_text(encoding="utf-8"))
    return {"templates": [{k: v for k, v in entry.items() if k != "files"}
                          for entry in catalog["templates"]],
            "workflow": "First record the user's WorkshopMods path and write permission. Inspect actual editor capabilities, assess every requested rule, submit a detailed gameplay review and show it to the user. Wait for explicit user approval, then record it with approve_gameplay_review before initialization or writes. After first creation, continue editing the returned original Mod folder in place; never regenerate another version. Initialization does not prove runtime playability."}


def initialize_mod_from_template(template_id: str, destination: str, *, allowed_root: Path) -> dict:
    catalog = json.loads((TEMPLATE_ROOT / "catalog.json").read_text(encoding="utf-8"))
    entry = next((e for e in catalog["templates"] if e["id"] == template_id), None)
    if entry is None:
        raise ValueError("Unknown template_id; use list_mod_templates")
    root = allowed_root.resolve(strict=True)
    target = Path(destination).expanduser()
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("destination must be a new directory inside the authorized WorkshopMods folder")
    if target.exists():
        raise ValueError("destination already exists; existing Mods are never overwritten")
    source = TEMPLATE_ROOT / entry["id"]
    for relative, expected in entry["files"].items():
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Bundled template integrity check failed: " + relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pummelmcp-init-", dir=target.parent) as staging:
        staged = Path(staging) / "mod"
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns(".gitkeep"))
        (staged / "Assets").mkdir(exist_ok=True)
        # Assets and scene GUIDs stay intact; publishing identity belongs to the new Mod.
        for filename, key in (("Meta.json", "Name"), ("WorkshopItem.json", "title"),
                              ("MinigameDefinitionData.json", "MinigameName")):
            path = staged / "Data" / filename
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            data[key] = target.name
            if filename == "WorkshopItem.json":
                data["publishedFileId"] = 0
            path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        # mkdir is exclusive, including when another request races initialization.
        target.mkdir()
        try:
            shutil.copytree(staged, target, dirs_exist_ok=True)
        except Exception:
            shutil.rmtree(target)
            raise
    return {"template_id": template_id, "mod_path": str(target),
            "scene_path": str(target / "Data" / "MainScene.scene"),
            "next_step": "Edit this copy using the scene tools. This new Mod is already inside the user's authorized WorkshopMods folder."}
