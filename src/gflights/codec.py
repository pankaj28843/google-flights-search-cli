"""Generic helpers for Google Flights protobuf-like URL evidence.

The codec intentionally emits numeric wire paths only. Human field names stay
in reviewed evidence ledgers until controlled evidence is strong enough to
promote them.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any

_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_NESTING_DEPTH = 12


class CodecError(ValueError):
    """Raised when an encoded query value cannot be decoded as wire evidence."""


@dataclass(frozen=True)
class WireField:
    number: int
    wire_type: int
    value: int | bytes
    children: tuple[WireField, ...] = ()
    text: str | None = None


@dataclass(frozen=True)
class QueryDecodeResult:
    key: str
    raw_value: str
    encoding: str
    decoded_byte_length: int
    round_trip_value: str
    wire_paths: list[dict[str, Any]]
    string_anchors: list[str]


def decode_query_value(key: str, raw_value: str) -> QueryDecodeResult:
    """Decode a URL-safe base64 Google Flights query value into generic paths."""

    payload = _decode_base64url_no_padding(raw_value)
    fields = _parse_message(payload, depth=0)
    return QueryDecodeResult(
        key=key,
        raw_value=raw_value,
        encoding="base64url-no-padding",
        decoded_byte_length=len(payload),
        round_trip_value=_encode_base64url_no_padding(payload),
        wire_paths=_collect_wire_paths(key, fields),
        string_anchors=_collect_string_anchors(fields),
    )


def _decode_base64url_no_padding(raw_value: str) -> bytes:
    if not raw_value or not _BASE64URL_RE.fullmatch(raw_value):
        raise CodecError("query value is not unpadded URL-safe base64")

    padding = "=" * ((4 - len(raw_value) % 4) % 4)
    try:
        payload = base64.urlsafe_b64decode(raw_value + padding)
    except (binascii.Error, ValueError) as exc:
        raise CodecError("query value could not be base64url decoded") from exc

    if _encode_base64url_no_padding(payload) != raw_value:
        raise CodecError("query value is not canonical unpadded URL-safe base64")
    return payload


def _encode_base64url_no_padding(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _parse_message(payload: bytes, *, depth: int) -> tuple[WireField, ...]:
    if depth > _MAX_NESTING_DEPTH:
        raise CodecError("protobuf-like nesting is too deep")

    fields: list[WireField] = []
    offset = 0
    while offset < len(payload):
        key_value, offset = _read_varint(payload, offset)
        field_number = key_value >> 3
        wire_type = key_value & 0b111
        if field_number <= 0:
            raise CodecError("protobuf-like field number must be positive")

        if wire_type == 0:
            value, offset = _read_varint(payload, offset)
            fields.append(WireField(number=field_number, wire_type=wire_type, value=value))
        elif wire_type == 1:
            value, offset = _read_fixed(payload, offset, width=8)
            fields.append(WireField(number=field_number, wire_type=wire_type, value=value))
        elif wire_type == 2:
            length, offset = _read_varint(payload, offset)
            end = offset + length
            if end > len(payload):
                raise CodecError("length-delimited field exceeds payload length")
            value = payload[offset:end]
            offset = end
            text = _decode_printable_text(value)
            children = _parse_nested_message(value, depth=depth + 1)
            fields.append(
                WireField(
                    number=field_number,
                    wire_type=wire_type,
                    value=value,
                    children=children,
                    text=text,
                )
            )
        elif wire_type == 5:
            value, offset = _read_fixed(payload, offset, width=4)
            fields.append(WireField(number=field_number, wire_type=wire_type, value=value))
        else:
            raise CodecError(f"unsupported protobuf wire type {wire_type}")

    return tuple(fields)


def _parse_nested_message(payload: bytes, *, depth: int) -> tuple[WireField, ...]:
    if not payload:
        return ()
    try:
        return _parse_message(payload, depth=depth)
    except CodecError:
        return ()


def _read_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(payload):
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift >= 64:
            raise CodecError("varint is too long")
    raise CodecError("truncated varint")


def _read_fixed(payload: bytes, offset: int, *, width: int) -> tuple[bytes, int]:
    end = offset + width
    if end > len(payload):
        raise CodecError("fixed-width field exceeds payload length")
    return payload[offset:end], end


def _decode_printable_text(value: bytes) -> str | None:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text and all(
        (character.isprintable() and character not in "\x0b\x0c") for character in text
    ):
        return text
    return None


def _collect_wire_paths(prefix: str, fields: tuple[WireField, ...]) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    for field in fields:
        path = f"{prefix}.{field.number}"
        if field.wire_type == 0:
            paths.append({"path": path, "wire_type": "varint", "value": field.value})
        elif field.wire_type == 1:
            paths.append(
                {
                    "path": path,
                    "wire_type": "fixed64",
                    "hex": bytes(field.value).hex(),
                }
            )
        elif field.wire_type == 2:
            if field.children:
                paths.extend(_collect_wire_paths(path, field.children))
            elif field.text is not None:
                paths.append({"path": path, "wire_type": "string", "value": field.text})
            else:
                paths.append(
                    {
                        "path": path,
                        "wire_type": "length_delimited",
                        "byte_length": len(bytes(field.value)),
                    }
                )
        elif field.wire_type == 5:
            paths.append(
                {
                    "path": path,
                    "wire_type": "fixed32",
                    "hex": bytes(field.value).hex(),
                }
            )
    return paths


def _collect_string_anchors(fields: tuple[WireField, ...]) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()

    def visit(items: tuple[WireField, ...]) -> None:
        for item in items:
            if item.text is not None and item.text not in seen:
                seen.add(item.text)
                anchors.append(item.text)
            if item.children:
                visit(item.children)

    visit(fields)
    return anchors
