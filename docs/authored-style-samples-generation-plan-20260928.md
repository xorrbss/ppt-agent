# Authored style samples — generation plan (2026-09-28)

Handoff for the session that generates the sample decks. The preview feature,
the freshness rules (`topicDigest`, cache version, `pending.txt`), and the
12-slide demo topic are on `main`. This plan only produces and commits the
sample files. The contract lives in `docs/authored-style-samples-design-20260928.md`:
§4 (storage), §5 (digests), §8 (generation), and §10 (operating rules).

## 0. Decisions and facts

- **Canvas 16:9 (1280×720), 12 slides per style.** Decided 2026-09-28. The
  grid is exactly 4 × 3. Thumbnails are 320×180. Byte caps are 96 KB and 14 KB.
  - Users currently get A4 landscape, because the upload page forces it
    (`useUploadPageState.ts:626-628`). The samples are therefore a design
    preview, not the exact output shape. An A4 pilot of the same topic was
    clean, so A4 is not being avoided because it fails.
  - 16:9 is the CLI's default request shape: send no `page_size` or
    `orientation`.
- **Topic.** `servers/fastapi/scripts/authored_style_samples_topic.md` holds
  12 slides, each with a different layout type. The same file lists the
  manifest titles and the order-guard keywords. Every manifest records the
  prompt's digest (`topicDigest`), so editing the prompt after generation
  stales all decks.
- **Sync endpoint.** It returns 410 on the shared PostgreSQL stack. Use
  `--async`.
- **Timing.** A 10-slide deck took 9–10 min, so expect about 12 min for 12
  slides. 39 styles take about 7.8 h in sequence, or about 4 h two at a time.
- **Model.** The backend default is Codex `gpt-6-sol`. Do not send
  `codex_generation_preset`.
- **Raster.** pypdfium2 renders at 2× and Pillow downsamples. WebP at q88 was
  24–55 KB in the 16:9 pilot.
- **Public canonical styles: 39.** The check script enforces the exact set.
  `authored_style_samples/pending.txt` exempts listed styles (D2).

## 1. Setup: generate from committed `main` only

Samples must show what `main` produces, and `generatorCommit` must be true.

1. **Clean tree.** Before every deck, the generator refuses to run unless
   `git -C <shared tree> status --porcelain -- servers` is empty. Lazy imports
   read the shared tree at call time, so an uncommitted edit would leak into
   the samples. `generatorCommit` is the shared tree's `git rev-parse HEAD`.
2. **Shared tree on the latest `main`, and the stack restarted on it.** The
   backend has no reloader. Restart only through
   `scripts/start-local-stack.ps1 -Stop -KeepDatabase`, then
   `scripts/start-local-stack.ps1`, and ask the user first because the stack
   is shared. Before restarting, check `app_data/logs/api-*.stdout.log` for a
   recent `POST .../generate/async` to confirm no one's job is in flight.
   Proof the new code is running: `GET /openapi.json` lists
   `/api/v1/ppt/authored/styles/{style_id}/samples/{index}`.
3. **Code changes go through a worktree.** Commit the generator in a worktree
   off `origin/main`, merge it, and fast-forward the shared tree before
   generating. Condition 1 forbids editing the shared tree in place.
   - To fast-forward the shared tree without touching others' files: run
     `update-ref` and `read-tree` for the branch, then `git checkout --` only
     the paths the merge changed.

Python tools use the main venv with the relevant tree first on the path
(editable-install trap):
`PYTHONPATH="<tree>/servers/fastapi" /c/project/PPT-agent/ppt-agent/servers/fastapi/.venv/Scripts/python.exe …`.
Do not run `uv sync`.

## 2. Generator

Write `servers/fastapi/scripts/generate_authored_style_samples.py` per design
§8.2:

- **Arguments:** `--styles a,b,c` (default: every public canonical style),
  `--parallel N` (default 1, max 2), `--scratch <dir>`, `--dry-run`.
- **Generation.** For each style, run:
  `node scripts/ppt-agent.mjs --async --content <prompt> --slides 12 --strategy svg --style <id> --export pdf --base http://127.0.0.1:8000 --out <scratch>/<id> --timeout 1800`.
  The prompt is the text between `## Prompt` and `## Fixed` in the topic file.
- **Deck acceptance.** Accept a deck only if the PDF has exactly 12 pages,
  every page is 16:9 (width/height 1.778 ± 0.01), and every order-guard
  keyword appears on its page (pypdfium2 text extraction). Retry a failed or
  rejected deck once, then report it and move on.
- **Raster.** Produce 1280×720 and 320×180 WebP. Start at quality 88 and step
  down by 4 until the image fits its cap. Fail the deck if it cannot fit at
  quality 60.
- **Manifest.**
  - `canvas` is `{1280, 720}`.
  - Titles are the 12 fixed role names from the topic file.
  - `styleDigest` comes from `style_render_digest`.
  - `topicDigest` comes from `topic_prompt_digest()`.
  - `generatorCommit` is `git rev-parse HEAD` of the shared tree.
  - `demoTopicId` is `business-plan-v1`.
  - Write the file with LF line endings.
- **Where files go.** Write into `servers/fastapi/authored_style_samples/<id>/`
  only after the deck passes. Keep the PDF and the presentation id in the
  scratch directory for traceability, and never commit them.
- **Contact sheet.** Emit `<scratch>/contact-sheet.html` with one row per
  style (12 thumbnails, style id, presentation id).
- **Final check.** End by running `scripts/check_authored_style_samples.py`.
  While decks are partial it fails by design (D2); read its per-style lines.
- Run `ruff check` on the generator, and run `--dry-run` for one style.

## 3. Pilot: 3 styles (about 40 min)

1. Run with `--styles default,kbs-company-proposal,neon-venture`. These are
   three different families: clean, formal Korean proposal, and dark
   expressive.
2. Show the contact sheet to the user. Check:
   - the 12 roles appear in order;
   - no clipped or overflowing text;
   - Korean renders correctly;
   - the fictitious-company notices are present;
   - the styles are visibly distinct.
3. If the prompt needs a change, change it once, rerun the pilot, and then
   treat the topic as frozen.
4. **Stop and ask the user before the full run.** It uses the shared Codex
   account for hours.

## 4. Full run: the remaining 36 styles

- Once the user approves, run `--parallel 2` in the background. Watch it with
  a Monitor that emits on deck done, deck failed, and process exit.
- Codex 429s are transient. The single retry covers them; add no backoff
  machinery.
- Regenerate failed decks with `--styles <ids>`.

## 5. Review and commit

1. Review the full contact sheet with the user. A deck with a broken slide is
   regenerated as a whole deck, never slide by slide.
2. The check script must exit 0.
3. Make two commits, in a worktree:
   - the generator;
   - `servers/fastapi/authored_style_samples/` alone, expected to be about
     20–25 MB. Check the size with `du -sh` first. If it exceeds 50 MB, stop
     and raise the D9 storage question with the user.
4. Push the branch. Fast-forward `main` only after the user confirms.

## 6. Live verification

After fast-forwarding the shared tree and restarting the stack (with the
user's approval), confirm:

- `GET /api/v1/ppt/authored/styles` returns `samples` for all 39 styles, and
  every image URL carries `&v=`.
- An image URL returns 200 with `image/webp`.
- In the UI, clicking a card opens the dialog, the grid shows 4 × 3, Shift+→
  keeps the slide number when moving to the next style, and
  "이 스타일로 만들기" selects the style.
