# Phase 6 생성 품질 계약 — Session E 구현 경계

기준 계약은 `public-generation-export-target-design-handoff-20260830.md`다. 이 문서는
2026-08-30 현재 작업 트리의 Phase 6 구현을 좁게 기록하며, 배포 승인이나 Hybrid
canary 활성화 문서가 아니다.

## 구현된 QualityReceiptV3 계약과 실제 generation 연결

`services/authored_quality_receipt_v2.py`의 `QualityReceiptV3`는 evidence snapshot
digest와 이전 receipt digest를 연결한 append-only record다. 같은 attempt/revision의
후속 evidence는 기존 record를 덮어쓰지 않고 sequence를 증가시킨다. V3 gate는 아래
evidence가 없거나 불일치하면 passed를 만들지 않는다.

- capability registry digest와 prompt, validator, font, Chromium, Scene, OOXML,
  readback 소비자 digest
- deterministic geometry, font, content 각각의 관측된 passed 증거 digest
- provider 최초 호출 1회와 recovery 0 또는 1회, 그리고 그 합계
- `text-image` source-content의 검증된 text/image occurrence inventory

이는 기존 V2 pre-export receipt를 소급해 바꾸지 않는 additive 경계다.

`services/authored_quality_receipt_v3_pipeline.py`와
`authored_presentation_generation.py`가 실제 authored generation의 V2 receipt를 검증한 뒤
V3로 전환한다. V3 durable ledger는 V2 manifest 옆의 별도 파일이며, publication input의
`authoredQualityGate.qualityReceiptV3`에 아래 값을 함께 봉인한다.

- slide별 provider `initial=1`, `recovery=0|1`, `total`
- 최종 Chromium metrics의 capability registry metadata
- source-content reflow일 때 manifest text/image occurrence와 browser readback binding
- V2 receipt digest, V3 evidence digest, 이전 V3 receipt digest
- qualityStatus와 별도의 `releaseReadiness=blocked`, `promotionReady=false`

V3 품질 차단은 generation 성공 상태와 동일시하지 않는다. T1 evidence가 없으면 V3는
blocked로 기록되지만 기존 deterministic V2 publication 결과를 소급 변경하지 않는다.

## Tier와 T1 승격

T0는 deterministic geometry/font/content 검사, T1은 risk-based visual review,
T2는 full-deck visual review, T3는 PowerPoint와 LibreOffice desktop parity를
요구한다. cover/closing, dense table/chart, layout capacity 80% 이상, repair,
CJK font warning, overlap/overflow 근접, 큰 raster island/Hybrid 경계, provider
recovery는 T1 trigger다. Trigger가 있는 evidence를 T0로 기록하면 V3 gate는
`tier.t1_required`로 차단한다.

T2/T3는 `fullDeck=true`인 passed visual review가 필요하며, T3는 PowerPoint와
LibreOffice readback 모두 passed여야 한다.

## 공유 capability registry

`services/authored_vnext/capability_registry.py`는 Scene capability, native/raster
subset, validator admission feature, authored font floor, prompt clause와 registry
digest를 한 registry payload에서 파생한다. 소비 adapter는 prompt, semantic
validator, font policy, Chromium renderer metadata, Scene compiler, OOXML projection,
readback metadata다. 지원하지 않는 native feature는 registry에 따라 explicit
raster-only 또는 unsupported로 판정되어야 하며, 이 registry 자체가 Hybrid 선택이나
silent Fidelity downgrade를 의미하지 않는다.

## Source-content text-image 보존 한계

`services/source_content/coverage.py`와 `publication.py`의 source-content receipt는
digest-bound `sourceContent` inventory를 봉인한다.

```json
{
  "sourceMode": "text-image",
  "sourceCompleteClaim": false,
  "preserved": ["text", "image-occurrences"],
  "excluded": ["tables", "charts", "original-layout"]
}
```

tables/charts와 original-layout shape 수는 제외 객체 수로 기록할 뿐, 보존 완료라고
주장하지 않는다. publication gate는 위 mode, false claim, preserved/excluded 집합이
정확히 일치하지 않으면 통과시키지 않는다. 구조 보존을 주장하려면 별도 capability와
새 evidence/contract가 필요하다.

## 검증 증거

- 실행됨: `servers/fastapi/.venv/Scripts/python.exe -m pytest -q
  tests/unit/test_source_content_publication_gate.py` — 2 passed. pytest cache의
  Windows access-denied warning 1건은 남았으나 test result에는 영향을 주지 않았다.
- 실행됨: `test_authored_vnext_capability_registry.py` — 현재 변경 후 3 passed.
  stale digest, consumer 오표기, validator feature drift를 차단한다.
- 실행됨: `test_authored_vnext_ooxml_capability_binding.py::test_ooxml_registry_binding_is_mode_independent_and_tamper_closed`
  — 1 passed. Fidelity/Hybrid OOXML과 readback registry tamper를 직접 확인했다.
- 실행됨: `test_authored_quality_receipt_v3_pipeline.py` — 3 passed. 실제 generation
  call-site, V2→V3 transition, slide provider budget, source occurrence/readback,
  export-service registry adapter를 확인했다.
- `test_authored_quality_receipt_v2.py`는 23 passed 후 Windows pytest temp root
  `WinError 5`로 5개가 setup되지 않았다. assertion failure는 없었지만 V3 durable
  reload/hash-chain을 포함한 tmp-path 5개는 이번 세션에서 완전 통과로 세지 않는다.
- 기존 integration fixture
  `test_export_service_runs_worker_buffer_readback_and_atomic_publication`은 registry seam에
  도달하기 전 fixture deck의 Scene 누락(`authored_vnext_scene_required`)으로 실패했다.
  이 실패는 현재 product-path integration 미확인 범위로 남긴다.

## Phase 7 readiness gap 및 비활성화 경계

Phase 7 Editable Hybrid canary는 이 문서로 승인되지 않는다. 활성화 전에는 다음의
product-path evidence가 별도로 필요하다.

- 모든 exported element의 native 또는 raster ownership map
- OOXML 구조 검사와 slide별 raster reason
- PowerPoint 및 LibreOffice desktop readback evidence
- explicit Fidelity/Hybrid 선택 결과와 fallback 결과의 일치
- canary/rollback 정책과 독립적인 운영 승인

이 Session은 canary, traffic activation, production publication, deployment를 수행하지
않았다. Hybrid 오류를 Fidelity로 조용히 바꾸는 동작도 이 계약의 허용 범위가 아니다.

Public v2 요청 계약은 Fidelity를 기본으로 두고 Hybrid에 명시적 mode/fallbackPolicy를
요구한다. 이번 레인은 public route를 수정하지 않았고, OOXML 레이어에서는 정확한 Hybrid
profile과 완전한 owner plan이 없으면 native promotion을 시작하지 않는다.

추가 미확인 범위는 실제 실행 중인 PPT-agent listener가 없어 API→worker→publication
live run을 하지 못한 점, operation 전체 provider stage를 아우르는 별도 cardinality
budget, PowerPoint/LibreOffice desktop readback, canary/rollback이다. 따라서
`promotionReady=false`와 `releaseReadiness=blocked`를 유지한다.
