"""Command-line interface: a thin application layer over the PMH core API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence, TextIO

from .pmh import (
    AmbiguousObjectError,
    ConcurrentModificationError,
    ObjectNotFoundError,
    PMHError,
    PMHFormatError,
    PMHWriterError,
    TransformNotFoundError,
    TransformWriteError,
    WriterValidationError,
)
from .pmh.action_research import diff_action
from .service import (
    get_object_details,
    get_scene_summary,
    get_transform_details,
    list_scene_objects,
    set_scene_transform,
    validate_scene_file,
)


EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_READ_OR_LOOKUP = 3
EXIT_WRITE_OR_SAFETY = 4


class CLIUsageError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit JSON only")


def _add_scene(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("scene", type=Path, help="PMH .scene/.pfab file")


def _add_identifier(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "identifier", help="object GUID, /hierarchy/path, or unique name"
    )


def _add_axes(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--x", type=float)
    parser.add_argument("--y", type=float)
    parser.add_argument("--z", type=float)
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="pummelmcp", description="Inspect and safely patch PMH v1 scenes")
    parser.add_argument("--debug", action="store_true", help="show Python tracebacks")
    commands = parser.add_subparsers(dest="command", required=True)

    summary = commands.add_parser("summary", help="show scene summary")
    _add_scene(summary)
    _add_json_option(summary)
    summary.set_defaults(handler=_command_summary)

    objects = commands.add_parser("objects", help="list scene objects")
    _add_scene(objects)
    objects.add_argument("--query", help="case-insensitive name/path filter")
    _add_json_option(objects)
    objects.set_defaults(handler=_command_objects)

    obj = commands.add_parser("object", help="show one object")
    _add_scene(obj)
    _add_identifier(obj)
    _add_json_option(obj)
    obj.set_defaults(handler=_command_object)

    transform = commands.add_parser("transform", help="show one ModTransform")
    _add_scene(transform)
    _add_identifier(transform)
    _add_json_option(transform)
    transform.set_defaults(handler=_command_transform)

    for command, property_name in (
        ("set-position", "position"),
        ("set-rotation", "rotation"),
        ("set-scale", "scale"),
    ):
        mutation = commands.add_parser(command, help=f"patch ModTransform.{property_name}")
        _add_scene(mutation)
        _add_identifier(mutation)
        _add_axes(mutation)
        _add_json_option(mutation)
        mutation.set_defaults(handler=_command_mutation, property_name=property_name)

    validate = commands.add_parser("validate", help="validate a PMH scene")
    _add_scene(validate)
    _add_json_option(validate)
    validate.set_defaults(handler=_command_validate)

    research = commands.add_parser("research", help="read-only format research")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    diff = research_commands.add_parser(
        "diff-action", help="compare one Action between before/after scenes"
    )
    diff.add_argument("before_scene", type=Path)
    diff.add_argument("after_scene", type=Path)
    diff.add_argument("object_identifier")
    diff.add_argument("component_identifier")
    diff.add_argument("event_property")
    selector = diff.add_mutually_exclusive_group(required=True)
    selector.add_argument("--rid", type=int, dest="action_rid")
    selector.add_argument("--index", type=int, dest="action_index")
    _add_json_option(diff)
    diff.set_defaults(handler=_command_diff_action)
    return parser


def _cli_scene_summary(scene_path: Path) -> dict[str, object]:
    summary = get_scene_summary(scene_path)
    return {
        "magic": summary["magic"],
        "version": summary["version"],
        "root_count": summary["roots"],
        "object_count": summary["game_objects"],
        "component_count": summary["components"],
        "file_size": summary["file_size"],
        "fully_consumed": summary["fully_consumed"],
        "valid": summary["valid"],
    }


def _command_summary(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    data = _cli_scene_summary(args.scene)
    human = "\n".join(
        (
            f"Magic: {data['magic']}",
            f"Version: {data['version']}",
            f"Roots: {data['root_count']}",
            f"GameObjects: {data['object_count']}",
            f"Components: {data['component_count']}",
            f"File size: {data['file_size']} bytes",
            f"Fully consumed: {_yes_no(bool(data['fully_consumed']))}",
            f"Valid: {'PASS' if data['valid'] else 'FAIL'}",
        )
    )
    return data, human


def _command_objects(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    result = list_scene_objects(args.scene, query=args.query, limit=None)
    items = result["objects"]
    data = {"count": len(items), "query": args.query, "objects": items}
    human = "\n".join(
        f"{item['path']}\n  Name: {item['name']}\n  GUID: {item['guid']}"
        for item in items
    )
    return data, human or "No matching objects."


def _command_object(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    data = get_object_details(args.scene, args.identifier)
    parent = data["parent"]["name"] if data["parent"] else "<root>"
    children = ", ".join(child["name"] for child in data["children"]) or "<none>"
    components = "\n".join(
        f"  {component['type']} ({component['guid']}) enabled={component['enabled']}"
        for component in data["components"]
    ) or "  <none>"
    human = "\n".join(
        (
            f"Name: {data['name']}",
            f"GUID: {data['guid']}",
            f"Path: {data['path']}",
            f"Active: {data['active']}",
            f"Layer: {data['layer']}",
            f"Tag: {data['tag']}",
            f"Parent: {parent}",
            f"Children: {children}",
            "Components:",
            components,
        )
    )
    return data, human


def _format_vector(label: str, value: dict[str, float]) -> str:
    return "\n".join(
        (
            label,
            f"  X: {value['x']:.9g}",
            f"  Y: {value['y']:.9g}",
            f"  Z: {value['z']:.9g}",
        )
    )


def _command_transform(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    details = get_transform_details(args.scene, args.identifier)
    obj = details["object"]
    position = details["position"]
    rotation = details["rotation"]
    scale = details["scale"]
    data = {
        "object": obj["name"],
        "object_guid": obj["guid"],
        "transform_guid": details["transform_guid"],
        "position": position,
        "rotation": rotation,
        "scale": scale,
    }
    human = "\n\n".join(
        (
            f"Object: {obj['name']}\nObject GUID: {obj['guid']}\nModTransform GUID: {details['transform_guid']}",
            _format_vector("Position", position),
            _format_vector("Rotation", rotation),
            _format_vector("Scale", scale),
        )
    )
    return data, human


def _command_mutation(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    if args.x is None and args.y is None and args.z is None:
        raise CLIUsageError("at least one of --x, --y, or --z is required")
    updates = {"x": args.x, "y": args.y, "z": args.z}
    properties = {
        "position": None,
        "rotation": None,
        "scale": None,
    }
    properties[args.property_name] = updates
    report = set_scene_transform(
        args.scene,
        args.identifier,
        position=properties["position"],
        rotation=properties["rotation"],
        scale=properties["scale"],
        dry_run=args.dry_run,
    )
    data = report.to_dict()
    changes = "\n".join(
        f"  {axis}: {change.before:.9g} -> {change.after:.9g}"
        for axis, change in report.changes.items()
    )
    human = "\n".join(
        (
            f"Object: {report.object_name}",
            f"Property: {report.property}",
            "Changes:",
            changes,
            f"Backup: {report.backup_path or '<none>'}",
            f"Validation: {'PASS' if report.validation.passed else 'FAIL'}",
            f"Dry run: {str(report.dry_run).lower()}",
            f"Bytes changed: {report.bytes_changed}",
        )
    )
    return data, human


def _command_validate(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    validation = validate_scene_file(args.scene)
    data = {
        "valid": validation["valid"],
        "checks": validation["checks"],
        "summary": _cli_scene_summary(args.scene),
    }
    checks = "\n".join(
        f"  {name}: {'PASS' if passed else 'FAIL'}"
        for name, passed in validation["checks"].items()
    )
    human = f"Validation: {'PASS' if validation['valid'] else 'FAIL'}\n{checks}"
    return data, human


def _command_diff_action(args: argparse.Namespace) -> tuple[dict[str, object], str]:
    data = diff_action(
        args.before_scene,
        args.after_scene,
        args.object_identifier,
        args.component_identifier,
        args.event_property,
        action_rid=args.action_rid,
        action_index=args.action_index,
    )
    if not data["changes"]:
        human = "Action diff: no field changes."
    else:
        lines = [f"Action diff: {data['changed_field_count']} field change(s)"]
        for item in data["changes"]:
            lines.append(f"  {item['field']}: {item['before']!r} -> {item['after']!r}")
        human = "\n".join(lines)
    return data, human


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _emit_error(
    message: str,
    code: int,
    *,
    json_output: bool,
    stderr: TextIO,
) -> int:
    if json_output:
        print(json.dumps({"error": {"code": code, "message": message}}), file=stderr)
    else:
        print(f"error: {message}", file=stderr)
    return code


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    json_requested = "--json" in arguments
    debug_requested = "--debug" in arguments
    try:
        args = build_parser().parse_args(arguments)
        data, human = args.handler(args)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, sort_keys=True), file=stdout)
        else:
            print(human, file=stdout)
        if args.command == "validate" and not data["valid"]:
            return EXIT_READ_OR_LOOKUP
        return EXIT_SUCCESS
    except CLIUsageError as exc:
        return _emit_error(str(exc), EXIT_USAGE, json_output=json_requested, stderr=stderr)
    except (FileNotFoundError, PMHFormatError, ObjectNotFoundError, AmbiguousObjectError, TransformNotFoundError) as exc:
        if debug_requested:
            raise
        return _emit_error(
            str(exc), EXIT_READ_OR_LOOKUP, json_output=json_requested, stderr=stderr
        )
    except (WriterValidationError, ConcurrentModificationError, TransformWriteError, PermissionError, OSError) as exc:
        if debug_requested:
            raise
        return _emit_error(
            str(exc), EXIT_WRITE_OR_SAFETY, json_output=json_requested, stderr=stderr
        )
    except (PMHWriterError, PMHError) as exc:
        if debug_requested:
            raise
        return _emit_error(
            str(exc), EXIT_WRITE_OR_SAFETY, json_output=json_requested, stderr=stderr
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
