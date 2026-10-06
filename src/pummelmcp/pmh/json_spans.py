"""Strict JSON parser that retains exact UTF-8 token source spans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .errors import PMHError
from .models import SourceSpan


MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000


class JsonSpanError(PMHError, ValueError):
    """JSON bytes cannot be mapped safely to an unambiguous token tree."""


@dataclass(frozen=True, slots=True)
class JsonMember:
    key: str
    key_span: SourceSpan
    value: JsonNode


@dataclass(frozen=True, slots=True)
class JsonNode:
    kind: str
    span: SourceSpan
    value: Any
    members: tuple[JsonMember, ...] = ()
    items: tuple[JsonNode, ...] = ()

    def member(self, key: str) -> JsonNode:
        matches = [item.value for item in self.members if item.key == key]
        if len(matches) != 1:
            reason = "missing" if not matches else "duplicated"
            raise JsonSpanError(f"JSON member {key!r} is {reason}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class JsonDocument:
    raw: bytes
    root: JsonNode

    def node_at(self, path: Iterable[str | int]) -> JsonNode:
        node = self.root
        for part in path:
            if isinstance(part, str):
                if node.kind != "object":
                    raise JsonSpanError(f"JSON path member {part!r} is not in an object")
                node = node.member(part)
            elif isinstance(part, int) and not isinstance(part, bool):
                if node.kind != "array" or not 0 <= part < len(node.items):
                    raise JsonSpanError(f"JSON array index {part} is unavailable")
                node = node.items[part]
            else:
                raise JsonSpanError(f"invalid JSON path component {part!r}")
        return node


def parse_json_spans(raw: bytes) -> JsonDocument:
    """Parse one UTF-8 JSON document and retain byte spans relative to it."""
    parser = _SpanParser(bytes(raw))
    root = parser.parse()
    return JsonDocument(bytes(raw), root)


class _SpanParser:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.offset = 0
        self.nodes = 0

    def parse(self) -> JsonNode:
        try:
            self.raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise JsonSpanError("JSON is not valid UTF-8") from exc
        self._skip_ws()
        node = self._value(0)
        self._skip_ws()
        if self.offset != len(self.raw):
            raise JsonSpanError(f"unexpected JSON data at byte {self.offset}")
        return node

    def _value(self, depth: int) -> JsonNode:
        if depth > MAX_JSON_DEPTH:
            raise JsonSpanError(f"JSON depth exceeds safety limit {MAX_JSON_DEPTH}")
        self.nodes += 1
        if self.nodes > MAX_JSON_NODES:
            raise JsonSpanError(f"JSON node count exceeds safety limit {MAX_JSON_NODES}")
        if self.offset >= len(self.raw):
            raise JsonSpanError("unexpected EOF while reading JSON value")
        byte = self.raw[self.offset]
        if byte == ord("{"):
            return self._object(depth)
        if byte == ord("["):
            return self._array(depth)
        if byte == ord('"'):
            start = self.offset
            value = self._string()
            return JsonNode("string", SourceSpan(start, self.offset - start), value)
        if byte in b"-0123456789":
            return self._number()
        for token, value, kind in (
            (b"true", True, "bool"),
            (b"false", False, "bool"),
            (b"null", None, "null"),
        ):
            if self.raw.startswith(token, self.offset):
                start = self.offset
                self.offset += len(token)
                self._require_delimiter()
                return JsonNode(kind, SourceSpan(start, len(token)), value)
        raise JsonSpanError(f"invalid JSON value at byte {self.offset}")

    def _object(self, depth: int) -> JsonNode:
        start = self.offset
        self.offset += 1
        self._skip_ws()
        members: list[JsonMember] = []
        seen: set[str] = set()
        if self._take(ord("}")):
            return JsonNode("object", SourceSpan(start, self.offset - start), {}, ())
        while True:
            if self.offset >= len(self.raw) or self.raw[self.offset] != ord('"'):
                raise JsonSpanError(f"expected JSON object key at byte {self.offset}")
            key_start = self.offset
            key = self._string()
            key_span = SourceSpan(key_start, self.offset - key_start)
            if key in seen:
                raise JsonSpanError(f"duplicate JSON object key {key!r}")
            seen.add(key)
            self._skip_ws()
            if not self._take(ord(":")):
                raise JsonSpanError(f"expected ':' at byte {self.offset}")
            self._skip_ws()
            child = self._value(depth + 1)
            members.append(JsonMember(key, key_span, child))
            self._skip_ws()
            if self._take(ord("}")):
                break
            if not self._take(ord(",")):
                raise JsonSpanError(f"expected ',' or '}}' at byte {self.offset}")
            self._skip_ws()
        value = {item.key: item.value.value for item in members}
        return JsonNode(
            "object", SourceSpan(start, self.offset - start), value, tuple(members)
        )

    def _array(self, depth: int) -> JsonNode:
        start = self.offset
        self.offset += 1
        self._skip_ws()
        items: list[JsonNode] = []
        if self._take(ord("]")):
            return JsonNode("array", SourceSpan(start, self.offset - start), [], (), ())
        while True:
            items.append(self._value(depth + 1))
            self._skip_ws()
            if self._take(ord("]")):
                break
            if not self._take(ord(",")):
                raise JsonSpanError(f"expected ',' or ']' at byte {self.offset}")
            self._skip_ws()
        return JsonNode(
            "array",
            SourceSpan(start, self.offset - start),
            [item.value for item in items],
            (),
            tuple(items),
        )

    def _string(self) -> str:
        start = self.offset
        self.offset += 1
        while self.offset < len(self.raw):
            byte = self.raw[self.offset]
            if byte == ord('"'):
                self.offset += 1
                token = self.raw[start : self.offset]
                try:
                    return json.loads(token.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise JsonSpanError(f"invalid JSON string at byte {start}") from exc
            if byte == ord("\\"):
                self.offset += 1
                if self.offset >= len(self.raw):
                    break
                escape = self.raw[self.offset]
                if escape == ord("u"):
                    digits = self.raw[self.offset + 1 : self.offset + 5]
                    if len(digits) != 4 or any(item not in b"0123456789abcdefABCDEF" for item in digits):
                        raise JsonSpanError(f"invalid unicode escape at byte {self.offset}")
                    self.offset += 5
                    continue
                if escape not in b'"\\/bfnrt':
                    raise JsonSpanError(f"invalid string escape at byte {self.offset}")
                self.offset += 1
                continue
            if byte < 0x20:
                raise JsonSpanError(f"unescaped control byte in string at {self.offset}")
            self.offset += 1
        raise JsonSpanError(f"unterminated JSON string at byte {start}")

    def _number(self) -> JsonNode:
        start = self.offset
        if self._take(ord("-")) and self.offset >= len(self.raw):
            raise JsonSpanError(f"incomplete JSON number at byte {start}")
        if self._take(ord("0")):
            if self.offset < len(self.raw) and self.raw[self.offset] in b"0123456789":
                raise JsonSpanError(f"leading zero in JSON number at byte {start}")
        else:
            if self.offset >= len(self.raw) or self.raw[self.offset] not in b"123456789":
                raise JsonSpanError(f"invalid JSON number at byte {start}")
            self.offset += 1
            while self.offset < len(self.raw) and self.raw[self.offset] in b"0123456789":
                self.offset += 1
        if self._take(ord(".")):
            fraction_start = self.offset
            while self.offset < len(self.raw) and self.raw[self.offset] in b"0123456789":
                self.offset += 1
            if self.offset == fraction_start:
                raise JsonSpanError(f"missing fraction digits at byte {start}")
        if self.offset < len(self.raw) and self.raw[self.offset] in b"eE":
            self.offset += 1
            if self.offset < len(self.raw) and self.raw[self.offset] in b"+-":
                self.offset += 1
            exponent_start = self.offset
            while self.offset < len(self.raw) and self.raw[self.offset] in b"0123456789":
                self.offset += 1
            if self.offset == exponent_start:
                raise JsonSpanError(f"missing exponent digits at byte {start}")
        self._require_delimiter()
        token = self.raw[start : self.offset]
        value = json.loads(token.decode("ascii"))
        return JsonNode("number", SourceSpan(start, len(token)), value)

    def _skip_ws(self) -> None:
        while self.offset < len(self.raw) and self.raw[self.offset] in b" \t\r\n":
            self.offset += 1

    def _take(self, byte: int) -> bool:
        if self.offset < len(self.raw) and self.raw[self.offset] == byte:
            self.offset += 1
            return True
        return False

    def _require_delimiter(self) -> None:
        if self.offset < len(self.raw) and self.raw[self.offset] not in b" \t\r\n,]}":
            raise JsonSpanError(f"invalid token continuation at byte {self.offset}")
