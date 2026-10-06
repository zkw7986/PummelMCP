"""Export visible Blender mesh objects for PummelMCP.

Run inside Blender, for example:
  blender casino.blend --background --python blender_export_pummel.py -- \
      --output D:/Exports/casino --collection casino

World rotation and scale are baked into each OBJ. World translation is written
to pummel_scene.json. Coordinates are converted from Blender Z-up/right-handed
to Pummel/Unity Y-up/left-handed while compensating for PummelOBJImporter's own
X-axis correction.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path

import bpy


def safe_name(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not text or not text[0].isalnum():
        text = fallback
    return text[:64]


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def write_solid_png(path: Path, rgba: tuple[float, float, float, float]) -> None:
    pixel = bytes(max(0, min(255, round(v * 255))) for v in rgba)
    raw = b"\x00" + pixel
    data = b"\x89PNG\r\n\x1a\n"
    data += png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    data += png_chunk(b"IDAT", zlib.compress(raw))
    data += png_chunk(b"IEND", b"")
    path.write_bytes(data)


def material_image(material):
    if material and material.use_nodes and material.node_tree:
        principled = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if principled:
            socket = principled.inputs.get("Base Color")
            if socket and socket.is_linked:
                node = socket.links[0].from_node
                if node.type == "TEX_IMAGE" and node.image:
                    return node.image
    return None


def material_color(material) -> tuple[float, float, float, float]:
    if material and material.use_nodes and material.node_tree:
        principled = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if principled and principled.inputs.get("Base Color"):
            return tuple(principled.inputs["Base Color"].default_value)
    if material:
        return tuple(material.diffuse_color)
    return (1.0, 1.0, 1.0, 1.0)


def export_texture(material, output: Path, stem: str, slot: int) -> str:
    target = output / f"{stem}__mat{slot + 1}.png"
    image = material_image(material)
    if image:
        source = Path(bpy.path.abspath(image.filepath)) if image.filepath else None
        if source and source.is_file() and source.suffix.lower() == ".png":
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            return target.name
        old_path, old_format = image.filepath_raw, image.file_format
        try:
            image.filepath_raw = str(target)
            image.file_format = "PNG"
            image.save()
            return target.name
        except Exception:
            pass
        finally:
            image.filepath_raw, image.file_format = old_path, old_format
    write_solid_png(target, material_color(material))
    return target.name


def convert_point(value):
    # Pre-mirror X because the game's OBJ importer mirrors it once more.
    return (-value.x, value.z, -value.y)


def loop_normal(mesh, loop_index: int):
    """Return a split/corner normal across supported Blender API versions."""
    try:
        return mesh.corner_normals[loop_index].vector
    except (AttributeError, IndexError):
        return mesh.loops[loop_index].normal


def export_object(obj, output: Path, logical_id: str) -> tuple[str, list[str], tuple[float, float, float]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        mesh.calc_loop_triangles()
        triangles = list(mesh.loop_triangles)
        if not triangles:
            raise RuntimeError(f"{obj.name!r} has no triangle faces to export")
        obj_path = output / f"{logical_id}.obj"
        mtl_path = output / f"{logical_id}.mtl"
        materials = list(mesh.materials) or [None]

        def material_index(triangle) -> int:
            index = int(triangle.material_index)
            return index if 0 <= index < len(materials) else 0

        # Export only slots referenced by a face. Blender files often retain
        # unused slots, while the importer requires one texture per usemtl.
        used_materials = sorted({material_index(triangle) for triangle in triangles})
        material_slots = {source: target for target, source in enumerate(used_materials)}
        textures = [
            export_texture(materials[source], output, logical_id, target)
            for target, source in enumerate(used_materials)
        ]
        world_matrix = evaluated.matrix_world
        origin = world_matrix.translation.copy()
        normal_matrix = world_matrix.to_3x3().inverted_safe().transposed()
        reverse_winding = world_matrix.to_3x3().determinant() < 0
        uv_layer = mesh.uv_layers.active.data if mesh.uv_layers.active else None
        uv_values: list[tuple[float, float]] = []
        normal_values: list[tuple[float, float, float]] = []
        triangle_records = []
        with obj_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"mtllib {mtl_path.name}\n")
            for vertex in mesh.vertices:
                world = world_matrix @ vertex.co
                local_baked = world - origin
                x, y, z = convert_point(local_baked)
                stream.write(f"v {x:.9g} {y:.9g} {z:.9g}\n")
            for triangle in triangles:
                triangle_records.append((triangle, len(uv_values) + 1))
                for loop_index in triangle.loops:
                    uv = uv_layer[loop_index].uv if uv_layer else (0.0, 0.0)
                    uv_values.append((float(uv[0]), float(uv[1])))
                    normal = (normal_matrix @ loop_normal(mesh, loop_index)).normalized()
                    normal_values.append(convert_point(normal))
            for u, v in uv_values:
                stream.write(f"vt {u:.9g} {v:.9g}\n")
            for x, y, z in normal_values:
                stream.write(f"vn {x:.9g} {y:.9g} {z:.9g}\n")
            last_material = None
            # Group faces by material so each OBJ material maps to exactly one
            # mesh submesh and the custom-material list uses the same order.
            for triangle, corner_start in sorted(
                triangle_records, key=lambda item: material_index(item[0])
            ):
                exported_material = material_slots[material_index(triangle)]
                if exported_material != last_material:
                    stream.write(f"usemtl material_{exported_material + 1}\n")
                    last_material = exported_material
                tokens = []
                corner_indices = range(corner_start, corner_start + len(triangle.loops))
                corners = list(zip(triangle.vertices, corner_indices))
                if reverse_winding:
                    corners.reverse()
                for vertex_index, corner_index in corners:
                    tokens.append(f"{vertex_index + 1}/{corner_index}/{corner_index}")
                stream.write("f " + " ".join(tokens) + "\n")
        with mtl_path.open("w", encoding="utf-8", newline="\n") as stream:
            for index, texture in enumerate(textures, 1):
                stream.write(f"newmtl material_{index}\nKd 1 1 1\nmap_Kd {texture}\n")
        position = (origin.x, origin.z, -origin.y)
        return obj_path.name, textures, position
    finally:
        evaluated.to_mesh_clear()


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--selected-only", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    collection = safe_name(args.collection, "blender_scene")
    candidates = bpy.context.selected_objects if args.selected_only else bpy.context.scene.objects
    objects = [obj for obj in candidates if obj.type == "MESH" and not obj.hide_render]
    if not objects:
        raise RuntimeError("no visible mesh objects were found")
    used: set[str] = set()
    entries = []
    for index, obj in enumerate(objects, 1):
        base = safe_name(obj.name, f"object_{index}")
        logical_id = base
        suffix = 2
        while logical_id in used:
            logical_id = f"{base[:58]}_{suffix}"
            suffix += 1
        used.add(logical_id)
        obj_name, textures, position = export_object(obj, output, logical_id)
        entries.append(
            {
                "id": logical_id,
                "obj": obj_name,
                "textures": textures,
                "name": obj.name,
                "parent": "/World",
                "position": {"x": position[0], "y": position[1], "z": position[2]},
                "rotation_degrees": {"x": 0, "y": 0, "z": 0},
                "scale": {"x": 1, "y": 1, "z": 1},
                "collision_type": "mesh",
            }
        )
    manifest = {"version": "0.1", "collection": collection, "objects": entries}
    (output / "pummel_scene.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Exported {len(entries)} object(s) to {output}")


if __name__ == "__main__":
    main()
