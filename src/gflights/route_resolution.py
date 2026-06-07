"""Pure route-autocomplete resolution from reviewed evidence fixtures."""

from __future__ import annotations

import re
from typing import Any

RouteFixtureSource = tuple[str, dict[str, Any]]

_ROUTE_STOP_MARKERS = (
    " Search ",
    " Explore ",
    " Find cheap flights",
    " Flexible?",
    " Language",
)
_CITY_DESCRIPTORS = (
    "Capital of the United States of America",
    "Capital of Denmark",
    "Capital of India",
    "City in India",
    "Township in Ontario, Canada",
    "City in Australia",
    "Suburb in Australia",
    "Village in New York State",
    "District in Delhi",
    "Town in New York State",
)
_AIRPORT_OR_STATION_RE = re.compile(
    r"^(?P<label>[A-Z][A-Za-z0-9À-ÿ .'/&()\-]+?(?:Airport|Station))"
    r"(?:\s+(?P<code>[A-Z]{3})(?:\s+\d+\s+km to destination)?)?"
)


def resolve_route_from_fixtures(
    input_text: str,
    fixture_sources: list[RouteFixtureSource],
) -> tuple[int, dict[str, Any]]:
    """Resolve route choices from already-loaded route autocomplete fixtures."""
    normalized = _normalize(input_text)
    if not normalized:
        return 2, {
            "status": "ambiguous",
            "input_text": input_text,
            "selected": None,
            "choices": [],
            "unsupported": [],
            "warnings": ["route input text is empty"],
            "evidence": {
                "run_id": "route-resolution-input-validation",
                "source_surfaces": ["input-validation"],
                "artifacts": [],
            },
        }

    not_found_payload: dict[str, Any] | None = None
    for artifact_path, fixture in fixture_sources:
        if fixture.get("fixture_type") != "route_autocomplete_choices":
            continue
        query = _matching_query(normalized, fixture.get("queries", []))
        if query is None:
            not_found_payload = _not_found_payload(input_text, fixture, artifact_path)
            continue
        return _payload_for_query(input_text, query, fixture, artifact_path)

    if not_found_payload is not None:
        return 3, not_found_payload

    return 3, {
        "status": "unsupported",
        "input_text": input_text,
        "selected": None,
        "choices": [],
        "unsupported": [
            {
                "field": "route.resolve.offline_fixtures",
                "status": "deferred",
                "reason": "no route_autocomplete_choices fixture was found",
            }
        ],
        "warnings": [],
        "evidence": {
            "run_id": "route-resolution-fixture-missing",
            "source_surfaces": ["offline-fixtures"],
            "artifacts": [source[0] for source in fixture_sources],
        },
    }


def extract_route_choices_from_snapshot(
    *,
    input_text: str,
    field: str | None,
    snapshot: Any,
    evidence_artifact: str,
    source_surface: str,
    confidence: str = "weak",
) -> tuple[int, dict[str, Any]]:
    """Extract reviewed route-autocomplete choices from visible text evidence."""
    normalized = _normalize(input_text)
    if not normalized:
        return 2, {
            "status": "ambiguous",
            "input_text": input_text,
            "selected": None,
            "choices": [],
            "unsupported": [],
            "warnings": ["route input text is empty"],
            "evidence": {
                "run_id": "route-resolution-input-validation",
                "source_surfaces": ["input-validation"],
                "artifacts": [],
            },
        }

    visible_text = _visible_text(snapshot)
    choices = _autocomplete_choices_from_text(
        visible_text,
        source_surface=source_surface,
        evidence_artifact=evidence_artifact,
        confidence=confidence,
    )
    if not choices:
        return 3, {
            "status": "unsupported",
            "input_text": input_text,
            "field": field,
            "selected": None,
            "choices": [],
            "confidence": "unknown",
            "unsupported": [
                {
                    "field": "route.resolve.live_autocomplete_extraction",
                    "status": "deferred",
                    "reason": "no reviewed route autocomplete choices were visible in the snapshot",
                }
            ],
            "warnings": [],
            "evidence": {
                "run_id": "route-autocomplete-visible-text",
                "source_surfaces": [source_surface],
                "artifacts": [evidence_artifact],
            },
        }

    status = "ok" if len(choices) == 1 else "ambiguous"
    return 0 if status == "ok" else 2, {
        "status": status,
        "input_text": input_text,
        "field": field,
        "selected": choices[0] if status == "ok" else None,
        "choices": choices,
        "confidence": confidence,
        "unsupported": [],
        "warnings": [],
        "evidence": {
            "run_id": "route-autocomplete-visible-text",
            "source_surfaces": [source_surface],
            "artifacts": [evidence_artifact],
        },
        **({"ambiguity_reason": "multiple_route_choices"} if status == "ambiguous" else {}),
    }


def _matching_query(normalized_input: str, raw_queries: Any) -> dict[str, Any] | None:
    if not isinstance(raw_queries, list):
        return None
    for raw_query in raw_queries:
        if not isinstance(raw_query, dict):
            continue
        if _normalize(raw_query.get("input_text")) == normalized_input:
            return raw_query
        for raw_choice in raw_query.get("choices", []):
            if not isinstance(raw_choice, dict):
                continue
            if _choice_matches(normalized_input, raw_choice):
                return raw_query
    return None


def _choice_matches(normalized_input: str, raw_choice: dict[str, Any]) -> bool:
    aliases = raw_choice.get("aliases")
    values = [
        raw_choice.get("text"),
        raw_choice.get("display_name"),
        raw_choice.get("code_or_id"),
    ]
    if isinstance(aliases, list):
        values.extend(aliases)
    return any(_normalize(value) == normalized_input for value in values)


def _payload_for_query(
    input_text: str,
    query: dict[str, Any],
    fixture: dict[str, Any],
    artifact_path: str,
) -> tuple[int, dict[str, Any]]:
    choices = [
        _public_choice(choice) for choice in query.get("choices", []) if isinstance(choice, dict)
    ]
    status = str(query.get("status") or ("ok" if len(choices) == 1 else "ambiguous"))
    if status == "ok" and len(choices) != 1:
        status = "ambiguous"
    selected = choices[0] if status == "ok" and len(choices) == 1 else None
    exit_code = 0 if status == "ok" else 2 if status == "ambiguous" else 3
    payload: dict[str, Any] = {
        "status": status,
        "input_text": input_text,
        "field": query.get("field"),
        "selected": selected,
        "choices": choices,
        "confidence": query.get("confidence") or fixture.get("confidence", "weak"),
        "unsupported": [],
        "warnings": [],
        "evidence": _fixture_evidence(fixture, artifact_path),
    }
    if status == "ambiguous":
        payload["ambiguity_reason"] = query.get("ambiguity_reason") or "multiple_route_choices"
    return exit_code, payload


def _public_choice(raw_choice: dict[str, Any]) -> dict[str, Any]:
    evidence = raw_choice.get("evidence")
    return {
        "text": str(raw_choice.get("text") or ""),
        "kind": str(raw_choice.get("kind") or "city_or_airport"),
        "display_name": str(raw_choice.get("display_name") or raw_choice.get("text") or ""),
        "code_or_id": raw_choice.get("code_or_id"),
        "confidence": str(raw_choice.get("confidence") or "weak"),
        "evidence": _choice_evidence(evidence),
    }


def _choice_evidence(raw_evidence: Any) -> dict[str, Any]:
    if not isinstance(raw_evidence, dict):
        return {"source_surfaces": [], "artifacts": []}
    source_surfaces = raw_evidence.get("source_surfaces")
    artifacts = raw_evidence.get("artifacts")
    return {
        "source_surfaces": [str(item) for item in source_surfaces]
        if isinstance(source_surfaces, list)
        else [],
        "artifacts": [str(item) for item in artifacts] if isinstance(artifacts, list) else [],
    }


def _not_found_payload(
    input_text: str,
    fixture: dict[str, Any],
    artifact_path: str,
) -> dict[str, Any]:
    return {
        "status": "unsupported",
        "input_text": input_text,
        "selected": None,
        "choices": [],
        "unsupported": [
            {
                "field": "route.resolve.input_text",
                "status": "not_found",
                "reason": "input text is not present in reviewed route autocomplete fixtures",
            }
        ],
        "warnings": [],
        "evidence": _fixture_evidence(fixture, artifact_path),
    }


def _fixture_evidence(fixture: dict[str, Any], artifact_path: str) -> dict[str, Any]:
    return {
        "fixture_id": fixture.get("fixture_id"),
        "run_id": fixture.get("run_id"),
        "source_surfaces": [fixture.get("source_surface")]
        if isinstance(fixture.get("source_surface"), str)
        else [],
        "artifacts": [artifact_path, *fixture.get("source_artifacts", [])],
    }


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().strip().split())


def _autocomplete_choices_from_text(
    text: str,
    *,
    source_surface: str,
    evidence_artifact: str,
    confidence: str,
) -> list[dict[str, Any]]:
    segment = _autocomplete_segment(text)
    if not segment:
        return []

    choices: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(segment):
        remaining = segment[cursor:].lstrip()
        if not remaining:
            break
        cursor = len(segment) - len(remaining)

        airport_match = _AIRPORT_OR_STATION_RE.match(remaining)
        descriptor_match = _first_descriptor_match(remaining)
        if airport_match and (
            descriptor_match is None or airport_match.start() <= descriptor_match.start()
        ):
            label = airport_match.group("label").strip()
            code = airport_match.group("code")
            choices.append(
                _live_choice(
                    display_name=label,
                    kind="airport_code" if code else "city_or_airport",
                    code_or_id=code,
                    source_surface=source_surface,
                    evidence_artifact=evidence_artifact,
                    confidence=confidence,
                )
            )
            cursor += max(airport_match.end(), 1)
            continue

        if descriptor_match is not None and descriptor_match.start() > 0:
            display_name = remaining[: descriptor_match.start()].strip()
            if display_name:
                choices.append(
                    _live_choice(
                        display_name=display_name,
                        kind="city",
                        code_or_id=None,
                        source_surface=source_surface,
                        evidence_artifact=evidence_artifact,
                        confidence=confidence,
                    )
                )
            cursor += max(descriptor_match.end(), 1)
            continue

        next_boundary = _next_parse_boundary(remaining)
        if next_boundary is None:
            break
        cursor += max(next_boundary, 1)

    return choices


def _live_choice(
    *,
    display_name: str,
    kind: str,
    code_or_id: str | None,
    source_surface: str,
    evidence_artifact: str,
    confidence: str,
) -> dict[str, Any]:
    text = f"{display_name} {code_or_id}" if code_or_id else display_name
    return {
        "text": text,
        "kind": kind,
        "display_name": display_name,
        "code_or_id": code_or_id,
        "confidence": confidence,
        "evidence": {
            "source_surfaces": [source_surface],
            "artifacts": [evidence_artifact],
        },
    }


def _autocomplete_segment(text: str) -> str:
    normalized = _normalize_visible_text(text)
    marker = "Select multiple airports"
    marker_index = normalized.find(marker)
    if marker_index == -1:
        return ""
    segment = normalized[marker_index + len(marker) :].strip()
    cut_points = [index for marker in _ROUTE_STOP_MARKERS if (index := segment.find(marker)) != -1]
    if cut_points:
        segment = segment[: min(cut_points)]
    return _normalize_visible_text(segment)


def _normalize_visible_text(text: str) -> str:
    cleaned = text.replace("\u200c", "").replace("\u200b", "")
    cleaned = re.sub(
        r"(?<=[a-z)])([A-Z]{3})(?=\s+\d+\s+km to destination)",
        r" \1",
        cleaned,
    )
    return " ".join(cleaned.split())


def _first_descriptor_match(text: str) -> re.Match[str] | None:
    matches = [
        match
        for descriptor in _CITY_DESCRIPTORS
        if (match := re.search(re.escape(descriptor), text)) is not None
    ]
    if not matches:
        return None
    return min(matches, key=lambda match: match.start())


def _next_parse_boundary(text: str) -> int | None:
    points: list[int] = []
    descriptor_match = _first_descriptor_match(text)
    if descriptor_match is not None:
        points.append(descriptor_match.start())
    airport_search = re.search(
        r"[A-Z][A-Za-z0-9À-ÿ .'/&()\-]+?(?:Airport|Station)",
        text,
    )
    if airport_search is not None:
        points.append(airport_search.start())
    return min(points) if points else None


def _visible_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    if isinstance(text, str):
        return text
    items = payload.get("items")
    if isinstance(items, list):
        return "\n".join(
            item["text"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    nested_snapshot = payload.get("snapshot")
    if isinstance(nested_snapshot, dict):
        return _visible_text(nested_snapshot)
    return ""
