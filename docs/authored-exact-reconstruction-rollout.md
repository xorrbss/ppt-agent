# Exact reconstruction 제한 배포·호환성 체크리스트

## 배포 원칙

`exact_reconstruction`은 일반 authored 생성의 새 기본값이 아니다. 저장된
스타일 프리셋의 private `slide_reference_images`가 슬라이드 수와 정확히
일치하고, 각 파일이 서로 다르며 읽을 수 있을 때에만 사용할 수 있다.
그 밖의 모든 기존 문서와 스타일은 `style_reference` render-only 검수
동작을 유지한다.

현재 공개 authored style 응답은 `reference_mode`, `slide_reference_images`,
role별 원본 참조 경로를 반환하지 않는다. 따라서 공개 API 계약은
변경하지 않는다. exact 스타일은 아래 서버측 gate와 allow-list를 모두
통과해야만 목록·선택·생성 경로에 들어간다.

## 구현된 활성화 gate

canary는 다음 두 설정을 명시적으로 활성화한 뒤에만 시작한다.

- 기본값 disabled인 `AUTHORED_EXACT_RECONSTRUCTION_ENABLED`.
- 활성화된 경우에도 `AUTHORED_EXACT_STYLE_IDS` allow-list에 든 preset만
  해석한다.
- gate가 꺼져 있으면 해당 preset은 style 목록과 선택/생성 경로 모두에서
  제외하거나 명시적 4xx를 반환한다. 자동 `style_reference` 하향은 금지한다.
- gate는 생성·export에 검수를 자동으로 붙이지 않는다. 기존의
  `/quality-review` opt-in만 검수 실행점으로 유지한다.

이 gate와 fail-closed 검증은 구현·테스트되었지만, 이번 변경에는 실제
운영 preset이나 배포 설정 변경이 포함되지 않는다. 그러므로 현재 상태는
**코드/테스트 준비 완료, 운영 exact preset 배포 보류**다.

## Canary 전 검증

- 이전 authored 문서(theme에 `reference_mode` 없음)를 열고 quality review
  `analyze_only`와 `analyze_and_fix`를 실행한다. render-only 호출 수와
  결과가 기존 기준과 같은지 확인한다.
- exact preset마다 `slide_reference_images` 개수 = outline slide 수,
  모든 실경로 고유성, 파일 읽기, PNG/JPEG/WebP magic bytes를 배포 전에
  검사한다. 하나라도 실패하면 시작하지 않고 명시적으로 실패해야 한다.
- same-role 슬라이드가 2장 이상인 표본에서 요청별 source/render 쌍의
  순서와 SHA-256을 기록해 1:1 매핑을 확인한다.
- exact `analyze_only`는 슬라이드당 비교 1회여야 한다. exact
  `analyze_and_fix`는 최초 비교 1회, 수정 후보만 최종 비교 1회여야 한다.
  style mode의 호출 수를 늘리지 않는다.
- 수정 후보는 source-vs-render critique score가 엄격히 개선될 때만
  반영되는지, 거부 시 HTML/PNG/버전 스냅샷이 바뀌지 않는지 확인한다.
- 허용된 수정 전에는 version snapshot이 생성되고, restore 후 authored
  HTML과 image metadata가 복구되는지 확인한다.
- Windows와 Linux runner에서 forward-slash asset path, PNG/JPEG/WebP source
  MIME 감지, Chrome 렌더를 각각 확인한다. MIME은 확장자가 아니라 bytes로
  판별한다.

## 관측 및 중단 기준

- 기록: mode, model, critique prompt/cache version, source/render hash(원문
  bytes는 로그 금지), 슬라이드 수, critique 호출 수, render·critique·수정
  시간, 수정 시도/수락/거부 수, 검수 오류 수.
- 표본별 OCR 문자 일치율과 bounding-box/레이아웃 차이를 사람이 함께
  검토한다. 사진·로고·설치되지 않은 폰트는 별도 위험군으로 분류한다.
- source 누락/중복/개수 불일치, critique 판정 불가, source fidelity 악화,
  예상보다 많은 critique 호출이 발생하면 canary를 중단하고 해당 문서는
  기존 HTML을 유지한다.

## 롤백

- gate를 끄고 exact preset을 운영 배포물에서 제거한다. 저장된 기존 문서는
  자동 재생성하거나 style mode로 조용히 전환하지 않는다.
- 이미 수락된 수정은 presentation version history에서 review 전 snapshot을
  restore한다. restore 전 현재 상태도 기존 서비스가 snapshot하므로 되돌림도
  다시 되돌릴 수 있다.
# Runtime observation event (content-free)

Each opt-in high-quality-review stage emits one structured
`authored_quality_review` JSON log event. The event never includes authored HTML,
slide text, prompts, image bytes, paths, job identifiers, or model responses.

- `source_batch_sha256` and `render_batch_sha256` are ordered batch fingerprints:
  SHA-256 over length-prefixed per-image SHA-256 digests. Thus they prove source /
  render pairing and slide order without exposing source content. The source field is
  null in `style_reference` because that mode keeps its render-only contract.
- `reference_mode`, reviewer `model`, `prompt_version`, `cache_version`, slide and
  attempt/accept/reject/indeterminate counts, rejection reasons, and the two
  critique-count fields make canary decisions auditable. `critique_batch_count`
  counts calls to the existing `critique_authored` batch API. Each batch fans out to
  per-slide cached critiques; `critique_slide_request_count` equals `cache_misses`
  and is a conservative per-slide remote-request estimate, not an exact remote-call
  counter.
- `render_ms`, `critique_ms`, and `revision_ms` are monotonic wall-clock durations.
  `ocr_executed=false` and `ocr_ms=0` are deliberately fixed: OCR is not part of the
  production high-quality-review path.
- `cache_hits` and `cache_misses` are sampled immediately before each existing
  critique call using the existing cache key. They add no VQA request and count an
  in-flight coalesced request as a miss (no completed cache result existed), so
  coalescing can make actual remote calls lower than
  `critique_slide_request_count`.

For exact `analyze_only` over N slides, the log therefore records one critique batch
and at most N estimated per-slide requests. Exact `analyze_and_fix` records that same
initial batch plus one final batch only when there are revision candidates; the final
batch and its per-slide estimate cover only those changed candidate slides. These
counts do not alter the preserved render-only style-mode call path.

Operational stop/rollback rule: stop the exact canary immediately for any source
mapping failure, unexpected critique-call count, indeterminate comparison, or
non-improving accepted revision. Disable/remove the exact preset from the allow-list;
do not silently switch saved exact documents to style mode. Restore a reviewed deck
from its normal version snapshot when a previously accepted revision must be undone.
