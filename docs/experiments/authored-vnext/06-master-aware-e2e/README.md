# Authored vNext published-master E2E

이 vertical slice는 외부 provider, 브라우저, PowerPoint 프로세스 없이 published `DeckMasterContract`가 authored 생성 전체 수명주기의 불변 입력임을 검증한다. 입력은 [`fixture-10-slides.json`](./fixture-10-slides.json), 실행 계약은 `AuthoredVNextGenerationPipeline(repository, core_bridge)`이다.

## 완료 조건

- 10개 outline 항목이 순서대로 생성되고 첫 장은 `cover`, 명시된 구간 장은 `section`, 일반 장은 `standard` 또는 title-fit 결과에 따라 `longTitle`을 사용한다.
- model-visible payload에는 writable body slot과 body만 있으며 logo/header/footer/divider/page-number를 표현할 수 없다.
- 모든 validated SlideSpec v2 checkpoint의 master/layout registry/font/source digest가 요청과 일치한다.
- CJK 제목은 UAX #14 17.0.0, kinsoku, Windows fallback, PowerPoint margin 규칙을 거친다. clipping은 0건이고 긴 제목은 `longTitle`로 승격된다.
- slide gate의 DOM/OCR/title-fit과 deck gate의 fonts/colors/frame/pageNo/layout/slot/source 검사가 모두 통과한다. furniture-body duplicate는 0건이다.
- 10장 montage manifest가 deck 순서로 전 장을 포함한다.
- 생성과 export는 `AuthoringCoreBridgePort`를 통하며 테스트에서는 `DeterministicCoreBridge`가 실제 호출을 기록한다.
- body-only regenerate는 detached candidate를 먼저 저장하고 CAS publish한다. stale write는 head를 바꾸지 않으며 rollback은 이전 immutable revision을 새 head로 복원한다.
- retry/resume은 checkpoint의 master digest를 유지한다. repository의 published master head가 바뀌었거나 요청 digest가 stale이면 export나 publish 없이 fail-closed한다.

## 실행

저장소의 `servers/fastapi`에서 다음 focused test만 실행한다.

```powershell
uv run pytest tests/integration/test_authored_vnext_generation_e2e.py -q
```

테스트는 `tests.support.authored_vnext_master_fixture.master_contract_payload()`로 draft를 만들고 기존 master service로 validate/publish한다. 이후 `InMemoryGenerationRepository`, `DeterministicCoreBridge`, 로컬 JSON outline만 사용한다. 네트워크나 유료 provider 호출은 fixture 설계상 존재하지 않는다.

## 결과 판독

성공 시 10장 최초 revision, 한 장을 바꾼 다음 revision, rollback revision이 immutable history로 남는다. bridge call receipt의 checkpoint는 요청의 네 digest와 같아야 한다. quality report와 montage의 deck digest는 publish된 head digest와 같아야 한다.

실패 시 가장 먼저 예외 code와 repository head를 확인한다. quality 실패는 candidate 또는 deck을 publish하지 않아야 하고, CAS conflict는 재시도 전에 최신 head를 다시 읽어야 하며, stale master는 새 master를 기준으로 작업 전체를 다시 계획해야 한다.

실제 PPTX serialization은 테스트 fixture가 아닌 production `OoxmlCoreBridgeAdapter`를 통해 core의 `OoxmlMasterCompilerPort`로 전달한다. generation 계층에서 OOXML 구현을 직접 호출하는 것은 이 계약 위반이다.
