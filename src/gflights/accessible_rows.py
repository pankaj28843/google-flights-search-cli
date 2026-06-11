"""Accessible Google Flights result-row discovery JavaScript."""

from __future__ import annotations

import json

ACCESSIBLE_ROW_LOCATOR_STRATEGY = (
    "stage-heading > [role=list] > [role=link][aria-label*=Select flight] -> closest li"
)

_ACCESSIBLE_ROWS_LIBRARY_JS = r"""
const GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY = "stage-heading > [role=list] > [role=link][aria-label*=Select flight] -> closest li";
const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
const visibleRect = (el) => {
  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  return {
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    top: Math.round(rect.top),
    left: Math.round(rect.left),
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  };
};
const stageHeadings = (stage) => {
  if (stage === "return") return ["returning flights", "choose return", "return flights"];
  if (stage === "outbound") return ["departing flights", "top departing flights", "other departing flights"];
  return [
    "departing flights",
    "top departing flights",
    "other departing flights",
    "returning flights",
    "choose return",
    "return flights"
  ];
};
const inferStage = (text) => {
  const lower = normalize(text).toLowerCase();
  if (lower.includes("returning flights") || lower.includes("choose return") || lower.includes("return flights")) {
    return "return";
  }
  if (lower.includes("departing flights") || lower.includes("top departing flights") || lower.includes("other departing flights")) {
    return "outbound";
  }
  return "unknown";
};
const headingMatchesStage = (el, requestedStage) => {
  const text = normalize(el.innerText || el.textContent || el.getAttribute("aria-label")).toLowerCase();
  return stageHeadings(requestedStage).some((heading) => text.includes(heading));
};
const rowLooksLikeFlight = (value) => {
  const text = normalize(value);
  if (!text) return false;
  const hasPrice = /(?:DKK|EUR|USD|INR|NOK|SEK|GBP|₹|€|\$)\s*[0-9][0-9,.]*(?:\s+round trip)?/i.test(text)
    || /[0-9][0-9,.]*\s+(?:us dollars?|dollars?|euros?|pounds?|indian rupees?|rupees?|danish kroner|norwegian kroner|swedish kronor|swedish kroner)/i.test(text);
  const hasFlightShape = /(nonstop|\d+\s+stops?|round trip|\d+\s+hr|\d+\s+min|total duration|flight with)/i.test(text);
  return hasPrice && hasFlightShape;
};
const listsForStage = (requestedStage) => {
  const records = [];
  const seenLists = new Set();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,[role="heading"]'))
    .filter((el) => visibleRect(el) && headingMatchesStage(el, requestedStage));
  for (const heading of headings) {
    const headingRect = heading.getBoundingClientRect();
    const root = heading.closest('[role="tabpanel"], [role="main"], main') || document.body;
    const stage = requestedStage === "auto"
      ? inferStage(heading.innerText || heading.textContent || heading.getAttribute("aria-label"))
      : requestedStage;
    const scopedLists = Array.from(root.querySelectorAll('ul[role="list"], ol[role="list"], [role="list"]'))
      .filter((list) => {
        if (seenLists.has(list)) return false;
        const rect = visibleRect(list);
        if (!rect) return false;
        if (rect.top < headingRect.bottom - 2) return false;
        return Array.from(list.querySelectorAll('[role="link"][aria-label]'))
          .some((link) => normalize(link.getAttribute("aria-label")).toLowerCase().includes("select flight"));
      })
      .sort((left, right) => left.getBoundingClientRect().top - right.getBoundingClientRect().top);
    for (const list of scopedLists) {
      seenLists.add(list);
      records.push({list, stage, headingText: normalize(heading.innerText || heading.textContent || heading.getAttribute("aria-label"))});
    }
  }
  if (!records.length) {
    for (const list of Array.from(document.querySelectorAll('ul[role="list"], ol[role="list"], [role="list"]'))) {
      if (seenLists.has(list) || !visibleRect(list)) continue;
      const hasSelectFlight = Array.from(list.querySelectorAll('[role="link"][aria-label]'))
        .some((link) => normalize(link.getAttribute("aria-label")).toLowerCase().includes("select flight"));
      if (hasSelectFlight) {
        seenLists.add(list);
        records.push({list, stage: requestedStage === "auto" ? "unknown" : requestedStage, headingText: ""});
      }
    }
  }
  return records;
};
const collectAccessibleFlightRows = (requestedStage, rowLimit) => {
  const rows = [];
  const seenRows = new Set();
  for (const record of listsForStage(requestedStage)) {
    const links = Array.from(record.list.querySelectorAll('[role="link"][aria-label]'))
      .filter((link) => normalize(link.getAttribute("aria-label")).toLowerCase().includes("select flight"));
    for (const link of links) {
      const rowEl = link.closest("li") || link.closest('[role="listitem"]') || link;
      if (seenRows.has(rowEl)) continue;
      seenRows.add(rowEl);
      const rect = visibleRect(rowEl);
      if (!rect || rect.width <= 250 || rect.height <= 35) continue;
      const rowText = normalize(rowEl.innerText || rowEl.textContent);
      const ariaLabel = normalize(link.getAttribute("aria-label"));
      const combinedText = normalize(`${rowText} ${ariaLabel}`);
      if (!rowLooksLikeFlight(combinedText)) continue;
      rows.push({
        rank: rows.length + 1,
        stage: record.stage,
        headingText: record.headingText,
        text: rowText || ariaLabel,
        ariaLabel,
        combinedText,
        role: link.getAttribute("role") || "",
        locatorStrategy: GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY,
        rowRect: rect
      });
      if (rowLimit && rows.length >= rowLimit) return rows;
    }
    if (rows.length && requestedStage !== "auto") break;
  }
  return rows;
};
""".strip()


def accessible_rows_js(*, stage: str = "auto", limit: int | None = None) -> str:
    """Return an IIFE that extracts visible flight rows via role/ARIA structure."""

    return (
        "(() => {\n"
        f"{_ACCESSIBLE_ROWS_LIBRARY_JS}\n"
        f"return collectAccessibleFlightRows({json.dumps(stage)}, {json.dumps(limit)});\n"
        "})()"
    )


def google_flights_stage_state_js(*, stage: str, limit: int = 20) -> str:
    """Return an IIFE that reports semantic Google Flights page readiness."""

    return (
        "(() => {\n"
        f"{_ACCESSIBLE_ROWS_LIBRARY_JS}\n"
        f"const requestedStage = {json.dumps(stage)};\n"
        f"const rowStage = requestedStage === 'booking' ? 'auto' : requestedStage;\n"
        f"const rows = collectAccessibleFlightRows(rowStage, {int(limit)});\n"
        "const text = normalize(document.body && (document.body.innerText || document.body.textContent) || '');\n"
        "const bookingButtons = Array.from(document.querySelectorAll('button,[role=\"button\"]'))\n"
        "  .map((button) => ({\n"
        "    text: normalize(button.innerText || button.textContent),\n"
        "    ariaLabel: normalize(button.getAttribute('aria-label')),\n"
        "    rect: visibleRect(button)\n"
        "  }))\n"
        "  .filter((button) => button.rect && /continue to book/i.test(`${button.text} ${button.ariaLabel}`));\n"
        "const lower = text.toLowerCase();\n"
        "let terminalCondition = false;\n"
        "if (!text) terminalCondition = false;\n"
        "else if (lower.includes('unusual traffic') || lower.includes('access denied')) terminalCondition = 'blocked';\n"
        "else if (lower.includes('sign in') && lower.includes('google')) terminalCondition = 'login_required';\n"
        "else if (lower.includes('oops, something went wrong') || (lower.includes('no results returned') && /\\breload\\b/.test(lower))) terminalCondition = 'google_page_error';\n"
        "else if (/no (matching )?flights|no results/.test(lower)) terminalCondition = 'no_results';\n"
        "else if (requestedStage === 'booking' && lower.includes('booking options')) terminalCondition = 'booking_summary';\n"
        "else if (requestedStage === 'booking' && lower.includes('book with')) terminalCondition = 'booking_summary';\n"
        "else if (requestedStage === 'booking' && bookingButtons.length > 0) terminalCondition = 'booking_summary';\n"
        "else if (requestedStage === 'booking' && location.href.includes('/travel/flights/booking')) terminalCondition = 'booking_url';\n"
        "else if (requestedStage !== 'booking' && rows.length > 0) terminalCondition = 'fare_rows';\n"
        "return {\n"
        "  requestedStage,\n"
        "  terminalCondition,\n"
        "  rowCount: rows.length,\n"
        "  rows,\n"
        "  bookingButtonCount: bookingButtons.length,\n"
        "  bookingButtons: bookingButtons.slice(0, 8).map((button) => ({text: button.text, ariaLabel: button.ariaLabel, rect: button.rect})),\n"
        "  currentUrl: location.href,\n"
        "  currentTitle: document.title,\n"
        "  hasDepartingHeading: /Departing flights|Top departing flights|Other departing flights/i.test(text),\n"
        "  hasReturningHeading: /Returning flights|Choose return|Return flights/i.test(text),\n"
        "  hasBookingOptions: /Booking options|Book with/i.test(text),\n"
        "  bodySample: text.slice(0, 1000)\n"
        "};\n"
        "})()"
    )


def accessible_row_expand_js(
    *,
    stage: str = "auto",
    limit: int,
) -> str:
    """Return an IIFE that expands row-local Flight details buttons in-page."""

    return (
        "(async () => {\n"
        f"{_ACCESSIBLE_ROWS_LIBRARY_JS}\n"
        f"const stage = {json.dumps(stage)};\n"
        f"const limit = Math.max(1, {int(limit)});\n"
        "const records = [];\n"
        "const buttons = [];\n"
        "const buttonRecords = [];\n"
        "let rank = 0;\n"
        "const seenRows = new Set();\n"
        "for (const record of listsForStage(stage)) {\n"
        "  const links = Array.from(record.list.querySelectorAll('[role=\"link\"][aria-label]'))\n"
        "    .filter((link) => normalize(link.getAttribute('aria-label')).toLowerCase().includes('select flight'));\n"
        "  for (const link of links) {\n"
        "    const rowEl = link.closest('li') || link.closest('[role=\"listitem\"]') || link;\n"
        "    if (seenRows.has(rowEl)) continue;\n"
        "    seenRows.add(rowEl);\n"
        "    const rowText = normalize(rowEl.innerText || rowEl.textContent);\n"
        "    const ariaLabel = normalize(link.getAttribute('aria-label'));\n"
        "    const combinedText = normalize(`${rowText} ${ariaLabel}`);\n"
        "    if (!rowLooksLikeFlight(combinedText)) continue;\n"
        "    rank += 1;\n"
        "    if (rank > limit) break;\n"
        "    const detailsButton = Array.from(rowEl.querySelectorAll('button,[role=\"button\"]'))\n"
        "      .find((button) => {\n"
        "        const label = normalize(button.getAttribute('aria-label')).toLowerCase();\n"
        "        return label.startsWith('flight details');\n"
        "      });\n"
        "    const expandedBefore = detailsButton ? detailsButton.getAttribute('aria-expanded') : '';\n"
        "    if (detailsButton && expandedBefore === 'false') {\n"
        "      buttons.push(detailsButton);\n"
        "    }\n"
        "    const outputRecord = {\n"
        "      rank,\n"
        "      stage: record.stage,\n"
        "      expandedBefore,\n"
        "      rowText: rowText.slice(0, 320),\n"
        "      ariaLabel: ariaLabel.slice(0, 500),\n"
        "      detailsAriaLabel: detailsButton ? normalize(detailsButton.getAttribute('aria-label')).slice(0, 500) : '',\n"
        '      locatorStrategy: `${GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY} > button[aria-label^="Flight details"]`\n'
        "    };\n"
        "    records.push(outputRecord);\n"
        "    if (detailsButton) buttonRecords.push({record: outputRecord, button: detailsButton});\n"
        "    if (rank >= limit) break;\n"
        "  }\n"
        "  if (rank >= limit || (rank > 0 && stage !== 'auto')) break;\n"
        "}\n"
        "for (const button of buttons) {\n"
        "  button.click();\n"
        "}\n"
        "if (buttons.length) {\n"
        "  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));\n"
        "}\n"
        "for (const item of buttonRecords) {\n"
        "  item.record.expandedAfter = item.button.getAttribute('aria-expanded') || item.record.expandedBefore;\n"
        "}\n"
        "return {\n"
        "  consideredRankCount: Math.min(rank, limit),\n"
        "  expandedClickCount: buttons.length,\n"
        "  alreadyExpandedCount: records.filter((record) => record.expandedBefore === 'true').length,\n"
        "  records,\n"
        '  locatorStrategy: `${GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY} > button[aria-label^="Flight details"]`\n'
        "};\n"
        "})()"
    )


def accessible_row_prepare_selection_js(
    *,
    stage: str,
    marker: str,
    preferred_carrier: str,
    require_nonstop: bool,
    row_rank: int,
    match_text: str,
) -> str:
    """Return an IIFE that marks one accessible flight row without clicking it."""

    return (
        "(() => {\n"
        f"{_ACCESSIBLE_ROWS_LIBRARY_JS}\n"
        f"const preferredCarrier = {json.dumps(preferred_carrier)};\n"
        f"const requireNonstop = {json.dumps(require_nonstop)};\n"
        f"const rowRank = Math.max(1, {int(row_rank)});\n"
        f"const matchText = {json.dumps(match_text)};\n"
        f"const marker = {json.dumps(marker)};\n"
        f"const stage = {json.dumps(stage)};\n"
        "for (const row of Array.from(document.querySelectorAll('[data-gflights-selection]'))) {\n"
        "  row.removeAttribute('data-gflights-selection');\n"
        "}\n"
        "for (const link of Array.from(document.querySelectorAll('[data-gflights-selection-link]'))) {\n"
        "  link.removeAttribute('data-gflights-selection-link');\n"
        "}\n"
        "const rowElements = [];\n"
        "for (const record of listsForStage(stage)) {\n"
        "  for (const link of Array.from(record.list.querySelectorAll('[role=\"link\"][aria-label]'))) {\n"
        "    const label = normalize(link.getAttribute('aria-label')).toLowerCase();\n"
        "    if (!label.includes('select flight')) continue;\n"
        "    const rowEl = link.closest('li') || link.closest('[role=\"listitem\"]') || link;\n"
        "    const ariaLabel = normalize(link.getAttribute('aria-label'));\n"
        "    const rect = visibleRect(rowEl);\n"
        "    if (!rect || !rowLooksLikeFlight(ariaLabel)) continue;\n"
        "    rowElements.push({link, rowEl, ariaLabel, combinedText: ariaLabel, rect});\n"
        "  }\n"
        "  if (rowElements.length) break;\n"
        "}\n"
        "const matches = rowElements.filter((row) => {\n"
        "  const searchable = row.combinedText.toLowerCase();\n"
        "  if (preferredCarrier && !searchable.includes(preferredCarrier.toLowerCase())) return false;\n"
        "  if (requireNonstop && !/\\bNonstop\\b/i.test(row.combinedText)) return false;\n"
        "  if (matchText && !searchable.includes(matchText.toLowerCase())) return false;\n"
        "  return true;\n"
        "});\n"
        "const selected = matches[rowRank - 1] || matches[0] || null;\n"
        "if (!selected) {\n"
        "  return {\n"
        "    selected: false,\n"
        "    found: false,\n"
        "    preferredCarrier,\n"
        "    requireNonstop,\n"
        "    rowRank,\n"
        "    matchText,\n"
        "    candidateCount: rowElements.length,\n"
        "    matchCount: matches.length,\n"
        "    locatorStrategy: GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY,\n"
        "    candidates: rowElements.slice(0, 8).map((row) => ({ariaLabel: row.ariaLabel.slice(0, 500)}))\n"
        "  };\n"
        "}\n"
        "selected.rowEl.setAttribute('data-gflights-selection', marker);\n"
        "selected.link.setAttribute('data-gflights-selection-link', marker);\n"
        "const rect = selected.rect;\n"
        "return {\n"
        "  selected: false,\n"
        "  found: true,\n"
        "  clicked: false,\n"
        "  locatorStrategy: GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY,\n"
        "  ariaLabel: selected.ariaLabel,\n"
        "  combinedText: selected.combinedText.slice(0, 1200),\n"
        "  point: {x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2)},\n"
        "  preferredCarrier,\n"
        "  requireNonstop,\n"
        "  rowRank,\n"
        "  matchText,\n"
        "  candidateCount: rowElements.length,\n"
        "  matchCount: matches.length,\n"
        "  text: selected.ariaLabel.slice(0, 700)\n"
        "};\n"
        "})()"
    )


def accessible_selected_row_click_js(*, marker: str) -> str:
    """Return an IIFE that clicks a previously marked accessible row once."""

    return (
        "(() => {\n"
        f"{_ACCESSIBLE_ROWS_LIBRARY_JS}\n"
        f"const marker = {json.dumps(marker)};\n"
        'const markedLink = document.querySelector(`[data-gflights-selection-link="${marker}"]`);\n'
        "if (markedLink) {\n"
        "  try {\n"
        "    markedLink.click();\n"
        "    return {\n"
        "      clicked: true,\n"
        "      clickDispatch: 'dom-link-click',\n"
        "      ariaLabel: normalize(markedLink.getAttribute('aria-label')).slice(0, 700),\n"
        "      currentUrl: location.href,\n"
        "      currentTitle: document.title\n"
        "    };\n"
        "  } catch (error) {\n"
        "    return {clicked: false, reason: String(error && error.message || error)};\n"
        "  }\n"
        "}\n"
        'const rowEl = document.querySelector(`[data-gflights-selection="${marker}"]`);\n'
        "if (!rowEl) return {clicked: false, reason: 'marked row not found'};\n"
        "const link = Array.from(rowEl.querySelectorAll('[role=\"link\"][aria-label]'))\n"
        "  .find((candidate) => normalize(candidate.getAttribute('aria-label')).toLowerCase().includes('select flight'));\n"
        "if (!link) return {clicked: false, reason: 'row-local select flight link not found'};\n"
        "try {\n"
        "  link.click();\n"
        "  return {\n"
        "    clicked: true,\n"
        "    clickDispatch: 'dom-link-click',\n"
        "    ariaLabel: normalize(link.getAttribute('aria-label')).slice(0, 700),\n"
        "    currentUrl: location.href,\n"
        "    currentTitle: document.title\n"
        "  };\n"
        "} catch (error) {\n"
        "  return {clicked: false, reason: String(error && error.message || error)};\n"
        "}\n"
        "})()"
    )


def accessible_row_selection_js(
    *,
    stage: str,
    marker: str,
    preferred_carrier: str,
    require_nonstop: bool,
    row_rank: int,
    match_text: str,
) -> str:
    """Return an IIFE that clicks one accessible row and verifies the next stage."""

    return (
        "(async () => {\n"
        f"{_ACCESSIBLE_ROWS_LIBRARY_JS}\n"
        f"const preferredCarrier = {json.dumps(preferred_carrier)};\n"
        f"const requireNonstop = {json.dumps(require_nonstop)};\n"
        f"const rowRank = Math.max(1, {int(row_rank)});\n"
        f"const matchText = {json.dumps(match_text)};\n"
        f"const marker = {json.dumps(marker)};\n"
        f"const stage = {json.dumps(stage)};\n"
        "const rows = collectAccessibleFlightRows(stage, null);\n"
        "for (const row of rows) {\n"
        "  if (row.el) row.el.removeAttribute('data-gflights-selection');\n"
        "}\n"
        "const rowElements = [];\n"
        "for (const record of listsForStage(stage)) {\n"
        "  for (const link of Array.from(record.list.querySelectorAll('[role=\"link\"][aria-label]'))) {\n"
        "    const label = normalize(link.getAttribute('aria-label')).toLowerCase();\n"
        "    if (!label.includes('select flight')) continue;\n"
        "    const rowEl = link.closest('li') || link.closest('[role=\"listitem\"]') || link;\n"
        "    const rowText = normalize(rowEl.innerText || rowEl.textContent);\n"
        "    const ariaLabel = normalize(link.getAttribute('aria-label'));\n"
        "    const combinedText = normalize(`${rowText} ${ariaLabel}`);\n"
        "    if (!rowLooksLikeFlight(combinedText)) continue;\n"
        "    rowElements.push({link, rowEl, rowText, ariaLabel, combinedText});\n"
        "  }\n"
        "  if (rowElements.length) break;\n"
        "}\n"
        "const matches = rowElements.filter((row) => {\n"
        "  const searchable = row.combinedText.toLowerCase();\n"
        "  if (preferredCarrier && !searchable.includes(preferredCarrier.toLowerCase())) return false;\n"
        "  if (requireNonstop && !/\\bNonstop\\b/i.test(row.combinedText)) return false;\n"
        "  if (matchText && !searchable.includes(matchText.toLowerCase())) return false;\n"
        "  return true;\n"
        "});\n"
        "const selected = matches[rowRank - 1] || matches[0] || null;\n"
        "if (!selected) {\n"
        "  return {\n"
        "    selected: false,\n"
        "    preferredCarrier,\n"
        "    requireNonstop,\n"
        "    rowRank,\n"
        "    matchText,\n"
        "    candidateCount: rowElements.length,\n"
        "    locatorStrategy: GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY,\n"
        "    candidates: rowElements.slice(0, 8).map((row) => ({text: row.rowText.slice(0, 320), ariaLabel: row.ariaLabel.slice(0, 320)}))\n"
        "  };\n"
        "}\n"
        "selected.rowEl.setAttribute('data-gflights-selection', marker);\n"
        "selected.rowEl.scrollIntoView({block: 'center', inline: 'center'});\n"
        "await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));\n"
        "const rect = selected.rowEl.getBoundingClientRect();\n"
        "const beforeUrl = location.href;\n"
        "const beforeTitle = document.title;\n"
        "const started = Date.now();\n"
        "let transitionMatched = false;\n"
        "let transitionCondition = 'timeout';\n"
        "let clickAttempts = 0;\n"
        "let lastClickAt = 0;\n"
        "while (Date.now() - started < 5000) {\n"
        "  if (Date.now() - lastClickAt >= 250) {\n"
        "    selected.link.click();\n"
        "    clickAttempts += 1;\n"
        "    lastClickAt = Date.now();\n"
        "  }\n"
        "  const body = normalize(document.body && (document.body.innerText || document.body.textContent) || '');\n"
        "  if (stage === 'outbound' && /Returning flights|Choose return|Return flights/i.test(body)) {\n"
        "    transitionMatched = true;\n"
        "    transitionCondition = 'return_rows';\n"
        "    break;\n"
        "  }\n"
        "  if (stage === 'return' && (location.href.includes('/travel/flights/booking') || /Booking options|Book with/i.test(body))) {\n"
        "    transitionMatched = true;\n"
        "    transitionCondition = location.href.includes('/travel/flights/booking') ? 'booking_url' : 'booking_summary';\n"
        "    break;\n"
        "  }\n"
        "  await new Promise((resolve) => setTimeout(resolve, 100));\n"
        "}\n"
        "return {\n"
        "  selected: transitionMatched,\n"
        "  clicked: true,\n"
        "  clickAttempts,\n"
        "  clickDispatch: 'dom-link-click',\n"
        "  transitionMatched,\n"
        "  transitionCondition,\n"
        "  transitionElapsedMs: Date.now() - started,\n"
        "  beforeUrl,\n"
        "  beforeTitle,\n"
        "  currentUrl: location.href,\n"
        "  currentTitle: document.title,\n"
        "  reason: transitionMatched ? '' : `row click did not reach expected ${stage === 'outbound' ? 'return rows' : 'booking'} stage within 5000 ms`,\n"
        "  locatorStrategy: GF_ACCESSIBLE_ROW_LOCATOR_STRATEGY,\n"
        "  ariaLabel: selected.ariaLabel,\n"
        "  combinedText: selected.combinedText.slice(0, 1200),\n"
        "  point: {x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2)},\n"
        "  preferredCarrier,\n"
        "  requireNonstop,\n"
        "  rowRank,\n"
        "  matchText,\n"
        "  candidateCount: rowElements.length,\n"
        "  matchCount: matches.length,\n"
        "  text: (selected.rowText || selected.ariaLabel).slice(0, 700)\n"
        "};\n"
        "})()"
    )
