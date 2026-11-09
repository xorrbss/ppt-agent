# Body-text failures after server charts (2026-11-09)

Continuation of `svg-server-chart-design-20261108.md`.

## Analysis of 68 measured decks

Per-attempt quality failures, all arms:

| Kind | Attempts |
|---|---|
| required_text_missing | 106 |
| svg_common_header_body_intrusion | 98 |
| native_requested_chart_missing | 80 |
| writing_policy_visible_symbols | 31 |
| text_overlap | 25 |

**`required_text_missing`, by cause:**
- **Author-drawn chart labels without units** (most of the 182 missing literals in the author arms): a label "310" where the source states "310억". Server charts do not produce this.
- **Server chart labels without trailing zeros** (27 literals in the server arms): a declared `9.0` was drawn as "9%" because labels went through `Decimal.normalize`. This caused the two remaining V5 fallback pages.
- **Body prose dropping repeated units**: "클라우드 420, 모바일 310, …, 기타 60억원". The check catches this as intended and a retry fixes it.

**`writing_policy_visible_symbols`.** The 5 flagged pages left in the server arms came from the frozen common header title: "권역별 이용 격차: 강원·제주권 …".
- The middle dot belongs to a name the request itself writes.
- That name was not a protected literal, so no retry could clear the failure.

## Changes

1. **Renderer `svg-server-chart/v7`** (now admitted):
   - Draws v6 with each value label keeping its declared digits (`AS_WRITTEN` in `server_chart_v4.py`).
   - The native number format follows (`#,##0.0"%"` for 8.1/9.0/9.8/10.4).
   - v1 to v6 are unchanged.
2. **Middle-dot source names.** `literal_source_values(..., middle_dot_names=True)` adds every word the request source joins with a middle dot (`강원·제주권`, `금융·보험업`) to the protected literals of SVG pages. A list the model joined with a dot is still judged. The authored path does not pass the option.
3. **Year suffix in grounding.** Inline grounding compares source text and category/series names with four-digit years written without `년`: the request's "2025 4분기 8.1" states the category "2025년 4분기".

## Measurement (v7 and change 2; business review and library prompts, 3 decks per arm)

| | Server V4 | Server V5 |
|---|---|---|
| Native charts in the PPTX | 27 | 25 |
| Fallback pages (of 42) | **0** | 2 |
| Flagged pages | 3 | 0 |
| Cost per deck (median) | $1.5 | $1.4 |

- No `9.0%` miss remains.
- The library prompt's middle-dot flags are gone.
- The two V5 fallbacks:
  - one is the year-suffix grounding miss that change 3 fixes; this has not been re-measured;
  - one is body prose that omitted "2026년" in both attempts. That is model behaviour, not a chart.
- The 3 V4 flagged pages are published pages whose last attempt hit `svg_common_header_body_intrusion` once after a clean first attempt. They are not fallbacks.
- Spend: $18.0.
