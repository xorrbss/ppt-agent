# Authored quality review browser E2E results (2026-08-02)

## Scope and environment

- Browser: signed-in local Chrome session through the in-app Browser controller.
- Frontend: local Presenton proxy at `http://localhost:5000` backed by Next.js and
  FastAPI local services.
- Presentation: `df05d48ba518445eb770619ca154dc4d` (an existing, five-slide
  authored deck). No new presentation or paid review job was created.

## Actual browser observations

1. Opened `/presentation?id=df05d48ba518445eb770619ca154dc4d` and confirmed the
   authored-mode read-only banner plus exactly five central `iframe` canvases.
   Each iframe had inline authored HTML (`srcdoc` lengths: 7,713; 1,156; 10,452;
   11,416; 8,786 characters).
2. The only slide PNGs in the outer page were the five left-rail thumbnail images:
   `/app_data/images/authored/df05d48b-a518-445e-b770-619ca154dc4d/slide_0.png`
   through `slide_4.png`. This confirms PNG is used for navigation/review assets,
   not as the central authored editing canvas.
3. Opened the unique **quality review trigger**. The dialog exposes whole-deck/current
   scope, analyze-only/repair mode, and explicitly states that the pre-repair state
   is saved to version history. A browser screenshot was captured at this state.
   The review action was cancelled before submission.
4. Reloaded the same `presentation?id=` URL and waited for the UI to settle. The
   authored banner, five iframes, five thumbnails, and one quality-review trigger
   all reappeared.

## Result persistence fix

Before this validation, the browser UI discarded its only quality-review task ID
on refresh. The backend already keeps the final payload behind the authenticated
generation-status API, but the client could not discover it again. The client now
stores only the task ID in session storage per presentation and, after refresh,
re-fetches that task through the existing authenticated API. Completed results are
normalized through the same privacy-safe renderer used for live polling. Error
results clear the stale ID; transient request failures retain it for retry on a
later refresh. No OCR source text is stored in browser storage.

The existing result UI renders the requested audit fields: slide, `qid`, before /
after, whether the candidate was actually applied, repair path, resolved and
remaining issues, OCR outcome, and timings/telemetry.

## Automated checks

From `servers/nextjs`:

```powershell
npx cypress run --component --spec "**/AuthoredQualityReview.cy.tsx"
# 5 passing (3 seconds)
npm run lint
# pass
npm run build
# pass; compiled in 45 seconds, TypeScript in 30.3 seconds
```

The added component coverage verifies both sides of refresh persistence: starting
a review records its task ID, and a fresh component mount restores a completed
result from `PresentationGenerationApi.getGenerationStatus`. Existing checks also
cover local-patch, full-fallback, rejected-candidate, telemetry, and OCR-redaction
display paths.

## Deliberate limits / reproducibility

- A direct unauthenticated request to
  `GET /api/v1/ppt/presentation/status/<task-id>` returned `401 Unauthorized`.
  Credentials were neither read nor printed; the real browser page uses its normal
  authenticated client headers.
- I did not click **analyze and fix** for this existing deck: that would invoke a
  live model/OCR review and incur cost or mutate its persisted slides. To exercise
  it in an authorized environment, open the presentation URL above, select the
  desired scope/mode, submit the dialog, wait for completion, then reload the same
  URL and reopen the **quality review trigger**. The task result will be restored through the
  generation-status API.
