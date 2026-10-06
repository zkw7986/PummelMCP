"""One-call, parameterized Joker-style arena recipe over the authoring compiler."""
from __future__ import annotations
import math
from .authoring import Donor, build_authoring, plan_authoring, fail, keys


def card_arena_spec(donor: Donor, options: dict) -> dict:
    keys(options, {"output_mod", "title"}, {"players", "seconds", "rounds", "scores", "joker_weight"})
    players, seconds, rounds = options.get("players", 8), options.get("seconds", 90), options.get("rounds", 5)
    scores, weight = options.get("scores", list(range(1, 12))), options.get("joker_weight", 2)
    if type(players) is not int or not 2 <= players <= 8: fail("PLAYERS_MUST_BE_2_TO_8")
    if type(seconds) is not int or not 10 <= seconds <= 1200: fail("SECONDS_MUST_BE_10_TO_1200")
    if type(rounds) is not int or not 1 <= rounds <= 20: fail("ROUNDS_MUST_BE_1_TO_20")
    if not isinstance(scores, list) or not 1 <= len(scores) <= 32 or any(type(x) is not int or not 1 <= x <= 1000 for x in scores): fail("INVALID_CARD_SCORES")
    if type(weight) is not int or not 1 <= weight <= 32: fail("INVALID_JOKER_WEIGHT")
    scene_file = "Data/MainScene.scene"
    def find(kind, file=scene_file, name=None):
        if file not in donor.models: fail("CARD_RECIPE_DONOR_MISSING: " + file)
        candidates = [o for o in donor.models[file].walk() if any(c.type_name == kind for c in o.components) and (name is None or o.name == name)]
        if not candidates: fail("CARD_RECIPE_COMPONENT_MISSING: " + kind)
        return {"file": file, "object": candidates[0].guid}
    def component(kind, **extra):
        return {"type": kind, "donor": find(kind), **extra}
    def v(x=0, y=0, z=0): return {"x": x, "y": y, "z": z}
    def action(kind, **fields): return {"type": kind, "target": "source", "fields": fields}
    cube = find("ModProp", name="LowPolyCube")
    visual = find("ModProp", "Assets/Prefabs/CardVisual_01.pfab")
    joker_visual = find("ModProp", "Assets/Prefabs/CardVisual_joker).pfab")
    item = find("ModItem", "Assets/Prefabs/Result_Card_01.pfab")
    prefabs = []
    for name, source in (("CardVisual", visual), ("JokerVisual", joker_visual)):
        prefabs.append({"id": name, "nodes": [{"id": "Visual", "scale": v(.8, 1.6, .001),
            "components": [{"type": "ModProp", "donor": source}]}]})
    pool = []
    for index, score in enumerate(scores):
        name = f"Card_{index + 1}"
        pool.append(name)
        prefabs.append({"id": name, "nodes": [{"id": "Item", "components": [{"type": "ModItem", "donor": item,
            "item_visual": "CardVisual", "events": {"OnPickupTrigger": [
                action("ChangeScoreAction", m_operation=1, m_value=score),
                action("ShowMessageAction", m_message=f"+{score}", m_messageTarget=2, m_duration=1.0)]}}]}]})
    prefabs.append({"id": "Joker", "nodes": [{"id": "Item", "components": [{"type": "ModItem", "donor": item,
        "item_visual": "JokerVisual", "events": {"OnPickupTrigger": [
            action("ChangeScoreAction", m_operation=0, m_value=0),
            action("SpawnEffectAction", m_effectType=2),
            action("ShowMessageAction", m_message="JOKER! Score reset", m_messageTarget=2, m_duration=2.0),
            action("KillAction")]}}]}]})
    pool += ["Joker"] * weight
    nodes = [{"id": "Arena", "components": []}]
    def box(name, position, size, color):
        nodes.append({"id": name, "parent": "Arena", "position": position, "scale": size, "components": [
            {"type": "ModProp", "donor": cube, "properties": {"tintColor": color, "collisionType": 0}},
            component("ModBoxCollider", properties={"center": v(), "size": v(1, 1, 1)})]})
    green = {"r": .06, "g": .22, "b": .14, "a": 1}
    gold = {"r": .8, "g": .5, "b": .12, "a": 1}
    box("Floor", v(0, -.5, 0), v(24, 1, 24), green)
    for name, position, size in (("North", v(0, 1, 12), v(24, 2, .5)), ("South", v(0, 1, -12), v(24, 2, .5)),
                                 ("East", v(12, 1, 0), v(.5, 2, 24)), ("West", v(-12, 1, 0), v(.5, 2, 24))):
        box(name, position, size, gold)
    for index in range(players):
        angle = 2 * math.pi * index / players
        x, z = math.sin(angle), math.cos(angle)
        nodes.append({"id": f"Spawn_{index}", "parent": "Arena", "position": v(x * 8, .5, z * 8), "rotation": v(0, 180 + 360 * index / players, 0),
            "components": [component("ModPlayerSpawn", properties={"AllowedPlayers": 1 << index, "PlayerCountMask": 255})]})
        button = f"Button_{index}"
        nodes.append({"id": button, "parent": "Arena", "position": v(x * 6, 1, z * 6),
            "components": [component("ModTrigger", properties={"Size": v(1.2, 1.2, 1.2), "Center": v(), "TriggerShape": 1,
                "DisableAfterTriggered": False, "OneUsePerPlayer": False}, events={"OnHitActions": [{"type": "SpawnPrefabAction", "target": "source",
                    "prefabs": pool, "fields": {"m_spawnAtPosition": False, "m_parentToTarget": False,
                        "m_targetPositionOffset": v(0, 0, .5), "m_targetRotationOffset": v()}}]})]})
        nodes.append({"id": f"ButtonVisual_{index}", "parent": button, "scale": v(.8, .8, .8),
            "components": [{"type": "ModProp", "donor": cube, "properties": {"tintColor": gold, "collisionType": 0}}]})
    nodes.append({"id": "Light", "parent": "Arena", "position": v(0, 8, 0), "components": [component("ModLight", properties={"range": 40, "intensity": 2})]})
    nodes.append({"id": "RulesSign", "parent": "Arena", "position": v(0, 3, -10),
        "components": [component("ModText", properties={"Text": "HIT GOLD BUTTONS\nCOLLECT CARDS FOR POINTS\nJOKER = RESET + ELIMINATION",
            "FontSize": 8, "Size": {"x": 16, "y": 4}, "Color": {"r": 1, "g": .85, "b": .35, "a": 1}})]})
    description = f"Hit the gold buttons to draw cards. Collect cards for points; Joker resets your score and eliminates you. {seconds}s per round, highest score wins."
    return {"version": "0.3", "output_mod": options["output_mod"], "title": options["title"], "description": description,
            "min_players": 2, "max_players": players, "settings": {"rounds": rounds, "round_duration_seconds": seconds,
                "end_conditions": ["timer", "remaining_players_alive"], "placement_condition": "most_points", "respawn_enabled": False,
                "players_alive_to_end": 1, "can_punch": True, "can_jump": True},
            "player_events": {"weaponHitTrigger": [{"type": "ChangeVelocityAction", "target": "receiver", "fields": {"m_speed": 20}}]},
            "scene": nodes, "prefabs": prefabs}


def generate_card_arena(archive_path, read_roots, output_root, options, *, dry_run=False,
                        allow_output_in_read_roots=False):
    donor = Donor(archive_path, read_roots)
    spec = card_arena_spec(donor, options)
    plan, _ = plan_authoring(archive_path, read_roots, output_root, spec,
                             allow_output_in_read_roots=allow_output_in_read_roots)
    if dry_run: return {"plan": plan, "spec": spec}
    report = build_authoring(archive_path, read_roots, output_root, spec, plan["plan_sha256"],
                             allow_output_in_read_roots=allow_output_in_read_roots)
    return {"report": report, "spec": spec}
