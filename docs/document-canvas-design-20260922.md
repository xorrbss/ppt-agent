# 모드 공통 문서 캔버스 설계

작성일: 2026-09-22

개정: 2026-09-22. 설계 검토에서 지적한 지원·인수표, 마스터 변환 방식, 글자 정책, 누락된 수명주기, 후보 검증·활성화 절차, 과거 기록과 현재 기준의 구분을 반영했다.

현재 기준: 문서 크기는 모드와 독립적인 공통 계약으로 정하고, 신규 A4 가로·세로 생성부터 기존 사용자 작업과 PPTX/PDF 다운로드까지 하나의 구현 작업으로 완성한다. 최신 사용자 지시에 따라 난이도별 멀티에이전트를 사용하며 모델 상한은 astra/medium이다. 구현을 진행했으며, 실제 사용 인수는 아래 외부 차단 상태로 구분한다.

상태 구분: 13·15절에는 과거 부분 구현과 좁은 검사 결과가 기록되어 있다. 이는 실제 사용 인수·최종 활성화 완료의 증거가 아니다. 최신 구현·검사·실행 결과는 다음 기록을 적용하며, 필수 제품 경로 인수 전에는 완료로 판정하지 않는다.

### 최신 구현 및 인수 기록

- 공통 캔버스의 생성 요청·저장·Scene 재생성/복원·썸네일 전파, 고정 마스터 목표 영역 배치, HTML 최종 캡처 불변성 및 native Scene의 A4 물리 단위 변환을 구현했다. SVG와 hybrid의 기존 공통 변환을 함께 유지한다.
- Scene의 canonical CSS point 값은 변경하지 않고 직렬화 경계에서 물리 단위로 변환한다. 명시된 canvas alias 간 충돌은 거부하며 레거시 배율은 보존한다. public export의 isolated worker 경계와 선택적 직접 Scene 경로는 구분한다.
- 글자 정책은 버전별로 보존한다. 현재 local-fit v1.5는 9pt이며 v1.3의 9.5pt와 별개다. dense Composer 7 CSS px, 일반 source-native 본문 12 physical pt, capability 9pt도 서로 대체하지 않는다.
- 집중 pytest 최초 실행은 91개 중 87개 통과, 4개 실패였다. 누락된 CSS parser 의존성, 테스트 기대값, Scene identity 처리와 세로 제목 줄바꿈을 수정했다. 실패 4개 재검사는 3개 통과·1개 실패였고, 마지막 세로 Scene 수정 후 해당 1개 검사는 통과했다. 전체 91개를 다시 실행한 결과라고 주장하지 않는다.
- 기존 managed PostgreSQL 환경을 유지해 API·생성/출력 worker를 반영했다. 후보 UI에서 Sol/Medium, 신규 A4 가로 첫 클릭 요청이 200으로 저장되는 것을 확인했다. 문서 ID: a1d4124a-0aca-4a64-ac8d-65d2edb6a8e7. 화면 증거: artifacts/a4-product-acceptance-20260922/candidate-upload.png.
- 실제 개요 생성은 연결된 모델 계정의 usage_limit_reached(429)로 차단됐다. 계정·결제·provider를 임의 변경하지 않는다. A4 양방향의 실제 모델 생성, 나머지 모드별 UI 수명주기 및 최종 PPTX/PDF 다운로드·시각 readback은 아직 확인하지 못했다. helper/단위 테스트 결과로 대체하지 않는다.
- 차단에 따라 임시 candidate writer를 False로 복구하고 release verified도 False로 유지한다. 저장된 후보 문서와 증거는 삭제하지 않는다. 모델 한도 해제 또는 사용자가 승인한 실행 경로가 필요하며, 현 상태는 사용 가능 인수 완료가 아니다.

| 문서 구역 | 용도와 우선순위 |
|---|---|
| 1, 3~8, 9.2~9.3, 11절 | 공통 계약·단위·호환·독립 readback 기준 |
| 16절 | 현재 실행·지원 범위·인수·활성화 기준. 이전 실행 계획과 충돌하면 이 절을 적용 |
| 2, 9.1절 | 최초 조사 당시의 구조와 문제 원인. 현재 코드 상태표가 아님 |
| 10, 12~15절 | 이전 계획·기록 상태·부분 구현 이력. 현재 완료 판정이나 최신 실행 증거로 재사용하지 않음 |

이후 설계 변경은 해당 계약과 16절을 갱신한다. 이전 기록을 지우거나 새로운 최종 계획을 뒤에 계속 덧붙여 권위 기준을 분산시키지 않는다.

## 1. 목적과 범위

문서에서 용지와 방향을 한 번 선택하면 HTML, SVG, 스타일, 미리보기, PPTX/PDF 출력이 동일한 크기를 사용한다. 새 모드를 추가할 때 크기 상수나 방향 정책을 다시 구현하지 않는다.

- 신규 UI는 A4 가로/세로를 제공한다. 선택한 모드나 스타일이 방향을 자동 변경하지 않는다.
- 저장된 문서, 복제, 페이지 재생성, 작업 재개, 내보내기는 저장된 캔버스를 사용한다.
- 기존 문서의 크기와 기존 클라이언트의 생략 기본값은 유지한다.
- 일반 생성과 source-content 보존 정책, SVG 보안/자산 계약, 편집 가능 출력 의미는 그대로 유지한다.
- 기존 adaptive/React 템플릿 생성 경로를 복원하지 않는다. 템플릿 호환 범위는 현재 스타일·회사 표준·고정 마스터 및 기존 Template V2 문서 읽기/출력이다.
- 이번 단계는 설계 문서만 작성·개정한다. 제품 코드, DB, 실행 프로세스는 변경하지 않는다.

## 2. 최초 조사 당시 연결부와 설계 근거 — 이력

아래 표는 공통 캔버스 구현 이전의 조사 기준이다. 이후 변경 이력은 13·15절, 현재 인수 기준은 16절을 따른다.

| 연결부 | 확인된 구조 | 설계 영향 |
|---|---|---|
| `servers/fastapi/services/authored_canvas.py` | authored-canvas/v1, 요청 해석, theme.authored_vnext.canvas 저장/복원, 레거시 기본값 | 기존 facade를 유지하며 공통 resolver로 위임 |
| `servers/nextjs/lib/authored-canvas.ts` | 캔버스 파싱 및 9525 EMU/px, 0.75pt/px 변환 | 파서·물리 변환을 모드 중립 모듈로 추출 |
| `servers/fastapi/models/generate_presentation_request.py` | authored/svg 전략, 기존 aspect_ratio | 모드 분기 전에 공통 페이지 요청 normalization |
| `servers/fastapi/workers/public_svg_export_worker.py` | canvas 검증, SVG 크기 검증, native 변환, PDF 연결; readback에 9525 직접 사용 | SVG 변환기 입력부터 실제 native 요소까지 물리 변환 연결 필요 |
| `servers/nextjs/app/(presentation-generator)/components/SvgSlidePreview.tsx` | 기존 AuthoredCanvasContract 사용 | 공통 계약 소비자로 전환 가능 |
| `servers/nextjs/lib/authored-hybrid/native-plan.ts`, `native-table.ts` | px, CSS pt, 출력 EMU 계산 혼재 | 필드의 단위 의미를 분류한 뒤 변환 context 적용 |

기존 SVG 설계의 생성 후 모드 고정 원칙을 유지한다. 생성 전 모드를 바꿀 때 페이지 설정은 유지하며, 저장 후 다른 모드로 만드는 동작은 같은 캔버스를 복사한 새 문서 생성이다. 저장된 SVG를 HTML로 암묵 변환하지 않는다.

## 3. 공통 계약

새 모드 중립 wire 계약의 권고 이름은 `document-canvas/v1`이다. 기존 `authored-canvas/v1`과 이름 공간이 다르므로 기존 버전의 의미를 바꾸지 않는다. 앞선 논의의 authored-canvas/v2 역할을 이 공통 계약이 담당한다.

권위 필드:

| 필드 | 의미 |
|---|---|
| contractVersion | document-canvas/v1 |
| logicalSize.width / height | HTML CSS px 또는 SVG user unit으로 사용하는 논리 좌표 범위 |
| physicalSize.widthEmu / heightEmu | 양의 정수로 저장하는 최종 물리 크기 |
| safeArea.x / y / width / height | 논리 좌표에서 콘텐츠 배치 가능 영역 |

물리 크기는 정수 EMU를 권위로 사용한다. mm와 point를 별도 수정 가능한 필드로 중복 저장하지 않는다. 방향, 비율, A4 라벨은 치수에서 파생한다. 정사각형·기타 크기는 레거시 호환 reader에서 표현할 수 있으나 신규 UI 선택지는 확장하지 않는다.

파생 계산:

- emuPerUnit = physicalSize.widthEmu / logicalSize.width
- 신규 document-canvas/v1은 세로에서도 동일 배율이어야 한다. 유효성 검사는 정수 치수의 교차 곱으로 수행한다. 레거시 원본에는 이 신규 검사를 소급 적용하지 않는다.
- pointPerUnit = emuPerUnit / 12700
- widthMm = physicalSize.widthEmu / 36000
- widthPt = physicalSize.widthEmu / 12700
- 방향과 aspect ratio는 logicalSize에서 계산한다.

PPTX 정수 반올림은 직렬화 경계에서만 수행한다. PDF point는 계산 정밀도를 유지한다. DPR과 캡처 해상도는 별도 render 옵션이며 문서 크기와 export 배율을 바꾸지 않는다.

### 수치 유효성 및 레거시 정밀도

신규 계약의 logicalSize는 각 축 1~7680의 정수, physicalSize는 각 축 1~2147483647 EMU의 정수로 제한하는 안을 채택한다. 이는 wire 수치 상한이며 임의 크기 생성 허용 범위가 아니다. 초기 신규 writer는 위 범위 안의 A4 두 프리셋만 허용한다. 크기별 렌더 자원 제한은 별도로 기존 admission에서 검사한다.

safeArea는 유한한 십진수로 소수 여섯 자리까지 허용하고, x/y는 0 이상, width/height는 양수이며 캔버스 내부여야 한다. 초과 정밀도를 조용히 반올림하지 않고 거부한다. NaN/Infinity, boolean, 숫자 문자열, 누락/추가 필드, 미지원 버전은 거부한다. 교차 곱은 Python 정수와 TypeScript BigInt로 검사해 중간 곱의 정밀도 손실을 막는다.

레거시 reader는 원본 계약 버전과 기존 좌표·물리 크기·안전영역을 보존한 LegacyCanvasContext를 반환한다. A4의 DocumentCanvasContext와 함께 소비자 경계의 명시적 union으로 취급한다. 기존 import/template에서 반올림한 px와 원본 EMU의 비율이 정확히 일치하지 않더라도 신규 스키마 검사로 거부하거나 강제 변환하지 않는다. 기존 exporter의 동작을 legacy adapter에서 유지하며 축별 비율 차이를 신규 A4 경로의 허용 근거로 사용하지 않는다.

레거시 문서의 최초 저장·복제는 기존 표현을 유지한다. A4 변경을 명시적으로 요청할 때만 새 계약과 재배치를 생성한다. 지원 가능한 레거시 context를 구성할 정보가 없으면 명시적 미지원으로 처리하며 물리 크기를 추정하지 않는다.

### A4 프리셋

| 항목 | 가로 | 세로 |
|---|---|---|
| 물리 mm | 297 × 210 | 210 × 297 |
| 논리 좌표 | 1188 × 840 | 840 × 1188 |
| 물리 EMU | 10692000 × 7560000 | 7560000 × 10692000 |
| 안전영역 | x=60, y=60, width=1068, height=720 | x=60, y=60, width=720, height=1068 |
| EMU/unit | 9000 | 9000 |
| PDF pt | EMU / 12700으로 계산 | EMU / 12700으로 계산 |

4 unit/mm와 15mm 안전여백은 신규 A4 프리셋에만 적용한다. 레거시의 배율 및 저장된 안전영역은 보존한다.

## 4. API 및 기본값

외부 생성 요청은 `page_size: A4`, `orientation: landscape | portrait`를 사용한다. 클라이언트가 임의 물리 배율이나 canvas digest를 결정하지 않는다. 서버가 프리셋에서 canonical canvas를 생성한다.

| 요청 | 처리 |
|---|---|
| 신규 두 필드 명시, aspect_ratio 생략 | A4 프리셋 해석 |
| aspect_ratio만 명시 | 기존 레거시 크기 |
| 세 필드 모두 생략 | 기존 클라이언트 기본 동작 유지 |
| 신규 필드와 aspect_ratio 동시 명시 | 422; 값이 가로를 뜻하더라도 중복 selector로 거부 |
| page_size 또는 orientation 중 하나만 명시 | 422 |
| null, 미지원 값, 유효하지 않은 크기 | 422 |

Pydantic 기본값이 채워지기 전 명시된 필드 여부를 판정한다. 신규 UI는 두 필드를 항상 함께 전송하며 legacy aspect_ratio를 덧붙이지 않는다. bootstrap/generate/workspace에서 같은 normalization을 사용한다.

기존 문서를 대상으로 하는 재생성·resume에서는 저장된 캔버스가 권위다. 일반 재생성 요청으로 크기 변경을 받지 않으며 불일치는 명시적 충돌로 반환한다.

## 5. 저장과 호환

권고 저장 위치는 기존 presentation theme JSON의 모드 중립 `documentCanvas`다. 기존 저장 기술을 재사용하며 이 설계만으로 DB 컬럼 추가를 요구하지 않는다. 구현 시 theme 병합·버전 복원 경로가 해당 필드를 보존하는지 확인한다.

Reader 우선순위:

1. documentCanvas가 있으면 엄격히 검증한다. 손상/미지원 버전을 레거시로 fallback하지 않는다.
2. 없으면 기존 theme.authored_vnext.canvas를 읽어 공통 in-memory 형태로 변환한다. 원본 문서에 다시 쓰지 않는다.
3. 기존 Template V2/import 문서는 해당 문서에 저장된 고유 크기 계약을 adapter로 읽는다. 데이터 위치는 구현 시 해당 reader에서 확인한다.
4. 정말 캔버스가 없는 기존 Authored 문서에만 현재 레거시 기본값을 적용한다. 출처 불명 문서에 전역 16:9 기본값을 적용하지 않는다.

신규 writer는 documentCanvas만 권위로 기록한다. 서로 다른 의미의 v1 필드를 이중 기록하지 않는다. 두 계약이 공존하면 의미가 일치하는지 검사하고 충돌을 숨기지 않는다.

### 소비자 전환 및 저장 보호

| 소비 경계 | 필수 변경 | 미지원 소비자 처리 |
|---|---|---|
| 문서 조회·편집·미리보기 | resolved canvas 및 계약 capability를 응답하고 비율/viewport에 사용 | 신규 계약 지원을 광고하지 않은 클라이언트에 A4 편집용 원본을 전달하지 않고 업그레이드 필요 오류 |
| 공개 공유·서버 렌더·썸네일 | 공통 reader 사용, canvasDigest가 포함된 캐시 키 | 지원 reader로 라우팅하거나 실패; 16:9 fallback 금지 |
| 페이지 재생성·복제·버전 복원 | 원본 revision의 캔버스 보존, 다른 요청 크기 거부 | 해당 계약을 처리할 작업자에게만 전달 |
| 작업 resume·export bundle | 고정 계약과 digest 검증, capability별 dispatch | 구 worker가 임대·실행하지 않도록 admission에서 차단 |
| theme 변경·일반 문서 저장 | documentCanvas를 서버 소유 필드로 보호 | 생략은 보존, 동일 값 roundtrip은 허용, 삭제·다른 값·legacy 중복 주입은 거부 |

capability는 UI 표시용 정보에 그치지 않는다. 구 클라이언트 차단은 서버 DTO/route에서 시행한다. 신규 클라이언트의 지원 버전 전달 방식과 각 실제 reader/저장 handler 파일은 단계 2의 전환 목록에 기록한다. 이름이 비슷한 새 경로를 만들지 않고 기존 API 확장 방식에 맞춘다.

배포 순서는 공통 reader·서버 저장 보호·worker capability → 지원 UI/공유 렌더 → 모든 활성 소비자의 준비 확인 → A4 writer 활성화다. version restore는 theme만 바꾸지 않고 해당 revision의 페이지 원본과 캔버스를 함께 복원한다. writer 중단 시에도 이미 생성된 A4의 reader/export 지원은 남긴다. 이를 제거하는 구 바이너리로의 완전 롤백은 안전한 롤백으로 간주하지 않는다.

각 경계는 담당 파일·입력 DTO·legacy fallback 여부·지원 버전·검증 항목을 전환 목록에 남겨야 한다. 하나라도 미확인인 소비 경계가 있으면 A4 writer 활성화 조건을 충족하지 못한다.

공통 스키마와 A4/레거시 변환 fixture는 `contracts/document-canvas/v1/`에 두는 안을 권고한다. Python과 TypeScript는 각 언어의 작은 helper를 사용하고 동일 fixture로 일치성을 확인한다. 별도 네트워크 변환 서비스는 만들지 않는다.

## 6. 작업·캐시·변경 수명주기

- 서버는 canonical 필드의 결정적 직렬화로 canvasDigest를 계산한다. digest는 스키마 필드 자체에 포함하지 않는다.
- operation 입력, 요청 identity, 생성 체크포인트, 측정 receipt, 렌더 캐시, export bundle에 캔버스와 digest를 연결한다.
- 동일 크기라도 안전영역이 달라지면 digest가 달라진다.
- 기존 idempotency 및 lease/fencing 계약을 재사용한다. 페이지마다 최신 설정을 다시 읽지 않는다.
- 복제는 원본 캔버스를 복사한다. 새 문서 기본 A4를 덮어씌우지 않는다.
- 실패 후 재개와 페이지 재생성은 snapshot의 캔버스를 사용한다.
- 저장 후 방향 변경은 별도 명시 동작으로 새 문서 revision을 만든다. 새 캔버스로 전체 재배치를 완료하고 기존 CAS 게시 경계에서 교체한다. 실패 시 기존 문서를 유지한다.
- 방향 변경 도중 생성된 오래된 페이지/미리보기/export를 새 revision에 붙이지 않는다. canvasDigest와 문서 revision을 모두 비교한다.
- 초기 공통화 릴리스는 생성 전 방향 선택을 우선한다. 저장 후 방향 변경 UI는 위 원자적 재배치 경로가 구현되기 전 노출하지 않는다.

### canvasDigest 정규화

신규 계약은 서버가 digest를 발급한다. 클라이언트는 서버 값을 전달하며 임의 digest를 권위로 제출하지 않는다. worker 및 검증기에서 재계산할 때도 다음 동일 규칙을 따른다.

1. 스키마 검증 후 safeArea의 각 값을 1000000배한 정수로 정규화한다. 십진수 표현을 기준으로 정확히 변환하며 binary float의 단순 곱 결과를 문자열화하지 않는다. -0은 0, 60과 60.0은 같은 값이다.
2. 고정 순서의 문자열 배열을 만든다: contractVersion, logicalWidth, logicalHeight, physicalWidthEmu, physicalHeightEmu, safeXMicro, safeYMicro, safeWidthMicro, safeHeightMicro.
3. 모든 수치는 부호와 십진 숫자만 사용한 정수 문자열이다. 선행 0, 양수의 +, 지수 표기를 쓰지 않는다. JSON 배열은 쉼표 뒤 공백·개행·BOM 없이 UTF-8로 직렬화한다.
4. 이 바이트열의 SHA-256을 소문자 hex로 반환하며 전달 형식은 sha256:<hex>다. digest 자신, 파생 orientation/비율, renderer 버전, 페이지 내용은 포함하지 않는다.
5. export/측정/캐시 identity는 canvasDigest 외에 원본·문서 revision·renderer/converter 버전 및 render 옵션을 기존 규칙대로 포함한다. 캔버스 digest가 같은 것만으로 작업을 재사용하지 않는다.

계약 fixture에는 A4 두 방향의 정규화 바이트열과 기대 digest, 60/60.0/-0 동등성, 허용 최소 소수 및 초과 정밀도 거부 사례를 체크인한다. 기대 digest를 테스트 실행 중 제품 helper로 생성하지 않는다. 실제 hash 값은 단계 1의 계약 산출물이며 이 문서에서 실행한 결과로 제시하지 않는다.

레거시 resume/cache는 기존에 동결된 계약과 identity 규칙을 유지한다. 기존 수치를 새 micro 단위로 양자화하거나 새 digest로 덮어쓰지 않는다. union의 legacy 분기는 원래 버전의 validator/identity adapter에 위임하며 신규 버전과 캐시 namespace를 공유하지 않는다.

## 7. 모드별 연결과 변환 소유권

공통 resolver → 모드별 생성/배치 → 최종 원본 확정 → 측정/검증 → 게시 → 렌더/export 흐름을 따른다.

| 소비자 | 책임 | 금지 동작 |
|---|---|---|
| HTML/Composer | 프롬프트, 루트, viewport, 안전영역에 resolved canvas 전달 | 고정 1280×720 또는 세로 크기 비교로 분기 |
| SVG | 루트 width/height 및 viewBox를 논리 캔버스에 맞춤, 생성 결과 일치 검사 | viewBox 변경만으로 기존 내용을 재배치한 것으로 처리 |
| 스타일/회사 표준 | 목표 캔버스에 재배치하거나 측정 가능한 고정 원본을 contain | 스타일 선택 시 문서 방향 변경 |
| 기존 Template V2 | 저장된 문서 크기를 공통 reader/export context에 연결 | 신규 생성 경로 복원 또는 자동 A4 변환 |
| 미리보기 | 문서 비율을 화면에 맞게 표시 | 화면 축소율을 문서 좌표나 폰트 크기에 저장 |
| PPTX/PDF | 공통 context로 페이지와 모든 요소 변환 | 자체 크기 기본값 및 전역 고정 배율 |

Contain은 기존 fit/layout 단계가 소유한다. scale=min(targetWidth/sourceWidth, targetHeight/sourceHeight) 및 중앙 정렬 offset을 계산하며 비균일 stretch/crop을 하지 않는다. sourceRect는 명시적 콘텐츠 프레임이 있으면 그것을 사용하고, 없으면 전체 원본 캔버스를 사용해 보수적으로 보존한다. 추정한 visible bounds만으로 콘텐츠를 잘라내지 않는다.

원본 크기조차 측정할 수 없으면 생성 전 admission에서 실패한다. 실제 내용에 따른 font floor/overflow 실패는 배치 후 판단한다. 재배치 지원이 없는 고정 마스터는 fit ladder가 있다고 가정하지 않는다.

배경 full bleed는 명시적으로 분리된 배경에만 허용한다. 전경에 포함된 배경을 자동 분리하거나 늘리지 않는다.

최종 확정 원본은 목표 논리 좌표계를 사용한다. 원본 안에 표현된 transform은 정상적으로 렌더·측정하되, export가 같은 contain 행렬을 다시 적용하지 않는다. 최종 source digest와 canvas digest를 측정/캡처/export 모두에 묶는다.

## 8. 단위와 글자 정책

CSS px↔CSS pt의 0.75와 논리 좌표↔출력 pt의 배율을 구분한다. 기존 필드 이름에 Pt가 있다고 실제 출력 pt라고 가정하지 않는다.

- 브라우저에서 수집한 CSS pt를 논리 px로 복원할 때는 CSS 변환 규칙을 유지한다.
- 복원한 논리 길이를 출력 pt/EMU로 변환할 때만 canvas context를 적용한다.
- 이미 물리 pt로 정의된 사용자 지시나 출력 속성은 다시 canvas 배율을 곱하지 않는다.
- 글자, 행간, 자간, 선 두께, 표 셀 여백, 도형 반경, 이미지 crop/clip, 그룹 transform도 단위 분류 대상이다.
- 기존 경로·역할의 font floor 정책은 별도로 유지하고 정책 식별값과 단위를 명시한다. A4 공통화 자체가 7px 또는 9.5pt의 전역 재정의를 승인하지 않는다.
- 품질 receipt에는 정책 기준과 최종 변환 후의 유효 물리 크기를 함께 남긴다. 최소 크기 위반을 추가 축소로 해결하지 않는다.

### 필드별 단위와 변환 책임

아래는 신규 context의 adapter 계약이다. 기존 scene 필드를 일괄 개명하지 않고 각 adapter 입출구에서 의미를 명시한다. U는 목표 캔버스의 논리 단위, E는 emuPerUnit, P는 pointPerUnit이다. 새 함수명은 제안이며 동일 책임의 기존 helper가 있으면 확장한다.

| 값 | 입력·내부 단위 | HTML/SVG 값 | PPTX/PDF 값 | 변환 책임 |
|---|---|---|---|---|
| 위치·폭·높이 | 최종 배치 U | CSS px / SVG user unit | U×E EMU / U×P pt | geometry adapter → export context |
| 브라우저 측정 글자·행간·자간 | CSS pt는 /0.75로 복원한 U, px 측정은 그대로 U | U | U×P pt; PPTX 글자 직렬화 시 ×100 | measurement adapter → typography serializer |
| 사용자가 지정한 물리 글자·행간·선 크기 | 목표 pt와 출처 보존, 생성 전 U=pt/P로 변환 | 변환한 U를 CSS px / SVG user unit으로 사용 | 동일 U×P로 원래 목표 pt 복원 | physical intent adapter → 공통 render/export |
| 선·셀 여백·도형 반경 | 측정 출처의 단위를 U로 정규화 | U | U×E 또는 U×P, 대상 속성에 맞춰 직렬화 | style/table adapter |
| 이미지 crop 비율·opacity | 무차원 비율 | 동일 비율 | 동일 비율 | 물리 배율 적용 금지 |
| transform | 이동은 U, scale은 무차원, 회전은 각도 | 같은 최종 배치 의미 | 이동만 물리 단위 변환, 중첩 순서 유지 | layout adapter → geometry serializer |
| viewBox·페이지 크기 | canonical logical/physical size | logicalSize | physicalSize | canvas resolver |

물리 9.5pt 지시는 A4에서 약 13.405556U가 된다. 이를 HTML/SVG에도 먼저 적용한 뒤 같은 최종 좌표를 export해야 한다. CSS에 9.5pt를 그대로 쓰고 PPTX에만 9.5pt를 기록하면 같은 크기를 보장하지 못한다. 이 수치는 변환 예시이며 모든 글자의 최소 크기를 9.5pt로 재정의하지 않는다.

contain 등 배치 변환 후의 유효 글자·선 크기로 최종 검사한다. 물리 크기 지시가 고정 조건이면 contain 축소로 그 조건을 깨뜨릴 수 없으며 재배치 또는 명시적 실패로 처리한다. DOM 측정값이 이미 transform을 포함하는지 adapter별로 기록하여 scale을 다시 곱하지 않는다.

각 구현 담당자는 fontSizePt, lineHeight.points, strokeWidthPt 등 기존 필드의 생산자·소비자를 좁게 확인하고 위 표의 어느 단위인지 매핑한다. 미분류 필드는 신규 A4 export에서 사용할 수 없다. 화면 표시용 미리보기 zoom과 캡처 DPR은 이 표의 물리 크기에 포함하지 않는다.

## 9. SVG 및 export 특별 사항

### 9.1 최초 조사에서 확인한 고정 배율 — 이력

다음은 최초 코드 조사 결과이며 현재 코드 상태나 실제 A4 생성 시험의 결과가 아니다. 줄 번호도 당시 기준이다. 아래 경로는 `servers/fastapi/` 기준이다.

| 위치 | 확인 사항 |
|---|---|
| `workers/public_svg_export_worker.py:38` | 변환기에 SVG 파일과 출력 옵션을 전달하며 별도 물리 크기/context 인자를 전달하지 않음 |
| `workers/public_svg_export_worker.py:49` | 완성된 PPTX를 다시 열어 페이지 수와 canvas px × 9525 크기를 비교하는 readback 검사. 실제 요소 변환 코드가 아님 |
| `vendor/slide_master/svg_to_pptx/pptx_package/builder.py:4010` | SVG 형식 또는 viewBox에서 논리 크기를 구해 페이지 크기 계산에 전달 |
| `vendor/slide_master/svg_to_pptx/pptx_package/dimensions.py:25` | 914400 / 96 = 9525 EMU/px로 페이지 물리 크기 계산 |
| `vendor/slide_master/svg_to_pptx/drawingml/utils.py:21` | native 좌표 변환용 9525 EMU/px와 글자 변환용 75 hundredths-of-point/px 상수 정의 |
| `workers/public_export_worker.py:571` | SVG PDF 경로에서도 재사용되는 함수가 canvas px × 0.75로 페이지 point 계산 |

96px/inch는 논리 좌표의 물리 크기 해석이며 캡처 DPR 또는 이미지 해상도 설정이 아니다. 기존 문서에는 유효한 배율이다. 제안한 A4 1188×840을 그대로 이 배율로 처리하면 계산상 314.325×222.25mm가 되어 목표 297×210mm보다 각 길이가 약 5.83% 커진다. 신규 A4에는 9000 EMU/unit을 사용해야 한다.

### 9.2 변경 계약

converter 경계에 명시적 물리 크기/context를 전달해 페이지와 native 요소를 함께 직렬화한다. p:sldSz만 바꾸는 사후 패치는 허용하지 않는다. worker의 readback 기대값만 9000으로 바꾸는 것도 실제 변환을 고치지 못하므로 허용하지 않는다.

페이지, 도형·이미지 위치와 크기, 글자·행간·선·여백 및 결과 검사에 동일 문서 context를 적용한다. 8절의 CSS 단위 복원과 출력 변환을 구분한다. 9525/0.75를 전역 치환하지 않으며 기존 문서는 기존 배율을 유지한다.

현재 호출 경계에 context가 없으므로 converter의 호출별 context 지원을 선행 구현한다. native 텍스트·도형을 전체 이미지로 대체해 크기 지원 성공으로 처리하지 않는다. vendor 파일만 수동으로 고치고 출처·manifest를 방치하지 않는다.

FastAPI public SVG/export/render worker와 Next.js hybrid/export/readback은 동일 context를 소비한다. PDF의 MediaBox와 paint matrix는 동일 physicalSize에서 파생한다. 구조 readback 성공과 시각 품질 검증 여부는 독립적으로 보고한다.

### 9.3 converter 변경 소유권과 공급 절차

출력 담당자가 converter 변경과 해당 worker/bundle을 함께 소유한다. upstream 원본은 현재 고정된 converter revision, 로컬 변경의 권위는 `compatibility/slide-master/patches/`에 버전 관리하는 제한된 패치 집합으로 두는 안을 채택한다. 이 경로와 패치 적용 도구는 제안이며 현재 존재하는 절차로 단정하지 않는다. 저장소에 동등한 관리 도구가 있으면 그 도구에 통합한다.

주요 수정 경계는 `pptx_package/builder.py`의 공개 진입점, `dimensions.py`의 페이지 크기 계산, `drawingml/utils.py` 및 이를 사용하는 좌표·text·stroke serializer, `workers/public_svg_export_worker.py`와 PDF helper다. 공개 진입점은 불변 conversion context를 선택 인자로 받고 신규 worker는 이를 항상 명시한다. 인자 생략은 구 호출자의 기존 96px/inch 동작만 보존한다.

context는 한 export 호출 및 그 하위 작업에만 전달한다. 모듈 상수 monkey patch, 환경변수로 현재 배율 전환, process-global mutable scale은 금지한다. 같은 worker에서 legacy→A4→legacy 순서 또는 서로 다른 문서가 실행되어도 배율이 누출되지 않아야 한다.

단계 1A 시작 시 구현할 공급 절차:

1. 고정 원본과 추적 가능한 로컬 패치에서 임시 경로의 converter를 재구성한다. 기존 vendor와 사용자 변경을 무조건 덮어쓰지 않는다.
2. 재구성 결과를 검토한 뒤 vendor 반영 파일을 제한하고, 같은 입력에서 같은 바이트가 나오는 재현 가능한 명령을 문서화한다. 원격 게시나 upstream 반영은 이 작업 범위가 아니다.
3. 파일 hash MANIFEST와 converter 버전 식별자에 로컬 패치 revision을 반영한다. manifest 검사를 끄거나 예상 digest만 임의 교체하지 않는다. 라이선스·원본 provenance는 보존한다.
4. export bundle 생산자와 worker의 버전/capability를 함께 갱신한다. 구 bundle을 실행해야 하는 경우 기존 고정 converter를 유지하거나 명시적 재시도 정책을 정의하며 현재 버전으로 몰래 실행하지 않는다.
5. 선행 readback은 검토한 manifest를 통과하는 worker 경로를 호출한다. 임시로 수정한 converter 함수를 직접 호출한 결과만으로 통과하지 않는다. 기록에는 실제 converter/version/manifest digest를 포함한다.

패치 재구성 명령과 실제 소유 경로 확정은 단계 1A의 첫 산출물이다. 공급 절차를 만들 수 없으면 converter 배포가 미해결인 것으로 보고하고 A4 활성화를 보류한다.

## 10. 구현 순서와 소유 경계

| 단계 | 산출물 | 결합 조건 |
|---|---|---|
| 1. 공통 계약 | 스키마, 프리셋, 단위 helper, shared fixture, v1 reader adapter | 프리셋/단위/직렬화 동결 |
| 1A. SVG 출력 선행 구현·검증 | 재현 가능한 converter 패치/manifest, context 연결, worker readback/PDF, A4와 레거시 파일 확인 | A4 통합·활성화의 선행 관문 |
| 2. 저장·작업 | 공통 admission, reader 전환 목록, theme 보호, snapshot/digest/resume | 1 이후 1A와 병렬 준비 가능; 신규 writer 비활성 유지 |
| 2U. UI 사전 작업 | 요청 타입, 가로/세로 상태, 모드 변경 시 선택 보존 | 1 이후 병렬 가능; 실제 A4 생성은 아직 노출하지 않음 |
| 3A. 생성 연결 | HTML/Composer/SVG, 스타일·마스터 admission/contain | 같은 canonical canvas 사용 |
| 3B. 출력 연결 확장 | 선행 SVG 경로를 제품 bundle에 통합, Python/TS worker 및 HTML hybrid native/table/PDF 연결 | 1A에서 확인한 context와 변환 fixture 재사용 |
| 3C. UI 연결 | 가로/세로, 모드 전환 상태 유지, bootstrap/generate payload | 서버 응답 resolved canvas 소비 |
| 4. 통합 | bundle 동기화, 문서·operation 전파 확인, 제한된 검증 | 구/신 계약 reader 지원 후 신규 writer 활성화 |

공통 계약 파일은 한 담당자만 수정한다. 1A는 생성 모델 호출 없이 고정된 작은 SVG 입력으로 실제 PPTX/PDF를 만들어 확인한다. A4 가로·세로의 페이지 크기, 좌상·우하 도형과 이미지, native 글자 크기 및 선 두께, 레거시 배율 보존을 확인한다. 페이지 크기만 맞거나 native 콘텐츠가 전체 이미지로 대체된 결과는 통과가 아니다.

단계 1 이후 1A, 2, 2U는 서로 소유 파일을 분리해 병렬 준비할 수 있다. 공통 계약 변경은 공통 담당자에게 모은다. 3A/3B/3C의 실제 제품 통합은 1A 통과와 2의 저장 계약 연결 이후 진행한다. 1A 실패 시 독립적인 UI/reader 준비까지 중단할 필요는 없으나 미검증 변환을 제품에 활성화하지 않는다. 이 설계 작성 중에는 별도 에이전트를 실행하지 않았다.

구 worker가 새 계약을 실행하지 않게 capability admission을 둔다. 신규 A4 writer를 중단하더라도 이미 저장된 A4 문서 reader/export는 유지한다. 모든 worker 소비자가 준비되기 전 UI만 먼저 활성화하지 않는다.

## 11. 검증 설계 및 완료 판정

검증 시점을 두 구현 단계로 구분한다. 1A의 좁은 선행 구현을 마친 뒤 해당 출력 경로를 덮는 검사 한 명령을 실행하고, 후속 제품 연결 구현을 마친 뒤 변경된 통합 경계를 덮는 가장 작은 검사 한 명령을 선택한다. 구현 변경 없이 같은 검사를 반복하지 않는다. 이는 종전의 마지막 한 번 검증 계획을 선행 위험 확인과 후속 통합 확인으로 구체화한 권고안이며, 현재 문서 수정에서는 명령을 실행하지 않는다.

전체 빌드·E2E·시각 비교를 자동 추가하지 않는다. 아래 범위를 실제로 덮는 harness가 없으면 검증 미포함 영역을 명시하며 한 export 단위 검사로 전체 제품 완료를 선언하지 않는다. 선행 결과는 이후 converter/context 변경으로 무효화될 수 있으므로 과거 통과를 최종 코드의 증거로 대체하지 않는다.

| 검사 범위 | 관찰할 결과 |
|---|---|
| 공통 normalization | 신규/레거시/생략/중복 필드 처리, 손상된 계약 거부 |
| 상태 전파 | UI 모드 선택 시 방향 유지, bootstrap/generate 동등성, 저장·resume digest 일치 |
| A4 × HTML/SVG × PPTX/PDF | 실제 파일 readback으로 페이지 물리 크기 확인 |
| native 배치 | 좌상·우하 기준점, 텍스트·표·도형·이미지 좌표/크기, 선·글자 단위 확인 |
| contain | 1회 적용, crop/stretch 없음, 안전영역 및 적용된 font policy 준수 |
| 레거시 | 16:9·9:16·9:13 및 기존 custom/template 치수 유지 |
| identity | 캔버스 변경 후 이전 체크포인트/캐시가 재사용되지 않음 |

### 독립 기대값과 검사 경계

제품의 좌표 변환 helper로 테스트 기대값을 계산하지 않는다. shared fixture의 기대 물리 값은 아래처럼 명시된 수치로 고정하고 Python/TypeScript 및 실제 파일을 각각 대조한다. 같은 helper를 호출한 두 결과의 일치만으로 통과하지 않는다.

| 기준 fixture | 독립 기대값 |
|---|---|
| A4 가로 PPTX | 10692000 × 7560000 EMU |
| A4 세로 PPTX | 7560000 × 10692000 EMU |
| A4 가로 PDF | 841.8897637795 × 595.2755905512 pt; 세로는 축 교환 |
| 25mm 도형/이미지 | A4에서 폭 100U, 출력 폭 900000 EMU / 70.8661417323pt |
| 15mm 위치 | A4에서 60U, 출력 위치 540000 EMU / 42.5196850394pt |
| 물리 9.5pt 글자 지시 | OOXML 글자 크기 950; 렌더 입력은 약 13.405556U |
| 물리 1pt 선 지시 | OOXML 선 두께 12700 EMU; 렌더 입력은 약 1.411111U |
| 기존 1280×720 | 12192000 × 6858000 EMU |
| 기존 720×1280 / 720×1040 | 6858000 × 12192000 / 6858000 × 9906000 EMU |

가로·세로 fixture에는 실제 우측·하단 기준점과 중첩 transform, 텍스트, 도형, 이미지가 포함된다. 각 기준점의 기대 좌표는 해당 fixture에 상수로 기록한다. HTML hybrid 통합에서 표·셀 여백을 추가 확인한다. 레거시 custom/import는 원본 파일에서 읽어 고정한 기대 크기를 사용한다.

PPTX native 글자는 텍스트 run의 존재와 크기를 읽고, 도형·이미지는 실제 요소 transform을 읽는다. PDF raster 경로는 MediaBox와 paint matrix를 검사하고 별도의 표시용 fixture에서 raster 내부 기준점 위치를 측정한다. 페이지 크기만으로 raster 콘텐츠 배율을 입증하지 않는다. 기준점 측정은 제한된 기하 검사이며 전체 슬라이드 시각 비교가 아니다. PDF vector 경로가 있다면 해당 콘텐츠 transform도 직접 검사한다.

| 속성 | 허용 오차 |
|---|---|
| PPTX 페이지 크기 | 정수 EMU 정확 일치 |
| 직접 직렬화한 위치·길이·선 두께 | 독립 기대값 대비 최대 1 EMU |
| PPTX 글자·point 기반 간격 | 각 속성의 저장 격자 기준; 1/100pt 속성은 반올림 오차 최대 0.005pt |
| 중첩 transform | 최종 월드 좌표에서 단계별 정수 반올림의 합성 상한을 fixture에 명시; 임의 1px 오차로 완화 금지 |
| PDF 페이지·paint matrix | 최대 0.01pt |
| raster 기준점 | source raster에서 최대 1픽셀; 캡처 배율을 고정하고 물리 환산 오차 함께 보고 |

같은 worker/context 사용 경로에 legacy→A4→legacy 순서 fixture를 두어 전역 배율 누출을 확인한다. digest 검사에는 고정 byte/hash 기대값을 사용한다. reader 검사는 구 capability의 A4 접근 거부와 theme 변경 후 크기 보존을 포함한다.

최종 검증 전 표의 각 항목을 실제 검사 파일/케이스에 연결한다. 한 명령으로 덮지 못한 항목은 미검증으로 표시하고 그에 해당하는 완료 주장을 제외한다. 구조 검사는 실제 PowerPoint의 글꼴 대체·렌더링 잘림까지 입증하지 않는다.

완료의 핵심은 새 생성/출력 모드에 프리셋 상수를 추가하지 않고 공통 계약 연결만으로 동일 크기를 사용하는 것이다. 모든 템플릿의 무조건 지원이나 무조건 잘림 없는 결과는 약속하지 않는다. 미지원·품질 실패를 크기 변경 없이 명시적으로 처리한다.

## 12. 기록 상태

compiled-spec-memory 스킬은 이번 세션의 제공 목록과 로컬 스킬 검색에서 발견하지 못했다. 자동 spec 기록은 수행하지 않았다. 사용자 승인 방향과 구현 제안을 이 문서에서 구분하며 런타임 검증으로 확인된 계약으로 표시하지 않는다.

## 13. 구현 상태 — 2026-09-22

이 절은 설계의 전체 완료 선언이 아니라 실제 구현과 남은 활성화 조건을 구분한다.

- 공통 Python/TypeScript 계약, 독립 digest fixture, 신규 요청 selector, 저장·durable 전달, reader 협상, theme/버전 복원의 캔버스 보존을 구현했다.
- UI bootstrap/generate/resume에 A4 방향 전달을 연결했다. 서버 capability가 false이면 기존 UI를 유지한다.
- public SVG native PPTX, HTML hybrid PPTX, raster PDF 경로에 문서별 물리 변환을 연결했다. SVG vendor 내부는 불변 context를 ContextVar token/reset 범위에서 사용하며 전역 가변 배율은 사용하지 않는다.
- 공통 contain 계산기는 기하·정역변환·명시적인 물리 글자 정책 검사까지만 구현했다. 실제 source-native 고정 마스터 HTML 생성에 연결하지 않았다. 따라서 고정 마스터/Design Pack은 A4 admission에서 명시적으로 거부한다.
- 별도 Python Scene 계약에 documentCanvas를 포함하되 해당 native exporter는 아직 A4를 지원하지 않는다. 잘못된 96dpi 출력을 막기 위해 명시적으로 거부한다. public SVG/HTML hybrid 지원과 혼동하지 않는다.
- Scene 요소 편집의 재봉인에는 documentCanvas를 보존한다. 레거시 레이아웃 기반 Scene 재생성·rollback 경계에서는 A4를 명시 거부하여 메타데이터 손실을 막는다.
- `DOCUMENT_CANVAS_A4_RELEASE_VERIFIED`는 false다. UI와 신규 A4 writer는 활성화되지 않았으며, 이 상태를 제품 A4 지원 완료로 보고하지 않는다.

확인한 집중 검사:

| 검사 | 실제 결과 / 경계 |
|---|---|
| SVG export fixture | 5 passed; native PPTX 물리 크기/요소와 PDF helper, vendor rebuild. 전체 browser PDF 생성은 아님 |
| TypeScript canvas + hybrid assembler | 23 passed; 공통 digest/물리 크기/native 변환. UI 실행 검사는 아님 |
| Python canvas + worker bundle | 47 passed; 이후 admission 보완은 아래 검사에서 다시 확인 |
| Python canvas + contain + Scene quality | 48 passed, 1 failed; 기존 HEAD에서도 exporter가 추가 registry 필드를 반환하지만 테스트는 두 필드 전체 일치를 요구하는 불일치를 소스 비교로 확인. 신규 A4 차단 케이스는 통과. 기준 HEAD 테스트를 별도로 실행한 것은 아님 |
| 기존 Scene mutation CAS 집중 검사 | 1 passed; 보존 패치 후 기존 mutation 회귀 확인. A4 편집 전체 동작을 입증하는 검사는 아님 |

출시 전 남은 범위: Python Scene의 물리 단위와 readback 동시 지원, 실제 고정 마스터 contain 연결 및 기존 source-preservation fit ladder 준수, 제품 경로의 가로·세로 HTML/SVG→PPTX/PDF readback, capability 활성화. 생성 모델 호출·전체 빌드·E2E·PowerPoint 실제 재열기는 수행하지 않았다.

## 14. 후속 구현 설계 — 남은 6개 작업

이 절은 후속 구현안이다. 13절의 완료 범위나 실제 검증 결과를 확장하지 않는다. 이번 갱신은 문서 변경만 수행하며 런타임 코드·기능 플래그·실행 중 서버는 변경하지 않는다.

후속 작업의 범위와 활성화 순서는 이 절을 우선한다. 앞 절의 모든 consumer 준비 조건은 아래에서 증거로 확정한 출시 대상 경로와 그 필수 의존 consumer에 적용한다. 공통 캔버스 계약 채택과 모든 모드의 A4 구현 완료는 서로 다른 판정이다.

### 14.1 공통 결합 계약과 작업 순서

문서의 canonical documentCanvas를 단일 권위로 유지한다. 모드·회사 표준·출력 형식은 캔버스를 선택하거나 재해석하지 않는다. 새 문서 생성 시 결정한 계약을 generation → capture → scene → version → export까지 전달한다. 저장된 계약 변경은 이 작업의 범위가 아니며 별도 명시적 resize 작업 없이는 금지한다.

작업 순서는 필수 제품 경로 확정 → 공통 변환 경계·최소 fixture 고정 → 지원 경로별 구현 → 제품 경로 검증 → 활성화다. Scene 출력과 고정 마스터 연결을 무조건 동시에 착수하지 않는다. 해당 출시 경로에서 실제로 필요한 작업만 병렬화한다. UI는 지원표 소비만 맡고 기하 변환을 구현하지 않는다.

먼저 실제 진입점에서 생성·저장·편집·재생성·복원·출력까지 직접 호출 관계를 확인한다. UI에 직접 드러나지 않아도 자동 persistence나 export chain에서 호출되면 필수 의존성이다. 파일 존재만으로 필수라고 판단하거나, 로그에 없다는 이유만으로 미사용이라고 판단하지 않는다.

| 경로 분류 | 필요한 근거 | 출시 처리 |
|---|---|---|
| 필수 | 현재 제품 진입점과 직접/간접 호출 연결 | 구현·검증 전 해당 출시 조합 활성화 금지 |
| 선택적 지원 | 독립적인 명시 선택 경로와 의존성 분리 확인 | 검증 후 지원표에 추가; 미지원은 사전 안내 |
| 도달 불가/보존 전용 | 호출 관계 및 기존 문서 소비 목적 확인 | 기존 동작 보존; 신규 A4 구현을 출시 조건으로 강제하지 않음 |
| 미확인 | 호출 경계 근거 부족 | 미사용으로 간주하지 않고 관련 조합 활성화 보류 |

Python Scene이 어느 분류인지는 이 조사 전 확정하지 않는다. 현재 사용 중인 기능을 단순히 차단해 출시 범위를 줄이지 않는다. 기존 기능을 제한해야 하는 부분 출시라면 제한과 영향을 명시하고 사용자 결정을 받는다.

공통 처리는 다음 세 경계로 분리한다. 새로운 병렬 계약을 만들기보다 기존 documentCanvas, contain helper, export context에 책임을 명확히 부여한다.

| 경계 | 입력 → 출력 | 소유 책임 / 금지 사항 |
|---|---|---|
| 문서 크기 | 생성 선택 또는 저장 계약 → logicalSize/physicalSize/safeArea | 문서 단위로 고정; 모드·마스터·export가 재선택하지 않음 |
| 원본 배치 | source-local 내용과 geometry → target 논리 좌표 | target-aware fit와 최종 배치만 수행; 용지 크기를 결정하지 않음 |
| 출력 단위 | target 논리 좌표 → EMU/물리 pt | 문서 context로 변환; contain·reflow를 재실행하지 않음 |

원본 없이 A4에서 직접 생성된 HTML/SVG는 원본 배치 변환이 identity다. 기존 마스터만 source→target 변환을 필요로 한다. 모든 경로는 출력 경계에 도달할 때 이미 target 좌표여야 한다.

| 트랙 | 난이도 / 모델 상한 | 주요 소유 범위 | 결합 산출물 |
|---|---|---|---|
| 통합·계약 | 상 / astra medium | 공통 계약, admission, capability, 설계 | 단위 분류와 지원표, 활성화 판정 |
| Scene 출력 | 상 / astra medium | ooxml/native_scene 및 직접 연결된 변환/readback 모듈 | 물리 변환과 독립 readback |
| 고정 마스터 | 상 / astra medium | source_native_generation, document_canvas_contain, 직접 연결된 render geometry | 최종 HTML과 contain receipt |
| Scene 수명주기 | 중상 / sol medium | scene_compiler, generation/pipeline, scene snapshot 경계 | 캔버스 불변 재생성·복원 |
| UI·독립 검수 | 중 / terra medium, 검수 sol medium | 기존 capability 소비와 집중 검증 결과 | 모드 전환 불변성, 누락 경로 보고 |

최대 동시 슬롯은 리드 포함 5개다. 검수는 구현 슬롯 종료 후 별도 담당자가 수행한다. 공통 scene 모델을 여러 에이전트가 동시에 수정하지 않으며 모델 변경은 통합 담당에게 모은다. 모델 배정은 사용자 지정 상한 내의 작업 계획이며 이번 문서 갱신에서 에이전트를 실행한 것은 아니다.

### 14.2 Python Scene A4 출력

현재 native_scene의 A4 차단은 아래 구현과 검증을 함께 완료한 뒤 제거한다. 9525, 96dpi, Pt 사용을 일괄 치환하지 않는다.

이 트랙의 출시 선행 여부는 14.1의 호출 관계 조사로 결정한다. 필수 경로라면 반드시 완료하고, 독립적인 선택 경로라면 미지원 상태를 명시한 채 다른 검증된 경로와 분리할 수 있다. 계약 전달 자체와 native 출력 지원을 동일한 상태로 표시하지 않는다.

| 입력 종류 | 의미 | 출력 처리 |
|---|---|---|
| Scene bounds, CSS px 길이 | 대상 논리 캔버스의 U | U × emuPerUnit |
| Scene CSS pt 글자·간격 | CSS 1pt = 4/3 U | CSS pt × 4/3 × emuPerUnit / 12700 |
| 이미 물리 pt/EMU인 원본 필드 | 물리 단위 | 해당 단위 그대로 사용; 중복 배율 금지 |
| 마스터 원본 좌표 | source-local 좌표 | 명시적 source→target 변환 후 물리 변환 |
| 비율·각도·색·opacity | 무차원 또는 비길이 | 변환하지 않음 |

1. 직접 호출 범위의 길이 필드를 위 표로 분류한다. 출처가 불명확한 필드는 추정하지 않고 계약을 보완하거나 명시적으로 거부한다.
2. 기존 공통 canvas resolver로 불변 출력 context를 만든다. 신규 A4는 canonical physicalSize가 권위이며 master.slideSizePx로 덮어쓰지 않는다. 레거시는 현재 크기와 변환을 그대로 유지한다.
3. geometry, run/font, 줄간격, 표 셀 여백·선, chart, 이미지, raster island, master furniture까지 길이 직렬화 경계에 context를 전달한다. 원본 Scene 값을 물리 단위로 덮어쓰지 않는다.
4. 내장 identity/custom metadata에 계약 버전과 canonical canvas/digest를 함께 기록한다. standalone readback은 외부 요청이나 실행 당시 전역 상태 없이 이 정보를 읽는다.
5. readback은 실제 p:sldSz와 실제 OOXML 요소를 검사한다. metadata에 적힌 값을 그대로 성공 receipt로 복사하지 않는다. CSS 기반 style digest 비교는 역변환과 저장 격자 오차를 고려한 단위별 비교를 사용하고, 기존 semantic digest를 몰래 재정의하지 않는다.
6. 레거시 파일의 metadata 생략과 digest를 보존한다. 선언된 새 계약이 손상되거나 페이지 크기와 다르면 레거시 기본값으로 우회하지 않는다.

신규 변환·readback 코드는 작은 보조 모듈로 분리하여 큰 native_scene 파일의 추가 비대화를 피한다. 기존 identity 스키마에 추가 필드가 허용되는지 먼저 확인하고, 필요하면 구 reader를 보존하는 명시적 새 버전을 도입한다.

완료 조건: 양 방향 A4 페이지, native 글자·선·표·이미지 및 raster island 정합성, standalone readback, legacy→A4→legacy 비누출. 단순 페이지 크기 일치나 전면 raster 대체만으로 완료하지 않는다.

### 14.3 실제 고정 마스터 contain 연결

연결 대상은 활성 source_native_generation 경로다. 호출되지 않는 legacy_bridge나 provenance만 저장하는 master-pinned Scene persistence에 adapter를 추가하지 않는다.

처리 순서: source geometry와 target safeArea 확인 → target-aware fit ladder에서 후보별 최종 크기 예측 → 정책을 만족하는 배치와 단일 최종 변환 확정 → 대상 크기의 HTML 확정 → 같은 HTML 캡처·검증·저장. fit가 끝난 원본을 뒤늦게 A4에 축소하는 독립적인 후처리 단계는 두지 않는다. 후보 계산은 여러 번 가능하지만 확정된 source→target 변환은 한 번만 적용한다. 최종 검증 후 HTML을 바꾸면 기존 캡처/receipt를 폐기하고 다시 측정해야 한다.

- sourceRect는 측정되거나 검증된 frame metadata로 구한다. 기본 1280×720을 무조건 대입하지 않는다. 현재 경로의 고정 viewport, capacity, 이미지 경계 판정을 source-local과 target-transformed로 분리한다.
- source-local 정책에서 선택한 layout scale과 마지막 contain scale의 합성을 명시한다. 기존 선택 배율을 다시 적용하지 않는다. target 측정값에는 source 배율을 재적용하지 않는다.
- contain receipt에는 sourceRect, target canvasDigest, scale, offset, transform version, 입력 geometry digest, 측정 좌표계, 최종 HTML digest, font policy ID를 기록한다. renderer와 exporter는 target 좌표를 소비하며 contain을 재실행하지 않는다.
- 실제 변환은 균일 비례 배치이며 임의 crop/stretch를 금지한다. 배경 full bleed는 배경으로 식별된 요소에만 허용하며 일반 콘텐츠를 배경으로 추정하지 않는다.
- 원문·표·이미지·페이지 대응을 유지한다. dense source restyle의 7 CSS px 정책은 대상 캔버스에서 측정한 유효 CSS 크기에 적용한다. 일반 authored의 물리 글자 정책과 혼합하거나 7pt로 재해석하지 않는다.
- 공통 helper에 물리 floor를 넘길 때 CSS 정책은 target pointPerUnit으로 변환한다. source-local 크기에 적용되는 contain 배율까지 반영해 최종 유효 크기를 검사한다.
- 후보의 최종 유효 글자 크기가 정책 미만이면 HTML 확정 전에 다음 fit ladder 후보를 검토한다. chrome 압축·원본 글자 크기·원본 geometry fallback도 동일한 A4 목표 영역에서 평가하며, 캔버스 변경이나 페이지 임의 분할을 뜻하지 않는다.

안전한 fallback 범위를 먼저 검증한다. 임의 입력을 유한한 A4 한 장에 최소 글자 크기까지 지키며 항상 넣을 수 있다는 보장은 하지 않는다. 기존 'geometry 때문에 실패하지 않는다' 정책과 최소 크기·원문 보존이 충돌하는 실제 fixture가 발견되면 임의 축소/삭제/페이지 분할로 정책을 바꾸지 않고 해당 조합의 활성화를 보류해 결정이 필요한 제약을 보고한다. 측정 불가능한 마스터는 명시적 admission 오류를 유지한다.

정책 충돌은 단순 미지원 오류와 구분해 기록한다. fixture, 보존할 원문/페이지 수, 최종 최소 글자 크기, 초과 영역, 시도한 fit 단계와 실패 근거를 남긴다. 한 페이지 유지·원문 보존·최소 글자 크기 중 어느 제약도 개발자가 임의로 완화하지 않는다. 이 경우 활성화 보류는 안전 조치일 뿐 해결 완료가 아니며, 정책 우선순위 결정이 필요한 상태로 보고한다. 기존 레거시 경로의 동작을 바꾸어 우회하지 않는다.

완료 조건: 선택 template/source 보존 receipt, 최종 DOM 경계 및 글자 정책 검사, 캡처·저장 HTML digest 일치, export의 중복 contain 없음. helper 단위 검사 통과만으로 연결 완료로 보지 않는다.

### 14.4 Scene 재생성·편집·롤백

이 경로가 출시 대상에서 호출될 때 아래 항목을 완료한다. 호출되지 않는 Scene 작업을 다른 출력 모드의 일괄 출시 조건으로 삼지 않되, 기존 제품 기능의 퇴행을 숨기기 위해 지원표에서 제외하지 않는다.

- 요소 편집은 현재 Scene의 documentCanvas를 그대로 재봉인하고 canvas 변경을 허용하지 않는다.
- 재생성 prepare는 현재 revision/scene digest/canvasDigest를 고정한다. publish에서는 세 값을 다시 비교하여 stale candidate가 다른 캔버스나 수정된 문서에 적용되지 않도록 한다.
- layout/author/render 호출에 canonical canvas를 전달하고 현재 레거시 viewport 전제를 제거한 뒤에만 A4 재생성 차단을 해제한다.
- rollback은 현재 문서와 대상 snapshot의 canvas 계약 동등성을 확인한다. 같은 계약의 콘텐츠 복원은 허용하고, 불일치 또는 A4 문서에 canvas 없는 구 snapshot 복원은 명시적으로 거부한다. 과거 snapshot에 새 A4 metadata를 덧씌워 복원하지 않는다.
- snapshot, theme, scene, revision 상태는 기존 트랜잭션/CAS 경계에서 함께 갱신한다. 실패 시 일부만 저장하지 않는다.
- 캔버스 또는 변환 버전이 다른 checkpoint/export cache는 재사용하지 않는다. 기존 digest가 이미 포함하는 identity와 중복된 별도 ID 체계를 만들지 않는다.

완료 조건: 동일 A4 편집·재생성·롤백, stale publish 거부, 다른 계약 복원 거부, 레거시 snapshot 직렬화 유지. 기존 CAS 검사 하나의 통과만으로 A4 수명주기 전체를 입증하지 않는다.

### 14.5 기존 Scene capability 검사 정리

실패한 test_authored_vnext_native_scene_quality의 capability 전체 비교는 별도 작은 변경으로 처리한다. supported/unsupported 값만 남기도록 응답을 축소하거나 registry 검증을 삭제하지 않는다. authoritative registry의 schema version, consumer, digest 및 readback registry를 각각 검증하도록 기존 기대값을 정리한다. 제품 helper의 출력 전체를 그대로 기대값으로 사용해 자기검증하지 않는다. 이 수정의 통과와 A4 출력 통과는 별도 증거로 기록한다.

### 14.6 통합 검증과 활성화

검증은 구현 후 변경 경계를 직접 덮는 최소 검사로 한 번 수행한다. 단계별 검사를 무조건 모두 실행하거나 하나의 명령에 전체 suite를 숨겨 넣지 않는다. 기존 집중 테스트/worker fixture를 우선 확장하며, 전체 빌드·E2E·실제 PowerPoint 조작은 자동 추가하지 않는다. 이번 문서 수정에서는 검증 명령을 실행하지 않는다.

구현 착수 전에 출시 대상의 각 실제 출력 경로를 기존 fixture ID/파일, 진입점·worker, 방향, 형식, 독립 기대값, 확인할 OOXML/PDF 항목, 합격 오차와 연결한다. 아래 표는 검사 후보 목록이지 전체 자동 실행 지시가 아니다. 같은 worker를 공유하면 fixture를 재사용하되 서로 다른 exporter의 결과를 대체 증거로 삼지 않는다. 미정인 fixture나 검증되지 않은 항목은 공란을 성공으로 해석하지 말고 미확인으로 표시한다.

| 증거 항목 | 최소 확인 범위 | 완료 판정에서 제외할 추정 |
|---|---|---|
| 제품 export 연결 | 실제 isolated worker의 HTML/SVG × 양 방향 × PPTX/PDF 산출물 | helper 결과만으로 전체 worker 성공 추정 |
| 별도 Scene 출력 | 양 방향 native Scene PPTX 및 독립 readback | public hybrid 성공으로 Scene 대체 |
| 고정 마스터 | 측정 가능한 고정 fixture의 source→target→export | contain 계산만으로 실제 생성 성공 추정 |
| 수명주기 | 요청/저장/resume/편집/복원 canvasDigest | 직렬화 단위 검사로 전체 UI 실행 추정 |
| 레거시 | 기존 세 비율 및 대표 custom/import 치수 | 새 A4 검사로 구 문서 불변 추정 |
| 실패 경계 | 손상 계약, 구 reader/worker, 미측정 master, stale revision | 오류를 default landscape로 우회 |

기대 물리 수치와 허용 오차는 11절을 그대로 사용한다. 제품 경로 검사는 저장된 최소 fixture를 사용하여 LLM 비용 없이 worker 경계를 확인할 수 있다. 실제 모델 생성 품질이나 UI 브라우저 조작을 실행하지 않았다면 이를 별도 미검증 항목으로 남긴다. 기존 harness로 덮지 못하는 경계는 통과로 간주하지 않으며 검증 범위 확장이 필요한 이유를 먼저 명시한다.

활성화 순서:

1. 서버의 단일 지원표에 경로 ID, 계약 버전, 원본/모드 조건, 작업·출력 형식, 필수 consumer, 구현·검증 상태와 미지원 이유를 기록한다. 경로 분류 근거와 fixture 증거를 연결한다. admission과 UI capability는 같은 판정에서 산출하고 별도의 허용 목록을 유지하지 않는다.
2. 배포될 API/worker/bundle이 같은 계약과 변환 버전을 지원하는지 확인한다. 정적인 release boolean 하나만으로 오래된 worker가 준비됐다고 가정하지 않는다. 기존 bundle identity/capability 경계를 재사용하고 혼합 버전은 해당 요청을 거부한다.
3. 검증된 지원 조합만 서버 admission에 열고 capability 응답에도 같은 범위를 노출한다. UI는 이 결과로 가로/세로를 표시하되 모드 변경 시 방향을 바꾸지 않는다. 미지원 조합은 사전에 이유를 표시하며 서버도 독립적으로 거부한다.
4. release flag는 지원 여부를 정의하는 근거가 아니라 신규 A4 생성의 운영상 차단 스위치로만 사용한다. 신규 생성 허용은 스위치 열림, 지원표의 검증된 조합, 해당 요청 consumer의 준비 상태를 모두 만족해야 한다. 스위치를 열어도 미지원 조합은 열리지 않는다. 로컬 서버 반영이 필요하면 기존 managed stack으로 영향받는 구성요소만 재시작한다. 외부 게시·배포는 별도 승인 없이 수행하지 않는다.
5. 문제 발생 시 신규 A4 writer만 닫는다. 이미 저장된 A4 reader와 검증된 export는 유지한다. 저장된 문서를 레거시 크기로 되돌리거나 삭제하지 않는다.

전체 완료는 지원표의 약속한 조합과 제품 readback 증거가 일치할 때만 선언한다. 특정 경로가 남아 있으면 부분 지원으로 보고하며 플래그 변경 자체를 완료 조건으로 삼지 않는다.

완료 보고는 공통 계약 적용, 출시 대상 경로 지원, 모든 모드 지원을 구분한다. 공통 계약 적용은 새 모드에 크기 상수를 복제하지 않아도 같은 문서 크기를 소비할 수 있다는 뜻이다. 출시 대상 지원은 확정된 경로의 검증 완료를 뜻한다. 모든 모드 지원은 실제 모든 대상 경로가 완료됐을 때만 사용한다. 현재 13절의 미완료 항목이 이 설계 수정으로 완료된 것은 아니다.

## 15. 후속 구현의 호출 경로 확인

sol/medium 에이전트 두 개로 지원표와 fixed-master 경계를 분담하고 통합 측에서 Composer publication 및 UI 연결을 보완했다. 13절의 과거 검사 결과와 이번 후속 변경의 검증은 별개다.

| 경로 | 코드에서 확인한 연결 | 출시 의존성 판정 |
|---|---|---|
| 원본 재구성 Composer | presentation_generate의 handler → prepare_source_content_authored_reflow → _composer_restyle일 때 run_composer_restyle → composer/publish | 현재 원본 재구성의 필수 경로 |
| 과거 원본 재구성 Authored | 봉인된 authored route에서는 Composer를 선택하지 않고 authored reflow로 계속 진행 | 기존 durable route 보존; 새 작업의 route와 구분 |
| source_content semantic native publication | generate_source_content_restyle_presentation 내부에서 native publication 호출은 있으나 services/api 범위의 제품 진입 caller는 확인되지 않음 | 현재 public restyle의 필수 의존성으로 간주하지 않음 |
| 별도 authored-vNext Scene export | feature-gated 별도 endpoint → integration worker → native Scene materializer | 선택적 경로; public HTML/SVG 출력의 필수 조건과 분리 |
| fixed-master source-native | generation operation handler → run_source_native_generation → executor | 독립된 실제 경로; contain 계산기만으로 지원 완료 아님 |

특히 source_content/publication에 native Scene 호출이 있다는 사실만으로 현재 원본 재구성 전체가 이 exporter에 의존한다고 판단하지 않는다. Composer early return과 역사적 Authored fallback을 먼저 확인한다. Python Scene A4 구현은 없어지는 작업이 아니라 해당 선택적 경로를 지원할 때 필요한 작업이다.

Composer 저장 단계에서 A4를 legacy authored-canvas metadata로 다시 만들던 연결 누락을 보완했다. 게시 전에 실제 deck.canvas와 canonical documentCanvas의 논리 geometry를 비교하고 theme과 master/composition identity에 물리 계약을 포함한다. 레거시 직렬화는 유지한다.

서버 지원표는 구현 여부와 제품 readback 검증 여부를 분리한다. capability endpoint의 선택 조합 판정과 실제 요청 admission은 같은 resolver를 사용한다. UI는 서버 selection 결과를 읽고 생성 직전에 다시 확인하며, 미지원 조합으로 변경해도 선택한 문서 방향을 임의로 바꾸지 않는다. 전역 release switch는 닫힌 상태를 유지한다.

초기 capability 조회가 끝나기 전에 생성 버튼을 누르는 경우도 시작 단계에서 다시 확인한다. 서버는 A4가 준비됐지만 현재 UI snapshot이 레거시라면 해당 클릭의 발행을 중단하고 A4 설정을 반영한 뒤 재확인을 안내한다. capability 조회 실패를 레거시 허용으로 간주하지 않는다. 원본 재구성의 회사 표준 selector는 기존 실제 요청이 masterPin을 생략하는 조건과 일치시킨다.

고정 마스터의 후속 구현은 px 단위의 source geometry에서 단일 contain 행렬을 만들고 capacity 후보의 최종 유효 글자 크기를 평가한다. 최종 HTML/canvas/geometry digest와 변환 receipt를 기록하며 목표 viewport에서 같은 HTML을 캡처·검사·저장하도록 연결했다. 기존 renderer scale은 source CSS에, contain scale은 마지막 matrix에 각각 한 번 적용한다. source CSS 7px 정책과 기존 물리 글자 정책은 구분한다. viewport 의존 CSS, 재변환, 필수 font 측정 누락은 명시적으로 거부한다. 이 구현 연결은 실제 fixed-master 제품 지원 검증 완료를 뜻하지 않으며 admission은 계속 닫혀 있다.

이번 후속 변경의 실제 검사:

- `test_document_canvas.py`, `test_composer_publish_cas.py`, `test_document_canvas_contain.py`, `test_source_verbatim_capacity_admission.py`, `test_source_native_product_route.py`를 한 집중 명령으로 실행: 70 passed, 2 failed.
- 새 target font 검사는 fixture의 22 CSS px × 0.5625 = 12.375px을 9px로 잘못 기대했다. 기대값과 거부 기준을 바로잡은 뒤 그 케이스만 재실행: 1 passed.
- 남은 실패는 `test_source_native_table_cells_share_data_driven_estimator_columns`: 기존 test는 열 수가 4보다 크기를 요구하지만 estimator 결과는 4다. 해당 테스트, 직접 geometry 파일, 호출 함수는 HEAD 대비 변경이 없고 이번 A4 분기에 진입하지 않는 것을 소스 비교로 확인했다. 기준 HEAD에서 별도 재실행한 것은 아니며, 관계없는 표 배치/기대값은 수정하지 않았다.

미확인 범위: 실제 Chromium capture부터 publication 및 PPTX/PDF readback까지의 fixed-master 제품 경로, UI 브라우저 실행, 기존 대형 모듈의 파일 크기 ratchet. 이번 집중 검사를 이 항목의 통과 증거로 대체하지 않는다. 서버 재시작·외부 배포·A4 활성화는 수행하지 않았다.

## 16. 최종 실행 설계 — 사용 가능한 상태까지 단일 작업으로 완료

### 16.1 목표와 우선순위

사용자 인수 기준은 실행 중인 로컬 UI에서 A4 문서를 생성하고, 저장·재열기·기존에 제공하던 편집/재생성/복원을 수행한 뒤, 정확한 크기의 PPTX/PDF를 다운로드하여 사용할 수 있는 상태다. 구현만 끝났거나 활성화 플래그가 꺼진 상태는 최종 완료가 아니다.

이 절은 10~15절의 작업 종료·출시 범위·검증 순서와 충돌할 때 우선한다. 13·15절의 과거 검사 결과는 과거 증거로 보존하며 새 구현의 통과 결과로 재사용하지 않는다. 이번 요청은 설계 갱신이므로 코드 수정·검사 실행·서버 재시작·기능 활성화는 지금 수행하지 않는다.

후속 구현 요청에서는 아래 전체를 하나의 작업으로 수행한다. 단계별 진행 보고는 계속하되, 일반적인 코드 수정·테스트 실패·로컬 환경 문제를 이유로 작업을 종료하고 사용자에게 다시 개발 지시를 요구하지 않는다. 구현, 직접 관련 결함 수정, 최소 재검증, 로컬 반영, 최종 인수 증거까지 같은 작업에 포함한다.

### 16.2 필수 사용자 기능과 제외 기준

- 필수 범위: HTML Authored, SVG, Composer 원본 재구성, 현재 제품에서 선택 가능한 회사 표준/고정 마스터의 A4 가로·세로 생성 및 PPTX/PDF 출력. 문서 크기는 모드별 상수가 아니라 documentCanvas에서 결정한다.
- 각 모드의 기존 편집 가능성은 유지한다. PDF에 편집성을 요구하거나 모든 모드를 같은 내부 표현으로 바꾸는 것이 아니라, 기존에 지원하던 사용자 작업이 A4 때문에 퇴행하지 않도록 한다.
- Python Scene 출력·재생성·롤백은 현재 사용자 기능이나 필수 경로의 의존성이면 이번 작업에 포함한다. 실제 호출 관계와 현재 노출 설정으로 독립적인 미사용 경로임을 확인한 경우에만 제외한다. 모듈 이름이나 기본 feature flag 값만으로 제외하지 않는다.
- 선택 가능한 회사 표준을 일괄 비활성화하거나 기존에 처리하던 정상 입력을 모두 거부한 상태를 완성으로 계산하지 않는다. px 외 단위나 viewport CSS를 현재 helper가 거부한다는 사실은 구현 제약이며, 원래 유효한 제품 입력이라면 단위 정규화·원본 viewport 보존 등 필요한 보완을 이번 작업에서 수행한다.
- 손상된 계약, 측정 불가능한 입력, 기존부터 미지원이었던 조합에는 명시적 오류를 유지한다. 지원 범위를 줄여 완료 판정을 쉽게 만드는 변경은 하지 않는다.
- Composer 원본 재구성은 원본 한 페이지당 한 슬라이드와 기존 fit ladder를 유지한다. 템플릿 장식 축소 → 원본 글자 크기 적용 → 원본 기하 fallback까지 수행하며, 선택한 A4 방향을 바꾸거나 단순 기하 부족을 정상 입력 거부 사유로 삼지 않는다. 측정 불가·원문 누락 또는 실제 정책 충돌은 16.7에서 별도로 다룬다.

### 16.3 단일 작업의 실행 단계와 산출물

아래는 구현 준비 상태가 아니라 확정된 요구사항 표다. 모든 행에 가로·세로 생성, 저장·재열기, PPTX/PDF 출력을 요구한다. 실행 증거가 없는 현재 상태를 지원 완료로 표시하지 않는다. 인수 ID는 이 문서의 추적 식별자이며 기존 API나 새 기능 플래그 이름이 아니다.

| 인수 ID·모드 | 결합 경로와 확인할 진입점 | 필수 출력·수명주기 | 주 담당 | 대표 fixture |
|---|---|---|---|---|
| A4-H · HTML Authored | 생성 요청 → Authored HTML → isolated hybrid/raster export | 가로·세로 생성·저장·재열기·PPTX/PDF, 기존 편집·재생성·복원 보존 | 계약·수명주기 / 출력 | H: 텍스트·표·도형·이미지·우하단 기준점 |
| A4-S · SVG | SVG 생성·저장 → public_svg_export_worker → SVG 변환기/PDF | 동일 공통 요구, SVG를 HTML로 암묵 변환하지 않음 | 출력 / 계약·수명주기 | S: native 텍스트·stroke·중첩 transform |
| A4-C · Composer | presentation_generate → prepare_source_content_authored_reflow → run_composer_restyle → publish | 동일 공통 요구, 원본 페이지 대응·원문·표·이미지·최종 HTML 보존 | 마스터·생성 / 출력 | C: 조밀한 복수 원본 페이지 |
| A4-M · 회사 표준 | UI 선택 → 실제 source-content/고정 마스터 분기 → 배치 → 출력 | 동일 공통 요구, 선택 시 방향 불변, 신규 문서는 회사 표준 미선택 | 마스터·생성 / UI | M: 현재 선택 가능한 실제 회사 표준 |
| A4-F · 고정 마스터 | source-native operation → run_source_native_generation → 실제 executor → 출력 | 동일 공통 요구, 원본 viewport·비례·기존 편집 가능성 보존 | 마스터·생성 / 계약·수명주기 | F: 고정 좌표 및 viewport 의존 원본 |

M/F가 같은 경로면 실제 호출 근거와 입력 차이를 기록하고 공통 검사를 재사용한다. 별도 경로라면 각각 인수한다. Scene은 16.2의 필수 의존성 판정에 따라 해당 행에 연결하며 모듈 이름만으로 새 사용자 모드를 만들지 않는다.

첫 단계에서 각 행에 실제 route ID, 진입 파일·함수, 후속 작업 endpoint/consumer, 기존 제공 기능, fixture 경로·해시를 결합한다. 제공되지 않던 편집기를 새로 만드는 것은 범위 밖이지만, 기존 편집·재생성·복원 제외에는 현재 UI와 호출 근거가 필요하다. A4 생성·저장·출력 자체는 과거 미제공을 이유로 제외할 수 없다. 이 연결 작업은 같은 개발 작업의 선행 단계이며 별도 개발 재지시를 요구하지 않는다.

실행 상태는 요구사항과 별도 기록한다: `미검증`, `실패`, `통과`, `기존 미제공(후속 기능만, 근거 필수)`. 통과에는 fixture·문서/revision·canvasDigest·실제 출력·검사 증거가 필요하다. 문서 작성만으로 미검증을 통과로 바꾸지 않는다.

공통 수명주기 인수도 아래처럼 고정한다. 기존 제공 기능에 적용하며 공통 consumer를 사용하는 경우 fixture는 재사용하되 각 진입점의 연결 확인을 생략하지 않는다.

| 인수 ID | 작업 | 합격 조건 | 담당 |
|---|---|---|---|
| LIFE-SAVE | 저장·재열기·기존 편집 | root 계약·canvasDigest 유지, 편집 후 HTML/SVG/Scene과 export 크기 일치 | 계약·수명주기 |
| LIFE-CLONE | 복제 | 새 문서 ID, 원본과 같은 캔버스, 이후 독립 편집, 원본 불변 | 계약·수명주기 |
| LIFE-SHARE | 기존 공유·공유 보기 | 기존 권한/노출 범위 유지, shared reader가 동일 캔버스 사용 | 계약·수명주기 / UI |
| LIFE-THUMB | 썸네일·미리보기·캐시 | 비율 유지, cache key에 canvas/revision 구분, 모드 전환이나 재생성 후 오래된 이미지 미재사용 | UI / 계약·수명주기 |
| LIFE-RESUME | 허용된 작업 재개 | 저장된 operation snapshot·revision·canvas 사용, 새 UI 기본값으로 덮어쓰지 않음, 재개 금지 terminal 상태 유지 | 계약·수명주기 |
| LIFE-REVISION | 기존 재생성·버전 복원 | 선택 revision의 캔버스와 콘텐츠를 원자적으로 복원, CAS 유지, 실패 시 부분 저장 없음 | 계약·수명주기 |

공유 확인은 승인된 로컬 테스트 계정·기존 공유 보기 범위로 제한한다. 새 공개 링크 게시나 외부 전송을 인수 절차에 암묵 포함하지 않는다.

확정한 필수 칸을 구현이 어렵다는 이유로 제외하거나 미지원으로 재분류하지 않는다. 범위 축소가 필요하면 기능 영향과 근거를 제시하고 사용자 결정을 받는다. 이후 발견한 필수 의존성은 같은 작업에 포함하며, 단순한 모듈 존재나 기본 feature flag 값만으로 의존성을 추가·제외하지 않는다.

| 단계 | 수행 내용 | 종료 조건과 증거 |
|---|---|---|
| 1. 필수 경로 확정 | 실제 UI 선택과 생성·후속 작업·출력 호출을 연결 | 경로별 진입점, 필수 consumer, 기존 사용자 기능, 대표 fixture 확정 |
| 2. 계약 누락 제거 | 요청·durable·캡처·저장·재개·Scene·theme·revision에 공통 계약 연결 | 동일 문서의 canvasDigest 보존, 손상/충돌 계약 거부, 레거시 직렬화 유지 |
| 3. 마스터 배치 완성 | target-aware fit, 단일 contain, 원본 viewport/단위 정규화, 글자 정책 적용 | 원문·표·이미지·페이지 대응 보존, 왜곡/중복 축소 없음, 최종 HTML과 캡처 identity 일치 |
| 4. 후속 사용자 작업 완성 | LIFE-SAVE~LIFE-REVISION의 기존 제공 경로를 A4에 연결 | 복제·공유·썸네일·재개를 포함한 크기/identity 보존, revision/CAS 유지, 실패 시 부분 저장 없음 |
| 5. 출력 완성 | 실제 worker의 PPTX/PDF와 필요한 Scene 경로 구현 | 11절 독립 수치로 물리 크기 및 native/raster 요소 readback 통과 |
| 6. UI 완성 | 가로/세로, 모드 전환, capability 조회와 생성 시점 경쟁 조건 처리 | 방향 유지, 준비 전 legacy 요청 오발행 없음, 실패 이유와 복구 방법 표시 |
| 7. 직접 관련 결함 정리 | 집중 검사 실패 원인 해결, 모듈 분리 및 파일 크기 제한 준수 | 이번 변경의 실패 없음, 관련 기존 테스트 불일치는 근거에 따라 수정 또는 독립 문제로 입증 |
| 8. 실제 사용 인수 | UI 생성→저장→재열기→기존 후속 작업→다운로드 검증 | 실제 입력/옵션, 문서 ID·revision, UI 결과, 최종 파일과 검사 결과 연결 |
| 9. 로컬 활성화 | API/worker/bundle 버전 정합, 검증된 지원표 반영, 필요한 서비스 반영 | 실행 중인 로컬 UI에서 A4 선택과 실제 생성·다운로드 가능 |
| 10. 최종 전달 | 산출물과 검증 근거, 필요한 정상 사용 안내 제공 | 사용 URL, 대표 가로/세로 PPTX/PDF 경로, 검증 요약, 알려진 제한 전달 |

단계 7은 모든 과거 저장소 결함을 수정하는 작업이 아니다. 다만 이번 기능의 인수 검사에 포함된 실패를 설명 없이 방치하지 않는다. 테스트 기대값이 오래됐으면 현재 권위 계약과 독립 근거로 바로잡고, 제품 결함이면 수정한다. 통과시키기 위한 assertion 삭제·허용 오차 확대·품질 기준 완화는 금지한다.

#### 마스터 adapter의 구현 선택

adapter는 기존 배치·측정·export 경계를 확장한다. 아래는 구현할 처리 계약이며 현재 helper가 모두 지원한다는 뜻이 아니다.

| 입력 유형 | 처리 방식 | 필수 확인 |
|---|---|---|
| 측정 가능한 고정 좌표 | source 크기와 content bounds 확정 → target-aware fit → source-to-target contain 1회 | 비균일 stretch/crop 없음, 배경만 full bleed, 요소·글자에 동일 변환 |
| pt/mm 등 절대 단위와 SVG viewBox | 원본 renderer의 단위 의미를 source 논리 좌표로 정규화한 후 공통 배치 적용 | source CSS 단위 변환과 target 물리 변환 분리, viewBox offset·중첩 transform 보존 |
| vw/vh, media/container query, 상대 단위 | 원래 viewport·container·글꼴·자산 조건에서 레이아웃을 먼저 resolve하고, 필요한 computed geometry/style을 캡처 가능한 고정 표현으로 확정 | target viewport 변경으로 다른 breakpoint가 재적용되지 않음, 줄바꿈·상속·pseudo content·clip·transform 보존 |
| source geometry/자산 측정 불가 또는 손상 | 측정 실패 원인과 복구 가능 여부 분류, 정상 입력의 adapter 결함이면 같은 작업에서 수정 | 크기를 추측하거나 crop/raster 전환으로 숨기지 않음, 원래 미지원/손상일 때만 명시적 입력 오류 |

viewport 의존 입력을 단순히 target body 크기로 바꾸고 축소하지 않는다. 우선 기존 측정 파이프라인으로 원본 레이아웃을 resolve하여 고정 표현으로 보존한다. 기존 편집 의미·DOM 소유권·native export가 손상되는 요소는 해당 adapter를 보완한다. iframe 도입이나 전체 raster화는 기본 해법이 아니며 별도 표현을 추가하려면 renderer·측정·export 모두의 소비 계약을 입증해야 한다.

폰트와 이미지가 준비된 후 source 측정 → fit ladder → 최종 좌표/스타일 확정 → 최종 HTML 측정·캡처·저장 순서를 지킨다. 확정 후 좌표나 CSS를 다시 수정하지 않는다. 기하만으로 실패한 정상 Composer 입력에는 기존 ladder를 끝까지 적용하고, 원본 기하 fallback도 선택한 target A4 안에 배치한다.

#### 글자 정책의 모드·역할별 계약

정책은 출력 형식이 아니라 생성 경로와 콘텐츠 역할이 선택한다. SVG라는 이유로 별도 하한을 만들거나 모든 텍스트를 7px/9.5pt로 일괄 보정하지 않는다. 아래 기존 ID는 코드에 있는 값이며 ID가 없는 품질 규칙은 출처 파일·상수와 버전으로 식별한다.

| 대상·역할 | 정책 식별/권위 | 최소값·단위 | 최종 측정 |
|---|---|---|---|
| Composer 원본 재구성의 축소 대상 텍스트 | composer-source-preservation-v2/dense-7-css-px | target에서 유효 7 CSS px | contain 이후 유효 크기. A4에서는 7×9000/12700 ≈ 4.96063 physical pt이며 7pt/9.5pt와 다름 |
| 일반 source-native 본문 | source-native/min-body-12-physical-pt | 물리 12pt | target U=12/pointPerUnit로 배치하고 실제 export pt로 역검증 |
| 일반 Authored의 capability 최소값 | authored_vnext/capability_registry.py의 AUTHORED_FONT_FLOOR_PT/PX, document_canvas_generation.py | 현재 코드 상수는 9pt / 레거시 12px | A4에서는 12px를 그대로 적용하지 않고 해당 물리 하한과 명시적 사용자 지시를 보존 |
| 기존 9.5pt 품질 게이트 적용 경로 | 해당 품질 검사/호출부의 실제 규칙 | 기존 물리 9.5pt 유지 | capability의 9pt와 동일 규칙으로 취급하지 않음. 해당 경로·역할을 착수 시 연결하고 실제 gate로 확인 |
| 제목·캡션·각주·표 셀·마스터 고정 텍스트 | 해당 모드의 기존 role/style/source 정책 | 기존 역할별 값과 명시적 지시, 출처 필수 | 전역 본문 규칙을 임의 전파하지 않음. 별도 role 규칙이 없으면 해당 생성 경로의 기존 fallback 사용 |
| exact reconstruction | 기존 원본 충실도 예외 정책 | 원본 크기와 기존 예외 유지 | 일반 Authored 하한을 새로 강제하지 않음, 기존 입력 지원·원문 보존 확인 |

이번 좁은 코드 확인에서 capability는 9pt로 확인되었다. 기존 9.5pt 품질 요구를 이 값으로 낮추거나 모든 코드가 이미 9.5pt라고 기록하지 않는다. 첫 단계의 정책 목록에 각 실제 호출부·적용 역할·예외·측정 단위를 연결하고, 같은 텍스트에 상충하는 규칙이 적용되면 권위 계약과 호출 근거로 해소한다. 해결되지 않은 정책 경로는 검증 통과로 표시하지 않는다.

DOM computed font-size만으로 통과시키지 않는다. 누적 transform의 포함 여부를 명시해 최종 target 크기를 한 번 계산하고, native 글자 직렬화는 11절 허용 오차로 확인한다. 브라우저 zoom·DPR은 글자 정책의 물리 배율에서 제외한다.

### 16.4 멀티에이전트 분업과 통합 책임

작업 모델은 사용자 지정대로 sol 이하로 제한한다. 구현 난도가 높은 공통 계약·마스터 배치·출력은 sol/medium, UI 연결은 terra/medium, 독립 검수는 sol/medium을 기본 배정으로 한다. 실제 동시 슬롯이 부족하면 같은 소유 경계를 유지하며 순차 인계한다. 슬롯 부족 자체로 전체 작업을 종료하지 않는다.

- 통합 담당: 필수 경로·fixture·단위 계약 확정, 변경 소유권 관리, 실제 사용 인수, 활성화와 최종 전달. 하위 작업의 완료 보고만으로 전체 완료를 선언하지 않는다.
- 계약·수명주기 담당: canonical canvas, 지원표, 저장·재개·편집·재생성·복원 및 필요한 Scene 연결.
- 마스터·생성 담당: source geometry와 target fit, 원본 보존, CSS/물리 글자 정책, 최종 HTML 확정.
- 출력 담당: HTML/SVG/필요한 Scene의 PPTX/PDF, worker/bundle identity, 파일 readback.
- UI·검수 담당: UI 연결 후 독립 검수에 인계. 구현자가 자기 결과만 확인하는 것으로 검수를 대체하지 않는다.

한 파일은 한 담당자가 편집하며 공통 계약은 통합 담당을 통해 변경한다. 기존 dirty 변경을 보존하고 reset/stash/광범위 포맷팅·무관한 커밋은 하지 않는다. 기존 대형 파일은 연관 로직을 작은 모듈로 분리하여 파일 크기 제한을 실제 충족시킨다.

### 16.5 검증 범위와 증거 설계

앞 절의 단위 검사만으로 완료하는 접근을 대체해, 이번 기능에 한정된 실제 사용 흐름 검사를 필수 인수 단계로 포함한다. 저장소 전체 E2E·전체 테스트·전체 빌드·전체 lint를 자동 실행한다는 뜻은 아니다. 검사 시점은 구현 완료 후로 모으고, 실패하면 근거 있는 수정 뒤 영향받은 최소 검사만 다시 실행한다.

최소 fixture는 구현 전에 고정한다. 같은 경로의 같은 입력으로 가로·세로를 각각 생성하고, 각 생성 결과에서 PPTX/PDF를 모두 출력한다. 형식이 바뀐다고 문서를 다시 생성하지 않는다.

fixture마다 입력 경계도 고정한다: 원본 용지/좌표 단위, 페이지 대응, 원문·표·이미지 보존 요구, 적용 font policy와 단위, 측정 가능 조건을 기록한다. 정상 지원 fixture, 정상 입력이지만 제약 충돌을 확인할 경계 fixture, 손상·기존 미지원 입력을 구분한다. 실패한 정상 입력을 사후에 미지원 fixture로 바꿔 통과 처리하지 않는다. 임의 원본의 무조건 수용을 약속하는 대신 정상 지원 fixture의 성공과 경계 입력의 정확한 판정을 각각 인수한다.

| fixture 구분 | 포함할 내용 | 확인할 경계 |
|---|---|---|
| HTML Authored | 텍스트·도형·표·이미지와 우하단 기준점 | 생성/재열기와 hybrid native/raster 정합성 |
| SVG | 텍스트·stroke·이미지·중첩 transform | SVG 생성·저장·출력의 공통 물리 배율 |
| Composer | 원문 텍스트·표·이미지를 포함한 복수 원본 페이지, 조밀한 본문 | 페이지 대응, 원문 보존, 7 CSS px 정책, 최종 HTML 불변성 |
| 회사 표준/고정 마스터 | 실제 선택 가능한 마스터, 다른 원본 비율과 복수 배치 후보 | template 우선 적용, 목표 영역 fit, contain 1회, 후속 편집/출력 |
| 레거시 | 기존 16:9·9:16·9:13와 실제 사용 중인 custom/import 대표 문서 | 새 코드로 재열기·출력해도 원래 크기와 동작 유지 |

회사 표준과 고정 마스터가 다른 실제 경로라면 각각 확인한다. 반대로 같은 구현 경로를 공유하는 경우에는 fixture를 재사용해 불필요한 모델 호출을 줄인다. 스타일 전체 조합을 무차별 생성하지 않되 서로 다른 필수 exporter나 수명주기를 검사에서 누락하지 않는다.

UI 인수에는 초기 로딩 중 생성, 모드/스타일 전환 후 방향 유지, 저장 후 재열기, 기존에 제공하는 편집·재생성·복원, 실제 다운로드를 포함한다. LIFE-CLONE·SHARE·THUMB·RESUME도 기존 제공 경로에서 확인하며, 모두 같은 reader를 쓴다는 추정만으로 생략하지 않는다. 브라우저 성공과 파일 readback 성공을 각각 기록한다. 화면과 산출물의 대표 페이지에서 잘림·왜곡·요소 위치를 함께 확인하되 자동 전체 시각 비교 suite로 확대하지 않는다. 테스트 double이나 helper 출력은 실제 생성·worker 경로를 거친 결과의 대체 증거가 아니다.

증거에는 인수 ID, fixture 식별값·해시, 입력/옵션, route와 consumer 버전, canvasDigest, 최종 HTML digest, 문서/revision 식별값, 출력 파일 경로와 실제 치수, 글자 정책 ID/출처·최소 측정값, 검증 결과를 연결한다. 각 필수 조합을 증거에 매핑하고 공통 fixture를 재사용했으면 어느 consumer 확인을 공유했는지 기록한다. 문서 본문·원문 로그·비밀정보는 사용자 보고에 출력하지 않는다. 실제 PowerPoint 실행·재저장 검증을 하지 않았다면 그 호환성까지 확인했다고 주장하지 않는다.

### 16.6 활성화·실행 환경 반영과 복구

후보 환경이 이미 있다는 가정, 테스트 계정만 나누면 worker가 격리된다는 가정, 계정별 A4 플래그가 있다는 가정을 하지 않는다. 현재 DOCUMENT_CANVAS_A4_RELEASE_VERIFIED 같은 전체 writer gate와 실제 route readiness를 확인하고, 최종 검증 전 shared runtime에서 전역 값을 바꿔 임시 개방하지 않는다. 별도 포트만 사용하는 것도 operation queue·저장소 격리를 보장하지 않는다.

기본 실행 방식은 기존 managed stack을 유지하는 것이다. 기존에 격리된 후보 구성이 실제로 있으면 이를 사용하고, 없다면 다른 사용자의 작업에 영향을 주지 않는 검증 시간대에 동일 managed 환경을 후보 단계에서 최종 단계로 전환한다. 후자는 테스트 계정별 격리가 아닌 환경 전체의 통제된 전환이다. 작업 중인 공유 환경을 비우거나 사용자 접근을 제한해야 한다면 먼저 확인받는다. 임의의 새 DB·인증·큐 체계를 구축하는 일은 이 설계의 기본 범위가 아니다.

| 순서 | 구체 절차 | 기록·진행 조건 |
|---|---|---|
| 1. 대상 확정 | 기존 managed 상태와 실행 설정에서 API/Next/proxy 포트, PostgreSQL 연결 대상, operation controller, 저장소, renderer·폰트·bundle 버전 확인 | 비밀값 없이 대상 식별자·버전·현재 writer 상태 기록. 실행 중인 작업을 무단 종료하지 않음 |
| 2. 데이터 범위 고정 | 승인된 로컬 테스트 계정과 H/S/C/M/F fixture로 새 문서만 생성. 레거시는 원본 불변 읽기/출력 또는 승인된 복제 사용 | 테스트 문서·operation ID를 별도 목록으로 관리. 실제 사용자 문서 덮어쓰기·외부 공유 없음 |
| 3. 후보 경계 결정 | 기존 분리 환경이면 DB/큐 claim·저장 경로·프로세스 설정까지 분리 확인. 없으면 안전한 동일 환경 전환 조건 확인 | 포트/계정만 다르고 queue를 공유하면 격리 판정 금지. 다른 작업에 영향이 있으면 승인 전 전환하지 않음 |
| 4. writer 개방 전 검사 | 닫힌 production writer와 별개로 기존 좁은 fixture 진입점을 통해 계약·변환·isolated worker 검사 | 테스트용 직접 진입은 UI 인수 증거가 아님. Core/durable 안에서 Chrome/OOXML 조립을 실행하지 않음 |
| 5. 후보 UI 인수 | 대상 후보 runtime에만 실제 후보 설정 반영, reader/API/worker/bundle identity 정합 후 A4-H~F 및 LIFE 경로 확인 | 임시 candidate와 최종 verified 기록을 구분. 일반 사용자 gate 우회 기능이 이미 있다고 가정하지 않음 |
| 6. 최종 반영 | 필수 증거가 모두 통과한 버전·route 지원표·영속 설정을 실제 사용 환경에 반영 | 후보와 최종이 같아도 상태 구분. 버전·설정·폰트가 달라지면 영향받는 증거를 재확인 |
| 7. 재시작·최종 확인 | 영향받는 managed 구성요소만 안전하게 반영한 후 생성·저장·재열기·편집·다운로드 확인 | 실제 문서/revision과 파일 readback 연결. 일반 시작 경로에서도 설정 유지 |

이 표는 실행할 절차이며 후보 환경 구성이나 검증 완료 기록이 아니다. 구현 첫 단계에서 실제 대상과 설정의 저장·적용 위치를 확정한다. 격리할 방법이 없고 다른 사용자의 작업에 영향을 줄 수 있다면 임의로 공유 서버를 전환하지 않고 필요한 환경 조정만 요청한다.

후보 인수 중 실패하면 신규 A4 writer를 다시 닫고 수정·영향 검사·인수를 같은 작업에서 이어간다. '꺼두었으므로 안전하다'는 이유로 작업 완료를 선언하지 않는다. 모든 필수 인수가 끝난 뒤에만 해당 실제 로컬 환경의 지원표와 A4 생성 스위치를 최종 활성화한다.

서버 반영은 scripts/start-local-stack.ps1의 기존 managed 구성을 사용한다. 현재 launcher의 Status와 RestartComponent(api, worker-generation, worker-export) 경계를 활용하고 존재하지 않는 후보 profile 옵션을 가정하지 않는다. 기존 PostgreSQL 연결·포트·실행 환경을 유지하며 영향받는 API/worker만 재시작하고 Next.js는 가능한 경우 HMR을 사용한다. 코드 상수로 관리되는 gate를 존재하지 않는 환경변수만 설정해서 변경했다고 처리하지 않는다. 진행 중인 다른 작업을 무단 종료하거나 app_data를 삭제하지 않는다. 실제 소비되는 worker/bundle이 검증한 버전인지 확인하고 활성화 후 대표 생성·다운로드 경로를 확인한다. 상태 페이지 응답만으로 사용 가능 판정을 내리지 않는다.

최종 실행 환경에서는 후보 환경의 결과를 재사용하는 데 그치지 않고 신규 생성 → 저장 → 재열기 → 해당 모드의 기존 편집 기능 → PPTX/PDF 다운로드를 수행한다. 확정 지원표의 서로 다른 필수 경로가 이 확인에서 빠지지 않도록 하며, 공통 경로의 중복 검증만 줄인다. 다운로드한 파일의 readback을 그 문서/revision과 연결한다.

필요한 글꼴, renderer 실행 파일, worker/bundle 및 canvas 설정은 일회성 터미널 환경이나 임시 monkey patch에만 의존하지 않아야 한다. 정상 managed 시작 경로에서 재현되는지 확인하고, 영향받는 구성요소를 안전하게 재시작한 뒤에도 reader·writer·export의 버전과 설정이 유지되는지 확인한다. 후보 환경 통과, 최종 환경 활성화, 정상 재시작 후 유지 여부를 구분해 기록한다. 이를 확인하지 못한 상태는 사용 가능 인수 완료가 아니다.

복구는 신규 A4 생성 차단과 이전 검증 버전으로의 안전한 실행 복원을 중심으로 한다. 복구 버전도 이미 저장된 documentCanvas 계약을 읽고 출력할 수 있어야 한다. 후보 실패 시 임시 writer 설정과 적용 버전을 원래 기록에 맞춰 되돌리되 사용자 소스 변경을 git reset 등으로 없애지 않는다. 이미 저장된 A4 문서를 삭제하거나 레거시 크기로 변환하지 않는다. reader와 지원되는 export는 유지한다. 테스트 문서·산출물은 인수 증거로 보존하고 자동 정리하지 않는다. 외부 전송·게시·배포, 자격 증명 변경, 구매, 파괴적 작업은 이 설계로 승인된 것으로 간주하지 않는다.

### 16.7 중단이 필요한 경우와 최종 인수 체크리스트

진행을 사용자에게 되돌리는 경우는 새로운 권한·사용자 선택·외부 환경 변경 없이는 안전하게 계속할 수 없는 경우로 제한한다. 일반적인 코드 오류, 테스트 불일치, 에이전트 종료, 수정 가능한 로컬 설정 문제는 조사·복구 후 계속한다.

한 작업으로 끝낸다는 것은 근거 없는 무제한 수정·재실행을 뜻하지 않는다. 실패 시 기대 결과와 실제 결과를 기록하고 구현 결함, 오래된 테스트 기대값, 실행 환경 문제, 입력/정책 충돌로 원인을 분류한다. 결과가 달라질 근거 있는 수정 후 해당 최소 검사만 재실행한다. 같은 실패가 반복되면 실패 범위를 넓히거나 동일 명령을 계속 돌리지 말고 원인 분류를 재검토한다. 안전한 범위 내 해결은 계속 수행하고, 새로운 제품 정책이나 관계없는 시스템 재설계가 필요하면 그 경계에서만 사용자에게 확인한다. assertion 삭제·허용 오차 확대·품질 기준 완화로 통과시키지 않는다.

Composer에서 단순 fit 실패는 기존의 기하 fallback까지 수행할 구현 책임이며 새 입력 거부 정책으로 바꾸지 않는다. 기존 ladder를 모두 적용하고도 원문 전부 보존·원본 페이지 대응·최소 글자 크기·선택한 A4 한 장의 제약 사이에 실제 정책 모순이 측정된 경우에만, 시도한 단계와 결과를 근거로 필요한 정책 선택을 요청한다. 내용 삭제, 기준 미만 축소, 무단 페이지 분할이나 A4 방향 변경으로 해결했다고 처리하지 않는다. 해당 충돌은 미완료로 남기며 단순한 오류 표시 구현을 제품 기능 완성으로 세지 않는다.

최종 완료 전에 모두 충족해야 할 항목:

- 필수 사용자 경로가 공통 캔버스를 사용하고 모드 전환으로 문서 크기가 바뀌지 않는다.
- 현재 사용 가능한 생성·편집·재생성·저장·복원·출력 기능이 A4 때문에 차단되지 않는다.
- 기존 복제·공유 보기·썸네일·재개 경로를 LIFE 인수 ID에 연결하고, 크기·캐시·revision·재개 허용 상태를 확인했다.
- 가로·세로 실제 PPTX/PDF의 A4 크기와 요소 배율을 readback으로 입증한다.
- 대표 원본/마스터 fixture에서 원문 보존·배치·글자 정책과 캡처/저장 일치를 확인한다.
- viewport/비-px 입력을 포함한 기존 지원 마스터의 변환을 확인하고, 7 CSS px·9pt capability·적용되는 9.5pt 품질 게이트·12pt 본문 정책을 혼동하거나 완화하지 않았다.
- 레거시 문서 크기와 해당 기존 동작이 유지된다.
- 이번 기능의 검사 실패와 파일 크기 제한 위반이 해결되어 있다.
- A4가 실제 로컬 환경에서 활성화되어 있고 UI 생성과 다운로드가 동작한다.
- 확정된 지원 조합표의 필수 칸이 모두 증거와 연결되고, 정상 지원 입력을 사후 미지원으로 제외하지 않았다.
- 최종 실행 환경의 생성·저장·재열기·기존 편집·다운로드를 확인했으며 필요한 글꼴·worker·설정이 정상 재시작 뒤에도 유지된다.
- 후보 환경의 실제 격리 또는 안전한 동일 환경 전환 조건, 최종 버전 정합과 writer 복구 절차를 기록했다.
- 대표 결과 파일과 사용 URL, 수행한 검증 및 제한을 전달한다. 필수 작업을 '다음에 개발' 항목으로 남기지 않는다.

완료 보고의 형식은 사용 가능 여부 → 실제 사용 URL/산출물 → 핵심 변경 → 실제 검증 결과 순서로 한다. 위 조건을 충족하지 못하면 정확한 미완료/차단 상태를 보고하며 완료라고 표현하지 않는다.
