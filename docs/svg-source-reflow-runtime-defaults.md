# SVG source-reflow runtime defaults

For source-content restyle SVG decks, `SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW`
defaults to six, `SVG_RENDER_CONCURRENCY_SOURCE_REFLOW` defaults to two, and
unset `SVG_SOURCE_OUTPUT_VIEW` defaults to `v1`. Explicit positive concurrency
environment values override the configured defaults. The author permit count
is capped by the process-wide authored slide executor; the pinned runtime
profile records the effective count and whether it came from the environment,
the default, or that executor cap. Set `SVG_SOURCE_OUTPUT_VIEW=off` to disable
the output view explicitly.

The author and render concurrency values are part of the page-render
environment fingerprint. A deck resumed after this change will therefore
re-render its completed pages once under the new fingerprint; these rerenders
reuse the pinned page inputs and incur no LLM cost.
