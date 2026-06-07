"""Pure route-autocomplete resolution from reviewed evidence fixtures."""

from __future__ import annotations

from typing import Any

RouteFixtureSource = tuple[str, dict[str, Any]]


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
