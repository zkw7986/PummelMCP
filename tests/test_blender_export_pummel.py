from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


class Vec:
    def __init__(self, x: float, y: float, z: float):
        self.x, self.y, self.z = x, y, z

    def __sub__(self, other):
        return Vec(self.x - other.x, self.y - other.y, self.z - other.z)

    def copy(self):
        return Vec(self.x, self.y, self.z)

    def normalized(self):
        return self


class IdentityMatrix:
    translation = Vec(0, 0, 0)

    def __matmul__(self, value):
        return value

    def to_3x3(self):
        return self

    def inverted_safe(self):
        return self

    def transposed(self):
        return self

    def determinant(self):
        return 1.0


def _load_exporter(monkeypatch, depsgraph):
    fake_bpy = types.SimpleNamespace(
        context=types.SimpleNamespace(evaluated_depsgraph_get=lambda: depsgraph),
        path=types.SimpleNamespace(abspath=lambda value: value),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    path = Path(__file__).resolve().parents[1] / "tools" / "blender_export_pummel.py"
    spec = importlib.util.spec_from_file_location("blender_export_pummel_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_writes_normals_and_skips_unused_material_slots(tmp_path, monkeypatch):
    materials = [
        types.SimpleNamespace(use_nodes=False, diffuse_color=(1, 0, 0, 1)),
        types.SimpleNamespace(use_nodes=False, diffuse_color=(0, 1, 0, 1)),
    ]
    triangle = types.SimpleNamespace(vertices=(0, 1, 2), loops=(0, 1, 2), material_index=1)
    mesh = types.SimpleNamespace(
        vertices=[
            types.SimpleNamespace(co=Vec(0, 0, 0)),
            types.SimpleNamespace(co=Vec(1, 0, 0)),
            types.SimpleNamespace(co=Vec(0, 1, 0)),
        ],
        loop_triangles=[triangle],
        loops=[types.SimpleNamespace(normal=Vec(0, 0, 1)) for _ in range(3)],
        corner_normals=[types.SimpleNamespace(vector=Vec(0, 0, 1)) for _ in range(3)],
        uv_layers=types.SimpleNamespace(
            active=types.SimpleNamespace(
                data=[types.SimpleNamespace(uv=(0, 0)), types.SimpleNamespace(uv=(1, 0)), types.SimpleNamespace(uv=(0, 1))]
            )
        ),
        materials=materials,
        calc_loop_triangles=lambda: None,
    )
    evaluated = types.SimpleNamespace(
        matrix_world=IdentityMatrix(),
        to_mesh=lambda **_kwargs: mesh,
        to_mesh_clear=lambda: None,
    )
    obj = types.SimpleNamespace(name="Table", evaluated_get=lambda _depsgraph: evaluated)
    exporter = _load_exporter(monkeypatch, object())

    obj_name, textures, position = exporter.export_object(obj, tmp_path, "table")

    text = (tmp_path / obj_name).read_text(encoding="utf-8")
    assert textures == ["table__mat1.png"]
    assert position == (0, 0, 0)
    assert text.count("usemtl ") == 1
    assert "usemtl material_1" in text
    assert text.count("vn ") == 3
    assert "f 1/1/1 2/2/2 3/3/3" in text
    assert (tmp_path / "table.mtl").read_text(encoding="utf-8").count("newmtl ") == 1
