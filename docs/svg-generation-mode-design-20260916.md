# SVG 생성 모드 설계

작성일: 2026-09-16

상태: 사용자 요구 확정 / 코드 조사 기반 구현 설계안. 이 문서는 설계 산출물이며 제품 구현·배포 완료를 의미하지 않는다.

개정: 2026-09-16, 품질 우선 개정. 사용자 결정에 따라 최초 생성의 잘림·겹침 감소와 내용·가독성 보존을 최우선 목표로 두고 초기 품질 관문을 추가했다. 기존 24개 비교의 우위 미확인 결과는 그대로 보존한다. 아래 세부 계약과 인수 기준은 구현 시 검증할 설계안이며 이미 입증된 제품 동작을 의미하지 않는다.

## 1. 목적과 확정 요구

- 기존 Authored를 기본으로 유지하고 사용자가 선택할 수 있는 SVG 생성 모드를 추가한다.
- SVG는 전체 문서를 생성·저장·재열기·수정·내보내는 제품 기능이다. 3페이지 샘플 생성 기능으로 축소하지 않는다. 페이지 수는 기존 제품의 요청·작업·export 제한을 따른다.
- OSB 스마트뱅킹, 현대백화점 문화센터를 포함한 기존 Authored 스타일을 공유한다. SVG 전용 카탈로그나 전체 YAML 복제본을 만들지 않는다.
- 공통 디자인 규칙과 HTML/SVG 작성 규칙을 분리한다. 필요한 경우에만 특정 스타일의 SVG 보완 규칙을 같은 스타일 정의에 추가한다.
- 기존 Authored·Composer·기존 문서의 결과와 동작을 보존한다.
- 최우선 목표는 동일 조건의 HTML보다 최초 생성의 넘침·잘림·의도하지 않은 겹침을 줄이면서 내용·수치·이미지 가독성을 보존하는 것이다. 생성 성공률·오류 해소·형식 지원 확대만으로 목표 달성을 선언하지 않는다. 품질 우위를 사실로 미리 가정하지 않으며, 초기 비교로 입증하는 것을 개발 확대·신규 일반 활성화의 관문으로 삼는다. 이미 제공된 기능을 이번 설계만으로 비활성화하지 않는다.

이하 필드명, 신규 파일명, 단계 구분은 구현 권고안이다. 사용자 확정 요구와 동일한 권위로 취급하지 않는다.

## 2. 확인한 현재 연결부

| 영역 | 현재 코드와 확인 사항 | 필요한 변화 |
|---|---|---|
| 요청 | `servers/fastapi/models/generate_presentation_request.py`는 strategy에 authored만 허용하고 source-content와 충돌 검증을 수행 | SVG admission을 명시적으로 추가 |
| 생성 입구 | `servers/fastapi/api/v1/ppt/endpoints/presentation_generate.py`에서 Authored 준비·스타일·캔버스·생성 호출 연결 | 공통 준비 후 생성 경로 분기 |
| 생성 허용 계약 | `services/compatibility_migration/authored_vnext_contract.py`는 authored-only를 생성 조건으로 사용 | 신규 SVG admission과 capability 계약 확장. 기존 retired engine은 계속 차단 |
| 스타일 | `utils/authored_styles.py`가 brief, 색상·폰트 토큰, 역할별 참조 이미지, exact-reference 정보를 읽음 | 같은 스타일 객체에서 SVG 작성용 입력 구성 |
| 스타일 카탈로그 | `utils/authored_style_catalogue.py`는 alias와 supportedAspectRatios를 제공 | 카탈로그 공유, 표현 방식 지원 정보를 가산적으로 제공 |
| 프롬프트 | `utils/llm_calls/author_slide_prompt.py`는 style.brief와 HTML exemplar를 결합 | SVG 요청에는 HTML 작성 지침·HTML exemplar를 주입하지 않음 |
| 생성 실행 | `services/authored_presentation_service.py`, `authored_presentation_pipeline.py`에 bounded workers, fenced checkpoints가 있음 | 작업 관리 재사용, HTML 전용 author/render/repair와 SVG 처리 분리 |
| 저장 | `models/sql/slide.py`는 html_content와 native ui의 동시 존재를 제한. `authored_presentation_publication.py`는 HTML을 저장 | SVG 원본 필드·출처 식별·배타성 검증 추가 |
| 문서 정체성 | `models/sql/presentation.py`에 mode, profile_driver_id, version, revision 존재 | SVG 정체성을 서버가 일관되게 지정. 기존 version을 템플릿 버전으로 오용하지 않음 |
| 작업 복구 | `models/sql/presentation_generation_job.py`에 request_payload, request_sha256, outline, lease 존재 | SVG 방식·스타일 스냅샷을 요청/체크포인트에 고정 |
| 화면 | `upload/components/upload-page/useUploadPageState.ts`, Redux upload 상태가 스타일·캔버스 관리 | 모드 선택을 생성 요청까지 전달. 기존 스타일/모델 선택 보존 |
| 미리보기 | `components/AuthoredSlidePreview.tsx`는 HTML sandbox iframe, Electron에서는 worker PNG 사용 | SVG 미리보기도 같은 격리 경계 유지 |
| 페이지 재생성 | `services/authored_page_remake_service.py`는 HTML 준비·검사와 CAS 게시를 결합 | 형식별 생성·검사 경로를 선택하되 미리보기 후 적용 계약 재사용 |
| 내보내기 | `workers/public_export_worker.py`는 versioned bundle, canvas, hybrid/raster 결과를 처리 | SVG 전용 bundle 분기·변환·검증을 같은 격리 worker에 추가 |

위 services/models/utils 경로는 `servers/fastapi/` 기준, 화면 경로는 `servers/nextjs/app/(presentation-generator)/` 기준이다. 현재 export worker의 MAX_SLIDES=100은 관찰된 제한이며 SVG에 무제한 분량을 약속하지 않는다.

## 3. 전체 흐름

자료·지시 입력 → Authored/SVG 선택 → 기존 스타일·캔버스 선택 → 공통 자료 분석·아웃라인·자산 준비 → 형식별 페이지 작성 → 형식별 검사/보정 → 확정 원본·미리보기 저장 → 전체 문서 게시 → 형식별 PPTX/PDF 내보내기.

분기는 페이지의 표현과 처리에서만 둔다. 별도 사용자 작업 큐, 별도 모델 설정, 별도 파일 다운로드 시스템은 만들지 않는다. 기존 HTML 파이프라인을 전체 범용화하는 대규모 리팩터링도 선행 조건으로 삼지 않는다. 공통 준비 결과와 문서 게시 경계에 작은 dispatch를 두고, 내부 HTML 의존 함수는 그대로 보존한다.

## 4. API와 문서 정체성

권고안:

- 생성 요청 `strategy: authored | svg`, 생략은 authored. 기존 `authored_style`, `aspect_ratio`, 모델·자료·아웃라인 옵션을 재사용한다.
- 문서의 신규 SVG mode/driver는 서버만 기록한다. 클라이언트가 profile_driver_id나 기존 호환 version을 직접 선택하지 않는다. 생성·읽기·export resolver를 한 릴리스에서 함께 갱신한다.
- API 응답에는 서버가 계산한 generationStrategy, sourceFormat 및 edit/export capabilities를 노출한다. 실제 필드 표기는 기존 DTO 명명 규칙에 맞춘다.
- 문서 생성 이후 모드는 고정한다. 재생성은 저장된 모드를 사용한다. 기존 문서를 SVG로 바꾸는 작업은 새 문서 생성이며 기존 자료를 덮어쓰지 않는다.
- 기존 authored-only compatibility DTO의 의미를 몰래 바꾸지 않는다. 버전이 있는 신규 capability 응답으로 SVG를 명시하고, 구 클라이언트가 SVG를 HTML로 오인하지 않게 차단/안내한다.
- 동기 API, 비동기 작업, workspace 생성, 자동 export에서 동일한 admission/resolver를 사용한다. request hash와 idempotency에 모드·스타일 스냅샷·canvas를 포함한다.
- source_content_restyle/Composer와 exact reconstruction은 별도 보존 계약이다. 최초 SVG 릴리스에서 이를 자동 승계하지 않는다. 사용 불가 조합을 UI와 API에서 같은 이유로 설명하고, 일반 자료 기반 SVG 생성은 허용한다.
- 회사 표준은 기존 선택·스냅샷 정책을 유지한다. 표현에 중립적인 브랜드·헤더 계약을 SVG로 적용한다. HTML 전용 표준은 변환 지원 여부를 admission에서 명시하고 조용히 생략하지 않는다.

## 5. 기존 템플릿의 SVG 적용

### 공통 디자인 입력

기존 YAML이 단일 권위다. id/alias, palette, fonts, 역할별 참조 이미지, mood, 제목 위계, header/footer, 배치·도표 원칙을 읽어 문서 생성 시 스냅샷으로 고정한다. 카탈로그 변경이 재시도 중인 문서의 디자인을 바꾸지 않게 한다.

### SVG 작성용 어댑터

신규 `utils/svg_style_adapter.py`와 `utils/llm_calls/author_slide_svg.py`를 권고한다.

1. palette, fonts, canvas와 이미 구조화된 헤더·푸터·브랜드 토큰은 원본 필드에서 직접 읽는다. 자유 서술 brief를 임의로 해석해 구조화된 값보다 우선시키지 않는다. 각 입력과 보완 규칙의 출처를 기록한다.
2. brief는 명시적으로 분류된 공통 디자인 지침과 형식별 작성 지침으로 분리한다. HTML/CSS·DOM·flex/grid·HTML exemplar 등 형식 전용 지시는 SVG용 중앙 규칙으로 대체한다. 단순한 전역 문자열 치환이나 런타임 LLM 분류를 제품 어댑터로 사용하지 않는다. 분류가 애매한 구간은 자동 생략하지 않고 스타일 진단으로 남겨 명시적 매핑 또는 svg_overrides로 해결한다. 해결되지 않은 필수 브랜드 규칙은 해당 스타일의 SVG admission을 제한한다.
3. HTML 경로의 기존 brief/exemplar 처리 결과는 바꾸지 않는다. SVG에서는 원시 HTML exemplar 대신 기존 역할별 참조 이미지를 사용한다.
4. SVG 중앙 규칙은 viewBox, text/tspan 줄 배치, 지원 도형, 글꼴·크기, 로컬 자산 참조, 식별자, 변환 제약을 정의한다.
5. 선택적 `svg_overrides`를 같은 YAML에서 허용한다. 레이아웃 힌트·지원 효과의 대체 표현만 보완하며, 공통 색상/브랜드/의미를 중복 정의하지 않는다. YAML loader와 스냅샷 직렬화도 함께 확장한다.
6. effective style digest에 원본 YAML, 참조 자산, adapter version, SVG 계약 버전을 포함한다. 중간 스타일 생성에 별도 LLM 호출을 필수로 추가하지 않는다.

OSB는 네이비 제목·블루 표 헤더·연결선·제한된 빨강 강조를, 현대백화점은 녹색 상단 띠·우측 태그·번호 제목·청록 표 구성을 유지한다. 표·도식의 내용은 실제 자료에서 얻으며 참조 이미지의 숫자나 문구를 복사하지 않는다.

모든 일반 스타일은 같은 어댑터를 통과시키되, 특정 템플릿이 이미 검증됐다고 일괄 표시하지 않는다. 적용 가능/검증 완료/특정 기능 제한을 구분한다. 기존 canvas 호환 정책을 유지하고 화면 비율 변경 시 선택 스타일을 조용히 초기화하지 않는다.

어댑터 출력은 공통 디자인 토큰, 형식 중립 지침, SVG 작성 규칙, 적용된 override, 미해결 진단, 출처와 digest를 구분해 저장한다. 지원 여부는 스타일 ID만이 아니라 canvas와 필요한 표현 기능의 조합으로 판정한다. OSB·현대백화점의 명시적 매핑부터 확인한 뒤 다른 스타일에 적용하며, 카탈로그 전체 지원을 먼저 선언하지 않는다.

페이지 번호는 LLM 생성 대상에서 제외한다. 번호의 위치·색상·크기는 스타일 계약으로, 실제 값은 확정된 문서 순서로 시스템이 생성한다. 재정렬·복제·삭제 시 영향받은 페이지의 파생 SVG·미리보기·digest·export cache를 같은 revision에서 갱신한다. 번호용 예약 ID를 사용해 본문에 포함된 절·항 번호를 변경하지 않는다.

## 6. SVG 원본과 저장

이미지 저장과 해석은 [SVG 이미지 자산 파이프라인 설계 v3](svg-image-asset-pipeline-design-20260916.md)를 따른다. PPTX·DOCX·PDF 및 직접 첨부 이미지가 수집 범위다. DOCX는 관계 참조와 문단/표/캡션을, PDF는 개별 이미지·도표 영역·스캔 페이지와 위치를 구분해 출처와 occurrence를 보존한다. PDF 페이지 렌더만으로 개별 이미지 추출을 완료했다고 표시하지 않는다. 첫 품질 비교에는 DOCX 내장 그림·PDF 개별 이미지를 연결하고 복합 개체·벡터 도표의 영역 식별은 후속 단계에서 확장한다. 최종 지원 범위에서 제외하는 것은 아니다.

정규 SVG `svg-static/v2`에는 base64 대신 고정된 source manifest의 `source-slot-*`만 저장한다. 배치별 screen/export 파생본은 SVG 작성 후 별도 placement manifest로 연결한다. 독립 실행형 SVG는 권한을 확인한 resolver/worker가 일시적으로 만든다. legacy v1의 읽기·수정·복원·내보내기와 v2 resolver를 먼저 지원한 뒤 새 writer를 활성화한다.

권고하는 가산적 DB 변경:

- `slides.svg_content` nullable text와 `slides.source_format` nullable discriminator를 추가한다. 새 HTML은 html, 새 SVG는 svg, 기존 native ui는 해당 native 형식으로 정규화한다.
- 기존 행의 null source_format은 현재 HTML/native 판정 규칙으로 읽는다. payload가 비어 있는 기존 임시 행까지 강제로 재작성하지 않는다.
- HTML/native UI/SVG의 실제 본문은 동시에 존재할 수 없다. 기존 배타 제약을 세 형식에 맞게 확장하고 discriminator와 payload 일치를 검증한다. SVG 게시 시 nonempty 원본과 일치하는 receipt를 요구한다.
- source digest, 자산 manifest digest, style digest, canvas, validator/renderer/converter version은 기존 artifact/receipt 저장 체계를 확장해 관리한다. SVG 내부에 base64 이미지, 임의 파일 경로나 비밀을 넣지 않는다.
- HTML wrapper와 PNG는 파생 결과이며 권위 원본이 아니다. 검사·캡처·저장·export에 동일한 확정 SVG digest를 사용한다.
- clone, 페이지 추가/삭제/재정렬, 버전 history, undo/restore, 소유권 검사에도 source_format/svg_content를 전달한다. export cache에 문서 revision과 모든 원본 digest를 포함한다.
- `is_authored()`를 무조건 SVG까지 확장하지 않는다. HTML 편집기로 잘못 보내지 않도록 의미가 필요한 곳에서 형식과 capability로 분기한다.

롤아웃은 nullable 컬럼·신규 reader/worker 지원 배포 → 기존 데이터와 제약 확인 → 신규 SVG writer/선택 기능 활성화 순서다. 구 worker는 SVG 작업을 임대하지 않게 capability를 확인한다. 신규 생성 중단 시에도 기존 SVG 문서의 읽기·내보내기는 유지한다. 데이터가 있는 컬럼을 되돌리기 위해 삭제하지 않는다.

## 7. 생성·검사·복구

- 기존 아웃라인/자료/이미지 참조와 모델 선택·사용량 영수증을 재사용한다. provider 선택을 endpoint에 새로 구현하지 않는다.
- 모든 페이지의 이미지 추출·선정·보존 정책·model-preview 생성과 source manifest 검증을 먼저 끝낸다. 이 preflight 전에 provider attempt를 예약하지 않는다. 실제 배치에 의존하는 screen/export 파생본은 SVG 후보 작성 후 생성하고 자산 변환 실패 시 해당 단계부터 복구한다.
- 역할과 내용이 정해진 페이지별로 SVG를 생성하고 기존 bounded concurrency를 적용한다. 문서 간 동시성, 취소, lease/fence, deadline, idempotency를 공유한다.
- 체크포인트는 source_format을 명시한 가산적 버전으로 확장한다. HTML digest 필드를 SVG 저장소처럼 사용하지 않는다. 형식·스타일·자산·canvas·renderer가 일치할 때만 재사용한다.
- 상태는 기존 공개 작업 상태에 매핑하되 내부 단계는 작성, 구문검사, 측정, 보정, 확정으로 기록한다. HTML 전용 local-fit과 placeholder/semantic publish를 SVG에 그대로 호출하지 않는다.
- SVG 검사는 XML 유효성, canvas, 지원 요소/속성, 모든 참조 자산, 텍스트 가시성, 문맥별 출처/수치 보존을 포함한다. 일반 생성의 요약과 원문 그대로 보존 요청을 구분한다.
- 실제 글꼴과 이미지 로드가 끝난 Chromium에서 transform이 적용된 글자 bounds, clipping, 캔버스 이탈과 충돌을 측정한다. 정적 글리프 폭이나 도형 bounds만으로 품질 통과시키지 않는다. 의도적 겹침과 잘림을 구분한다.
- 최초 권고는 페이지당 한 번의 보정이다. 일시적 provider 오류 재시도와 품질 보정 횟수는 별개로 집계한다. 예산 초과는 review_required 또는 명시적 실패로 남기고 무제한 재생성하지 않는다.
- 검사를 건너뛰면 verified가 아니다. 유효하지 않거나 안전하지 않은 원본은 게시하지 않는다. 유효하지만 품질 결함이 남으면 기존 review_required UX에 연결하고 미검증 상태를 유지한다.
- 실패 페이지만 복구하며 정상 페이지를 다시 생성하지 않는다. SVG 실패를 HTML 전환이나 전체 페이지 이미지 대체로 숨기지 않는다.
- provider attempt는 기존 admission/usage ledger의 `reserved`, `dispatch_intent`/`dispatched`, `completed`와 연결한다. intent commit 전이며 이전 실행자가 fenced된 경우만 안전하게 재예약한다. intent 이후 응답 없는 중단은 실제 송신 전일 수도 있으므로 uncertain으로 취급하고, 확인된 provider idempotency/reconciliation 없이 자동 재구매하지 않는다. DB 상태와 네트워크 요청의 원자성을 가정하지 않는다.
- required 배치, 내용상 생략 허용, 변환 실패 시 제외 허용은 상세 설계의 중앙 정책표에서 파생한다. 각 단계가 독립 기본값을 만들지 않는다. 핵심 도표·본문 참조 이미지 누락은 검수 대상으로 남기며 offered/used 차이를 기록한다. 미선정 참고 이미지의 오류만으로 문서 전체를 막지 않되 전체 보존 지시와 문서 보안·권한 오류는 우선 적용한다. 수집기의 작은 이미지·반복·backplate 필터보다 사용자 보존 지시가 우선한다.

### 7.1 내용과 관계 보존

- 공통 자료 준비 결과에 content block ID와 입력에 근거한 관계 종류(비교, 순서, 병렬, 계층)를 전달한다. 근거가 없으면 관계를 미지정으로 유지하며 LLM이 순서·인과·계층을 새로 만들지 않게 한다.
- SVG 텍스트/도형과 입력 block ID의 대응을 별도 receipt로 보관한다. 연결선은 근거가 있는 관계 ID에 연결한다. 독립 기능 나열에는 방향 화살표를 추가하지 않는다. 장식선은 의미 연결선과 구분한다.
- 표는 문구 존재 여부뿐 아니라 행·열 제목과 셀의 대응을 검사한다. 수치·단위·고유명사와 원문 보존 요청의 필수 문구는 별도 대조한다. 요약 허용 입력은 사전에 정한 필수 사실을 검사하고, 원문 문자열 일치만으로 의미 보존을 통과시키지 않는다.
- 설명되지 않은 관계 추가나 필수 사실 누락은 품질 결함으로 남긴다. 자동 검사로 판단할 수 없는 의미 동등성은 확인 필요로 표시한다.

### 7.2 품질 판정 계약

검증기는 다음 항목을 독립적으로 보고하며 단일 점수로 결함을 상쇄하지 않는다. 수치 기준은 canvas·역할·사용자 typography 지시와 함께 생성 전에 고정하고 receipt에 기록한다.

| 항목 | 통과 기준 | 실패 처리 |
|---|---|---|
| 구문·격리·지원 범위 | 허용 계약 위반 0건, 자산 해결 완료 | 게시 차단; 허용 표현으로 재작성 |
| 텍스트 경계 | 의도하지 않은 글자 잘림·캔버스 이탈 0건 | 보정 후에도 남으면 review_required |
| 충돌 | 판정된 비의도 텍스트 겹침 및 연결선/레이블 충돌 0건 | 후보와 최종 판정을 분리; 미판정은 검증 완료로 표시하지 않음 |
| 가독성 | 본문/제목의 기존 유효 font floor와 사용자 지정 크기 준수, 역할별 행간·내부 여백 기준 충족 | 글씨 축소로 결함을 숨기지 않고 재배치 또는 확인 필요 |
| 내용·관계 | 필수 사실·수치·원문 보존 정책 준수, 근거 없는 관계 추가 0건 | 누락·관계 오류를 구분해 보정 |
| 스타일·문서 장치 | 필수 브랜드 규칙과 시스템 페이지 번호 일치 | 스타일 결함으로 기록하고 보정 |

페이지 번호·각주는 본문과 별도 역할 기준을 사용한다. 시험의 12.667px 기준을 모든 문서의 새 기본값으로 도입하지 않는다. 구현 시 역할별 기존 기준을 확인하고, 없는 행간·여백 기준은 명시적으로 확정하기 전 verified 판정을 활성화하지 않는다. 측정 오차 허용값도 버전 계약으로 고정하며 실제 잘림을 무시하는 여유값으로 사용하지 않는다.

최초 원시 응답, 최초 측정, 보정 이유/횟수, 최종 원본과 최종 측정을 각각 보관한다. 최초 성공률과 보정 후 성공률을 분리하고, 검사 생략·보정 예산 초과·자동 판정 불가를 성공으로 집계하지 않는다. 최초 비교는 시스템 페이지 번호 합성 등 결정적 후처리 전후를 구분해 LLM 자체의 결과와 혼동하지 않는다.

## 8. SVG 표현 범위와 격리

최초 지원: svg/g, text/tspan, rect, circle/ellipse, line/polyline/polygon, path, image, 제한된 transform·색상·stroke·opacity. 표·조직도는 편집 가능한 개별 텍스트/도형이다. 글자는 path로 윤곽선화하지 않는다.

위 목록은 지원 후보이며 요소 이름만으로 허용하지 않는다. 버전이 있는 단일 표현 계약에 요소·속성·값·부모/자식 조합·중첩 깊이·변환 조합과 필요한 converter capability를 정의한다. 생성 프롬프트, 서버 validator, Chromium 측정, PPTX 변환 전 검사와 export receipt가 같은 계약 버전을 사용한다. 계약/변환기 버전이 맞지 않는 작업은 조용히 실행하지 않는다.

| 표현 | 최초 계약의 보수적 범위 | 확대 조건 |
|---|---|---|
| text/tspan | 명시적 font 속성, x/y 기반 줄 배치, 지원 확인된 anchor와 span 구조 | dy·혼합 서식·회전·textLength 등은 브라우저/PPTX 대응을 확인한 조합만 허용 |
| g/transform | 명시적으로 검증한 translate 및 제한된 중첩 | scale·rotate·matrix와 조합은 텍스트 bounds 및 PPTX readback 확인 후 허용 |
| 도형/path | 유한 좌표, 단색 fill/stroke, 제한된 path 명령·구간 수 | 곡선·stroke 효과·opacity 조합은 변환 결과까지 확인 |
| image | 정규 SVG의 `source-slot-*`를 권한·digest가 고정된 manifest로 해결. 목적별 파생본은 worker에서만 주입 | crop·투명도·회전은 확인한 조합만 허용 |
| defs/참조 효과 | 기본 비활성 | 내부 gradient/clipPath도 validator·renderer·converter 모두 확인한 항목만 활성화 |

속성·단위·숫자 범위·path 복잡도·자산 용량의 구체값은 converter 조사 후 계약 파일에서 확정한다. 미확정 항목은 지원으로 노출하지 않는다. 알 수 없는 속성을 삭제해 겉보기 성공을 만들지 않고, 재작성 또는 명시적 미지원으로 처리한다. 세부 지원표는 구현 및 패키징의 선행 산출물이다.

필터·마스크·foreignObject·애니메이션·임의 CSS·script·event handler·외부 use는 최초 계약에서 제외한다. `defs` 기반 gradient/clipPath 등은 변환기와 검사기 양쪽에서 검증한 항목만 추가한다. 미지원 디자인은 생성 단계에서 지원 표현으로 다시 작성한다. 이미지 대체가 필요하면 개체별 이유와 편집성 저하를 기록한다.

XML DTD/entity, 외부 URL/file 참조, 무제한 중첩/path를 차단한다. 정규 SVG의 data URI도 차단한다. 이미지·폰트는 사용자 소유권을 확인한 불투명 자산 ID로 해결하고, worker에는 허용 manifest와 목적별 파생본만 전달한다. 수량·바이트·복잡도·시간 제한은 원본 파일 크기가 아니라 실제 사용 목적과 worker budget으로 판정한다.

서버 정화 후에도 React에 원시 SVG를 직접 주입하지 않는다. 웹은 권한 확인 resolver의 파생 SVG wrapper를 기존 sandbox/CSP 경계에 표시하고 Electron/썸네일은 기존 격리 worker PNG를 유지한다. 미리보기 포맷을 일괄 PNG로 바꾸지 않는다. API 원본 제공은 기존 문서 권한으로 보호한다.

## 9. 미리보기와 편집

- 생성 중 완료된 페이지부터 썸네일/본화면에 표시하고 진행률·실패 페이지를 기존 UI에 연결한다.
- 모드는 생성 화면에서 선택하고 문서 화면에는 현재 방식을 표시한다. 새 문서는 Authored 기본, 사용자가 선택한 SVG는 아웃라인 왕복·재시도에서 보존한다.
- 첫 릴리스부터 페이지 단위 AI 수정·재생성을 지원한다. 변경 전 원본 revision을 기준으로 새 SVG를 만들고 검사한 뒤 미리보기와 CAS 적용을 거친다.
- 페이지 삭제·재정렬·복제·발표자 노트·문서 버전 복원은 형식 중립 기능으로 연결한다. 노트는 SVG 그림에 넣지 않고 PPTX 노트로 전달한다.
- HTML 전용 텍스트/DOM 편집 도구는 SVG에서 비활성화 이유를 안내하고 페이지 수정 동작을 제공한다. 브라우저 직접 도형 조작 편집기는 후속 범위다.

## 10. PPTX/PDF 내보내기

- `public_export_worker`에 SVG bundle schema와 처리 분기를 추가한다. Core/durable controller에서 Chrome나 OOXML assembler를 실행하지 않는다.
- bundle은 확정 정규 SVG, 순서, canvas, 노트, 글꼴·source/placement manifest, digest를 전달한다. 재사용 파생본에는 현재 export operation에 귀속된 ref를 발급하고 owner/tenant 및 digest를 검증한다. 내부 worker의 검증된 opaque storage key 계약은 유지하되 provider/사용자에게 노출하지 않는다. worker가 일시적인 독립 실행형 SVG를 만들며, 입력 형식이 SVG이면 HTML hybrid assembler로 우회하지 않는다.
- 시험에 사용한 slide-master 커밋 `166472bd2a22de9aa9fb6c8cdf8b0cdfc6b698ef`를 최초 변환기 후보로 한다. 관련 모듈·필수 도형 데이터·라이선스만 제품 의존성으로 고정하고 NOTICE/SBOM·패키징에 반영한다. 시험 디렉터리의 다운로드 코드에 제품이 의존하지 않게 한다.
- 비공식 Presenton upstream으로 취급하지 않는다. 외부 변환기 의존성 도입으로 관리하고 upstream intake/라이선스 정책을 따른다.
- 변환기는 shell 문자열 없이 로컬 인자로 실행하며 출력 경로·임시 폴더·timeout·process 종료를 worker가 소유한다. Docker/Windows/Electron 패키지에 필요한 데이터가 포함돼야 한다.
- PPTX의 슬라이드 수·크기·문구·relationships·폰트·네이티브 개체 대응을 검사한다. 중첩 그룹은 재귀 집계한다. 생성 완료, 열기 성공, 편집성, 시각 품질은 별도 지표다.
- PDF는 같은 확정 SVG와 폰트로 worker에서 페이지별 렌더/인쇄한다. Chrome print 경로를 우선 검토하되 SVG 기능이 PDF에 보존되는지는 실제 readback으로 판정한다. 기존 이미지 PDF 경로를 쓰면 그 특성을 결과에 명시한다.
- 요청 포맷에 따라 작업을 완료하고 부분 실패를 전체 성공으로 표시하지 않는다. fallback을 허용하는 경우 개체/페이지·이유·편집성에 반영한다.

지원 상태는 preview, PPTX fidelity, PPTX text/shape editability, PDF를 각각 표시한다. 브라우저에서 보인다는 이유로 PPTX/PDF 지원을 추론하지 않는다. 직접 편집 인수는 실제 PowerPoint에서 텍스트와 도형을 변경하고 저장·재열기한 결과를 요구하며, 개체 수 집계나 내용 변경 없는 저장·재열기로 대체하지 않는다.

## 11. 변경 파일군과 개발 순서

| 단계 | 주 변경 지점 | 완료 조건 |
|---|---|---|
| 0. 내부 관통 구현 | 최소 표현 계약, 기존 수집기와 DOCX 내장/PDF 개별 이미지, request dispatch, 가산적 저장/reader, 격리 export | 내부 flag 아래 추출→SVG 최초 응답→화면→PPTX 연결. 비교 입력과 최초/보정 후 artifact를 고정하고 별도 시험용 제품 경로를 남기지 않음 |
| Q. 초기 최초 생성 품질 관문 | 기존 OSB/현대백화점, 동일 자료·모델·폰트·canvas, HTML/SVG 측정 | 보정 전 넘침·잘림·겹침 감소와 내용·가독성 비퇴행 확인. 미확인이면 생성 입력·배치·글꼴·이미지 표현을 먼저 개선하고 기능 확대로 대체하지 않음 |
| 1. 계약/저장 | generation request, presentation/slide 모델, Alembic, admission/driver resolver, 응답 types | 기존 authored 요청/문서 호환, SVG 형식 식별·배타 저장·읽기 지원 |
| 2. 스타일/작성 | authored_styles loader, 신규 svg_style_adapter, author_slide_svg, 공통 생성 dispatch | 기존 스타일을 선택한 전체 아웃라인으로 SVG 생성, 요청 모델 영수증 확보 |
| 3. 검사/복구 | 신규 svg 문서 validator/measurement, checkpoint·publication | 동일 원본 기준 캡처·저장, 페이지별 재시도·취소·fence 보존 |
| 4. 내보내기 | public_export_worker와 bundle/receipt, 고정 converter 패키지 | 문서 전체 PPTX/PDF, 품질·편집성 결과와 다운로드 연결 |
| 5. 화면/수정 | UploadPage/useUploadPageState, Redux upload·generation DTO, preview, remake/history | 사용자가 모드 선택부터 재열기·페이지 수정·다운로드까지 이용 |

단계별 개발 중 내부 flag로 노출을 제어하되 최종 완료는 품질 관문 Q와 1~5 전부다. 최초 품질 비교를 전체 기능 개발 뒤로 미루지 않는다. Q 이후 DOCX SmartArt·PDF 벡터/혼합 도표 등 추출 범위를 확대하고 범위별 품질을 확인한다. 한 페이지 관통이나 3페이지 샘플 성공을 모드 구현 완료로 보고하지 않는다. flag와 worker capability는 서버 권위이며, 기존 API의 feature 설정 배포 관례를 따른다.

단계 0은 연결 위험과 최초 생성 품질을 일찍 드러내기 위한 최소 제품 구현이다. 필요한 권한·preflight·provider ledger·reader/resolver는 이 단계부터 적용한다. 임시 HTML 필드에 SVG를 저장하거나 API/권한을 우회하지 않는다. Q 통과 후 1~5에서 전체 범위와 복구를 완성하며, 모든 reader 및 전체 문서·페이지 수정·PDF 인수 전 새 저장 형식을 일반 활성화하지 않는다. 기존 문서 기능은 보존한다.

## 12. 검증과 인수 기준

이번 설계 작업은 문서 검토만 수행한다. 구현 단계에서는 변경된 경계에 해당하는 가장 좁은 검증을 단계 완료 후 한 번 선택한다. DB 제약과 SVG 격리는 영향이 큰 경계이므로 필요한 migration/보안 입력 확인을 포함한다. 전체 테스트·전체 빌드·전체 E2E를 자동 실행하지 않는다.

제품 인수 항목:

- 생략 요청은 Authored이며 기존 저장 문서를 그대로 열고 export할 수 있다.
- OSB와 현대백화점 스타일을 같은 카탈로그에서 SVG로 선택해 실제 제품 생성 경로를 실행할 수 있다.
- 페이지 수에 인위적인 3페이지 제한이 없고 요청 범위 전체가 순서·canvas·노트를 유지한다. 일부 실패도 페이지별로 복구한다.
- 새로고침/재열기/재시도/수정에서 SVG 모드·스타일·모델 설정을 보존한다.
- 프로세스 재시작·작업 takeover에서 완료 페이지를 재사용하고 이전 attempt가 새 결과를 덮어쓰지 못한다.
- 자료의 필요한 문구·수치·이미지를 확인하고 최종 SVG/미리보기/export의 digest 연결이 일치한다.
- DOCX의 본문/표/anchor/반복 그림과 PDF의 embedded/벡터 영역/스캔 사례에서 occurrence·출처·추출 coverage·최종 사용을 대조한다. 영역 렌더한 그림 내부까지 native 편집 가능하다고 표시하지 않는다.
- 이미지 파이프라인 지원과 HTML 대비 품질 우위는 별도로 판정하되 최초 품질을 최우선 완료 조건으로 둔다. 동일 자료·스타일·모델 조건에서 보정 전 넘침·잘림·의도하지 않은 겹침이 개선되고 내용·이미지 가독성이 악화되지 않아야 초기 품질 관문을 통과한다. 양쪽 모두 결함 0건이거나 반복 결과가 엇갈리면 우위 미확인이며 기능 완료로 대신하지 않는다.
- PowerPoint에서 텍스트·도형을 수정·저장·재열기할 수 있고 PDF의 페이지 수·크기를 확인한다.
- exact/source-restyle 등 미지원 조합과 구 worker/구 client는 잘못된 형식으로 진행하지 않는다.

추가 품질 인수 항목:

- OSB·현대백화점 각각에 비교표, 독립 기능 나열, 밀집 본문을 적용한다. 순서 근거 없는 화살표가 없고 페이지 번호는 생성·재정렬 후에도 정확하다.
- 실제로 넘침·잘림이 발생했던 HTML 입력을 확보하면 동일 자료·스타일·모델 설정으로 최초 응답과 보정 후 응답을 나눠 비교한다. 개선 효과를 주장할 때는 해당 비교 근거를 함께 제시한다.
- 긴 표·긴 단어·한글/영문 혼합·이미지와 레이블·폰트 부재/대체를 포함한 지원 경계 입력을 확인한다. 원문 보존 모드에서는 내용을 삭제하거나 임의 페이지 확장으로 통과시키지 않는다.
- 세로 canvas는 가로 결과로 검증을 대체하지 않는다. 스타일/canvas 조합별로 확인하고 미검증 조합은 지원 상태를 제한한다.
- 지원하는 텍스트·도형·이미지·transform 조합은 SVG 미리보기와 PPTX/PDF readback을 대조한다. 개체 fallback은 위치·이유·편집성 저하를 기록한다.
- PowerPoint에서 실제 텍스트 및 도형 변경 후 저장·재열기를 수행한다. PDF는 페이지 수·크기뿐 아니라 폰트·문구·잘림을 확인한다.
- 품질 검사를 생략하거나 일부 검증기가 실패한 결과는 verified가 아니며, 최초 결함이 최종 보정 결과에 가려지지 않는다.
- 최초 응답은 파싱·slot 해결·공통 고정 요소 적용 후, 글자 축소·줄바꿈 추가·좌표 이동·LLM 교정 전에 측정한다. 최초/결정적 보정 후/LLM 교정 후를 따로 보존한다. 렌더 실패도 시도 수에 포함하며 본문 삭제·작은 글씨·페이지 이미지화로 결함만 줄인 결과는 품질 개선이 아니다.

### 12.1 이번 비교가 입증한 범위

`artifacts/svg-html-quality-20260916/report.md`의 실험은 기존 DevAX 배포 기능 DOCX 3개 내용 × OSB/현대백화점 × HTML/SVG × 2회, 총 24개 최초 응답이다. 양쪽 모두 텍스트 경계/겹침 후보와 원문 블록 누락 검출은 0페이지였으며 최초 생성 품질의 SVG 우위는 확인되지 않았다. 밀집 본문의 작은 글씨는 양쪽에 남았고, SVG 일부의 근거 없는 화살표·페이지 번호 불일치가 관찰됐다.

실험은 제품 durable job이 아닌 독립 provider 호출이며 형식별 프롬프트도 다르다. 이미지 혼합·복잡한 도식·세로 canvas·PDF·실제 텍스트/도형 변경은 이 실험의 검증 범위가 아니다. PPTX 각 12페이지의 실제 렌더와 내용 변경 없는 저장·재열기를 확인했으나, 이를 전체 제품 경로 또는 직접 편집 완료 증거로 취급하지 않는다. 제품용 어댑터에 시험의 문자열 치환을 그대로 옮기지 않는다.

검증용 기존 3페이지는 대표 도식·표·밀집 본문을 고르는 수단일 뿐 기능 범위를 제한하지 않는다. 사용할 기존 원본 HTML/본문을 확보하지 못하면 이미지 PPTX의 OCR을 정확한 원문으로 간주하지 않는다.

## 13. 구현 시작 시 남은 좁은 확인

1. 실제 driver registry/read DTO와 문서 history 직렬화의 모든 직접 호출 지점을 확인해 새 mode를 일관되게 연결한다.
2. HTML 체크포인트/semantic emission 결합 중 재사용 가능한 작업 관리와 형식 전용 부분을 나눠 최소 인터페이스를 확정한다.
3. 고정 변환기의 SVG 요소 지원·글꼴 처리·중첩 그룹·반환 오류를 확인해 allowlist와 packaging manifest를 확정한다. 시험 성공은 복잡한 스타일 품질 보장이 아니다.
4. 회사 표준과 exact-style 중 SVG로 적용 가능한 표현 중립 계약을 판정해 capability에 반영한다.

위 항목은 구현 세부 조사이며 사용자에게 템플릿 재선택이나 기능 범위 재승인을 요구하는 항목이 아니다.
