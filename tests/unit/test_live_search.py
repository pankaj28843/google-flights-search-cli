from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from gflights.browser import BrowserMode, CdpResult
from gflights.live_search import run_live_search

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


class FakeCdpAdapter:
    def __init__(self, results: list[CdpResult]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], BrowserMode, float]] = []

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        self.calls.append((list(args), browser_mode, timeout_seconds))
        return self.results.pop(0)


def cdp_result(
    args: list[str],
    payload: dict[str, object],
    *,
    status: str = "ok",
    exit_code: int = 0,
    browser_mode: BrowserMode = "headless",
    fallback: dict[str, str] | None = None,
) -> CdpResult:
    return CdpResult(
        argv=["cdp", *args],
        browser_mode=browser_mode,
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
        status=status,
        exit_code=exit_code,
        json_payload=payload,
        stop_state=payload.get("stop_state")
        if isinstance(payload.get("stop_state"), str)
        else None,
        fallback=fallback,
    )


def write_intent(path: Path) -> Path:
    intent_path = path / "intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "live-cph-del",
                "origin": {"text": "CPH", "kind": "airport_code"},
                "destination": {"text": "DEL", "kind": "airport_code"},
                "trip_type": "round_trip",
                "departure_window": {"start": "2026-10-01", "end": "2026-10-01"},
                "return_window": {"start": "2026-11-24", "end": "2026-11-24"},
                "passengers": {
                    "adults": 2,
                    "children": 0,
                    "infants_in_seat": 0,
                    "infants_on_lap": 0,
                },
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "top_flights",
            }
        )
    )
    return intent_path


def test_live_search_orchestration_opens_google_flights_and_records_artifacts(
    tmp_path: Path,
) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-live-search",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["query_id"] == "live-cph-del"
    assert payload["browser_mode"] == "headless"
    assert payload["live_mode"] is True
    assert payload["results"] == []
    assert payload["unsupported"][0]["field"] == "live_result_extraction"
    assert payload["evidence"]["run_id"] == "gf-test-live-search"
    assert payload["evidence"]["source_surfaces"] == [
        "cdp:open",
        "cdp:wait",
        "cdp:snapshot",
        "cdp:network",
    ]

    run_root = tmp_path / "runs" / "gf-test-live-search"
    assert (run_root / "intent.json").is_file()
    assert (run_root / "command-log.json").is_file()
    assert (run_root / "open.json").is_file()
    assert (run_root / "snapshot.json").is_file()
    assert (run_root / "network.json").is_file()
    assert adapter.calls == [
        (
            ["open", "https://www.google.com/travel/flights?hl=en&curr=EUR"],
            "headless",
            30.0,
        ),
        (["wait", "load-state", "domcontentloaded", "--target", "page-1"], "headless", 30.0),
        (["snapshot", "--target", "page-1", "--limit", "80"], "headless", 30.0),
        (["network", "--target", "page-1", "--limit", "50", "--wait", "1s"], "headless", 30.0),
    ]


def test_live_search_retries_transient_wait_context_error(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(
                ["wait"],
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": "cdp Runtime.evaluate failed: Cannot find default execution context (-32000)",
                },
                status="tool_error",
                exit_code=6,
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-wait-retry",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["evidence"]["source_surfaces"] == [
        "cdp:open",
        "cdp:wait",
        "cdp:wait:retry",
        "cdp:snapshot",
        "cdp:network",
    ]
    assert adapter.calls[1][0] == adapter.calls[2][0]
    run_root = tmp_path / "runs" / "gf-test-wait-retry"
    assert (run_root / "wait.json").is_file()
    assert (run_root / "wait-retry-1.json").is_file()


def test_live_search_stops_on_blocked_headless_state(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {"status": "blocked", "stop_state": "unusual_traffic"},
                status="blocked",
                exit_code=4,
                fallback={
                    "recommended_browser_mode": "headed",
                    "reason": "headless blocked or human confirmation required",
                },
            )
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-blocked",
        )
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["stop_state"] == "unusual_traffic"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert payload["evidence"]["run_id"] == "gf-test-blocked"
    assert len(adapter.calls) == 1
    assert (tmp_path / "runs" / "gf-test-blocked" / "open.json").is_file()


def test_live_search_can_execute_fake_form_interaction_steps(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            *[cdp_result(["form"], {"ok": True}) for _ in range(12)],
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Search results"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-live-form",
            interact_with_form=True,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert "cdp:form:origin-fill" in payload["evidence"]["source_surfaces"]
    assert "cdp:form:search-submit" in payload["evidence"]["source_surfaces"]
    assert adapter.calls[2][0] == [
        "fill",
        "Where from?",
        "CPH",
        "--by",
        "label",
        "--exact",
        "--target",
        "page-1",
        "--wait-text",
        "CPH",
    ]
    assert any(call[0][0:2] == ["click", "Add adult"] for call in adapter.calls)
    assert adapter.calls[-2][0][0] == "snapshot"

    run_root = tmp_path / "runs" / "gf-test-live-form"
    assert (run_root / "form-origin-fill.json").is_file()
    assert (run_root / "form-search-submit.json").is_file()


def test_live_search_extracts_primary_results_from_snapshot_payload(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "primary_results_visible_text_fixture.json").read_text())
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], fixture["snapshot"]),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-results",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["confidence"] == "weak"
    assert payload["unsupported"] == []
    assert payload["results"][0]["carriers"] == ["KLM", "IndiGo"]
    assert payload["results"][0]["price"] == {"amount": 3206, "currency": "EUR", "text": "€3,206"}
    assert payload["results"][0]["duration_minutes"] == 920
    assert payload["results"][0]["evidence"]["artifacts"] == [
        str(tmp_path / "runs" / "gf-test-results" / "snapshot.json")
    ]


def test_live_search_stops_on_form_interaction_stop_state(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["form"],
                {"status": "blocked", "stop_state": "human_required"},
                status="blocked",
                exit_code=4,
                fallback={
                    "recommended_browser_mode": "headed",
                    "reason": "headless blocked or human confirmation required",
                },
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-form-blocked",
            interact_with_form=True,
        )
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["stop_state"] == "human_required"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert len(adapter.calls) == 3
    assert (tmp_path / "runs" / "gf-test-form-blocked" / "form-origin-fill.json").is_file()
