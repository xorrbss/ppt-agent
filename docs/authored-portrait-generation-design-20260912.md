# Authored 세로형 PPT 생성 설계

작성일: 2026-09-12. 갱신일: 2026-09-13. 상태: 재사용 중심 상세 설계 v3, 제품 구현 전. 아래 10절은 1~9절에 남은 구현 선택을 구체화하며 충돌 시 10절을 우선한다. 선택안은 설계 결정이며 현재 구현된 계약으로 간주하지 않는다.

## 1. 목표와 결정 상태

사용자가 승인한 방향은 생성 화면에서 가로 16:9 또는 세로 9:16을 선택하고, Authored 생성·미리보기·PPTX·PDF에서 동일한 비율을 유지하는 것이다. 기존 기본값은 가로형이다. 세로형은 가로 결과를 축소하거나 회전하지 않고 처음부터 세로 공간에 맞게 구성한다. 첫 버전은 Authored 9:16이며 A4는 후속 범위다.

사용자가 추가 승인한 구현 방향은 기존 Authored 경로에 비율 선택을 연결하고 필요한 가로 고정 계약을 일반화하는 것이다. 기존 캔버스·스타일·품질·저장·출력 구조를 우선 재사용한다. 별도 세로 생성기, 병렬 저장 체계, 별도 품질 엔진을 신설하지 않는다. 기존 스타일 중 세로 대응이 확인된 구성부터 지원하고 회사 표준 마스터는 크기·배치 호환성이 해결된 경우에만 허용한다.

이 문서의 필드명, 최종 저장 위치, 내부 크기, 오류 이름, 작업 순서는 구현 제안이다. 사용자 확정 요구사항이나 구현 완료 사실과 구분한다. 생성 완료 후 방향 전환, 한 덱 내 혼합 비율, 기존 가로 문서의 자동 세로 변환은 첫 버전 범위에서 제외하는 안이다.

## 2. 현재 확인한 근거와 한계

| 경로 | 확인한 사항 | 설계 영향 |
| --- | --- | --- |
| `servers/fastapi/models/generate_presentation_request.py` | 생성 요청 모델에 방향·화면 비율 필드가 없음 | 정식 요청 계약과 전달 경로 필요 |
| `servers/fastapi/models/workspace.py` | bootstrap의 generation_settings는 사전형 | 입력을 넣었다는 사실만으로 워커 전달을 보장하지 못함 |
| `servers/fastapi/models/sql/presentation.py` | 전용 비율 열은 없으나 layout·structure·deck_plan 등 기존 저장 필드가 있음 | 기존 필드의 소유권과 복원 경로를 확인한 뒤 저장 위치 결정; 새 열을 선결 조건으로 삼지 않음 |
| `servers/fastapi/utils/slide_capture.py:70` | 캡처 기본 크기 1280×720 | viewport·clip·대체 이미지의 크기를 함께 전달 |
| `servers/fastapi/services/authored_composition_policy.py:201` | 구성 지침에 1280×720 언급 | 프롬프트와 실제 측정이 같은 캔버스를 사용해야 함 |
| `servers/nextjs/app/(presentation-generator)/components/TemplateV2HtmlSlidePreview.tsx:373` | 16:9 미리보기 고정값 존재 | 이 컴포넌트를 쓰는 경로는 문서 비율로 표시 |
| `servers/nextjs/lib/authored-hybrid/pptx-assembler.ts:43` | 1280×720 및 12,192,000×6,858,000 EMU 상수 존재 | 페이지 크기뿐 아니라 요소·배경·클리핑 계산도 함께 변경 |
| `servers/nextjs/lib/authored-hybrid/raster-pdf.ts:3` | 입력 이미지 크기와 별개로 PDF 페이지·배치 크기가 960×540pt로 고정 | MediaBox와 이미지 배치 행렬을 함께 파라미터화해야 함 |
| `servers/nextjs/app/(presentation-generator)/public-v2/PublicV2OperationsClient.tsx:385` | 1280×720 raster 출력 안내가 있음 | 출력 안내와 실제 지원 비율을 함께 갱신 |

앞선 로컬 bootstrap 요청은 HTTP 422를 반환했다. 당시 오류 상세를 보존하지 않아 원인을 화면 비율로 확정할 수 없다. 세로형 기능 전체가 불가능하다는 증거로 사용하지 않는다. 이번 설계 작업에서는 생성 요청을 재실행하지 않았다.

실제 생성 화면의 최종 입력 컴포넌트, public-v2의 입력 정규화·워커 전달 경로, fidelity export의 실제 진입점은 구현 첫 단계에서 연결 관계를 확인한다. 위 파일 목록은 영향 후보이며 모든 파일의 수정이 필수라는 뜻은 아니다.

### 기존 기능 재사용 지도

| 기존 기능과 근거 | 재사용 방식 | 남아 있는 경계 |
| --- | --- | --- |
| `models/authored_vnext/master/contract.py:235`의 CanvasContract | width_px·height_px·safe_area를 기존 계약으로 표현; 별도 캔버스 모델 신설하지 않음 | 가변 크기를 담는 모델이 있다는 사실과 모든 실행 경로의 세로 지원은 다름 |
| `models/authored_vnext/deck/composition.py:249`의 ResolvedReferenceLayoutSnapshot | canvas와 digest를 포함한 기존 snapshot·seal 구조에 선택 결과 반영 | 크기 변경 시 sealed snapshot을 직접 수정하지 않고 기존 생성·봉인 절차로 새로 구성 |
| `services/authored_vnext/generation/retained_reference.py:2102`의 compile_retained_reference_layout | 기존 폭·높이 인자를 이용해 스타일 레이아웃을 구성 | 기존 배치가 세로로 적절히 재구성되는지는 별도 확인; 인자 지원만으로 호환 판정하지 않음 |
| `services/authored_vnext/generation/effective_style.py:428` | 기존 계약 크기에서 레이아웃 snapshot으로 이어지는 연결 활용 | `_canonical_role_policy`는 1280×720 이외를 명시적으로 거절; 안전 영역·역할 배치를 지원한 뒤 조건을 일반화 |
| `services/authored_vnext/integration/export_payload.py:232`의 worker_master_contract | 기존 워커 계약에 전달되는 canvas 폭·높이 활용 | 이 함수가 실제 공개 fidelity 경로에서 쓰이는 범위를 호출 체인으로 확인 |
| `services/authored_local_fit_policy.py:97`의 pt_per_css_px | 기존 viewport·물리 크기 기반 변환과 local-fit 정책 재사용 | 호출부에 세로 치수를 전달하고 기존 글자 하한을 보존 |
| `services/authored_vnext/source_render_geometry.py` | 폭 기반 줄 수·본문·표·카드 높이 계산 재사용 | 해당 Authored 경로와의 연결을 확인; 새 세로 전용 측정 엔진을 만들지 않음 |
| `services/presentation_generation_job_service.py`의 request_payload 및 기존 composition snapshot | 기존 durable 요청 저장·재시도·복원 체계 확장 | 문서 재열기·버전 복원까지 동일 값이 남는지 확인 후 최종 저장 위치 선정 |
| `lib/authored-hybrid/schema.ts`, `contract.ts:542`, `browser-source.ts` | 기존 renderer·schema·검증기를 유지하며 지원 치수를 명시적으로 확장 | 1280×720 고정 계약을 우회하지 말고 생산자와 소비자를 함께 변경 |

위 표의 Python 경로는 `servers/fastapi/`, TypeScript 경로는 `servers/nextjs/` 기준이다. Design Pack에도 별도의 Canvas 모델이 있으나 source-native 범위의 기존 계약이다. 이를 세로형 전용으로 복제하거나 첫 버전에 Design Pack 경로 전체를 편입하지 않는다.

## 3. 사용자 흐름

1. 생성 설정에 ‘슬라이드 방향’을 두고 ‘가로 16:9’를 기본 선택한다.
2. ‘세로 9:16’ 선택 시 세로 카드 모양의 미리보기와 짧은 안내를 표시한다.
3. 프롬프트·자료·스타일을 정하고 생성한다. 첫 버전에서 지원하지 않는 마스터나 템플릿 조합은 생성 전에 이유를 안내한다.
4. 생성 결과, 썸네일, 상세 보기, 전체 화면은 모두 저장된 문서 비율을 따른다.
5. 재열기와 재시도에서도 선택한 비율을 유지한다. 다운로드에는 기존 공개 출력 방식과 동일한 PPTX·PDF 선택을 제공한다.

자연어에 ‘세로’라고 적었다는 이유로 구조화된 설정을 임의 변경하지 않는다. 명시 설정이 권위다. 첫 버전은 방향 선택 컨트롤로 지원하며 프롬프트 자동 추론은 별도 기능이다.

## 4. 요청과 저장 계약 — 제안

공개 설정은 `aspect_ratio` 하나로 통일하고 허용값은 `16:9`, `9:16`으로 제한한다. 별도 orientation을 함께 저장해 두 값이 충돌하는 구조를 만들지 않는다. 생략은 가로형으로 처리하고 알 수 없는 값은 생성 시작 전에 검증 오류로 반환한다.

작업 접수 시 정규화한 선택을 기존 request_payload에 넣고, 기존 유효 캔버스 계약과 레이아웃 snapshot으로 해석한다. 문서 재열기·내보내기에 필요한 권위값은 기존 저장 필드와 snapshot을 이용해 보존하는 안을 우선 검토한다. 모든 후보 필드에 중복 저장하지 않는다. 문서에서 어느 snapshot을 참조하고 버전 복원이 어떤 필드를 복원하는지 확인해 하나의 권위 위치를 정한다. 요청의 비율과 해석된 캔버스의 일치 여부를 검사한다.

워커 재개는 저장된 계약을 사용하며 최신 UI 상태나 프로세스 전역값으로 덮어쓰지 않는다. 문서 복사·버전 복원·페이지 재생성·내보내기 요청도 같은 계약을 계승한다. 회사 마스터가 없는 일반 Authored에서도 이 경로가 성립해야 하며, 캔버스 재사용을 위해 회사 마스터 등록을 필수화하지 않는다.

DB migration은 기본 작업이 아니다. 기존 저장 구조가 재열기·재시도·복원·내보내기에서 권위값을 보존하지 못하는 구체적인 이유가 확인될 때만 최소 변경을 검토한다. 필요하다면 기존 Alembic 경로를 사용하고 과거 행은 가로형으로 호환한다. 비율이 명시된 새 문서에서 값이 손실되거나 체크포인트와 충돌하면 오류로 처리한다. 과거 문서의 값 부재만 가로형 호환 대상으로 삼는다.

접수 영수증·문서 응답에는 확정 비율을 반환한다. idempotency 비교, 렌더·결과 캐시 키와 스타일 snapshot 식별에는 비율 또는 이를 포함한 캔버스 계약 digest를 반영한다. 같은 내용의 가로 결과가 세로 요청에 재사용되면 안 된다.

## 5. 공통 캔버스 계약 — 제안

| 비율 | CSS 논리 크기 | PPTX 크기(EMU) | PDF 페이지(pt) |
| --- | --- | --- | --- |
| 16:9 | 1280×720 | 12,192,000×6,858,000 | 960×540 |
| 9:16 | 720×1280 | 6,858,000×12,192,000 | 540×960 |

가로의 기존 물리 크기를 유지하며 세로는 가로·세로 축을 교환하는 안이다. A4 크기를 의미하지 않는다. 논리 좌표와 캡처 해상도 배율은 분리한다. 캡처 배율 변경으로 PPTX의 물리 크기나 글자 크기가 달라지면 안 된다.

백엔드는 선택 비율을 기존 CanvasContract에 해석하고, 기존 snapshot·worker payload를 통해 전달한다. 필요한 비율 프리셋 매핑만 추가한다. 프런트엔드는 전달된 치수를 사용하고 기존 단위 변환 함수를 재사용한다. 지원 비율과 치수의 권위는 한 곳에 두며 각 호출부에 독립적인 삼항식이나 고정값을 늘리지 않는다. 기존 스타일·마스터가 자체 캔버스를 지정하면 호환 여부를 먼저 검사한다. 게시된 마스터·기존 snapshot은 변경하지 않는다.

## 6. 생성·품질·렌더링

- 콘텐츠 구성: 제목 → 핵심 메시지 → 이미지·차트 → 보충 설명 순서를 기본으로 한다. 좌우 카드와 비교 내용은 수직 배치 또는 의미 단위의 장 분할을 사용한다.
- 정보량: 캔버스 면적이 같아도 좁은 폭 때문에 줄바꿈과 차트 레이블이 늘어난다. 가로의 수용량을 그대로 적용하지 않고 실제 폭·높이로 측정한다.
- 분량 계약: 사용자가 장수를 고정했거나 원문 보존 정책이 장 확장을 금지하면 임의로 장수를 늘리지 않는다. 기존 초과 처리와 review-required 계약을 따르고 필요한 선택을 알린다.
- 글자와 복구: 기존 품질 기준과 최소 글자 크기를 낮추지 않는다. 로컬 수리·offcanvas 판정·빈 공간/밀도 평가에 실제 캔버스를 전달한다. 세로형 통과를 위해 가로 전용 검사를 단순히 끄지 않는다.
- HTML과 캡처: 생성 지침, 루트 HTML 크기, 브라우저 viewport, clip, 배경과 실패 대체 이미지가 같은 크기를 사용한다. 프로세스 전역 폭·높이는 변경하지 않아 가로·세로 동시 작업을 격리한다.
- UI: 문서 비율로 컨테이너와 축소 배율을 계산한다. 확대 보기·썸네일도 같은 기준을 쓰며 세로 하단이 잘리지 않게 한다.
- 스타일: 지원 여부가 확인된 구성만 첫 버전에서 제공한다. 가로형 마스터를 무조건 회전·축소하거나 세로형을 조용히 가로형으로 바꾸지 않는다.

초기 지원 스타일은 기존 카탈로그에서 선정한다. 색상·폰트·장식·콘텐츠 구조를 재사용하고 해당 스타일의 세로 배치만 기존 레이아웃 경로에 추가한다. 허용 조합 판단은 기존 스타일/라우팅 검증에 연결하고 UI와 서버가 같은 결과를 사용한다. 회사 표준 마스터는 현재 canonical canvas 거절 조건뿐 아니라 제목 영역·본문 영역·공통 요소·safe_area까지 호환되는 경우에만 허용한다. 조건문 삭제만으로 지원 처리하지 않는다.

## 7. PPTX·PDF 내보내기

첫 버전은 현재 공개된 fidelity-raster 경로를 유지한다. 세로형 지원을 이유로 hybrid/편집 가능 출력의 공개 범위를 확대하지 않는다.

PPTX에서는 `p:sldSz`뿐 아니라 전체 배경 이미지, 요소 좌표 변환, 클리핑 경계와 그룹 좌표도 같은 치수를 사용한다. 기존 조립기를 확장하며 별도 세로 조립기를 만들지 않는다. PDF에서는 현재 960×540pt로 고정된 MediaBox와 이미지 배치 행렬을 함께 전달 치수로 바꾼다. 이미지 파일의 width/height만 바꾸면 완료가 아니다. 중간 렌더 scene, export manifest/receipt와 캐시에도 기존 계약을 통해 비율을 전파한다.

브라우저 추출기의 WIDTH/HEIGHT, schema 상수, viewport 고정 검증, Chrome 실행 설정을 같은 변경 단위로 취급한다. 기존 16:9 계약의 과거 입력은 계속 해석하고 새 9:16 입력은 명시적으로 검증한다. schema 버전 변경 필요 여부는 기존 소비자 호환성을 기준으로 결정한다. 크기 검증을 제거해 임의 viewport를 허용하지 않는다.

공유 조립 코드가 hybrid 경로에도 사용되면 기존 가로 동작을 보존한다. 세로 hybrid 지원 여부는 별도로 명시하며, 미지원 경로는 접수 단계에서 제한한다. 확장자만 생성됐다는 사실로 내보내기 성공을 판정하지 않는다.

## 8. 구현 순서

1. **기존 전달 경로 확정:** 실제 생성 화면 → 접수 → request_payload → 유효 CanvasContract/snapshot → 워커 → 공개 fidelity 출력의 연결을 확인한다. 기존 저장 필드의 소유권과 복사·재시도·복원 경로를 대조해 권위 위치를 결정한다. 422 오류는 해당 요청을 식별할 근거가 없으면 미확정으로 남긴다.
2. **최소 계약 확장:** 비율 입력과 프리셋 해석을 추가하고 기존 캔버스·snapshot·요청 저장을 연결한다. 기존 계약의 seal·digest 생성과 단위 변환을 재사용한다. DB 변경은 확인된 저장 결손이 있을 때만 포함한다.
3. **기존 생성기 일반화:** 지원할 기존 스타일을 고르고 retained-reference 레이아웃, 폭 기반 측정, local-fit·수리·캡처에 치수를 전달한다. 회사 마스터의 고정 조건은 실제 배치 호환이 확보되는 범위에서만 확장한다.
4. **기존 renderer·출력기 일반화:** viewport schema/검증과 추출기·Chrome 설정을 맞추고 기존 PPTX 조립기·PDF 작성기를 확장한다. 미리보기를 연결한 뒤 지원 조합에만 방향 선택을 활성화한다.
5. **사용 가능 판정:** 아래 실물 수용 기준으로 확인한다. 아직 확인하지 않은 항목은 완료로 보고하지 않는다.

## 9. 검증 계획과 완료 기준

이번 턴은 문서 설계만 수행한다. 제품 코드·DB·서버 설정은 변경하지 않으며 테스트·빌드·생성을 실행하지 않는다.

구현 단계에서는 변경된 층을 직접 확인하는 가장 작은 기존 검증 하나를 선택한다. DB 스키마 변경은 영향이 큰 변경이므로 필요한 범위에서 마이그레이션 호환을 추가 확인한다. 전체 테스트·전체 빌드를 기본 실행하지 않는다.

최종 제품 수용은 실제 로컬 PostgreSQL 운영 경로에서 표지·본문·이미지·차트·마무리로 구성한 짧은 세로 덱을 생성해 확인한다. 실제 provider와 durable operation을 사용하고 fixture 성공을 실제 생성 성공으로 대체하지 않는다.

완료 조건:

- 생성 화면에서 선택한 9:16이 문서 응답·워커 입력·재열기에 유지된다.
- 결과의 모든 페이지가 세로형이고 썸네일·상세 보기·캡처에 내용 잘림이 없다.
- 내려받은 PPTX의 페이지 크기가 세로 9:16이며 PowerPoint에서 열린다.
- 내려받은 PDF의 페이지 크기가 세로 9:16이며 내용·장수가 PPTX와 일치한다.
- 기존 가로 문서는 가로로 유지되고 최소 글자 크기·내용 보존 기준을 완화하지 않는다.
- 일반 Authored 세로 생성에 회사 마스터 등록이 필수로 추가되지 않는다.
- 미지원 스타일·회사 마스터는 접수 전에 명확히 제한되며 가로형으로 조용히 대체되지 않는다.
- 비율이 다른 요청 간 캐시가 혼용되지 않고, 기존 digest·snapshot 검증을 우회하지 않는다.

미해결 사항은 생성 화면의 정확한 진입 컴포넌트, 일반 Authored의 캔버스 해석 진입점, 문서 저장의 권위 위치, 공개 export의 최종 호출 체인, 초기 지원 스타일 목록이다. 이들은 구현 첫 단계에서 결정하며 기본 제품 범위를 다시 협의할 필요는 없다. 기존 함수·모델의 존재는 확인했으나 실제 세로 실행 지원이나 무수정 재사용이 검증된 것으로 표현하지 않는다.

## 10. 구현 선택 구체화 — 2026-09-13

### 10.1 확인된 실행 경로와 초안 정정

`presentation_generate.py:271`에서 마스터 관련 pin이 모두 없는 요청은 마스터 해석 경로를 빠져나간다. 따라서 회사 마스터의 CanvasContract가 일반 Authored에도 자동으로 만들어진다고 가정하지 않는다. 일반 생성 접수에서 기존 CanvasContract 타입으로 캔버스를 해석하는 연결을 추가한다. 이를 위해 DeckMasterContract나 회사 마스터를 가짜로 만들지 않는다.

`presentation_generation_staging.py`의 `_durable_request_payload`와 `_request_from_durable_payload`는 기존 요청과 composition/style/deck metadata를 직렬화·복원한다. 비율도 이 경로를 통과하게 한다. PrivateAttr는 자동 직렬화되지 않으므로 내부 객체에만 담고 완료했다고 판단하지 않는다.

`authored_operations.py:297`은 문서 theme의 authored_vnext에서 deckMetadata를 읽어 재생성 요청에 사용하는 기존 패턴을 보여준다. 이 메타데이터 영역을 확장하는 저장안을 채택한다. 현재 비율 보존 기능이 이미 있다는 의미는 아니다.

`lib/authored-hybrid/export.ts`의 `runAuthoredFidelityPresentationExport`는 `runBundledPresentationExport`를 호출한다. `assembleAuthoredHybridPptxAsync`는 hybrid 경로의 호출이다. 그러므로 앞 절의 hybrid 조립기는 첫 버전의 무조건적인 수정 대상이 아니다. fidelity PPTX의 실제 크기 변경은 번들 입력 계약과 그 입력을 읽는 런타임 소스에서 처리한다. 다운로드된 `presentation-export` 산출물을 직접 편집하지 않는다.

PDF는 `runAuthoredPdfPresentationExport` → `buildRasterPdfFromPngs` 경로이며, 후자의 고정 페이지 크기와 배치 행렬을 수정한다. 공개 전략 선택은 기존 `presentation-export-strategy.ts`를 유지한다.

### 10.2 입력·저장·전달의 권위

공개 입력은 `aspect_ratio`이며 요청 기본값은 `16:9`다. 폭·높이의 별도 공개 입력은 제공하지 않는다. 서버가 비율을 기존 CanvasContract 타입으로 해석한다. 안전 영역은 선택한 레이아웃에서 계산하고 캔버스 안에 있는지 검증한다. 마스터의 안전 영역을 일반 생성의 기본값으로 복사하지 않는다.

문서의 권위 위치는 기존 JSON 열인 `theme.authored_vnext.canvas`로 정한다. 새 하위 항목은 비율, CanvasContract 값, 해석 계약 버전을 함께 담는다. 기존 theme/authored_vnext의 다른 키를 보존하는 병합으로 기록한다. `deckMetadata`의 기존 의미를 비율 저장용으로 바꾸거나 다른 문서 열에 중복 권위를 만들지 않는다. 새 DB 열은 추가하지 않는 설계다.

| 단계 | 전달값과 권위 | 필요한 연결 |
| --- | --- | --- |
| 생성 설정 | aspect_ratio 선택 | 현재 생성 폼과 요청 타입에 필드 추가 |
| 생성 접수 | 정규화된 aspect_ratio와 해석된 CanvasContract | GeneratePresentationRequest와 실제 workspace/public 입력 정규화에 연결 |
| durable job | 기존 request_payload의 선택값 및 해석 버전 | staging 직렬화·복원 시 누락 검증 |
| 생성·체크포인트 | 해당 시도에서 확정한 캔버스와 기존 snapshot digest | 프로세스 전역값을 읽지 않고 함수 인자로 전달 |
| 문서 발행 | theme.authored_vnext.canvas | 기존 문서 발행 트랜잭션에서 결과와 함께 기록 |
| 재열기·내보내기 | 저장된 문서 캔버스 | 서버 projection에서 검증 후 UI·export payload로 전달 |
| 복사·버전 복원 | 원본/해당 버전의 canvas | 기존 theme 보존 경로에 포함; 현재 설정을 재적용하지 않음 |
| 페이지 재생성·스타일 적용 | 문서 canvas | 요청 비율 생략 시 문서 값을 상속; 명시 충돌은 거절 |

저장 위치를 정했다는 사실이 기존 모든 복원 경로의 보존을 확인했다는 뜻은 아니다. 구현에서는 발행·복원 writer가 theme를 재구성하는 지점을 좁게 확인하고 canvas 병합 누락을 수정한다. 기존 JSON 경로가 특정 writer에서 빠진다면 해당 writer를 수정하며 바로 DB migration으로 확대하지 않는다.

새 문서의 비율과 해석된 폭·높이가 불일치하면 거절한다. 체크포인트나 pinned snapshot의 치수가 다르면 새 비율로 강제 덮어쓰지 않는다. 미완료 요청의 비율 변경은 재시도로 처리하지 않고 새 생성 요청으로 취급한다.

### 10.3 초기 스타일과 배치

첫 구현 대상은 기존 `authored_styles/default.yaml`의 default 스타일 하나로 한정한다. 카탈로그 ID·색상·폰트·기존 스타일 loader를 재사용하고 새 스타일 ID를 만들지 않는다. 이 선택은 개발 대상 선정이며 현재 default가 세로형으로 검증됐다는 뜻은 아니다.

표지는 제목·부제를 위쪽에, 본문은 제목 아래 단일 열, 이미지는 가로폭 안에 배치하고 캡션을 아래에 둔다. 차트·표는 폭 기준 수용량을 기존 측정 함수로 판단하고 가로 3열 카드를 세로 스택으로 구성한다. 마무리는 핵심 메시지와 다음 행동을 수직으로 배치한다. 기존 프롬프트/레이아웃 해석 지점에 선택 캔버스와 이 규칙을 추가한다. 별도 세로 생성 파이프라인은 만들지 않는다.

default 외 스타일, 회사 마스터, registered-template 및 source-native/fidelity-first 조합의 9:16은 첫 릴리스에서 비활성화한다. 이후 각 조합의 호환 검증이 완료되면 기존 capability/라우팅 판정에 추가한다. 사용자가 고른 스타일을 default로 조용히 교체하지 않는다. 서버 판정이 권위이며 UI는 동일한 지원 정보를 표시한다. 가로형의 기존 지원 범위에는 이 제한을 적용하지 않는다.

### 10.4 오류·과거 호환·노출 규칙

| 상황 | 동작 |
| --- | --- |
| 새 생성에서 입력 생략 | 기존 가로형 기본값 |
| 과거 문서에 canvas 항목 없음 | 가로형으로 읽고, 읽기만으로 DB를 수정하지 않음 |
| canvas 항목이 있으나 형식이 잘못되었거나 일부 누락 | 무조건 가로 fallback하지 않고 계약 오류 |
| 비율 값이 허용 목록 밖 | provider 호출 전 422 |
| 세로형과 미지원 스타일/마스터/모드 결합 | durable 생성 시작 전 의미 있는 사유로 422 |
| 문서·snapshot·render viewport 간 충돌 | 출력 차단; 가로 이미지 늘리기나 강제 회전 금지 |
| 세로형에서 고정 장수 안에 내용이 안 들어감 | 기존 내용 보존·품질 실패/검토 계약 적용; 임의 장 확장과 글자 하한 완화 금지 |

canvas 계약 버전이 없는 기존 결과는 과거 가로 계약으로만 해석한다. 9:16 결과에는 새 계약 버전을 명시하고 이를 모르는 소비자는 거절하도록 한다. 기존 snapshot의 digest를 바꾸지 않으며 새 크기가 들어간 snapshot은 기존 seal 함수로 새로 봉인한다. 캐시 식별에는 확정된 크기와 계약 버전을 포함한다.

세로 옵션은 API·worker·renderer·PPTX·PDF 지원이 연결된 이후에만 활성화한다. 구현 단계의 테스트 통과만으로 지원 목록에 올리지 않고 실제 생성과 파일 readback까지 완료한 default 조합부터 노출한다.

### 10.5 변경 범위와 구현 종료 조건

필수 변경 층은 요청 모델/정규화, staging 직렬화, 문서 theme 발행·읽기·복원, 일반 Authored 캔버스 해석, default 구성 지침, 캡처 치수, 브라우저 viewport 계약, fidelity 번들 export 입력과 소유 소스, raster PDF, 생성 폼과 문서 미리보기다. 각 층은 기존 파일의 연결 지점을 확장한다.

확정된 직접 파일은 `models/generate_presentation_request.py`, `api/v1/ppt/endpoints/presentation_generation_staging.py`, `api/v1/ppt/endpoints/presentation_generate.py`, `utils/slide_capture.py`, `utils/authored_styles.py`, `lib/authored-hybrid/export.ts`, `lib/run-bundled-presentation-export.ts`, `lib/authored-hybrid/raster-pdf.ts`다. schema·contract·browser-source는 실제 fidelity 렌더 호출의 사용 범위를 확인해 함께 변경한다. 생성 폼, 문서 발행/복원 writer, 번들 런타임의 소유 소스는 구현 직전 호출자에서 찾아 파일을 확정한다. 확인하지 않은 경로를 임의 파일명으로 채우지 않는다.

조건부 변경 대상은 hybrid 조립기·회사 표준 composition·Design Pack이다. 첫 버전에서 미지원 조합을 제한할 수 있다면 해당 엔진 전체의 세로 확장은 하지 않는다. 검증 기준은 9절을 유지하며 추가로 요청 직렬화 왕복, 문서 복원, 가로/세로 캐시 분리, PDF 실제 페이지 크기를 변경층의 좁은 검증에 포함한다. 모든 검사를 매번 실행하는 계획은 아니다.

설계의 주요 선택은 위와 같이 정했다. 아직 확인되지 않은 것은 일부 실제 writer/소유 소스 파일 위치와 실행 결과이며, 이는 구현 전 연결 확인과 제품 수용 검증의 대상이다. 이 문서를 세로 기능의 구현 완료 또는 무수정 재사용 보증으로 보고하지 않는다.

### 10.6 구현 조사에서 확인한 연결 보완 (2026-09-13)

아래는 구현 조사에서 현재 코드로 확인한 사실이며, 세로 지원 완료를 뜻하지 않는다. 파일별 소유권·확정 인터페이스·검증 결과는 `docs/authored-portrait-implementation-20260913.md`에 기록한다.

- 일반 default의 실제 생성은 `authored_presentation_generation.py`와 `authored_presentation_pipeline.py`를 사용하며 회사 마스터 CanvasContract를 자동 해석하지 않는다. 기존 함수 인자와 renderer worker 직렬화에 캔버스를 연결한다.
- theme 하위 wire는 기존 CanvasContract alias 관례를 따라 `contractVersion`, `aspectRatio`, `widthPx`, `heightPx`, `locale`, `safeArea`로 정했다. 공개 요청 이름 `aspect_ratio`와 문서 권위 위치는 10.2를 유지한다. 일반 안전 영역은 기존 공통 author 지침과 fallback의 64px 여백에서 계산하며 회사 마스터의 canonical 영역을 복사하지 않는다.
- 문서 발행 writer는 `authored_presentation_publication.py`이다. 기존 복사는 theme 전체를 보존하며 버전 복원은 슬라이드를 복원하고 불변 문서 theme를 유지한다. canvas만 있는 authored_vnext 객체를 회사 마스터로 오인하던 workspace 분기는 명시적인 master pin 기준으로 구분한다.
- 10.1의 번들 호출 설명은 현재 코드의 최종 writer까지 충분히 추적하지 못했다. Next Fidelity 경로는 번들 호출 뒤 기존 `prepareFidelitySlideLayer`와 `writeHybridPptx`를 통해 `assembleAuthoredHybridPptxAsync`로 최종물을 다시 작성한다. 따라서 이 checkout에서는 기존 조립기가 실제 수정 대상이다. 이는 hybrid 편집 출력의 공개 확대를 뜻하지 않는다.
- public-v2 durable 출력은 별도로 이미 존재하는 `public_export_operation.py` → `public_render_worker.py` → `public_export_worker.py`를 사용한다. 이 저장소 소유 소스의 bundle·viewport·PPTX/PDF 치수를 함께 연결한다. 다운로드된 export 런타임은 직접 편집하지 않는다.
- 실제 수용 전에는 default의 세로 공개 capability를 비활성 상태로 유지한다. 환경 및 코드 검사만으로 지원 완료를 선언하지 않는다.
- 실제 5장 생성 검증에서 일반 생성 완료 시의 자동 출력도 별도 필수 연결로 확인했다. `authored_presentation_generation.py` → `authored_presentation_artifacts.py::_build_authored_export` → `author_deck.py::build_image_pptx`가 사용된다. 기존 가로 고정 페이지와 세로 local-fit 증거가 충돌해 실제 작업이 차단되었으며, 해당 저장소 소유 조립기에 캔버스·PNG 크기 검증을 연결한다. 기존 체크포인트와 HTML을 보존한 재시도로 최종 산출물을 확인해야 한다.


### 10.7 실제 수용 실행으로 확인한 소유 경로와 정정 (2026-09-13)

이 절은 설계 가정이 아니라 관리 스택·PostgreSQL·실제 provider·다운로드 파일에서 확인한 사실이다. 앞선 절과 경로 설명이 다르면 이 절을 우선한다.

- 현재 화면 Fidelity/PDF는 workspace command → `workspace_quality_export_operation_handlers` → `render_and_export_authored_htmls` → Python public render/export worker를 사용한다. Next `runAuthoredFidelityPresentationExport`와 번들/최종 조립 helper는 현재 production 호출자가 없다. 해당 helper의 크기 지원과 실제 제품 사용 검증을 구분한다.
- 생성 직후 자동 PPTX는 `authored_presentation_artifacts._build_authored_export` → `author_deck.build_image_pptx`를 사용한다. 이 소유 builder의 페이지 크기까지 전달해야 일반 Authored 세로 생성이 완료된다. 다운로드된 runtime을 편집하지 않는다.
- 최초 admission은 원 생성 operation에만 연결된다. 재시도는 같은 generation job과 immutable 입력을 보존하면서 child operation, 새 canonical payload hash, parent action receipt를 만든다. 복원은 검증된 retry ancestry를 따라 원 admission과 artifact provenance를 확인하며, snapshot 또는 artifact digest를 수정하지 않는다.
- 최종 semantic projection writer도 기존 `theme.authored_vnext.canvas` 및 snapshot 의미를 보존해야 한다. 단순 theme 하위 객체 대입은 staging에 저장한 canvas를 소실시켰으며 merge helper로 연결하였다. 과거 누락 문서는 읽기 projection에서만 가로 기본값을 제공한다.
- 관리 generation controller의 기존 등록 범위에 단일 페이지 `slide.regenerate.preview`와 `slide.regenerate`를 포함한다. 일반 `presentation.edit` 및 batch 범위를 임의로 확대하지 않는다.
- PostgreSQL slide content/properties는 JSON이다. 발행 CAS는 기존 JSONB 변환 비교 helper를 사용하며 기존 owner, source HTML, content, properties 경쟁 검사를 보존한다.
- header chrome는 독립 `AuthoredHeaderContract`의 위치 규칙을 따른다. body safeArea 64px를 모든 header glyph의 경계로 재해석하지 않는다. 실제 상단 28px header는 기존 가로 계약에도 속하며 육안 잘림이 없었다.
- 첫 지원 조합은 일반 default Authored + 9:16 + Fidelity/PDF이다. 회사 master, 다른 style, source-native/hybrid를 조용히 대체하지 않는다. 실제 생성·재열기·재생성 후 export 수용이 끝나기 전에는 catalog capability를 공개하지 않는다.

- 페이지 재생성은 preview와 publish 모두 기존 `_fit_content_revision`에서 유효 local-fit 영수증을 만든다. publish는 승인 후보 canonical digest, 동일 HTML 바이트 및 content-addressed PNG를 확인하고, 새 대상 영수증과 slide/theme를 같은 transaction의 CAS로 저장한다. untouched slide 영수증은 현재 HTML digest와 계속 일치해야 한다. 기존 generation V2/V3·deck digest는 과거 생성 증거로 보존하며 새 revision의 release-ready 인증으로 재표기하지 않는다.
- DOM footer 경계는 실제 viewport 높이에서 기존 720px 기준 inset(페이지 번호 있음 72px, 없음 32px)을 뺀다. 가로 648/688px 규칙은 그대로이며 세로는 1208/1248px이다. 세로형을 위해 기준을 낮춘 것이 아니라 같은 하단 여백 계약을 실제 canvas에 전달한 것이다.
