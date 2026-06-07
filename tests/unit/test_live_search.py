from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from gflights.app_state import PriceCache
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


def write_lucknow_oneway_intent(path: Path) -> Path:
    intent_path = path / "intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "live-cph-lko",
                "origin": {"text": "CPH", "kind": "airport_code"},
                "destination": {"text": "Lucknow", "kind": "city_or_airport"},
                "trip_type": "one_way",
                "departure_window": {"start": "2026-06-15", "end": "2026-06-15"},
                "return_window": None,
                "passengers": {
                    "adults": 1,
                    "children": 0,
                    "infants_in_seat": 0,
                    "infants_on_lap": 0,
                },
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "price",
            }
        )
    )
    return intent_path


def successful_capture_results(
    snapshot_payload: dict[str, object] | None = None,
    *,
    query_settle: bool = False,
) -> list[CdpResult]:
    results = [
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
    ]
    if query_settle:
        results.append(cdp_result(["network-idle"], {"ok": True}))
    results.extend(
        [
            cdp_result(
                ["snapshot"],
                snapshot_payload or {"ok": True, "items": [{"text": "Flights"}]},
            ),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )
    return results


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
    assert payload["query_population"]["status"] == "encoded"
    assert payload["unsupported"][0]["field"] == "live_result_extraction"
    assert payload["evidence"]["run_id"] == "gf-test-live-search"
    assert payload["evidence"]["source_surfaces"][0] == "query-state:tfs"
    assert payload["evidence"]["source_surfaces"][1:] == [
        "cdp:open",
        "cdp:wait",
        "cdp:snapshot",
        "cdp:network",
    ]

    run_root = tmp_path / "runs" / "gf-test-live-search"
    assert (run_root / "intent.json").is_file()
    assert (run_root / "query-state.json").is_file()
    assert (run_root / "command-log.json").is_file()
    assert (run_root / "open.json").is_file()
    assert (run_root / "snapshot.json").is_file()
    assert (run_root / "network.json").is_file()
    assert adapter.calls[0][0][0] == "open"
    assert adapter.calls[0][0][1].startswith("https://www.google.com/travel/flights?")
    assert "tfs=" in adapter.calls[0][0][1]
    assert adapter.calls[1:] == [
        (["wait", "load-state", "domcontentloaded", "--target", "page-1"], "headless", 30.0),
        (["snapshot", "--target", "page-1", "--limit", "80"], "headless", 30.0),
        (["network", "--target", "page-1", "--limit", "50", "--wait", "1s"], "headless", 30.0),
    ]


def test_live_search_opens_populated_query_state_url_when_supported(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?tfs=encoded&tfu=encoded&hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_lucknow_oneway_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-query-state",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    opened_url = adapter.calls[0][0][1]
    assert opened_url.startswith("https://www.google.com/travel/flights?")
    assert "tfs=CBwQAhokEgoyMDI2LTA2LTE1" in opened_url
    assert "tfu=EgYIAhAAGAA" in opened_url
    assert "hl=en" in opened_url
    assert "curr=EUR" in opened_url
    assert payload["query_population"]["status"] == "encoded"
    assert payload["query_population"]["confidence"] == "strong"
    assert "query-state:tfs" in payload["evidence"]["source_surfaces"]
    assert "query-state:tfu" in payload["evidence"]["source_surfaces"]
    assert "cdp:wait:query-network-idle" in payload["evidence"]["source_surfaces"]
    assert adapter.calls[2] == (
        ["wait", "network-idle", "--target", "page-1", "--idle", "1s"],
        "headless",
        5.0,
    )
    assert payload["unsupported"][0]["field"] == "live_result_extraction"
    assert (tmp_path / "runs" / "gf-test-query-state" / "query-state.json").is_file()
    assert (tmp_path / "runs" / "gf-test-query-state" / "wait-query-network-idle.json").is_file()


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
    assert payload["evidence"]["source_surfaces"][0] == "query-state:tfs"
    assert payload["evidence"]["source_surfaces"][1:] == [
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


def test_live_search_retries_transient_snapshot_context_error(tmp_path: Path) -> None:
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
                ["snapshot"],
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": "snapshot target page-1: cdp Runtime.evaluate failed: Cannot find default execution context (-32000)",
                },
                status="tool_error",
                exit_code=6,
            ),
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
            run_id="gf-test-snapshot-retry",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["evidence"]["source_surfaces"][0] == "query-state:tfs"
    assert payload["evidence"]["source_surfaces"][1:] == [
        "cdp:open",
        "cdp:wait",
        "cdp:snapshot",
        "cdp:snapshot:retry",
        "cdp:network",
    ]
    run_root = tmp_path / "runs" / "gf-test-snapshot-retry"
    assert (run_root / "snapshot.json").is_file()
    assert (run_root / "snapshot-retry-1.json").is_file()


def test_live_search_keeps_snapshot_output_when_network_capture_fails(tmp_path: Path) -> None:
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
            cdp_result(
                ["network"],
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": "capture network target page-1: daemon read i/o timeout",
                },
                status="tool_error",
                exit_code=6,
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-network-timeout",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["evidence"]["source_surfaces"][-1] == "cdp:network"
    assert any("network evidence capture failed" in warning for warning in payload["warnings"])
    assert (tmp_path / "runs" / "gf-test-network-timeout" / "network.json").is_file()


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


def test_live_search_writes_extracted_result_prices_to_sqlite_cache(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "primary_results_visible_text_fixture.json").read_text())
    adapter = FakeCdpAdapter(successful_capture_results(fixture["snapshot"]))

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-cache",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    with sqlite3.connect(tmp_path / "cache.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT cache_key, query_id, departure_date, return_date, currency,
                   price_amount, price_payload, source_run_id
            FROM flight_price_cache
            ORDER BY price_amount
            """
        ).fetchall()

    assert len(rows) == 2
    first = rows[0]
    assert first["query_id"] == "live-cph-del"
    assert first["departure_date"] == "2026-10-01"
    assert first["return_date"] == "2026-11-24"
    assert first["currency"] == "EUR"
    assert first["price_amount"] == 3206
    assert first["source_run_id"] == "gf-test-cache"
    assert "live-cph-del" in first["cache_key"]
    assert "2026-10-01" in first["cache_key"]
    cached_payload = json.loads(first["price_payload"])
    assert cached_payload["price"] == {"amount": 3206, "currency": "EUR", "text": "€3,206"}
    assert cached_payload["result_id"] == "visible-text-result-1"
    assert cached_payload["carriers"] == ["KLM", "IndiGo"]
    assert "requests" not in cached_payload
    assert "stdout" not in cached_payload
    assert "json_payload" not in cached_payload
    assert PriceCache(tmp_path / "cache.sqlite").get_fresh_price(first["cache_key"]) is not None


def test_live_search_preserves_json_array_input_order(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            *successful_capture_results(),
            *successful_capture_results(query_settle=True),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=FIXTURES / "search_intents.json",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
        )
    )

    assert exit_code == 0
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "del-cph-senior-oct-nov",
        "cph-lko-oneway-jun",
    ]
    assert len(adapter.calls) == 9


def test_live_search_reports_query_population_boundary_per_batch_item(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            *successful_capture_results(),
            *successful_capture_results(query_settle=True),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=FIXTURES / "search_intents.json",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
        )
    )

    assert exit_code == 0
    assert isinstance(payload, list)
    assert payload[0]["query_population"]["status"] == "unsupported"
    assert payload[0]["unsupported"][0]["field"] == "departure_window"
    assert "tfs=" not in payload[0]["target_url"]
    assert payload[1]["query_population"]["status"] == "encoded"
    assert "tfs=" in payload[1]["target_url"]
    assert payload[1]["unsupported"][0]["field"] == "live_result_extraction"


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
