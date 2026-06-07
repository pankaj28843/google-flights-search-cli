from __future__ import annotations

import pytest

from gflights.codec import CodecError, decode_query_value


PRICE_SORT_TFU = "EgYIAhAAGAA"
DATED_ROUND_TRIP_TFS = (
    "CBwQAhokEgoyMDI2LTA2LTE1agcIARIDQ1BIcg0IAxIJL20vMDIydHE0"
    "GiQSCjIwMjYtMDYtMjJqDQgDEgkvbS8wMjJ0cTRyBwgBEgNDUEhAAUgBcAGCAQsI"
    "____________AZgBAQ"
)


def test_decode_query_value_round_trips_urlsafe_base64_without_padding() -> None:
    decoded = decode_query_value("tfu", PRICE_SORT_TFU)

    assert decoded.encoding == "base64url-no-padding"
    assert decoded.round_trip_value == PRICE_SORT_TFU
    assert decoded.decoded_byte_length == 8


def test_decode_query_value_extracts_generic_nested_wire_paths() -> None:
    decoded = decode_query_value("tfu", PRICE_SORT_TFU)

    assert {"path": "tfu.2.1", "wire_type": "varint", "value": 2} in decoded.wire_paths
    assert {"path": "tfu.2.2", "wire_type": "varint", "value": 0} in decoded.wire_paths
    assert {"path": "tfu.2.3", "wire_type": "varint", "value": 0} in decoded.wire_paths


def test_decode_query_value_keeps_tfs_hypotheses_generic() -> None:
    decoded = decode_query_value("tfs", DATED_ROUND_TRIP_TFS)

    assert decoded.round_trip_value == DATED_ROUND_TRIP_TFS
    assert "2026-06-15" in decoded.string_anchors
    assert "2026-06-22" in decoded.string_anchors
    assert "CPH" in decoded.string_anchors
    assert "/m/022tq4" in decoded.string_anchors
    assert {"path": "tfs.9", "wire_type": "varint", "value": 1} in decoded.wire_paths
    assert {"path": "tfs.19", "wire_type": "varint", "value": 1} in decoded.wire_paths


def test_decode_query_value_rejects_invalid_base64url() -> None:
    with pytest.raises(CodecError):
        decode_query_value("tfu", "not a valid encoded value!")
