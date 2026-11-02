# SVG content brief on/off: live pilot (2026-11-02)

A small live pilot of the `brief_off` arm from
`docs/svg-outline-speed-ab-plan-20260930.md` (arm B, prompt-only). It is a
pilot, not that plan's decision run: n = 3 pairs, no blind human panel, and no
density/recall extractor. It answers one question: is the saving real, and
does anything obviously break?

## Setup

- Backend: the fake-LLM harness in real-provider mode
  (`servers/fastapi/scripts/fake_llm_e2e/`, `FAKE_LLM_CLIENTS=0`), Linux VM,
  `CANVAS_BENCH_ENABLED=true`, image generation off.
- Provider: Anthropic `claude-sonnet-4-6`. Sonnet 5.5 / Opus 5.5 / Fable 5.1
  reject the forced `tool_choice` llmai uses for structured output.
- Code: branch head `a6388fdb` with commit `6205b67f` (Anthropic authoring
  reasoning) reverted locally. With that commit, Sonnet 4.6 author calls run
  adaptive thinking at the role's effort, and 4 of 5 calls in the first deck
  spent the whole 16k output budget (about 230 s each) without returning text
  (`public_provider_empty_output`). Reverting it keeps the pilot comparable to
  the earlier live runs, which had no thinking. See "Findings" below.
- Requests: 5 slides, Korean, `common_header_mode: "free"`, PPTX export,
  `benchSpeedArm` `baseline` or `brief_off`. Three topics, run one at a time
  in interleaved order (A B, B A, A B).

## Results

| topic | arm | wall (s) | brief (s) | outline (s) | cost (USD) | text chars/slide | svg quality fail | fallback page |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| t1 automation plan | baseline | 401 | 128 | 50 | 1.48 | 498 | 0 | 0 |
| t1 | brief_off | 220 | - | 40 | 1.16 | 423 | 2 | 0 |
| t2 marketing strategy | brief_off | 225 | - | 41 | 1.29 | 418 | 2 | 2 |
| t2 | baseline | 443 | 161 | 60 | 1.54 | 555 | 1 | 1 |
| t3 security programme | baseline | 461 | 190 | 59 | 1.64 | 578 | 2 | 0 |
| t3 | brief_off | 246 | - | 56 | 1.29 | 593 | 2 | 0 |

"text chars/slide" counts non-whitespace characters inside `<text>` of the
published SVG. "svg quality fail" is pages flagged `svg:svg_source_quality_failed`;
"fallback page" is pages published as the text fallback
(`author:attempts_exhausted`).

Means: baseline 435 s / $1.55 / 544 chars, brief_off 230 s / $1.25 / 478 chars.

- **Time:** brief_off saved 181-215 s per pair (mean 205 s, -47 %). The brief
  call itself was 128-190 s (8-12k output tokens); the rest came from a
  slightly shorter outline and fewer repair tokens.
- **Cost:** -20 % (the brief call is about $0.12-0.18; the rest is shorter
  downstream prompts).
- **Density:** -12 % on the mean, but t3 was denser without the brief, so the
  spread is larger than the effect at n = 3.
- **Flags:** brief_off flagged 8 of 15 pages (6 quality, 2 fallback) against 4
  of 15 for baseline (3 quality, 1 fallback). That is above the plan's no-loss
  tolerance (+5 points) on its face, though n is too small to separate it from
  noise.
- **Visual check** (contact sheets of all six decks): both arms produce
  comparable layouts. Baseline pages carry more attributed statistics and
  source lines (consultancy and survey names); these come from the brief and
  were not fact-checked here, so "more cited numbers" is not by itself a
  quality gain.

## Findings outside the arm comparison

1. **Every page is flagged `design:unreviewed` on Anthropic.** The design
   review's response is rejected as a whole when a single `visibleEvidence`
   string exceeds 14 words or 160 characters
   (`vision_advisory._parse_design_axes`, `schemaRejectionReason:
   evidence_bounds`). The JSON schema states those bounds, and providers with
   strict structured output enforce them, but llmai's Anthropic path does not,
   so one long phrase discards the review of all five pages. Both arms are
   affected equally; the page flag rate above therefore counts only the svg
   and fallback flags.
2. **`6205b67f` regresses models that do not think by default.** The commit
   fixes `claude-sonnet-5`, which thinks when `thinking` is omitted, by sending
   adaptive thinking at the authoring effort. For `claude-sonnet-4-6`, which
   does not think unless asked, the same change turns thinking on, and the
   SVG author calls ran out of output budget. There is no switch to turn it
   off: the effort comes from the role profile, and `AUTHORED_REASONING_EFFORT`
   only applies when no per-call effort is given.

## Reading

The saving is real and large (about 3.5 minutes per 5-slide deck), but this
pilot does not support turning the brief off by default: flagged pages
doubled and density fell on two of three topics. The plan's decision run (with
density/recall extraction and a blind panel) is still the way to decide. A
cheaper next step is to shorten the brief instead of removing it: its 8-12k
output tokens are the single largest stage in every baseline deck.
