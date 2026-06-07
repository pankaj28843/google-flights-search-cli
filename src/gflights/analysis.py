"""Optional tabular analysis adapters for agent-facing output."""

from __future__ import annotations

from typing import Any

_AUTO = object()


def date_scan_analysis_table(
    scan_results: list[dict[str, Any]],
    *,
    pandas_module: Any = _AUTO,
) -> dict[str, Any]:
    """Return a pandas-backed flat table for ranked date-scan pairs."""

    if pandas_module is None:
        return _pandas_unavailable()
    if pandas_module is _AUTO:
        try:
            import pandas as pandas_module  # type: ignore[no-redef]
        except ImportError:
            return _pandas_unavailable()

    rows = _date_scan_rows(scan_results)
    frame = pandas_module.DataFrame(rows)
    return {
        "status": "ok",
        "row_count": len(frame),
        "columns": [str(column) for column in frame.columns],
        "rows": frame.to_dict(orient="records"),
        "warnings": [],
    }


def _pandas_unavailable() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": "install google-flights-search-cli[analysis] to enable pandas-backed analysis",
        "rows": [],
        "warnings": [],
    }


def _date_scan_rows(scan_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in scan_results:
        ranked_pairs = result.get("ranked_pairs")
        if not isinstance(ranked_pairs, list):
            continue
        for pair in ranked_pairs:
            if isinstance(pair, dict):
                rows.append(_date_scan_row(result, pair))
    return rows


def _date_scan_row(result: dict[str, Any], pair: dict[str, Any]) -> dict[str, Any]:
    price = (
        pair.get("best_observed_price") if isinstance(pair.get("best_observed_price"), dict) else {}
    )
    summary = (
        pair.get("top_result_summary") if isinstance(pair.get("top_result_summary"), dict) else {}
    )
    stops = summary.get("stops") if isinstance(summary.get("stops"), dict) else {}
    explanation = (
        pair.get("scoring_explanation") if isinstance(pair.get("scoring_explanation"), dict) else {}
    )
    return {
        "query_id": result.get("query_id"),
        "ranking_policy": result.get("ranking_policy"),
        "departure_date": pair.get("departure_date"),
        "return_date": pair.get("return_date"),
        "price_amount": price.get("amount"),
        "currency": price.get("currency"),
        "result_id": summary.get("result_id"),
        "carriers": ", ".join(str(carrier) for carrier in _list(summary.get("carriers"))),
        "duration_minutes": summary.get("duration_minutes"),
        "stops": stops.get("count"),
        "score": explanation.get("score"),
        "source": pair.get("source"),
        "google_flights_filters_applied": explanation.get("google_flights_filters_applied"),
    }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
