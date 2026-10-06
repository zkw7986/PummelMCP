"""First-creation review and authorized, in-place delivered-Mod iteration."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any

ASSET_SOURCING_POLICY = (
    "MANDATORY PROACTIVE ASSET SOURCING: During minigame planning and production, "
    "actively search online for suitable assets when network/search tools are available; "
    "do not wait for the user to supply every asset. Consider sound effects, background "
    "music/BGM, OBJ models, textures and materials according to gameplay, visual style "
    "and performance needs. Inspect existing Mod and built-in assets to avoid unnecessary "
    "duplicates. Prefer assets with clear reuse and redistribution permission, such as "
    "CC0 or appropriately attributed CC-BY; respect the user's constraints and do not "
    "purchase assets without explicit authorization. Record each selected asset's source "
    "URL, creator, license, attribution and modifications in the asset plan and the Mod's "
    "CREDITS or asset manifest. Verify actual file format, texture/material dependencies, "
    "model scale/complexity and audio compatibility before importing through supported "
    "tools into authorized directories. Convert unsuitable formats with available tools "
    "and validate the resulting files. If online access, licensing or conversion is "
    "unavailable, report the limitation and use a suitable built-in asset or clearly "
    "identified placeholder; never invent downloads, source URLs or license claims. "
)

class GameWorkflow:
    def __init__(self):
        self.root: Path | None = None
        self.delivered: Path | None = None
        self.pending: dict[str, Any] | None = None
        self.approved: str | None = None

    def authorize(self, root: Path | None):
        if root != self.root:
            self.delivered = None
            self.pending = None
            self.approved = None
        self.root = root

    def target(self, value: str) -> Path:
        if self.root is None:
            raise ValueError('WORKSHOP_AUTHORIZATION_REQUIRED')
        raw = Path(value).expanduser()
        if not raw.is_absolute():
            raw = self.root / raw
        resolved = raw.resolve(strict=False)
        if resolved.parent != self.root or resolved == self.root:
            raise ValueError('GAME_DIRECTORY_MUST_BE_IMMEDIATE_WORKSHOP_CHILD')
        # Reject links and junctions, including links resolving within the root.
        if raw.is_symlink() or raw.is_dir() and getattr(raw, 'is_junction', lambda: False)():
            raise ValueError('REDIRECTED_GAME_DIRECTORY_REJECTED')
        return resolved

    @staticmethod
    def is_initialized_mod(target: Path) -> bool:
        """An existing native Mod can be resumed after a server restart."""
        scene = target / 'Data' / 'MainScene.scene'
        if not scene.is_file():
            return False
        for name in ('Meta.json', 'ModSettings.json'):
            try:
                document = json.loads((target / 'Data' / name).read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                return False
            if not isinstance(document, dict):
                return False
        return True

    def submit(self, game_directory: str, requested_rules: list[str],
               capability_assessment: list[dict[str, Any]], gameplay_plan: dict[str, Any],
               user_requested_separate_game: bool = False) -> dict[str, Any]:
        target = self.target(game_directory)
        if self.delivered is not None and target != self.delivered and not user_requested_separate_game:
            raise ValueError('ITERATE_EXISTING_DELIVERED_MOD: submit changes for ' + str(self.delivered))
        if not requested_rules or any(not isinstance(r, str) or not r.strip() for r in requested_rules):
            raise ValueError('REQUESTED_RULES_REQUIRED')
        if len(capability_assessment) != len(requested_rules):
            raise ValueError('ASSESS_EVERY_REQUESTED_RULE')
        for index, entry in enumerate(capability_assessment):
            if not isinstance(entry, dict) or entry.get('requested_rule') != requested_rules[index]:
                raise ValueError('CAPABILITY_ASSESSMENT_RULE_MISMATCH')
            if entry.get('status') not in ('supported', 'equivalent', 'unsupported'):
                raise ValueError('INVALID_CAPABILITY_STATUS')
            for key in ('editor_evidence', 'implementation_or_boundary'):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    raise ValueError('EDITOR_CAPABILITY_EVIDENCE_AND_BOUNDARY_REQUIRED')
        for key in ('flow', 'rules_and_parameters', 'scene_and_assets', 'limitations', 'playtest_checks'):
            if not gameplay_plan.get(key):
                raise ValueError('DETAILED_GAMEPLAY_PLAN_REQUIRED: ' + key)
        proposal = {'game_directory': str(target), 'mode': 'iterate_existing' if target.exists() else 'create_new',
                    'requested_rules': requested_rules, 'capability_assessment': capability_assessment,
                    'gameplay_plan': gameplay_plan, 'user_requested_separate_game': user_requested_separate_game}
        digest = hashlib.sha256(json.dumps(proposal, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        # Initial creation still requires direct approval of the exact proposal.
        # An optional iteration plan must not suspend writes to an existing Mod.
        self.pending = json.loads(json.dumps(proposal))
        self.pending['review_sha256'] = digest
        self.approved = None
        iteration = ((self.delivered == target and target.is_dir()) or
                     (self.delivered is None and self.is_initialized_mod(target)))
        return {**self.pending, 'requires_user_review': not iteration,
                'status': 'ITERATION_READY' if iteration else 'AWAITING_USER_REVIEW',
                'next_step': ('Continue requested in-place edits to the existing Mod; another human gameplay review is not required.' if iteration else
                             'Show this exact capability assessment and detailed plan to the user. Wait for their explicit approval before first creation.')}

    def approve(self, review_sha256: str, user_approved: bool, user_reply: str) -> dict[str, Any]:
        if self.pending is None or review_sha256 != self.pending['review_sha256']:
            raise ValueError('STALE_OR_UNKNOWN_GAMEPLAY_REVIEW')
        if user_approved is not True:
            self.approved = None
            return {'status': 'AWAITING_USER_REVIEW', 'approved': False}
        if not user_reply.strip():
            raise ValueError('DIRECT_USER_APPROVAL_REPLY_REQUIRED')
        self.approved = review_sha256
        return {'status': 'USER_REVIEW_APPROVED', 'approved': True, 'review_sha256': review_sha256,
                'game_directory': self.pending['game_directory'], 'scope': 'Only the exact reviewed plan, target directory, and current MCP session.'}

    def require_write(self, target: Path, *, creation: bool = False):
        target = self.target(str(target))
        if creation and target.exists():
            raise ValueError('ITERATE_EXISTING_DELIVERED_MOD: use existing-scene writers; do not regenerate or replace this Mod')
        if not creation:
            if self.delivered == target and target.is_dir():
                return
            if self.delivered is None and self.is_initialized_mod(target):
                self.delivered = target
                return
        if self.delivered is not None and target != self.delivered and not (
                self.pending and self.pending['user_requested_separate_game']):
            raise ValueError('ITERATE_EXISTING_DELIVERED_MOD')
        if self.pending is None or self.approved != self.pending['review_sha256']:
            raise ValueError('USER_GAMEPLAY_REVIEW_REQUIRED: first game creation requires a complete capability-bounded plan and direct user approval')
        if target != Path(self.pending['game_directory']):
            raise ValueError('REVIEW_TARGET_MISMATCH: edit the reviewed game directory only')
        if self.delivered is not None and target != self.delivered and not self.pending['user_requested_separate_game']:
            raise ValueError('ITERATE_EXISTING_DELIVERED_MOD')

    def wrote(self, target: Path):
        if target.is_dir():
            self.delivered = target

    def status(self):
        return {'workshop_directory': str(self.root) if self.root else None,
                'asset_sourcing_policy': ASSET_SOURCING_POLICY,
                'delivered_game_directory': str(self.delivered) if self.delivered else None,
                'pending_review': self.pending, 'user_review_approved': self.approved is not None,
                'review_policy': 'Direct human review is required for first creation of a game; requested edits to the existing delivered Mod do not require repeated gameplay review.',
                'iteration_policy': 'Edit the delivered folder in place, preserving current saved edits and publishing identity. No regenerated replacement game unless the user explicitly requests a separate game.'}
