# Codex image generation handoff — 2026-09-12

- User decision: use the existing Codex OAuth login for image generation without a separate OpenAI API key; no intermediate image display.
- Active image provider: `codex_image`. Requests `gpt-image-2.5-sunburst` with the existing `GPT_IMAGE_2_5_QUALITY` setting.
- Model evidence: the Codex service accepted both GPT Image 2.5 IDs and a deliberately nonexistent model ID. Images and Responses paths did not report an effective image model. A successful request is not proof that GPT Image 2.5 was used.
- Orchestrator reporting: report "GPT Image 2.5 requested; effective model unverified". Usage receipts identify the route as `codex-image-default`; each completed PNG has a metadata sidecar distinguishing requested and effective models.
- Transport: authenticated Codex Images SSE; consume only the completed PNG. No automatic retries after uncertain completion and no fallback to the separately billed OpenAI API.
- Verification: one live check through `ImageGenerationService` with `OPENAI_API_KEY` absent successfully decoded and verified a PNG and its provenance receipt. The effective model remained unknown. The server returned 1254×1254 despite a requested 1536×1024 size, so requested dimensions are not an output guarantee either.
- Scope: regular generated illustrations. Existing text-rendering and transparent-icon capability gates are unchanged for the new provider.
