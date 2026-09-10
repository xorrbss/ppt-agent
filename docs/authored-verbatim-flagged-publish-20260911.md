# Authored verbatim flagged publish 확인 기록

작성일: 2026-09-11

상태: 최신 코드와 기록된 artifact를 대조한 구현·제품 경로 확인 기록이다. 아래는 확인 가능한 사실만 적었으며, 시각적 승인이나 Hybrid editable export 성공을 의미하지 않는다.

## 1. 검토 결론과 결정

2026-09-10 검토에서는 동일한 35-page 입력의 full run 다섯 건이 서로 다른 slide set에서 모두 실패했고, 실패를 따라가며 slide-shaped helper가 누적되고 있다는 결론에 도달했다. 당시의 구조적 품질 경계와 중간 보정의 한계는 [`docs/authored-structural-quality-repair-v3-design-20260910.md`](authored-structural-quality-repair-v3-design-20260910.md)와 다음 구현 이력에서 확인된다: `919071f92`, `5b438b4e8`, `7ff1a9e18`.

사용자는 source integrity가 완전한 verbatim deck이면 결함 slide를 flag한 상태로 publish하기로 결정했다. 최초 결정은 `.spec-memory/decisions/authored.verbatim-flagged-publish.1.json` (revision 1, 2026-09-10)이며, rev-2는 visibility-only 실패를 이미 flag된 slide의 flag reason으로 좁혔다: `.spec-memory/decisions/authored.verbatim-flagged-publish.2.json` (revision 2, 2026-09-11). 따라서 missing, changed, duplicated, reordered source block이나 불완전한 browser telemetry는 publish 사유가 될 수 없다.

## 2. 운영 knob과 fail-closed 경계

이 동작의 knob은 `AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW`이다. registry의 default는 `false`이고 대상은 `fastapi.runtime`이다 (`servers/fastapi/constants/settings_registry.json`, `servers/fastapi/utils/get_env.py`). Windows local stack에서는 값을 DPAPI-protected stack store의 `api`와 `worker-generation` sections에 각각 설정해야 한다. `scripts/start-local-stack.ps1`가 읽는 `app_data/public-v2-local/local-stack-env.dpapi.xml`의 component 환경을 사용하며, controller는 repository-root `.env`를 읽지 않는다.

세 fail-closed seam은 다음과 같이 구현 및 기록되어 있다.

1. bounded repair 뒤 terminal slide를 re-author하지 않고 source reflow fallback으로 보낸다: `e3de7de85`, `d764f8968`, `744305782`.
2. VQA residual을 terminal slide fallback으로 라우팅한다: `204b0ab68`.
3. strict source readback을 유지하면서 flag된 slide의 visibility-only 실패만 publish receipt에 기록한다: `9fd93f132`.

관련 보강과 조사 경계는 `b25f3dc12` (empty/non-rendering content declaration stripping), `5b438b4e8` (structural repair proposal instrumentation), `919071f92` (repair core 분리), `17b2b140d` (managed render concurrency 상속)에 기록되어 있다. shape-specific helper는 `wip/authored-shape-helpers-20260910`의 `7ff1a9e18`에서 quarantine되었다. 이 문서는 해당 branch를 통합되었다고 주장하지 않는다.

## 3. Full product validation run 7–11

입력과 옵션은 각 run의 `04-generate-request.json` 및 `06-final-status.json`에 기록되어 있다. 실패 코드는 `06-final-status.json`의 `error.code` 또는 `failure.code`를 그대로 옮겼다.

| run | final status | 결과 / failure code |
| --- | --- | --- |
| 7 | `error` | `authored_composition_safety_gate_failed` |
| 8 | `error` | `authored_slide_author_failed` (cause: `authored_renderer_css_property_budget_exhausted:CSS property is not allowed: content: ""`) |
| 9 | `error` | `authored_vqa_residual_dom_issues` |
| 10 | `error` | `source_content_browser_readback_failed` |
| 11 | `completed` | flag-only fallback artifact download evidence 확보; quality receipt에는 7 review/flagged slides가 기록됨 |

근거 디렉터리: `artifacts/authored-full-product-validation-20260910/run-7/06-final-status.json`부터 `run-11/06-final-status.json`까지.

run-11의 기록은 `source-verbatim`, 35 slides, `checkedSlideCount: 35`, `checkedBlockCount: 289`, `missingBlockIds: []`, `orderViolations: 0`, `integrityVerified: true`를 담는다. `passed: false`와 `verified: false`도 함께 남아 있으며 visibility-only 실패가 `flaggedVisibilityFailures`로 분리되어 있다. `flaggedSlides`와 `pageSummary.reviewRequired`는 7개이다. 18:06:25Z 시작에서 18:18:41Z 종료까지 736초가 걸렸다.

`run-11/07-download-evidence.json`은 실제 다운로드된 image-per-slide PPTX의 path, 2,108,093 bytes, SHA-256를 기록한다. 이 run에서 산출물은 image-per-slide PPTX이며, Hybrid editable export는 실행되지 않았다. 따라서 이 artifact로 Hybrid 편집 가능성, native coverage, 또는 시각적 동등성을 주장하지 않는다. `06-final-status.json`의 quality receipt가 `quality_gate_status: blocked` 및 `publicationStatus: pending`도 기록한다는 점 역시 보존한다.

## 4. Read-only probe와 audit 기준

좁은 checkpoint 재현은 `servers/fastapi/scripts/authored_checkpoint_repair_probe.py`와 이를 DPAPI stack store의 `worker-generation` 환경으로 감싸는 `servers/fastapi/scripts/run_authored_checkpoint_repair_probe.ps1`를 사용한다. probe는 기록된 checkpoint를 읽고 candidate/measurement를 조사하는 read-only 경로이며, secrets를 출력하거나 저장하지 않는다.

`artifacts/authored-full-product-validation-20260910/audit-full-run.ps1`의 새 기준은 document completion, 35 checked slides, 289 checked source blocks, `integrityVerified` (기존 `passed+verified`도 허용), missing block 0, order violation 0, visibility failure와 flagged visibility failure의 완전한 대응, 그리고 PPTX 다운로드/hash/readability를 각각 검사한다. 즉 flag는 integrity gate를 우회하지 않고, visibility-only defect의 귀속을 검사하는 별도 조건이다.

## 5. 남은 항목

- flagged deck의 Hybrid editable export를 실제 제품 경로에서 실행하고 readback을 확보해야 한다.
- 생성 결과에 invented empty image panel이 생기는 경로를 조사해야 한다.
- 두 pre-existing over-cap file은 아직 남아 있다.
- `servers/fastapi/tests/unit/test_authored_pipeline.py`의 pre-existing failing tests 6개는 아직 해결·재검증되지 않았다.

이 기록의 범위는 문서와 위 artifact/commit 근거의 정리뿐이다. 코드 변경, service restart, secret 변경, 또는 외부 게시를 수행하지 않았다.
