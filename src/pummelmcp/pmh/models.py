"""Typed, read-only data model for a parsed PMH scene."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from .errors import PMHLookupError


class DecodeConfidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Half-open byte span in the source PMH file."""

    start: int
    length: int

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass(frozen=True, slots=True)
class Vector2:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Vector3:
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class Vector4:
    x: float
    y: float
    z: float
    w: float


@dataclass(frozen=True, slots=True)
class Color4:
    r: float
    g: float
    b: float
    a: float


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    raw: bytes
    value: Any
    confidence: DecodeConfidence
    source_span: SourceSpan | None = None

    @property
    def is_unknown(self) -> bool:
        return self.confidence is DecodeConfidence.UNKNOWN


@dataclass(slots=True)
class Component:
    type_name: str
    guid: str
    enabled: bool
    fields: list[Field] = field(default_factory=list)
    identity_span: SourceSpan | None = None
    index_span: SourceSpan | None = None

    def get_field(self, name: str) -> Field:
        matches = [item for item in self.fields if item.name == name]
        if len(matches) != 1:
            reason = "not found" if not matches else "ambiguous"
            raise PMHLookupError(
                f"field {name!r} on component {self.type_name!r} is {reason}"
            )
        return matches[0]


@dataclass(slots=True)
class GameObject:
    name: str
    active: bool
    layer: int
    tag: str
    children: list[GameObject] = field(default_factory=list)
    guid: str = ""
    components: list[Component] = field(default_factory=list)
    parent: GameObject | None = field(default=None, repr=False)
    hierarchy_span: SourceSpan | None = None
    identity_span: SourceSpan | None = None
    index_span: SourceSpan | None = None
    preorder_index: int = -1
    sibling_index: int = -1

    def walk(self) -> Iterator[GameObject]:
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def hierarchy_path(self) -> str:
        parts = []
        current: GameObject | None = self
        while current is not None:
            parts.append(current.name)
            current = current.parent
        return "/" + "/".join(reversed(parts))

    def get_component(self, type_name: str) -> Component:
        matches = [item for item in self.components if item.type_name == type_name]
        if len(matches) != 1:
            reason = "not found" if not matches else "ambiguous"
            raise PMHLookupError(
                f"component {type_name!r} on object {self.name!r} is {reason}"
            )
        return matches[0]


@dataclass(slots=True)
class Scene:
    magic: str
    version: int
    roots: list[GameObject]
    byte_length: int
    consumed_bytes: int
    source: Path | None = None

    def walk(self) -> Iterator[GameObject]:
        for root in self.roots:
            yield from root.walk()

    @property
    def root_count(self) -> int:
        return len(self.roots)

    @property
    def object_count(self) -> int:
        return sum(1 for _ in self.walk())

    @property
    def component_count(self) -> int:
        return sum(len(obj.components) for obj in self.walk())

    @property
    def fully_consumed(self) -> bool:
        return self.consumed_bytes == self.byte_length

    @property
    def remaining_bytes(self) -> int:
        return self.byte_length - self.consumed_bytes

    def find_game_objects(self, name: str) -> list[GameObject]:
        return [obj for obj in self.walk() if obj.name == name]

    def find_game_object(self, name: str) -> GameObject:
        matches = self.find_game_objects(name)
        if len(matches) != 1:
            reason = "not found" if not matches else f"ambiguous ({len(matches)} matches)"
            raise PMHLookupError(f"game object {name!r} is {reason}")
        return matches[0]
