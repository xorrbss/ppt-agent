# Authored hybrid leftovers

The authored Hybrid PPTX export and its Next.js Chrome renderer were removed
with the authored generation strategy (authored removal P3, B3 and B5), and
the PPTX archive reader with the OOXML vNext export (release gate). What
remains here is shared by surviving code:

- `schema.ts`: the authored slide canvas constants (`AUTHORED_SLIDE_WIDTH_PX`,
  `AUTHORED_SLIDE_HEIGHT_PX`) and slide contract types that the svg editor,
  the canvas helpers and the master fixtures import; `local-fit-browser.ts`
  stays only for the `LocalFitObservation` type `schema.ts` references.
- `bundled-fonts-manifest.ts`: the bundled font faces the svg preview fonts
  resolve against.

Next.js no longer drives Chrome directly. Slide rendering runs in the Python
renderer worker under `contracts/authored-renderer`.
