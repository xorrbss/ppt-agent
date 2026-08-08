# PPT Master × Template V2 통합 리포트 및 실행 프롬프트

작성일: 2026-08-02
분석 대상: `hugohe3/ppt-master` commit `8858c4960b870f12baf952994fbd0c76ab5ba4ec`

## 결론

신규 템플릿 선택 화면은 Template V2 전용으로 전환하고, 기존 저품질 레이아웃·사용자 정의·adaptive 템플릿 카드는 숨긴다. 다만 기존 문서에 저장된 V1 템플릿 ID와 AI 저작 모드는 삭제하지 않고 직접 조회·재생성 호환 경로로 유지한다.

PPT Master의 핵심 가치는 화면 스타일을 복사하는 데 있지 않다. 원본 PPTX의 슬라이드 마스터, 레이아웃, 테마, 관계, 미디어를 보존한 상태에서 네이티브 텍스트와 표만 교체해 PowerPoint 편집 가능성을 유지하는 것이다. Template V2는 이 원칙을 `native_clone` 보존 모드와 출판 전 품질 게이트로 수용한다.

외부 예제 4종은 디자인 후보로 등록하되 기본 공개하지 않는다. 저장소 코드의 MIT 라이선스는 확인됐지만 예제에 포함된 사진·생성 이미지·파생 SVG/PPTX의 개별 출처와 재배포 권리가 별도로 확인되지 않았기 때문이다. 권리 증거와 네이티브 사전 검증을 모두 통과한 항목만 신규 UI에서 발견 가능해야 한다.

## 분석한 디자인 후보

| Template V2 후보 | PPT Master 원본 | 분류 | 현재 상태 | 차단 이유 |
|---|---|---|---|---|
| Swiss Grid | `examples/ppt169_swiss_grid_systems` | Layout | blocked | 래스터 이미지와 파생 SVG/PPTX의 개별 권리 미확인 |
| Editorial Magazine | `examples/ppt169_pritzker_2026` | Deck | blocked | 건축 사진의 출처·라이선스·귀속 정보 미확인 |
| Data Journalism | `examples/ppt169_global_ai_capital_2026` | Deck | blocked | 이미지 및 파생 산출물의 재사용 권리 미확인 |
| Glassmorphism SaaS | `examples/ppt169_glassmorphism_demo` | Brand | blocked | 이미지 및 파생 산출물의 재사용 권리 미확인 |

카탈로그는 원본 저장소 URL, 고정 commit SHA, 원본 경로, 근거 파일 SHA-256, 저장소 라이선스, attribution, importer 버전, capability 및 자산 권리 검토 결과를 기록한다. 런타임 네트워크 다운로드와 외부 자산 자동 복사는 금지한다.

## 목표 구조

1. 신규 UI는 공개 가능한 Template V2만 목록에 표시한다.
2. 정확히 하나의 `is_default`가 있을 때만 신규 흐름에서 자동 선택한다.
3. 기본 템플릿이 없거나 둘 이상이면 legacy/adaptive로 대체하지 않고 오류 상태로 닫는다.
4. 기존 V1 저장값은 rehydrate와 직접 조회를 계속 지원한다.
5. `native_clone`은 원본 PPTX를 template ID/revision 범위에 내구 저장하고 SHA-256과 크기를 검증한다.
6. 출력 시 원본 OOXML 패키지의 마스터·레이아웃·테마·관계·미디어를 유지하고, 허용된 텍스트·표 슬롯만 패치한다.
7. 지원하지 않는 요소, 누락된 근거, 무결성 오류가 있으면 일반 exporter로 우회하지 않고 생성 전에 실패한다.
8. `reconstructed` 템플릿도 deterministic compiler preflight, package/structural digest, `unsupported: null`이 있어야 공개한다.

## 출판 계약

`TemplateV2.assets.native_pptx`에는 최소한 다음 정보가 필요하다.

```json
{
  "schema_version": "presenton.template-v2-native-source/v1",
  "preservation_mode": "native_clone",
  "storage_key": "template-v2/native/<template-id>/revision-<n>/source.pptx",
  "source_sha256": "<64 lowercase hex>",
  "source_size_bytes": 12345,
  "layout_source_parts": {
    "title-body": "ppt/slides/slide1.xml"
  },
  "capabilities": {
    "native_clone": true,
    "text_patch": true,
    "table_patch": true,
    "master_layout_theme": true,
    "relationships_media": true
  },
  "publication_status": "publishable"
}
```

누락·오형식·unknown mode·blocked 상태는 모두 비공개다. 이 판정은 신규 discovery에만 적용하며 기존 저장 문서의 직접 읽기에는 적용하지 않는다.

## 2026-08-02 구현·검증 상태

- Data Journalism 후보의 네이티브 프레임에 Stanford HAI, IEA, NVIDIA, Alphabet, OpenAI, Anthropic 공식 데이터를 채운 20장 제안 덱을 `docs/artifacts/ppt-master-data-journalism/ai-capital-2026-data-journalism.pptx`로 추가했다. 3개 래스터 이미지와 로고·직접 인용은 제거했고, 슬라이드별 발표자 노트에 URL·확인일·계산 정의를 기록했다.
- 제안 덱은 마스터 1개, 레이아웃 11개와 편집 가능한 도형을 유지한다. 20/20 렌더 및 몽타주 육안 검수, 템플릿 프레임 매핑과 충실도 검사를 통과했고 `ppt/media` 항목은 0개다.
- 이 산출물은 운영 검토용이며 권리 승인이나 신뢰된 HMAC attestation을 대신하지 않는다. 따라서 정적 카탈로그와 신규 UI의 공개 상태는 계속 `blocked`다.
- 신규 템플릿 선택 화면과 대시보드는 Template V2 목록만 표시하며 AI 저작 모드는 별도 섹션으로 유지한다.
- Redux 신규 기본값은 `null`이고, 공개 목록에 정확히 하나의 `is_default`가 있을 때만 자동 선택한다. 기존 저장 선택값은 덮어쓰지 않는다.
- PPTX import 확인 시 원본을 template ID/revision 범위의 내구 저장소로 승격하고 digest·크기·source slide part를 기록한다.
- `native_clone` 생성은 원본 OOXML을 바탕으로 텍스트·표 슬롯을 패치하고, PPTX 요청에는 사전 검증된 동일 바이트를 직접 내보낸다.
- 비장식 TextList는 문단 수가 원본과 같은 고정 cardinality일 때만 편집하며 `bodyPr`, `lstStyle`, 문단·run 서식과 공백을 보존한다. 문단 수 변경, 필드, 줄바꿈, 하이퍼링크는 fail-closed한다.
- 이미지와 차트 편집은 관계 교체가 구현되지 않았으므로 명시적인 unsupported 오류로 차단한다.
- 공개 목록은 rollout allowlist, 정적 카탈로그 상태, publication evidence를 모두 적용한 뒤 페이지네이션한다. 차단 행 뒤의 공개 템플릿도 누락되지 않는다.
- 후보별 권리 증빙 계약과 운영 런북을 추가했다. 증빙 파일·승인자·권한 범위·후보/카탈로그 digest가 모두 일치해야 `ready_for_publication_review`가 되며, 목록 API와 응답 메타데이터도 이 검증을 필수로 적용한다. 카탈로그 상태만 바꿔서는 공개되지 않는다.
- Template V2·structured-template 백엔드 회귀 테스트 572개, 프런트엔드 Template V2 Cypress 테스트 8개, 일반 렌더러 테스트 8개, export fidelity 테스트 13개와 TypeScript 검사가 통과했다.
- 실제 Next API, presentation-export, LibreOffice, Poppler를 연결한 PPTX E2E 3개가 통과했다. 제목·본문, geometry, text-fit, rich-elements의 렌더 임계치를 확인했고 `sharp` 0.35.3 네이티브 로드와 PNG 인코딩도 통과했다.
- 저장소의 고정 presentation-export 런타임에는 번들과 일치하는 `sharp` 0.34.4 네이티브 애드온을 복구했으며, 루트 `npm run check:presentation-export`가 버전 v0.4.2와 Windows x64 converter를 확인하고 통과했다.
- 로컬 ignored `pnpm-workspace.yaml`의 placeholder `allowBuilds` 설정 때문에 pnpm 집계 명령만 사전 중단됐으나, 동일 구성요소의 분해 실행과 실제 E2E는 모두 통과했다. 사용자 로컬 파일은 변경하지 않았다.
- 외부 후보 4종은 파일을 복사하거나 공개하지 않았다. 따라서 실제 후보 덱의 렌더 비교·육안 승인은 권리 확인 및 승인된 원본 반입 이후의 공개 전 작업으로 남는다.

## 재사용 실행 프롬프트

아래 프롬프트는 후속 구현·검수 작업에 그대로 사용할 수 있다.

```text
역할
당신은 Presenton 기반 PPT 생성기의 Template V2 통합 책임자다. 목표는 PPT Master의 네이티브 PowerPoint 원칙을 반영하면서 신규 템플릿 UI를 V2 전용으로 전환하고, 기존 문서 호환성과 저작권·품질 안전성을 유지하는 것이다.

원본
- 저장소: https://github.com/hugohe3/ppt-master
- 고정 commit: 8858c4960b870f12baf952994fbd0c76ab5ba4ec
- 분석 후보: Swiss Grid, Editorial Magazine, Data Journalism, Glassmorphism SaaS
- 런타임 네트워크 fetch 금지. 외부 파일은 commit과 SHA-256으로 추적하되 권리가 확인되지 않은 미디어를 복사하지 말 것.

제품 정책
- 신규 레이아웃 템플릿 UI에는 출판 게이트를 통과한 Template V2만 표시한다.
- Create Custom, custom template, 기존 built-in, adaptive 카드는 신규 UI에서 제거한다.
- AI 저작 모드는 레이아웃 템플릿과 별도 모드로 유지한다.
- 신규 기본값을 legacy/adaptive에 하드코딩하지 않는다. 공개 목록에 정확히 하나의 is_default가 있을 때만 자동 선택한다.
- 기존 저장 문서의 V1/custom/built-in ID는 직접 조회·rehydrate·render 호환을 유지하되 신규 discovery에는 노출하지 않는다.

백엔드 구현
- TemplateV2.assets에 versioned provenance/publication evidence를 저장한다. DB migration은 피한다.
- native_clone 원본은 <app-data>/template-v2/native/<template-id>/revision-<n>/source.pptx에 원자적으로 보관한다.
- 경로 정규화, template/revision 소유 범위, symlink/path traversal, SHA-256, 파일 크기를 검증한다.
- OOXML 출력은 원본 master/layout/theme/relationships/media를 유지하고 지정된 source slide part를 복제·정렬한다.
- 텍스트와 표의 명시적 슬롯만 패치한다. 비장식 TextList는 원본과 문단 수가 같은 경우에만 `bodyPr`, `lstStyle`, 문단·run 서식을 보존해 패치한다. 필드, 줄바꿈, 하이퍼링크, 이미지, 차트, 애니메이션 등 미지원 변경은 조용히 재구성하거나 일반 exporter로 우회하지 말고 fail-closed한다.
- reconstructed 모드는 deterministic native compiler의 compiled 증거, structural/package digest, unsupported=null이 있어야 출판한다.
- 목록 API는 rollout allowlist, static catalog status, publication evidence를 모두 통과한 항목만 반환한다. direct get은 기존 문서 호환을 유지한다.

카탈로그·권리
- 각 후보에 repository, commit, path, source-file SHA-256, template_kind(brand/layout/deck), preservation_mode, importer_version, attribution, license review, asset counts, capabilities를 기록한다.
- 저장소 MIT와 임베디드 미디어 권리를 구분한다.
- 개별 사진/이미지/파생 산출물의 재배포 권리가 확인되지 않으면 publication_status=blocked로 유지한다.
- 로컬 권리 증빙 파일의 digest, 승인자, 승인 시각, 사용·수정·재배포·상업 이용 범위, 후보 PPTX와 카탈로그 entry digest를 검증한다. 검증 성공은 publication review 준비 상태일 뿐 자동 공개 승인으로 취급하지 않는다.
- blocked 후보는 API direct get에서 근거를 확인할 수 있어도 신규 목록에는 나타나면 안 된다.

프런트엔드
- TemplateSelection과 dashboard TemplatePanel을 Template V2 목록 전용으로 만든다.
- 카드에는 name, revision, default, source/provenance, capability를 표시한다.
- loading, API error, empty, 기본 템플릿 누락/중복을 명시적으로 구분한다.
- 빈 목록이나 오류에서 legacy fallback을 렌더링하지 않는다.

검증
- native clone 결과에서 master/layout/theme/relationships/media part가 바이트 단위로 보존되는지 검사한다.
- source slide 중복·재정렬, text/table/TextList patch, unsupported gate, storage scope/integrity, 일반 exporter 우회 방지를 테스트한다.
- 생성 PPTX를 PowerPoint 호환 편집·저장 경로로 다시 열어 TextList가 네이티브 편집 가능한지 검사한다.
- 기존 reconstructed compiler, V1 직접 조회·render, AI 저작 경로가 영향받지 않는 좁은 회귀 테스트를 실행한다.
- 프런트엔드 TypeScript와 컴포넌트 테스트에서 V2-only, 단일 default auto-select, empty/error fail-closed, legacy rehydrate 보존을 검증한다.
- git diff --check를 통과시킨다.

완료 조건
- 신규 사용자가 저품질 legacy 레이아웃 템플릿을 볼 수 없다.
- 공개 가능한 V2만 선택·생성할 수 있다.
- native_clone 결과가 평면 이미지가 아니라 PowerPoint에서 계속 편집 가능하다.
- 마스터/레이아웃/테마/관계/미디어 보존과 무결성 실패가 자동 테스트로 증명된다.
- 권리 불명확한 PPT Master 예제는 카탈로그에 추적되지만 공개되지 않는다.
- 기존 저장 문서는 계속 열린다.

마지막에 변경 파일, 테스트 명령/결과, 남은 차단 사유를 한국어로 보고하라. 검증하지 않은 사항은 완료로 표시하지 말라.
```

## 공개 전 운영 체크리스트

- 예제별 이미지 출처, 라이선스, attribution, 재배포 허용 여부를 문서화한다.
- 승인된 원본 PPTX를 고정 revision 저장소로 가져오고 digest/size를 기록한다.
- 권리 증빙 매니페스트와 승인 문서를 채우고 `ready_for_publication_review` 검증을 통과시킨다.
- 레이아웃 ID별 source slide part와 편집 슬롯 이름을 검토한다.
- native preflight를 통과시키고 실제 후보의 모든 슬라이드를 렌더링해 육안 검수한다.
- 공개 가능한 V2 중 정확히 하나만 `is_default=true`로 지정한다.
- 목록 API와 신규 UI에서 blocked/V1 항목이 보이지 않는지 확인한다.
- 기존 문서를 샘플링해 direct read와 재생성 호환을 확인한다.
