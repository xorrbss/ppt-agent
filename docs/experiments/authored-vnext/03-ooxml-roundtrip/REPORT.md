# GenOffice `pptx-engine` OOXML round-trip PoC 결과

검증일: 2026-08-13 (Asia/Seoul)  
범위: 제품 소스와 분리된 `docs/experiments/authored-vnext/03-ooxml-roundtrip/` 전용 harness

## 결론

현재 pin은 **buffered no-op open-save 실험에는 통과**했지만, 제품의 일반 편집 adapter로 채택하기에는 **NO-GO**다.

- 기존 로컬 90장과 새 synthetic 90장의 buffered no-op은 uncompressed part payload 변화 0, 관계 closure 변화 0, 누락 part 0을 충족했다.
- Git 추적된 실제 프로젝트 PPTX 6개(15장 5개, 20장 1개)도 no-op에서 같은 기준을 통과했다.
- 기존 90장 targeted text edit는 대상 `slide1.xml` 하나만 바뀌었지만 `<a:t>` 외에 `i="0"`, `b="0"`가 물질화되어 strict allowlist를 실패했다. 이 pin의 text mutation은 중단 대상이다.
- master/layout은 Read와 기존 master의 제한적 Update만 증거화됐다. Create/Delete 공개 API가 없으므로 full CRUD 요구에는 부적합하다.
- `savePptxToFile` streaming 결과는 part payload/closure 검사를 통과했지만 LibreOffice가 기존 90장을 열지 못했다. facade는 검증된 buffered `savePptx`만 사용한다.
- buffered 결과는 LibreOffice 26.2.5.2에서 90장 read-back에 성공했다. Windows PowerPoint는 기존 사용자 프로세스를 보호하기 위해 자동 검증이 차단됐고, `repair dialog = 0`은 확인하지 못했다.

따라서 허용 가능한 다음 단계는 exact pin + fail-closed facade를 유지한 **read-only/no-op lab 평가**뿐이다. 제품 text mutation, streaming save, full master CRUD, PowerPoint 호환성 주장은 차단한다.

## 공식 pin, 라이선스, 도입 경계

- 공식 저장소: <https://github.com/genspark-ai/genoffice>
- exact SHA: [`dc4d7e5927864498913b7ba42d0da06cc7cf628e`](https://github.com/genspark-ai/genoffice/commit/dc4d7e5927864498913b7ba42d0da06cc7cf628e), 확인 당시 공식 `main` HEAD 및 tag `v0.6.279`
- 허용 소스: [`packages/pptx-engine`](https://github.com/genspark-ai/genoffice/tree/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine) 37 files
- 런타임 dependency: `fast-xml-parser@5.10.1`, `jszip@3.10.1`
- 라이선스: [Apache-2.0 LICENSE](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/LICENSE), [upstream NOTICE](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/NOTICE)
- 차단: `apps/**`, `ee/**`, 다른 `packages/**`, Electron/AI 앱, branding, 다른 `@genoffice/*`

`compliance/check-compliance.mjs`는 pin/origin/sparse allowlist, LICENSE/NOTICE hash, runtime import, SBOM closure, forbidden path와 vendored 37 files의 exact bytes를 검사한다. 현재 결과는 `PASS (17 checks)`이며, SBOM은 SPDX 2.3 production closure 22 packages/25 relationships다. 상세 근거는 `compliance/README.md`, `compliance/upstream-pin.json`, `compliance/sbom.spdx.json`에 있다.

## 격리 facade와 corpus

`src/genoffice-facade.mjs`만 제품 후보 API 경계로 간주한다. GenOffice의 `OpenedPptx`, `Slide`, `PackageArchive`, mutable `archive.entries`는 외부로 반환하지 않고 JSON-safe DTO만 제공한다. 매크로는 parse 전에 quarantine하며 feature mutation API는 노출하지 않는다.

| Corpus | 분류 | 슬라이드 | 비고 |
|---|---|---:|---|
| `.codex-tmp/export-audit-dfec1e94/original-export.pptx` | 기존 로컬 Presenton 생성 산출물 | 90 | Git 미추적, blank metadata, 고객 파일로 간주하지 않음. 외부 전송 없음 |
| `artifacts/synthetic-90.pptx` | 새 synthetic | 90 | GenOffice blank + insert로 생성 |
| `docs/artifacts/**` 6개 | Git 추적 프로젝트 공개 corpus | 15×5, 20×1 | 모두 no-op 검사 |
| `artifacts/policy-features*.pptx/pptm` | synthetic feature fixture | 90 | macro/external/SmartArt/OLE/animation/comments/custom XML/unknown evidence 전용 |

PPTX input은 모두 로컬에서만 처리했다. 네트워크는 공식 GenOffice source/metadata 확인에만 사용했으며 PPTX는 전송하지 않았다.

## 결과표

| Case | Parts / topology | Untouched 변화 | Closure 변화 | 누락 | Office read-back | 판정 |
|---|---|---:|---:|---:|---|---|
| 기존 90장 buffered no-op | 308; slide 90, master 1, layout 11, media 91, unknown 0 | 0 | 0 | 0 | LibreOffice 90→90 PASS | **PASS** |
| synthetic 90장 buffered no-op | 189; slide 90, master 1, layout 1 | 0 | 0 | 0 | LibreOffice 90→90 PASS | **PASS** |
| Git 추적 real corpus 6개 no-op | 97 parts×5, 117 parts×1 | 0 | 0 | 0 | 15장 대표 LibreOffice PASS; 나머지는 package read-back | **PASS** |
| 기존 90장 targeted text | 308; `slide1.xml` 1 part 변경 | 1 | 0 | 0 | LibreOffice 90→90 PASS | **FAIL** — text 외 formatting normalization |
| 기존 master Read | master 1, layout 11 열거 | 0 | 0 | 0 | package read-back | **PASS** |
| 기존 master Update name probe | master XML 1 part만 allowlisted 변경 | 0 | 0 | 0 | LibreOffice 90→90 PASS | **PASS (제한적)** |
| master/layout Create/Delete | 공개 API 없음 | — | — | — | 실행 전 reject | **UNSUPPORTED / fail-closed** |
| 기존 90장 streaming no-op | uncompressed payload 변화 0 | 0 | 0 | 0 | LibreOffice `source file could not be loaded` | **FAIL** |
| Windows PowerPoint reopen | executable 16.0.20228.20158 확인 | — | — | — | 기존 PID 61264 때문에 안전 차단 | **미검증** |

targeted edit의 exact 원인 분류는 `xml-structural-or-formatting`이다. `<a:t>` 36개 중 요청된 1개만 텍스트가 바뀌었지만, changed span은 274→301 bytes였고 기존에 없던 `i="0"`, `b="0"` 속성이 추가됐다. LibreOffice가 열 수 있다는 사실은 unrelated preservation 실패를 상쇄하지 않는다.

## 특수 feature 정책과 증거

| Feature | facade 정책 | 실험 증거 |
|---|---|---|
| Macro/VBA | **quarantine** | synthetic `.pptm`에서 검출 후 `QUARANTINE_MACRO_PACKAGE`로 parse 전 reject |
| External relationship | **preserve inert / no dereference**, mutation reject | synthetic 1 external edge; no-op exact preservation, closure 0 |
| SmartArt | **preserve pass-through**, mutation reject | diagram part 1; no-op exact preservation |
| OLE | **preserve pass-through**, mutation reject | embedding part 1; no-op exact preservation |
| Animation | **preserve pass-through**, mutation reject | timing marker 1 slide; no-op exact preservation |
| Comments | **preserve pass-through**, mutation reject | classic comment parts 2; no-op exact preservation |
| Custom XML | **preserve pass-through**, mutation reject | custom XML part 1; no-op exact preservation |
| Unknown part | **preserve opaque**, mutation reject | synthetic unknown part 1; no-op exact preservation |

non-macro policy fixture는 195 parts에서 feature/unknown payload 변화 0, closure 변화 0, 누락 0으로 통과했다. 이는 synthetic preservation 증거이며 실제 고객의 복잡한 SmartArt/OLE/modern comments semantic 편집 지원을 뜻하지 않는다.

## PowerPoint, LibreOffice, package 검증의 분리

1. **Package/read-back**: `tools/ooxml-audit.mjs`가 ZIP을 추출하지 않고 part SHA-256, content types, relationship edge/closure, topology, feature evidence를 비교한다. no-op은 changed part 0만 허용하고 text는 `<a:t>` byte skeleton 외 변화를 실패시킨다.
2. **LibreOffice**: `tools/libreoffice-readback.ps1`이 격리 프로필과 disposable copy를 사용해 변환 후 OOXML과 slide count를 다시 읽는다. buffered 기존/synthetic 90장과 edit/master probe는 90→90으로 통과했다. 이 결과는 PowerPoint repair dialog 증거가 아니다.
3. **PowerPoint 자동 read-back**: `tools/windows-reopen.ps1`은 read-only/non-visible COM, macro force-disable, save call 0으로 설계됐다. 기존 사용자 PowerPoint 프로세스가 있으면 attach/quit하지 않고 차단한다. 이번 run은 이 조건으로 미실행이다.
4. **PowerPoint `repair dialog = 0`**: non-visible COM으로는 신뢰할 수 있게 관찰할 수 없다. `WINDOWS-REOPEN.md`의 수동 witness 절차대로 disposable copy를 visible UI에서 열고, repair/복구 대화상자가 없음을 사람이 기록해야 한다.

공식 동작 근거는 Microsoft [`Presentations.Open`](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentations.open), [`AutomationSecurity`](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.application.automationsecurity), LibreOffice [시작 매개변수](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)다.

## Kill criteria 적용 결과

현재 실제로 발동한 kill criteria:

1. targeted text edit가 승인된 `<a:t>` 외 formatting을 바꿈 → **text mutation NO-GO**
2. streaming output을 LibreOffice가 기존 90장에서 거부 → **streaming save NO-GO**
3. master Create/Delete가 필요한 full CRUD → **현재 pin NO-GO**
4. PowerPoint reopen/repair dialog 0 witness 없음 → **PowerPoint 호환성 주장 금지**

계속 유지할 fail-closed 조건:

- no-op part 추가/누락/변화 1개 이상, relationship/content-type/closure drift 1개 이상
- external target/TargetMode 변화 또는 네트워크 dereference
- macro를 quarantine 밖으로 출력
- SmartArt/OLE/timing/comments/custom XML/unknown payload 또는 관련 rel 변화
- exact pin, LICENSE/NOTICE, SBOM drift 또는 `ee`/앱/Electron/AI/branding 유입
- 실제 배포 NOTICE와 third-party license 검토 미완료

## 확인 사실, 추론, 미검증

확인 사실은 위 JSON audit, local Office 실행 결과, exact-pin source와 compliance 검사에 근거한다. “streaming ZIP 컨테이너 호환성 문제”는 uncompressed payload가 동일하지만 LibreOffice 결과가 buffered와 달랐다는 관찰에 기반한 원인 **추론**이며, JSZip/LibreOffice 내부 원인을 확정한 것은 아니다.

미검증 항목:

- PowerPoint 자동 COM reopen과 visible `repair dialog = 0`
- 실제 고객 90장 및 실제 macro/SmartArt/OLE/animation/modern comments corpus
- full master/layout Create/Delete
- external SPDX validator, 법률 검토, 실제 배포물의 generated third-party notice
- 90장을 넘는 대용량 deck에서 buffered save의 메모리 상한

## 주요 증거 파일

- `results/existing-90-noop-buffered.audit.json`
- `results/synthetic-90-noop-buffered.audit.json`
- `results/existing-90-text-edit-buffered.audit.json`
- `results/existing-90-master-update-buffered.audit.json`
- `results/existing-90-noop-streaming.audit.json`
- `results/policy-features-no-macro-noop.audit.json`
- `results/policy-facade-evidence.json`
- `results/existing-90-noop-buffered.libreoffice.json`
- `results/existing-90-noop-streaming.libreoffice.json`
- `results/existing-90-noop.powerpoint.json`
- `results/operation-results.json`
