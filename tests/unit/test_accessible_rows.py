from __future__ import annotations

from gflights.accessible_rows import accessible_row_prepare_selection_js, accessible_rows_js


def test_accessible_row_selection_does_not_fallback_to_first_match() -> None:
    js = accessible_row_prepare_selection_js(
        stage="outbound",
        marker="gflights-test",
        preferred_carrier="",
        require_nonstop=False,
        row_rank=9,
        match_text="",
    )

    assert "const selected = matches[rowRank - 1] || null;" in js
    assert "matches[rowRank - 1] || matches[0]" not in js
    assert "requested row rank exceeds matched row count" in js
    assert "const seenRows = new Set();" in js
    assert "if (seenRows.has(rowEl)) continue;" in js
    assert "seenRows.add(rowEl);" in js


def test_accessible_rows_collects_all_same_stage_lists() -> None:
    js = accessible_rows_js(stage="outbound", limit=20)

    assert 'if (rows.length && requestedStage !== "auto") break;' not in js
