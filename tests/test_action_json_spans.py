from __future__ import annotations

from pummelmcp.pmh import parse_json_spans


def _slice(raw: bytes, node) -> bytes:
    return raw[node.span.start : node.span.end]


def test_action_json_bool_source_span() -> None:
    raw = b'{ "m_spawnAtPosition" : false }'
    node = parse_json_spans(raw).root.member("m_spawnAtPosition")
    assert node.kind == "bool"
    assert node.value is False
    assert _slice(raw, node) == b"false"


def test_action_json_number_source_span() -> None:
    raw = b'{"value":-12.50e+2}'
    node = parse_json_spans(raw).root.member("value")
    assert node.kind == "number"
    assert node.value == -1250.0
    assert _slice(raw, node) == b"-12.50e+2"


def test_action_json_nested_vector_source_spans() -> None:
    raw = b'{"m_position": { "x": 0.0, "y":-2, "z":3e1 }}'
    document = parse_json_spans(raw)
    assert _slice(raw, document.node_at(("m_position", "x"))) == b"0.0"
    assert _slice(raw, document.node_at(("m_position", "y"))) == b"-2"
    assert _slice(raw, document.node_at(("m_position", "z"))) == b"3e1"


def test_action_json_escaped_string_span_safety() -> None:
    raw = b'{"text":"brace } quote \\" slash \\\\ unicode \\u4f60","flag":true}'
    document = parse_json_spans(raw)
    text = document.root.member("text")
    flag = document.root.member("flag")
    assert _slice(raw, text) == b'"brace } quote \\" slash \\\\ unicode \\u4f60"'
    assert _slice(raw, flag) == b"true"
    assert text.value.endswith("unicode 你")


def test_action_json_array_null_and_whitespace() -> None:
    raw = b' \r\n [ null, {"a": [true, false, 1]} ] \t'
    document = parse_json_spans(raw)
    assert document.root.kind == "array"
    assert document.root.items[0].kind == "null"
    assert document.node_at((1, "a", 2)).value == 1
