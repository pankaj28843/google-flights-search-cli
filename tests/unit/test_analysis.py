from __future__ import annotations

from gflights.analysis import date_scan_analysis_table


def test_date_scan_analysis_flattens_ranked_pairs_with_injected_pandas() -> None:
    payload = date_scan_analysis_table(
        [
            {
                "query_id": "del-cph-window",
                "ranking_policy": "comfort_aware_v1",
                "ranked_pairs": [
                    {
                        "departure_date": "2026-10-01",
                        "return_date": "2026-11-24",
                        "best_observed_price": {"amount": 702, "currency": "EUR"},
                        "top_result_summary": {
                            "result_id": "cached-ai-702",
                            "carriers": ["Air India"],
                            "duration_minutes": 870,
                            "stops": {"count": 1},
                        },
                        "scoring_explanation": {
                            "policy": "comfort_aware_v1",
                            "score": 885.5,
                            "google_flights_filters_applied": False,
                        },
                        "source": "fresh_cache",
                    }
                ],
            }
        ],
        pandas_module=FakePandas,
    )

    assert payload["status"] == "ok"
    assert payload["row_count"] == 1
    assert payload["columns"] == [
        "query_id",
        "ranking_policy",
        "departure_date",
        "return_date",
        "price_amount",
        "currency",
        "result_id",
        "carriers",
        "duration_minutes",
        "stops",
        "score",
        "source",
        "google_flights_filters_applied",
    ]
    assert payload["rows"][0]["query_id"] == "del-cph-window"
    assert payload["rows"][0]["price_amount"] == 702
    assert payload["rows"][0]["carriers"] == "Air India"
    assert payload["rows"][0]["google_flights_filters_applied"] is False


def test_date_scan_analysis_reports_unavailable_without_pandas() -> None:
    payload = date_scan_analysis_table([], pandas_module=None)

    assert payload["status"] == "unavailable"
    assert payload["rows"] == []
    assert "analysis" in payload["reason"]


class FakePandas:
    @staticmethod
    def DataFrame(rows: list[dict[str, object]]) -> "FakeDataFrame":
        return FakeDataFrame(rows)


class FakeDataFrame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.columns = list(rows[0]) if rows else []

    def __len__(self) -> int:
        return len(self._rows)

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self._rows
