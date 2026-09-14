# Authored portrait implementation and actual acceptance

## 최종 판정 — 2026-09-13

일반 **default Authored의 16:9 / 9:16 생성과 Fidelity PPTX / PDF**를 구현하고 실제 수용 검증을 완료했다. 세로 capability를 활성화했으며 실제 catalog의 37개 style 중 `default`만 9:16을 노출한다. 생성 설정에서 9:16 선택, 재열기·썸네일·전체화면을 실제 브라우저로 확인했다. 아래 과거 실행 기록의 실패/미확인은 당시 상태이며, 이 최종 판정과 `final-acceptance-manifest.json`이 현재 결과다.

## 핵심 구현과 설계 보정

- 요청 `aspect_ratio` / 공개 v2 `aspectRatio`를 기존 durable payload와 `theme.authored_vnext.canvas`에 연결했다. 누락된 과거 canvas는 DB 변경 없이 가로 projection을 제공하고, 잘못된 명시 계약은 오류로 처리한다. staging뿐 아니라 최종 semantic projection writer에서도 canvas와 기존 snapshot 의미를 보존한다.
- 기존 생성·배치·style·local-fit·캡처 경로에 같은 1280×720 / 720×1280 계약을 전달한다. 별도 세로 생성기나 저장 체계를 추가하지 않았다. 캡처 cache identity에 크기를 포함하고, footer는 기존 하단 inset을 실제 viewport 높이에 적용한다.
- 실제 자동 PPTX 소유 소스는 `_build_authored_export` → `author_deck.build_image_pptx`; 현재 화면 Fidelity/PDF는 workspace command → Python public render/export worker다. Next 번들 helper는 현재 production 호출자가 없으며 수정된 helper 자체를 실제 제품 사용 증거로 계산하지 않았다.
- 재시도 복원은 실제 writer의 parent action receipt·immutable generation payload·원 admission·artifact origin을 검증한다. 기존 snapshot과 HTML/PNG digest를 수정하거나 검증을 우회하지 않는다.
- 관리 generation controller의 범위는 기존 생성/review 및 단일 페이지 preview/publish로 한정했다. page-remake는 기존 PostgreSQL JSON CAS helper를 재사용한다. 새 공식 local-fit producer 결과와 승인 candidate canonical digest를 검증한 후 slide/theme 및 대상 receipt를 원자적으로 발행한다. untouched slide receipt와 과거 V2/V3는 보존한다.
- 회사 master, 다른 style, source-native/hybrid의 세로 조합은 제한한다. 회사 master 등록은 일반 세로 생성의 전제 조건이 아니다. 생성 provider/model 설정은 변경하지 않았다.

## 실제 수행한 수용 검증

| 항목 | 확인 결과 |
|---|---|
| 관리 스택 / DB | 기존 포트·환경과 PostgreSQL 사용. API 또는 generation controller만 필요한 시점에 재시작. 무관한 `presentation.edit` 1건은 계속 queued. |
| 실제 생성 | 기존 `codex / gpt-5.6-sol` provider로 표지·본문·도서관 일러스트·차트·마무리 5장 생성. job `6f7cc178-8d68-45ec-b2b6-ec43ed4c3086`, operation `7f473710-05b0-4925-803f-4ec4c09e1d84`와 최종 문서 연결 확인. |
| 생성 품질 | 5장 캡처 모두 720×1280, 최소 관측 글자 11.25pt, clipped text 0, local-fit passed. 기존 9.5pt 하한과 내용 보존 기준 유지. |
| 재시도 | 최초 진단 run의 같은 durable job을 공식 operation retry로 복원. 승인된 앞 4장 author/render 증거 보존; 마지막 장 재저장에서도 원 HTML/PNG 바이트 동일 확인. |
| 재생성·재열기 | 최종 문서 `27b9b99a-11f7-4c24-b598-c582045ce2d4`, revision 3. 공식 단일 페이지 preview/publish 성공. 앞 4장 불변, 마지막 HTML은 승인 후보와 동일, 5장 local-fit digest 모두 현재 HTML과 일치. 브라우저 재열기·썸네일·전체화면 9:16, JS 오류 0. |
| PPTX | revision 3의 실제 workspace export/download 성공, SHA가 operation artifact와 일치. PowerPoint에서 5장 열기, 540×960pt(6858000×12192000 EMU), 내용 확인. 앞 4장 PowerPoint 렌더 픽셀은 이전 검증본과 동일, 새 마지막 장 잘림 없음. |
| PDF | revision 3 실제 download SHA 일치. 5페이지 MediaBox 540×960pt, 각 배치 행렬 `[540,0,0,960,0,0]`. 실제 PDF render에서 내용 확인. |
| 가로 호환 | 기존 canvas 없는 가로 문서의 재열기와 실제 PPTX/PDF export 확인. PowerPoint/PDF 960×540pt, 내용 정상. |
| 캐시 분리 | 실제 슬라이드 HTML로 가로 2회·세로 2회 캡처. 서로 다른 identity, 두 방향 각각 정확한 PNG 크기와 반복 cache hit 확인. |
| 제한 / UI | 미지원 조합 4건 실제 admission 422. 최종 catalog는 default에만 9:16. 실제 생성 설정에서 9:16 선택 가능. |

구현 층별 좁은 검증은 persistence 발행 1건, isolated export worker 3건, checkpoint authority 22건, 외부 worker 소유 범위 1건, 최종 page-remake CAS 1건 등이 통과했다. 독립 Sol/high 검토에서 계약·retry provenance·publication·worker ownership·remake freshness/CAS/footer의 발견 사항을 수정하고 범위 내 통과를 받았다. 전체 테스트·빌드·린트를 실행하지 않았다.

## 산출물과 증거

- 최종 PPTX: `artifacts/authored-portrait-acceptance-20260913/portrait-verified-r3-slides.pptx`
- 최종 PDF: `artifacts/authored-portrait-acceptance-20260913/portrait-verified-r3-slides.pdf`
- 전체 수용 요약: 같은 폴더의 `final-acceptance-manifest.json`, `evidence-summary-final.md`
- 파일·무결성 증거: `final-artifact-acceptance-witness.json`, `final-publication-integrity-witness.json`, `actual-canvas-cache-witness.json`
- 모델/검토: A/B/C Sol high, D Terra medium. R Sol high 및 E Luna medium은 완료 슬롯에 해당하는 독립 CLI로 실행했다. 기존 root 세션을 요청된 Sol로 바꿀 수 없는 점은 시작 시 알렸다. R은 sandbox 파일 읽기 제한 때문에 리드가 제공한 현재 소유 소스 문맥을 읽기 전용으로 검토했다.
- `Spec Memory: authored.portrait-canvas-end-to-end@1 auto-recorded`

## 남은 제한

- 세로는 ordinary default Authored + Fidelity/PDF만 지원한다. 회사 master·다른 style·source-native/hybrid는 지원으로 노출하지 않는다.
- 재생성된 마지막 장에는 중간 심각도의 색 대비 경고 2건이 남아 있다. 경고나 품질 기준을 삭제·완화하지 않았으며 글자 잘림은 확인되지 않았다.
- 과거 generation V2/V3는 immutable evidence다. 재생성 후 현재 revision을 새로운 release-ready V3로 재인증하지 않으며 일반 Fidelity/PDF 수용 결과와 구분한다.
- 이미지 장은 실제 Authored 일러스트로 검증했다. 별도 사진 생성 provider 호출이나 모든 스타일·콘텐츠에 대한 품질 보장을 주장하지 않는다.

## 이전 실행 기록 — 원인 조사와 해결 과정


## P0 contract and ownership

This records code-confirmed connections separately from the v3 design proposal. The existing design and its section 10 remain the requirements. Implementation and real acceptance are pending.

- Public request: `aspect_ratio`, exactly `16:9` (default) or `9:16`.
- Durable and document canvas wire: flat camelCase object `{contractVersion, aspectRatio, widthPx, heightPx, locale, safeArea}`. Version is `authored-canvas/v1`; document authority is `theme.authored_vnext.canvas`. Existing theme and projection keys are merged, not replaced.
- Dimensions: CSS 1280x720 / 720x1280; EMU 12192000x6858000 / 6858000x12192000; PDF points 960x540 / 540x960.
- General default safe area uses 64px on each edge, from the existing general author shared prompt and fallback body padding. It does not copy the company-master canonical safe area. Existing slide footer rules remain additional constraints.
- Missing historical canvas means landscape without a database write. Present malformed metadata, unsupported versions, and request/checkpoint/document dimension conflicts fail explicitly. Sealed snapshots are never rewritten or bypassed.
- Portrait admission: ordinary default Authored only, without company master, registered template, source-native or fidelity-first source mode. No implicit style or orientation replacement.
- Existing `/authored/styles` supplies `supportedAspectRatios`. Public portrait capability remains disabled until actual generation and artifact readback pass. API admission can exercise the implemented portrait contract before public release verification.
- Python helper owner A: `services/authored_canvas.py`, with request/presentation canvas contract and metadata functions. TS helper owner C: `lib/authored-canvas.ts`, `resolveAuthoredCanvas(theme)`.

| Owner | Exclusive source scope |
| --- | --- |
| A | Generate request model; authored_canvas helper; presentation_generation_staging, presentation_generate, presentation_crud, authored_operations, authored endpoint; authored_style_catalogue; authored_presentation_publication; workspace page/batch remake and quality-export handler master predicates |
| B | authored_presentation_generation/pipeline; authored_composition_policy/local_fit_policy/admission_capture; author_deck/slide/slide_prompt/slide_rules/batch; slide_capture/reusable_render/pipe_render/worker_client/worker_session; renderer_isolation/authored_renderer_worker; authored_page_remake_service and authored_quality_review_service |
| C | authored-canvas TS helper; run-bundled-presentation-export; authored-hybrid export/raster-pdf/schema/contract/browser-source and direct renderer consumers, with additional paths declared before edits |
| D | upload type/useUploadPageState/ConfigurationSelects; presentation-generation API client; durableGenerationTask/usePresentationGeneration; PresentationRender/PresentationContentRender/AuthoredSlidePreview/SlideThumbnailCard and direct prop consumers |
| Lead | This evidence document and design evidence updates; cross-owner seams; managed server control and real acceptance; release capability flip after readback |

## Code-confirmed differences from design assumptions

1. Actual upload input passes through `useUploadPageState`, local durable task and outline generation hooks to `presentation-generation.ts`. A request-model-only change loses the choice in that client staging path.
2. General default generation uses `authored_presentation_generation` and `authored_presentation_pipeline` without automatically resolving a company-master CanvasContract. Reuse requires explicit per-request geometry through existing author/capture functions.
3. The production Python capture also serializes work to a renderer worker. Changing only `slide_capture.py` misses that wire boundary.
4. General final theme is written by `authored_presentation_publication`. Copies already retain the theme. Existing version restore restores slides while keeping the immutable presentation theme.
5. Several workspace remake/export consumers treat any `authored_vnext` object as a company-master projection. A canvas-only general document requires explicit master-pin detection there.
6. Public fidelity PPTX initially calls `runBundledPresentationExport` and the synced `presentation-export` v0.4.8 artifact, but the current checkout then calls `prepareFidelitySlideLayer` and `writeHybridPptx(..., artifactKind='authored-fidelity')`. That final writer calls `assembleAuthoredHybridPptxAsync` and rewrites the output. Therefore the existing `pptx-assembler.ts` is an actual final fidelity owner in this checkout, unlike the earlier design assumption. C owns its canvas generalization. This reuses the existing final writer and does not expose hybrid editing or create a separate portrait exporter. The intermediate bundle preserves portrait picture extents but writes landscape page dimensions; the existing final writer must validate and apply the selected canvas consistently before publication. No downloaded runtime file will be edited.

## Verification status

Further P0/P1 path reconciliation: public-v2 durable exports have another existing, repository-owned chain: `public_export_operation.py` -> `public_render_worker.py` -> `public_export_worker.py`. The render and export bundle contracts both need canvas propagation, including their checkpoint/cache identity. These three files are assigned exclusively to C. This is reuse of an existing path, not a new portrait storage or exporter. The absence of editable source for the intermediate Next bundle is not a blocker for these owned final writers. Actual acceptance will identify which operation path published each artifact.

P0 verified existing supervised API, generation controller and export controller PostgreSQL configuration. Existing product provider settings are preserved. PowerPoint and Poppler are installed. These environment observations are not proof of portrait generation or artifact support.

Implementation checks and real operation/artifact evidence will be appended after execution. No full suite, full build or full lint is authorized by this plan.

## Implementer checks before integrated acceptance

- A: `uv run pytest tests/unit/test_authored_exact_durable_payload.py -q`, 2 passed. The final 9.52s run includes required version, explicit-null versus absent legacy payload, and master safe-area preservation after the lead identified those contract issues.
- C: one pytest invocation for `test_isolated_public_workers.py::test_export_worker_builds_portrait_artifacts_with_exact_canvas` (PPTX/PDF) and `::test_render_worker_forwards_portrait_canvas_and_checks_png_size`, 3 passed, exit 0, 1.01s.
- D: focused AuthoredSlidePreview Cypress invocation was attempted but the execution tool exposed no final process code or test counts. Result remains unverified. No rerun was claimed. Lead browser acceptance remains pending.
- B: implementation finalization and one narrow check pending.

Collaboration tool limitation: the tool rejected creation of a new agent after A completed because it counts existing agent threads toward its thread limit. The lead used a separate read-only Codex process for E, explicitly `gpt-5.6-luna` with `medium` reasoning; the CLI reported those settings and exited 0. Its initial evidence summary is under the acceptance artifact folder. R will use a fresh read-only Sol/high process so the review is independent of A/B/C/D. At no point are more than five workers including the lead run concurrently. Product provider/model configuration is unchanged.

## Real acceptance: first operation and diagnosed integration gap

The lead admitted one actual 5-slide 9:16/default request through `/api/v1/ppt/presentation/generate/async` using the existing provider settings. PostgreSQL witnesses bind the request and its frozen canvas to the generation job and workspace operation. Four invalid combinations (unknown aspect, unsupported style, template and mode) each returned HTTP 422 before admission.

The first real attempt did not complete. All five author/render stages finished, but final persistence/export failed with `authored_local_fit_export:output_scale_mismatch`. The existing automatic generation artifact chain is another required final owner: `_build_authored_export` in `services/authored_presentation_artifacts.py` calls `utils/llm_calls/author_deck.py::build_image_pptx`. That writer still emitted landscape page EMU despite portrait capture/local-fit metadata. B owns the minimal canvas propagation fix in those existing sources. This finding came from the actual failed operation, not from a successful fixture. The quality check remains enforced; no font floor or content criterion is relaxed.

The lead will resume the same durable generation using its saved checkpoints after the fix. Completed authored slides must not be generated again. Actual export/readback and release capability remain pending. Evidence lives under `artifacts/authored-portrait-acceptance-20260913/`, including `03-status.json` and `database-witness-first-failure.json`.

## 재시도 복원에서 확인한 실제 연결 (2026-09-13)
- 실제 5장 생성의 첫 실패는 자동 산출물 경로의 가로 고정 크기였다. 해당 소유 소스 수정 후 formal workspace retry를 사용했으며, 동일 generation job과 immutable request hash를 유지한 replacement operation이 만들어졌다.
- 복원 reader는 artifact origin operation을 현재 operation과만 비교하고 있어 정상 retry ancestry를 거부했다. 저장된 checkpoint, HTML, PNG, snapshot, digest를 수정하지 않고 reader 계약을 보강한다.
- 독립 검토는 ancestor의 명시적 same-job proof와 복원 이미지의 current-canvas 검사를 추가 요구했다. 실제 PostgreSQL에서 원 operation과 replacement operation 모두 request_payload.job_id가 현재 generation job과 일치함을 확인했다.
- 일반 세로 옵션의 capability gate는 실제 수용 검증 완료 전까지 닫힌 상태다. narrow tests나 fixture 결과만으로 공개하지 않는다.

### 실제 가로 호환 확인
- 과거 canvas 메타데이터가 없던 완료 Authored 문서를 API로 읽고 브라우저에서 재열었다. detached 응답 기본값은 1280×720/16:9였으며 원 DB 문서에 기본값을 기록하지 않았다.
- 같은 문서의 공식 workspace export.pptx/export.pdf operation이 완료됐고 다운로드 SHA-256이 operation result와 일치했다.
- PPTX 내부는 12192000×6858000 EMU, PNG 1280×720이었다. PDF는 960×540pt MediaBox와 [960, 0, 0, 540, 0, 0] 배치 행렬이었다.
- 세로 재시도 reader의 다음 실제 실패는 current replacement operation의 PublicOperationAdmission 부재였다. 원 생성 operation admission만 존재하는 writer 구조를 확인하고 있으므로, 모든 retry operation에 admission이 있다는 설계 가정을 확정 계약으로 취급하지 않는다.

### 상단 헤더의 시각 검토 판정
- 실제 slide 2/3 사본의 PNG와 viewport는 모두 720×1280이고 증거 HTML/PNG digest가 동일하다. 첫 유효 픽셀은 y=32, eyebrow bbox는 y=28..48이며 glyph clipping은 없다.
- 코드 사실: models/authored_header_contract.py의 header_top_px는 기본32/허용16..96이고, utils/authored_header_prompt.py 및 header geometry/validation은 별도의 frozen header band와 viewport/ancestor clip을 사용한다. CanvasContract.safeArea를 모든 헤더 glyph의 경계로 검사하지 않는다. default.yaml이 top28을 고정한 것은 아니다.
- 설계 해석: 일반 safeArea64의 공통 prompt/fallback 본문 여백 의미와 별도 header chrome 계약을 구분한다. 현재 가로의 허용 헤더 위치를 세로 지원 때문에 새 규칙으로 거부하거나 변경하지 않는다. 이번 사본의 위쪽 배치는 출력 잘림으로 판정하지 않았다.

### 현재 노출된 출력 경로 — 앞선 번들 경로 서술의 정정
- 실제 PresentationHeader/ExportMenu의 Fidelity/PDF는 workspace command → workspace_quality_export_operation_handlers → render_and_export_authored_htmls → Python public_render_worker/public_export_worker로 끝난다.
- `/api/export-presentation` 역시 workspace command admission wrapper다. Next runAuthoredFidelityPresentationExport 및 번들/최종 assembler helper는 현재 production caller가 없으므로 사용자 수용 경로로 간주하지 않는다. 해당 소유 소스의 canvas 대응과 실제 제품 실행 증거를 구분한다.
- 실제 자동 생성 PPTX는 authored_presentation_artifacts → author_deck.build_image_pptx 경로이며, 다운로드된 5장 파일을 PowerPoint로 열어 540×960pt, PNG720×1280, 전체 내용 표시를 확인했다.
- 실제 캡처 evidence의 최소 visible owned text는 10.5pt(표지15pt), clipped text 0, local-fit quality passed였다. frozen header_top_px=28도 최종 문서에서 확인됐다.
- 세 번째 formal retry가 같은 job을 published로 완료했다. 0..3은 기존 timestamp/ref 그대로이며 4번은 render 완료 기록/ref가 갱신됐지만 원 evidence와 최종 HTML/PNG bytes가 동일하다. Author 재호출이나 accepted HTML 변경은 없었다.
- 아직 미완료: 최종 durable theme writer가 canvas를 유실해 API read가16:9로 투영하는 결함. 자동 산출물의 올바른 세로 크기는 이 저장 결함을 해결한 증거로 취급하지 않는다.

## 최종 새 run 수용 확인
- 최종 publication의 semantic projection은 기존 authored_vnext 키를 보존 병합하도록 수정했다. 기존 snapshot 내용/digest를 변경하지 않으며 새로운 semantic projection의 해당 필드만 갱신한다. 관련 좁은 테스트 1건과 독립 검토가 통과했다.
- 동일한 5장 요청으로 새 실제 provider run을 수행했다. 최초 run과 immutable request hash가 같으며 새 job은 PostgreSQL published, 문서 theme.authored_vnext.canvas는9:16/720×1280으로 최종 보존됐다. 첫 run DB를 직접 수정하지 않았다.
- 공식 workspace Fidelity PPTX/PDF operation이 모두 succeeded; 내려받은 각 파일의 SHA-256이 result와 일치했다. PowerPoint 실제 열기5장540×960pt, PDF5페이지 MediaBox540×960pt 및 동일 배치 행렬을 확인했고 모든 페이지 내용을 시각 검토했다.
- 새 run 최종 캡처는5장 모두 minimum visible owned text11.25pt, clipped text0, local-fit passed였다.
- 실제 브라우저 재열기/발표/전체 화면/대시보드 모두 확인했다. 대시보드는 외곽만 세로이고 내부 preview가 Redux default를 다시 읽던 누락을 수정해 동일 canvas를 직접 전달했다. 최종 main/thumbnail logical canvas720×1280, 대시보드 inner canvas720×1280, JS 오류0이다.
- 합성 workspace-page-preview task의 공식 poll은 `/api/v1/ppt/presentation/status/{id}`이다. generic async-task status는 generation job join 전용이므로 여기서404를 돌려준다.
- 관리형 external generation controller에는 single-page preview/publish ownership이 누락돼 실제요청이queued됐다. public_worker_profiles 및 workspace_operation_worker를 정렬해 정확히 slide.regenerate.preview와 slide.regenerate만 기존 generation controller에 연결했다. presentation.edit와 batch 작업은 포함하지 않았다. 좁은 controller test1건/독립 검토 통과 후 해당 controller만 재시작했다. 실제 remake 완료 증거는 후속 절에 기록한다.

### 단일 페이지 재생성 실제 publish에서 확인한 PostgreSQL 계약
- managed generation controller가 기존 queued preview를 정상 실행해 preview_ready, candidate720×1280을 만들었다. 이어 공식 workspace slide.regenerate publish가 실패했으며 revision1/기존 HTML/이미지는 rollback으로 유지됐다.
- 코드상 CAS는 slides.content/properties를 SQL equality로 비교했다. 실제 PostgreSQL read-only 조회로 두 컬럼이 json, json equality 연산자는없음, jsonb equality 연산자는있음, 양 컬럼의 jsonb 비교는정상임을 확인했다. SQLite 중심 좁은 테스트만으로는 이 실제 경로 결함을 발견할 수 없었다.
- 기존 slidespec_artifact_store._json_cas_predicate를 재사용해 두 JSON 비교만 PostgreSQL 호환으로 바꾸고 owner/HTML/content/properties CAS 전체 보호를 유지한다. schema/DB 데이터/admission/candidate를 직접 수정하지 않는다. 같은 완료 candidate의 공식 retry 결과는 후속 수용 기록으로 남긴다.

### 재생성 후 export freshness writer 누락
- 같은 완료 candidate의 공식 publish retry가 성공했다. revision1→2, canvas9:16유지, 앞4장HTML/이미지불변, 마지막HTML/이미지가candidate와정확히일치했다.
- revision2의 export는 worker 실행 전 authored_local_fit_export_evidence_stale로 거절됐다. theme.authoredQualityGate.localFit.slides[4].finalHtmlDigest가 이전HTML을 가리켰으며0..3은현재HTML과일치했다. canvas와PPTX/PDF geometry문제가아니다.
- resolve_authored_local_fit_receipts의 digest 검사는 올바른 fail-closed 계약이다. 수정 대상은 publish_authored_page_remake가 새검증metrics의 해당slide receipt 및 deck freshness메타데이터를 함께 갱신하도록 하는 producer 연결이다. oldreceipt삭제나consumer검증완화로통과시키지 않는다.
