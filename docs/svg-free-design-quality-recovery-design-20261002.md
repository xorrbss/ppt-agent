# SVG 자유 생성 디자인 품질 회복 설계

작성일: 2026-10-02

개정: 3. 내부 비교 프로파일 구현, 작업 고정·재개 계약, 실제 생성·PowerPoint 비교와 차트 변환 수정의 근거를 반영했다.

상태: 내부 비교 프로파일과 durable 계약 구현 완료. A4 가로 네 덱 48장 생성 완료. 품질 회복과 기본 전환은 실제 평가 결과에 따라 별도로 판정한다.

상위 설계: `svg-all-canvas-free-composition-design-20261001.md`.
이 문서는 그 설계의 자유 구도 원칙을 유지하면서 실제 A4 결과에서 발견한 표현력 문제를 개선하는 후속 설계다.

## 1. 확정된 방향과 검증할 제안

사용자 지시로 유지하는 방향:

- SVG 기반으로 선택한 캔버스에 맞는 PPT를 생성한다.
- 구도는 모델이 자유롭게 설계한다. 고정 레이아웃 선택·컴파일로 대체하지 않는다.
- Authored 기능 확장은 범위에서 제외한다.
- 템플릿은 필요하면 다시 만들 수 있다.
- 디자인의 다양성과 완성도를 개선하면서 내용 정확성과 PPTX 내보내기를 유지한다.

아래의 입력 구조, 실험 기준, 전환 조건은 설계 제안이다. 비교 결과로 확정하며 현재 제품 계약이나 달성 결과로 간주하지 않는다.

## 2. 현재 확인된 사실과 원인 가설

### 2.1 확인된 구현

- 실제 현대백화점 A4 요청은 `strategy=svg`, `generationMode=free-design`이다. 요청의 `engine=authored` 표기만으로 Deck IR 생성이라고 판단하지 않는다.
- `project_style.py`는 `composition_mode=free-svg`, `reference_semantics=design-identity`를 전달한다. 구도·열 수·위치를 참고 이미지에서 강제하지 않는다는 지침이 이미 있다.
- `author_slide_svg.py`에도 내용 기반 자유 배치, 가독성, 독립 항목 사이의 불필요한 연결 금지, 정확한 네이티브 표·차트 선언을 요구하는 규칙이 있다.
- 모델에는 토큰뿐 아니라 `common_guidance`, `mapped_guidance`, 일부 `overrides`, 디자인 참조 정보 등이 투영된다. 자유 배치 지침 추가만으로 문제를 해결했다고 간주할 수 없다.
- 현대백화점 매핑과 스타일 본문에는 상단 밴드·패널 등의 구체적 표현 지침이 있다. 강제 시스템 요소와 선택 가능한 스타일 표현을 구분해야 한다.
- `visual_intent`는 기존 페이지 내용에서 만든 강조·밀도·읽기 흐름 조언이다. 좌표나 카드 수를 정하는 레이아웃 계획은 아니다.
- 물리 캔버스와 사용자 글자 크기를 고려하는 `generation_font_floor`가 이미 있다. 별도의 폰트 단위 체계를 새로 만들지 않는다.

### 2.2 확인된 결과

- 현대백화점 A4 가로 12장·세로 4장의 생성, 데이터 보존, 실제 PowerPoint 렌더링을 확인했다.
- 본문 페이지에서 상단 띠·제목·박스의 반복, 약한 시각적 비중 차이, 복잡한 구조도·간트의 작은 글자가 관찰됐다.
- 일부 구조도·간트 설명은 13–15 CSS px로 요청한 가독성 목표에 미달한다.
- PPTX 차트의 제목·범례·범주 표시 문제는 별도 변환 오류였으며 수정됐다. 해당 오류와 원본 SVG의 디자인 완성도를 혼동하지 않는다.
- 실제 요청은 사진·외부 이미지 없이 텍스트·데이터 중심으로 구성하고, 문장·표·수치를 보존하며 페이지 수를 고정했다. 이 조건 자체가 표현 범위와 밀도를 제한한다. 다른 허용 조건으로 생성된 과거 결과를 동일 조건 비교로 취급하지 않는다.

### 2.3 검증할 가설

| 가설 | 분리해 확인할 방법 |
| --- | --- |
| 중복되거나 구체적인 스타일 문구가 구도를 위축시킨다 | 참조 이미지·데이터·모델을 고정하고 스타일 투영만 변경 |
| 사용자 요구와 시스템 지침의 누적이 모든 정보를 같은 비중으로 표현하게 한다 | 실제 조립된 입력을 확인하고 선택적 디자인 조언만 정리 |
| 참고 이미지 자체의 표현 범위가 좁다 | 지침 개선 후에도 부족할 때 이미지 자료만 변경 |
| 원본보다 PPTX에서 표현이 약해진다 | 같은 생성 결과의 SVG와 PowerPoint 렌더링 비교 |
| 페이지 수 고정과 완전한 문구 보존이 가독성 충돌을 일으킨다 | 동일한 요구 조건에서 공간 재구성 성공 여부 확인 |
| 시각 자료 금지 등 요청 조건이 디자인 표현 범위를 제한한다 | 생성 경로 비교와 별도 허용 조건의 잠재력 비교를 분리 |

이전 자유 생성의 실제 설정과 결과를 복원하기 전에는 현재 방식의 퇴행 원인이나 과거 대비 우열을 확정하지 않는다.

## 3. 첫 구현의 범위

첫 구현은 SVG 작성 모델에 전달하는 스타일·디자인 입력의 정리에 한정한다.

포함:

- 실제 조립된 모델 입력의 관련 부분 확인 및 중복 조언 정리.
- 브랜드 정체성과 선택적 표현 지침 구분.
- 기존 필수 제약·사용자 지시를 보존한 SVG용 디자인 투영.
- 변경된 작성 프로필·입력 버전을 작업 생성 시 고정하고 증거에 남기는 처리.
- 대표 페이지의 동일 조건 비교와 기존 내보내기 경로 확인.

조건부 후속 작업:

- 비교 결과가 근거를 제공할 때만 참고 이미지 재제작.
- 일부 페이지 실패가 남을 때만 기존 시각 검토·수정 경로 보강.

첫 구현에서 추가하지 않는 기능:

- 별도의 Deck IR, 구도 선택기, 고정 레이아웃 카탈로그.
- 페이지마다 기획 모델을 호출하는 단계.
- 모든 페이지의 다중 후보 생성 또는 새 필수 시각 검토 호출.
- 새 사용자 설정 화면, 공개 요청 필드, 데이터베이스 테이블.
- Authored 전용 지침·렌더러 변경.

## 4. 입력 책임과 우선순위

### 4.1 필수 제약

기존 책임을 유지한다.

- 선택한 출력 캔버스의 논리·물리 크기와 안전 영역.
- SVG 계약, 허용 도형·이미지 슬롯, 글꼴·자산 소유권.
- 정확한 사실·수치·단위·표 관계와 실제 의존관계.
- 명시적으로 요구한 편집 가능 표·차트와 그 데이터.
- 사용자 지정 글자 크기, 원문 보존·페이지 수 조건.
- 실제 시스템 소유 로고·푸터·마스터 요소의 예약 영역.

필수 제약끼리 양립할 수 없을 때 선택적 디자인 조언으로 이를 덮어쓰지 않는다. 기존 오류·수정 예산 안에서 충돌을 드러내며, 자동 축약·분할의 새 권한을 만들지 않는다.

### 4.2 디자인 정체성

- 승인된 팔레트와 글꼴.
- 제목·본문의 시각적 성격과 기본 위계.
- 선·면·이미지·표·차트의 표현 성격.
- 내용에 맞게 선택할 수 있는 브랜드 모티프.

강제 로고·워드마크는 기존 시스템 계약에 따른다. 전체 페이지에 같은 위치·밴드·박스 구성을 적용하는 것은 디자인 정체성의 필수 조건으로 취급하지 않는다.

스타일 정리 시 각 요소를 아래 세 종류로 명시한다. 특징적인 표현을 전부 제거해 팔레트와 폰트만 남기는 변경은 통과시키지 않는다.

| 종류 | 보존·변경 규칙 | 현대백화점 예시 |
| --- | --- | --- |
| 필수 정체성 | 승인된 값과 실제 계약 유지 | 승인된 팔레트·글꼴·시스템 브랜드 요소 |
| 특징적인 표현 | 시각적 성격을 유지하고 사용 맥락을 선택 | 강한 제목 위계, 그린·청록 구분선, 읽기 쉬운 표 헤더, 기술 정보의 네이비 강조 |
| 선택적 구성 | 내용과 캔버스에 맞춰 배치·사용 여부 결정 | 상단 밴드·태그 위치, 패널·열·그룹의 수와 위치 |

### 4.3 내용 맥락

기존 페이지 내용과 `visual_intent`를 활용한다. 핵심 메시지, 실제 비교·순서·관계, 정보 밀도와 주변 페이지의 읽기 흐름을 전달한다.

밀도가 높다는 신호는 축소 지시가 아니다. 카드 개수·열 수·기하 좌표를 이 단계에서 결정하지 않는다. 반복이 내용상 유효한 비교 페이지는 동일 구성을 사용할 수 있다.

단순히 제약을 줄이는 데서 끝내지 않고 기존 내용·intent로 다음 판단을 지원한다. 별도 모델 호출을 추가하지 않는다.

- 원본에서 가장 먼저 보여야 할 메시지와 그 근거를 식별한다.
- 핵심 정보와 보조 설명의 시각적 비중을 구분한다.
- 실제 비교·관계·기간을 공간·크기·연결로 읽기 쉽게 표현한다.
- 메시지가 원본에서 확정되지 않으면 새 주장이나 해석을 만들어 강조하지 않는다.

참고 사례에는 필요할 때 표현이 효과적인 이유를 짧게 설명한다. 이 설명을 실행할 좌표·레이아웃 ID로 변환하지 않는다.

### 4.4 선택적 표현 조언

상단 띠·패널·구분선·큰 숫자·비대칭 배치 등의 표현은 내용에 적합할 때 선택하는 사례다. 이 목록을 필수 도형이나 레이아웃 선택 메뉴로 만들지 않는다.

핵심 정보와 보조 설명의 시각적 비중을 구분하고, 관계를 설명하는 구성과 의미 있는 여백을 활용하도록 간결하게 안내한다. 장식 수나 레이아웃 종류 수를 품질 지표로 사용하지 않는다.

## 5. 스타일 입력 정리 설계

### 5.1 한 곳에서 투영

SVG용 작성 입력은 기존 `project_style.py` 및 `svg_prompt_design`의 경계를 활용한다. 프리셋 YAML 전체를 먼저 다시 쓰거나 Authored와 공유하는 스타일 의미를 변경하지 않는다.

첫 대상은 현대백화점과 대조용 스타일 하나다. 새로운 SVG 프로필에 한해서 모델에 전달할 정체성·선택적 조언을 정리한다. 각 변경에는 원본 필드와 정리 이유를 내부 근거로 남긴다.

- 브랜드 토큰과 실제 시스템 예약 요소는 보존한다.
- 레이아웃을 유도하는 기본 매핑은 선택적 표현으로 정리한다.
- 반복되는 일반 조언은 한 번만 전달한다.
- 사용자 `instructions`와 원본 `source_content`는 임의로 정리하거나 제거하지 않는다.
- 안전·소유권·내용 보존·네이티브 선언 규칙은 줄이지 않는다.

문장의 `must`, 숫자, 좌표를 정규식으로 일괄 삭제하는 방식은 사용하지 않는다. 사용자 요구와 실제 마스터 계약을 손상할 수 있기 때문이다. 정리 대상은 출처가 확인된 스타일 조언으로 제한한다.

### 5.2 현재 입력이 정말 중복되는지 확인

`author_slide_svg.py`와 `author_messages`가 최종 조립한 메시지를 기준으로 확인한다. 코드 중간에 디자인 문자열이 두 번 보인다는 이유만으로 최종 모델 입력에 중복됐다고 판단하지 않는다.

확인할 내용은 총 텍스트량, 디자인·내용·네이티브 데이터 블록의 위치, 참고 이미지 수와 순서다. 프롬프트 원문이나 원본 자료를 사용자 화면에 출력하지 않고 로컬 검토 근거로 관리한다. 비밀 값은 수집하지 않는다.

## 6. 작업 고정·재개·캐시 계약

새 프로필은 신뢰된 내부 실험 호출과 기존 staging·준비 checkpoint 경계를 통해 선택한다. 공개 요청 본문에 임의의 내부 키를 넣어 선택하는 경로를 만들지 않는다. 공개 API 필드나 전역 환경 기본값을 먼저 추가하지 않는다.

제안하는 최소 내부 메타데이터:

| 항목 | 의미 |
| --- | --- |
| 작성 프로필과 버전 | 현행 입력 또는 새 디자인 투영을 선택한 근거 |
| 원본 스타일 digest | 정체성과 참조의 원본 연결 |
| 작성 입력 digest | 실제 전달된 버전별 입력을 구분 |
| 캔버스·내용·참조 이미지 digest | 비교 조건과 작업 재개 동일성 확인 |

이번 구현의 계약은 다음과 같이 고정한다. 새 필드·프로필은 이 설계의 구현 대상이며 현재 제품에서 이미 지원한다고 표시하지 않는다. 기존 JSON payload·snapshot·receipt를 사용하고 별도 테이블을 만들지 않는다.

| 단계 | 필드·연결 함수 | 계약 |
| --- | --- | --- |
| 선택 | 요청 private `_svg_authoring_profile` | 신뢰된 내부 runner가 명시적으로 지정. 새 선택은 기존 bench 허용 환경에서만 가능 |
| 허용 값 | `current-v4`, `art-direction-v1` | 미지정·`current-v4`는 현행 의미. 새 프로필은 version 1 |
| 스타일 고정 | `svg_generation_admission.pin_svg_style` / snapshot `authoring_profile` | 새 프로필 객체 `{id: art-direction-v1, version: 1}`를 canonical digest 계산 전에 고정. 현행 프로필은 추가 필드 없이 유지 |
| durable 저장 | `presentation_generation_staging._durable_request_payload` / `_internal_svg_authoring_profile` | 명시적으로 직렬화. 제외되는 Pydantic 필드에 의존하지 않음. 기존 `_internal_svg_style_snapshot`과 일치 |
| durable 복원 | `presentation_generation_staging._request_from_durable_payload` | 내부 키를 공개 request 생성 전에 제거하고 private 상태로 복원. snapshot과 충돌하면 실패 |
| worker 준비 | `svg_presentation_prepare._prepare_generation` / `job.svg_style_snapshot` | 기존 snapshot이 작업의 고정 근거. worker takeover 때 새로운 선택을 적용하지 않음 |
| 작성 | `author_slide_svg.svg_prompt_design` / 기존 SVG 작성 함수 | frozen 프로필에 맞는 투영만 적용. 없는 프로필은 기존 경로 유지 |
| 증거 | 새 프로필의 기존 작성 receipt | 프로필 ID·버전과 최종 조립 작성 입력 digest 기록. 스타일 digest와 입력 digest를 혼용하지 않음 |

미지원 ID·버전과 request/payload/snapshot 간 충돌은 provider 호출 전에 기존 오류 처리로 드러낸다. snapshot이 없는 새 작업은 입장 시 선택한 프로필로 한 번 고정하고, snapshot 없는 기존 작업은 현행으로 해석한다. 이미 고정된 작업은 bench 환경의 이후 변화와 관계없이 같은 프로필로 재개한다. 신규 선택 제한과 기존 작업 복원의 책임을 분리한다.

내부 runner 진입점은 기존 `POST /api/v2/presentations/generations`의 bench 전용 `X-Presenton-Internal-Svg-Authoring-Profile` 헤더다. 공개 DTO 필드·UI에 추가하지 않는다. 정확한 bench 허용 값, middleware가 검증한 로컬 개발 관리자 issuer·method, 개발 환경의 실제 loopback peer와 URL host를 모두 요구한다. 일반 세션·외부 요청·비관리자·비허용 환경은 403, 미지원 ID는 422이며 작성 호출 전에 거절한다. 헤더가 없는 공개 경로의 위임 인수는 그대로 유지한다.

`PublicV2Orchestrator.submit_generation`의 내부 keyword `svg_authoring_profile`을 통해 명시적으로 전달한다. 새 프로필은 요청 hash와 admission payload에 포함하며 동일 idempotency key의 다른 프로필을 동일 요청으로 재사용하지 않는다. 정상 및 IntegrityError 재생 경로 모두 같은 hash를 사용한다. `current-v4`와 미지정의 hash는 기존과 동일하다. helper의 trusted Python 선택 경로도 같은 신규 입장 gate를 적용한다.

계약:

- 새 작업의 프로필은 준비 시 고정한다. 페이지 작성·재시도·재개 중 변경하지 않는다.
- 프로필 메타데이터가 없는 기존 작업은 현행 입력 의미로 재개한다.
- 오래된 snapshot을 새 투영으로 자동 재해석하지 않는다.
- 스타일 snapshot을 바꾸면 기존 canonical digest 계산 경로에서 다시 계산한다. receipt의 `style_digest`만 수동 치환하지 않는다.
- 이미 고정된 페이지 결과는 새 프로필이라는 이유로 다시 쓰지 않는다.
- 프로필이나 입력 변경은 작성 결과 캐시의 식별 근거에 포함한다. 자산 분석·참고 이미지 캐시는 원본 digest가 동일할 때 재사용한다.
- 모델 요청 설정을 기록하되 provider가 확인하지 않은 실제 모델을 검증 완료라고 표시하지 않는다.
- SVG 게시·수정은 기존 `svg_page_publication.py`를 사용하고 canonical bytes와 측정·품질 digest를 일치시킨다.

최종 작성 입력 digest는 profile·규칙 버전, 실제 조립한 system/user 텍스트, 순서가 보존된 이미지 바이트 digest, 캔버스·글자 정책과 model/output 설정을 식별한다. API key·원본 이미지 데이터·추적 ID·wall-clock 값은 포함하지 않는다. digest 계산은 최종 메시지 조립 이후에 한다. 페이지별 digest는 receipt에만 두고 덱 공통 프롬프트에 삽입하지 않는다.

기본 전환과 되돌리기의 소유자는 SVG admission에서 새 작업의 프로필을 선택하는 한 지점이다. 각 worker나 스타일에 별도 기본값을 두지 않는다. 비교 단계의 기본은 현행이며 새로운 전역 설정을 추가하지 않는다.

## 7. 가독성과 내용 밀도

기존 `document-canvas/v1`과 `generation_font_floor`를 재사용한다. CSS px를 모든 출력에서 같은 물리 크기로 간주하지 않는다.

검토 근거에는 논리 글자 크기, 출력 캔버스의 변환 비율, 최종 PowerPoint의 실제 표시를 함께 사용한다. 사용자 조건을 다음 세 종류로 구분하며 생성 후 평가에 맞춰 임의로 재분류하지 않는다.

| 조건 | 판정·처리 |
| --- | --- |
| 명시적 필수 크기·보존 조건 | 반드시 준수. 미달은 제약 실패 |
| 권장 크기·가능한 범위·목표 표현 | 미달을 디자인 문제로 기록. 자동으로 전체 생성 실패와 동일시하지 않음 |
| 기존 제품 최소 가독성 조건 | 기존 측정·실패 계약 유지. 권장 목표로 낮추지 않음 |

본문·표·주석은 역할별로 평가한다. 목표에 미달한 페이지를 잘림이 없다는 이유로 완성도 높음으로 평가하지 않는다. 이번 A4 요청의 목표 크기와 실제 필수 조건을 별도 목록으로 고정해 비교한다.

제약 충돌은 페이지 번호, 충돌한 요구, 충돌이 발생한 역할·영역을 기존 오류/수정 증거에 기록한다. 명시적 페이지 수·문구 보존·최소 크기가 함께 수용되지 않으면 기존 수정 예산 내에서 재배치하고, 계속 불가능하면 미해결로 보고한다. 사용자가 허용하지 않은 축약·분할·최소 크기 완화로 성공 처리하지 않는다.

높은 밀도를 해결하는 순서:

1. 중복 제목·불필요한 장식·큰 빈 헤더 영역을 줄인다.
2. 원본의 실제 의미에 따라 묶고 공간을 다시 배분한다.
3. 핵심 정보와 세부 설명의 비중을 구분한다.
4. 표·구조도·일정의 배치와 텍스트 줄바꿈을 다시 설계한다.
5. 사용자에게 허용된 범위에서만 문장 정리나 페이지 분할을 사용한다.
6. 불가능한 조합은 기존 실패·수정 처리로 드러낸다.

가독성 목표에 못 미친 결과를 넘침이 없다는 이유만으로 디자인 성공이라고 보고하지 않는다.

## 8. 비교 실험

### 8.1 생성 비교

실험 목적을 아래 두 종류로 구분한다. 목적별 결과를 합쳐 생성 경로의 개선율을 계산하지 않는다.

| 실험 | 고정·변경 조건 | 결론의 범위 |
| --- | --- | --- |
| E1 생성 경로 비교 | 현재 사진 제한·원문 보존·페이지 수·네이티브 요구까지 모두 고정. 작성 프로필만 변경 | 지침 정리의 효과 |
| E2 디자인 잠재력 비교 | 같은 사실·데이터 유지. 시각 자료·문장 재작성·분할 허용은 명시한 별도 실험 조건에 따라 한 축씩 변경 | 허용된 표현 범위에서의 잠재력과 비용 |

E1을 먼저 실행한다. E2의 조건은 첫 실험에서 자동 완화하지 않으며 실제 사용자 결과를 변경하는 권한으로 해석하지 않는다. E2에서도 승인된 자산 경로와 편집 가능한 네이티브 데이터 계약을 유지한다.

| 비교군 | 설정 | 목적 |
| --- | --- | --- |
| A | 현재 SVG 자유 생성 입력 | 기준 |
| B | 복원 가능한 이전 자유 생성 입력 | 과거 표현력의 근거 확인 |
| C | 필수 제약을 유지하고 스타일 입력만 정리 | 최소 변경의 효과 확인 |

처음에는 A/C를 필수 비교한다. B는 모델·참조·입력 구성을 복원할 수 있을 때만 추가하며, 복원 범위와 차이를 기록한다. 과거 단일 이미지나 다른 모델 결과를 동일 조건 기준으로 사용하지 않는다.

모델·설정, 내용·문구 보존 조건, 페이지 수, 캔버스, 템플릿·참조 이미지, 자산 사용, 출력 예산과 수정 예산을 고정한다. 네이티브 요구와 사용자 입력도 동일하게 유지한다.

최초 비교 대상은 동일 덱의 네 페이지다:

- 핵심 주장과 근거를 전달하는 본문.
- 실제 관계를 가진 구조도.
- 기간·병행·승인 시점이 있는 간트.
- 표·차트·해석이 결합된 데이터 페이지.

각 페이지의 기존 역할·덱 내 순서·인접 맥락은 동일하게 둔다. 하위 네 페이지를 새 덱으로 만들어 표지·마지막 페이지로 바꾸지 않는다. 기존 내부 평가 runner가 이 맥락을 보존할 수 없다면 전체 덱을 동일 조건으로 생성하고 선택한 네 페이지를 우선 평가한다.

현재 저장된 A4 결과는 문제 진단과 비교 자료로 사용하되, 새 C와의 단일 대조만으로 개선을 확정하지 않는다. A를 같은 환경에서 재생성해 생성 편차를 확인한다. A/C 실행 순서를 번갈아 배치하고 동일한 시간·환경의 대조 기록을 남긴다. provider 혼잡이나 외부 오류가 큰 쌍은 별도로 표시하며 숨기지 않는다.

대표 네 페이지는 후보 선별에 사용한다. 메시지·가독성·배치·브랜드 기준을 평가하고 전체 덱의 흐름 점수를 붙이지 않는다. 후보가 선별되면 동일 조건 전체 덱에서 제목·표지·연속 페이지·마지막 페이지와 전체 흐름을 평가한다. 전체 덱 결과 없이 기본 프로필을 전환하지 않는다.

품질 차이가 작거나 평가 이유가 엇갈리면 판정 보류로 처리한다. 필요한 범위에서만 대조를 추가하고, 제한된 반복 후에도 불안정하면 자동 전환하지 않는다. 표본·평가자·반복 횟수와 한계를 결과에 명시한다.

### 8.2 변환 비교

각 생성 결과의 게시된 SVG/PNG와 같은 결과를 내보낸 실제 PowerPoint 렌더링을 비교한다. 원본 디자인의 문제인지 변환 문제인지 구분한다.

최종 디자인 점수의 대상은 사용자에게 전달하는 PowerPoint 렌더링이다. SVG는 생성·변환 중 어디서 문제가 발생했는지 확인하는 근거로 사용한다. 다음 항목을 페이지별로 확인한다.

- 제목·범례·축·데이터 라벨의 표시, 대비, 위치와 단위.
- 글꼴 대체, 줄바꿈, 텍스트의 잘림·겹침 변화.
- 도형·연결선의 위치·방향과 실제 관계의 유지.
- 표의 행 높이, 셀 연결, 헤더 강조와 글자 배치.
- 원본의 핵심 메시지·크기 대비·여백이 최종 출력에서도 유지되는지.

데이터와 페이지 수만 같아도 시각적 표현이 약해졌으면 변환 문제로 기록한다. 이미지로 평탄화해 표·차트 편집성을 잃은 결과를 성공으로 대체하지 않는다.

전체 캔버스 확대는 A4에서 효과를 확인한 뒤 수행한다. 다음 확인 대상은 A4 세로와 16:9이며, 이후 비율이 크게 다른 정사각형·세로형 중 대표 입력을 선택한다. 유한한 표본을 모든 템플릿·크기의 품질 인증으로 보고하지 않는다.

### 8.3 디자인 평가와 필수 통과 조건

생성 방식 이름을 가리고 좌우 순서를 섞은 비교 자료를 만든다. 실제 보기 크기와 원본 확대 보기를 함께 사용한다. 방식 매핑은 별도 로컬 근거로 보관하고 평가 시 먼저 노출하지 않는다. 평가자가 내부적으로 방식을 아는 경우 그 한계를 명시한다.

| 디자인 항목 | 비중 | 판단 근거 |
| --- | --- | --- |
| 메시지 위계·강조 | 20% | 중심 메시지와 근거를 빠르게 파악할 수 있는가 |
| 가독성 | 20% | 읽기 어려운 글자·밀도·대비가 없는가 |
| 내용에 적합한 구성 | 20% | 비교·관계·기간을 올바르게 표현하는가 |
| 타이포·여백·정렬 | 20% | 비중·간격·정렬이 완성도 있게 연결되는가 |
| 브랜드 정체성 | 10% | 색상 외에도 특징적인 제목·선·표·이미지 표현이 유지되는가 |
| 덱의 흐름·변화 | 10% | 내용상 필요한 반복과 변화가 자연스러운가 |

각 항목은 1–5점이며 가중 평균을 사용한다. 현재·과거 방식의 점수는 실제 평가 전에는 기입하지 않는다. 모델 자체 점수만으로 승자를 정하지 않는다.

대표 페이지 선별에서는 흐름 항목을 제외하고 나머지 90%를 정규화한다. 전체 덱에서만 100% 기준 점수를 계산한다. 점수와 함께 페이지별 선호·문제·이유를 기록하며 평균으로 나쁜 페이지를 숨기지 않는다.

필수 통과 조건은 점수와 별개다:

- 요청한 사실·문구 보존 조건, 수치·단위·표 관계 유지.
- 실제 단계·의존관계 보존, 지원되지 않는 관계 추가 없음.
- 기존 SVG 계약과 측정·게시 digest 일치.
- 사용자 필수 가독성 제약과 기존 제품 최소 조건 준수, 명백한 잘림·겹침 없음. 권장 목표 미달은 별도 디자인 평가에 반영.
- PPTX의 실제 캔버스·페이지 수, 네이티브 표·차트 데이터 확인.
- PowerPoint에서 제목·범례·축·글꼴·배치의 실제 표시 확인.

첫 후보 선별 기준은 A/C 페이지 점수 차이 0.5점 이상과 네 대표 페이지 중 최소 세 페이지의 선호다. 0.5점은 파일럿 기준이며 평가 편차를 보고 조정 여부를 명시한다. 통계적 우월성 인증이나 자동 기본 전환 기준으로 사용하지 않는다.

전체 덱 전환 판정에서는 필수 조건 전부 통과, 브랜드와 가독성의 명확한 퇴행 없음, 전체 디자인 선호와 페이지별 근거, 시간·비용 근거를 함께 검토한다. 작은 차이·상충 결과·심한 단일 페이지 퇴행은 판정 보류다.

## 9. 속도와 추가 호출

첫 단계의 목표는 페이지별 작성·수정 호출 수를 늘리지 않는 것이다. 정적인 스타일 투영과 기존 내용 조언을 활용한다. 현재 모델을 그대로 두고 품질 변경과 모델 변경을 한 실험에 섞지 않는다.

실험 시 기존 timing·provider usage receipts로 준비, 작성, 기존 수정, 측정, 내보내기, 최종 완료까지의 시간을 분리해 기록한다. 입력 토큰, 참고 이미지 수, 재시도 수, 일시적인 provider 대기 여부도 함께 확인한다.

프롬프트 캐시 보존 조건:

- `author_messages`가 사용하는 덱 공통 디자인 입력은 덱 내 페이지마다 동일한 문자열로 유지한다.
- 페이지 내용·intent·수정 힌트는 변동 영역에 배치한다.
- 페이지별 입력 digest·추적 메타데이터는 공통 prefix에 추가하지 않는다.
- 참고 이미지의 선택·순서도 실제 입력 증거에 남겨 입력 토큰 감소와 이미지 변경 효과를 혼동하지 않는다.
- provider가 제공하는 cached-token 근거가 있으면 기록한다. 근거 없이 실제 캐시 적중을 확정하지 않는다.
- 입력·출력 토큰, 작성·수정 호출 수, 전체 시간의 변화를 함께 판단한다.

추가 호출이 없더라도 생성 시간이 같다고 가정하지 않는다. 첫 소표본에서는 시간 차이와 원인을 보고하며 p95나 전체 속도 개선률을 확정하지 않는다. 상위 설계의 장기 속도 목표도 이 문서로 달성 처리하지 않는다.

시각 검토를 추가해야 할 경우 기존 검토·수정 경로를 우선 활용한다. 검토 지침은 내용과 가독성 문제를 구체적으로 지적하도록 하고, 과감한 구도나 내용상 유효한 반복을 일괄 수정하지 않는다. 기존 수정 예산 밖의 호출을 먼저 늘리지 않는다.

## 10. 조건부 참고 자료 재제작

지침 정리로 충분히 개선되면 이미지 재제작을 진행하지 않는다. 참조의 한계가 남으면 같은 브랜드 정체성 아래 서로 다른 내용 표현 사례를 보강한다.

- 주장 중심, 핵심 숫자와 근거, 비교, 구조도, 일정, 데이터와 해석 등 의미가 다른 사례를 제공한다.
- 고정 좌표·배치 ID·필수 카드 수를 사례에 연결하지 않는다.
- 정확한 실제 데이터로 품질을 확인하며 원본 고객 문구·로고·수치를 재사용하지 않는다.
- 참조 파일 digest를 갱신하고 변경된 자료는 새 작업에만 적용한다.
- 입력 이미지 수를 무조건 늘리지 않는다. 관련 사례의 기존 선택 경로를 활용한다.

지침 변경과 이미지 변경은 별도의 비교로 분리해야 어느 변경이 효과가 있는지 알 수 있다.

## 11. 구현 연결 지점과 작업 순서

| 순서 | 대상 | 작업 | 완료 근거 |
| --- | --- | --- | --- |
| 1 | `author_slide_svg.py`, `author_prompt_split.py` | 최종 조립 입력·요구 조건 확인, 프로필 연결 계약 고정 | 중복·충돌 출처와 E1/E2 조건이 명시됨 |
| 2 | `project_style.py`, `svg_style_adapter.py`, 현대백화점 스타일의 SVG 투영 | 세 종류 스타일 요소와 내용 기반 강조를 반영 | 필수 계약·특징적인 표현 보존, 선택적 배치만 완화 |
| 3 | SVG admission·준비 checkpoint·receipt의 직접 연결 부분 | 지정한 필드·프로필·digest·실패 처리 적용 | staging→restore→prepare 의미 일치, 옛 snapshot의 기존 동작 유지 |
| 4 | 기존 SVG 평가 runner와 로컬 산출물 | E1 A/C 후보 선별 후 전체 덱 판정, B 복원 가능성 확인 | 눈가림·편차·브랜드·PowerPoint·시간 근거, 불확실 시 판정 보류 |
| 5 | 참고 이미지 또는 기존 시각 검토 | 확인된 부족만 개선 | 별도 비교로 추가 품질·비용 확인 |
| 6 | 기존 캔버스·내보내기 경로 | A4 가로·세로와 16:9 확인 | 실제 생성·PowerPoint 표시와 데이터 확인 |

첫 구현의 기본 소유 범위는 SVG 입력 투영·작성·준비 연결 부분이다. 실행 중인 서비스, 공급자 설정, 공용 스타일 YAML은 조사 결과 없이 먼저 변경하지 않는다.

## 12. 검증·전환·되돌리기

- 코드 변경 후에는 변경 경계를 직접 확인하는 가장 작은 기존 테스트 하나를 선택한다. 후보는 `test_svg_style_adapter.py` 등이며 실제 변경에 맞춰 확정한다.
- 제품 디자인 비교는 테스트 통과와 구분한다. 이 설계 작업에서는 실제 생성·성능 실험을 실행하지 않는다.
- 비교에서 효과가 없으면 새 프로필을 기본으로 전환하지 않고 원인 가설을 갱신한다.
- 효과가 확인되면 전체 덱과 캔버스에서 브랜드·가독성·출력·비용을 확인한 범위부터 새 작업에 적용한다. 대표 네 페이지의 결과만으로 기본 전환하지 않는다.
- 되돌리기는 신규 작업의 프로필 선택을 현행으로 복원하는 방식이다. 이미 생성·게시된 결과나 진행 중 작업을 덮어쓰지 않는다.
- 전체 저장소 테스트·빌드·성능 측정은 자동 실행하지 않는다. 필요한 실제 비교 범위를 개발 작업에서 명시한다.

## 13. 이번 설계의 완료 조건

- 최소 변경으로 시작하고 후속 기능은 근거에 따라 추가하는 순서가 명시됐다.
- 디자인·내용·캔버스·네이티브 데이터의 책임을 분리했다.
- 동일 조건 비교와 원본/변환 비교를 분리했다.
- 작업 재개·digest·캐시·기존 요청의 보존 조건을 정의했다.
- 디자인 평가, 필수 통과 조건, 속도 측정의 의미와 한계를 정의했다.
- 구체적인 구현 연결 지점과 단계별 완료 근거를 제시했다.
- 요청 조건이 다른 디자인 잠재력 실험을 분리하고, 대표 페이지 선별과 전체 덱 전환 판정을 구분했다.
- 브랜드 정체성·가독성 조건의 구분, 공통 프롬프트 캐시와 PowerPoint 최종 디자인 평가 기준을 보완했다.

구현과 실제 비교의 현재 근거는 다음 절에 기록한다. 디자인 품질 회복, 전 캔버스 품질 인증, 일반적인 속도 개선은 별도 근거가 있어야 달성으로 표시한다.

## 14. 구현 및 실제 비교 기록

### 14.1 구현된 계약

- `art-direction-v1@1`을 내부 비교에서만 선택한다. 미지정 및 `current-v4`는 기존 입력과 identity를 유지한다. SVG 합성은 자유롭게 수행하며 고정 레이아웃 ID나 Deck IR을 추가하지 않는다.
- 현대백화점과 기본 스타일의 입력을 한 곳에서 투영한다. 필수 브랜드·업로드 원본·사용자 조건·표/차트 데이터를 보존하고 페이지별 진단 정보는 공통 모델 프롬프트에서 제외한다. 추가 디자인 모델 호출은 없다.
- frozen style에 프로파일을 고정하고 durable staging을 통과시킨다. 새 admission의 bench gate와 이미 고정된 작업의 복원은 분리한다. 현재 DB의 소유 작업 snapshot이 재개 시 기준이며 충돌은 거부한다.
- 프로파일 및 실제 조립된 메시지·참조 이미지·모델/출력 설정의 digest를 새 프로파일 receipt에 기록한다. 기존 요청 receipt는 유지한다.
- 비교 헤더는 development의 실제 loopback 관리자 principal에만 허용한다. 공개 요청 필드, UI 설정, 전역 기본값은 추가하지 않는다. 실험 후 로컬 bench flag를 원복했다.

### 14.2 실행한 비교의 조건

현대백화점 스타일, A4 가로, 12페이지, 사진 제외, 같은 요청 모델 설정(`gpt-6-sol`)으로 세 덱을 실제 제품 경로에서 생성했다. 이 모델 표시는 provider receipt의 요청/설정 값이며 provider의 실제 effective model을 독립 확인했다는 뜻은 아니다.

| 덱 | 프로파일 | 설명 문장 조건 | 실제 생성 시간 | Provider 호출 |
|---|---|---|---:|---:|
| A | current-v4 | 문장 그대로 보존 | 422.80초 | 14 |
| C | art-direction-v1@1 | A와 동일 | 427.57초 | 16 |
| E2 | art-direction-v1@1 | 사실을 보존하며 설명 문장 재작성 허용 | 252.82초 | 13 |

시간은 DB 작업의 마지막 실행 시작부터 게시까지이며 단일 trial이다. 별도 추가 디자인 호출은 없지만 기존 repair/VQA 호출 횟수는 결과별로 다르다. E2는 입력 허용 조건을 바꾼 별도 실험이므로 E1의 프로파일 효과로 합산하지 않는다. 캐시 replay나 일반 성능 향상을 입증한 결과가 아니다.

- 36장 published SVG와 측정 receipt의 digest 및 실제 PNG digest 일치를 확인했다.
- 각 덱의 원본 네이티브 표 3개·차트 2개가 모두 승인됐고 값·순서·셀 데이터가 요청과 일치했다.
- 새 프로파일의 C/E2 작업은 gate를 원복한 뒤에도 고정된 프로파일로 생성됐다. C의 12페이지 모두 author profile과 input digest receipt를 확인했다.
- E2의 원본 사실 검토에서는 수치·표·관계·일정·승인·의존관계 누락이나 새로운 사실 추가가 관측되지 않았다. 이는 사실 검토이며 설명 문장을 문자 단위로 보존했다는 판정은 아니다.

### 14.3 실제 내보내기에서 발견한 수정

첫 PowerPoint 비교에서 source SVG의 차트 제목 굵기·정렬, 범례의 시리즈 의미, 막대 영역의 높이가 변환 중 달라졌다. `chart-furniture.3`에서는 원본에서 증명할 수 있는 제목/plot 정보를 선택적으로 native chart에 투영한다. 양수 단일 시리즈의 공통 0 기준과 막대 높이/값 비례를 확인할 수 있는 경우에만 axis/plot/gap을 조정하며 모호한 경우 기존 동작을 유지한다.

Office는 점별 색상이 있는 단일 시리즈 범례를 원본 시리즈명 대신 카테고리명으로 표시하는 사례가 있었다. 원본 범례가 명확한 해당 bar/column에서는 native legend를 숨기고 원본 텍스트와 색상 키를 편집 가능한 별도 객체로 보존한다. 차트와 workbook·값·카테고리·점 색상은 native 상태를 유지한다. 별도 범례는 차트의 시리즈명 변경과 자동 연동되지 않는다.

변환용 임시 SVG만 수정하며 published SVG와 frozen native sidecar는 변경하지 않는다. 기본 및 다중 시리즈/line/pie/모호한 범례 경로는 기존 처리를 유지한다.

### 14.4 품질 판정과 전환

최종 chart-furniture.3 PowerPoint 평가의 가중 점수는 A 3.975/5, C 4.067/5(+0.092), E2 4.150/5(C 대비 +0.083)였다. E1 대표 페이지 선호도 C 2/A 1/동률 1로 후보 선별 기준인 +0.5 및 3/4 선호를 충족하지 않았다. E2 전체 선호는 E2 7/C 2/동률 3으로 일부 페이지의 이점이 있으나 점수 개선 폭은 선별 기준 미만이다. 차트 수정 중 10장 중간 평가와 최종 평가를 별도 파일로 보존한다. 최종 5·12페이지의 제목·범례·축·plot은 실제 PowerPoint와 원본 비교에서 정상으로 확인됐다.

평가는 구현 정보를 아는 별도 에이전트가 수행했으며 완전 눈가림이 아니다. 단일 비교쌍·A4 가로에서 관측한 주관적 점수다. 일부 KPI·표·절차·일정 표현은 개선됐지만 전체 디자인 품질 회복이나 기본 전환을 확정할 근거는 부족하다. 기본 프로파일은 `current-v4`로 유지한다. E2의 문장 재작성 허용은 사용자 요청에 자동 적용하지 않는다.

완료 근거와 최종 실제 PowerPoint 파일/비교 그림은 `artifacts/svg-free-design-quality-recovery-20261002/trial-1/`에 보관한다. 다른 캔버스의 새 프로파일 품질 인증과 참조 자료 변경은 현재 결과에서 분리한다.

### 14.5 조건부 참조 선택 보완

현재 준비 경로는 표지 외 모든 페이지에 `content` 역할을 부여하며, 역할별 이미지가 하나인 현대백화점 스타일에서는 일정·데이터·구조도에도 같은 본문 이미지를 전달한다. 실제 세 작업의 frozen role별 이미지 digest 18개가 선언 및 builtin 파일 bytes와 일치했고 모두 같은 6개 역할별 참고 자료를 보유했다. 사용 가능한 내용별 참고 사례가 실제 작성에 전달되지 않는 문제를 확인했다. 다만 반복 밴드의 인과가 이미지에 있다는 판단은 아직 추론이다.

`art-direction-v1@2`에서 참고 이미지의 선택 역할만 내용에 맞게 바꾸는 별도 E3 실험을 진행한다. 기존 이미지·프롬프트 투영·사용자 문장 보존·네이티브 데이터·페이지 역할·예산·모델·캔버스를 유지한다. 표/차트는 data, 실제 기간이 있는 일정은 timeline, 명시 관계 구조는 architecture, 일반 주장과 절차는 content 사례를 선택하며 해당 frozen 참고 자료가 없으면 원래 역할로 복귀한다. 기존 `art-direction-v1@1`의 입력과 복원은 유지한다. 고정 레이아웃·새 참고 이미지·추가 모델 호출을 도입하지 않는다. E3 결과는 E1/E2와 분리한다.

### 14.6 E3 실행 및 필수 조건 판정

E3는 C와 public 요청 본문이 완전히 같은 12페이지 덱으로 생성했고, 프로파일 revision에 따른 참조 선택만 바꿨다. 생성 시간은 457.93초, provider 호출은 15회였다. 모든 작성/수정 attempt의 참조 이미지 수는 1이며 실제 digest가 선택된 frozen 역할의 이미지와 일치했다. 데이터 페이지 5·6·9·12는 data, 일정 7·11은 timeline, 구조도 10은 architecture를 사용했다. 네 작업의 frozen 참고 이미지 24개는 역할별로 동일하며 선언 digest 및 builtin byte digest와 일치했다.

전체 48장의 published SVG/측정 및 PNG digest, 원본 네이티브 데이터는 일치했다. 그러나 원본 품질 조건 통과는 47/48장이다. E3 10페이지는 이미 text_overlap(`svg-85`, `svg-89`) 실패와 verified=false로 기록돼 있다. 독립 시각 평가에서도 서로 다른 저장/집계 연결의 설명 두 줄이 너무 가까워 대응이 모호한 문제가 확인됐다. 이미지 bytes·데이터·출력 파일 검증과 시각적 필수 조건의 충족을 구분한다. 원본을 수정해 동일 조건 비교 결과를 바꾸지 않는다.

E3는 현재 참고 자료를 의미에 맞게 전달하는 기능을 검증했으나 전체 품질 개선과 전환 요건을 만족한 결과로 간주하지 않는다. 일정 표시 개선은 관측됐지만 반복 밴드의 이미지 인과나 새 참고 자료 재제작의 효과는 이 단일 trial로 확정하지 않았다. 따라서 두 새 revision 모두 내부 비교용으로 유지한다. 후속 참고 자료 재제작은 별도 가설과 실험 조건으로 다룬다.

### 14.7 격자 차트의 변환 및 fallback 보완

E3 5페이지에서는 원본의 격자 plot을 인정하지 않는 fallback 때문에 native plot이 위쪽까지 확대돼 별도 원본 범례와 겹쳤다. `chart-furniture.4`에서 공통 baseline(596), 막대 높이/값 비례(0.425), 동일 간격 격자(170)를 증명한 경우 원본 plot y=256..596, 상한 800, major step 400을 투영한다. 불규칙 격자·숫자축·모호한 baseline·동일값·음수 등은 추론을 강제하지 않는다.

plot을 증명하지 못한 경우에는 manual title/legend overlay를 제거하고 native 자동 배치의 공간 예약을 복원한다. 이 fallback에서는 기존 Office의 점별 색상 범례 의미 차이가 남을 수 있다. 원본 범례의 별도 편집 객체 보존은 plot이 증명된 경로의 보장이다. 이는 모든 차트 스타일의 완전 일치 인증이 아니다.

최종 좁은 검증은 API 경계 3개, admission/복원 39개, author/reference 11개, 실제 worker→PPTX 10개로 총 63개가 통과했다. 전체 suite·build·lint를 실행한 결과가 아니다. 공개 API/네이티브 readback/PowerPoint를 통한 실제 결과는 별도 아티팩트에 기록한다.

### 14.8 최종 비교의 디자인 판정

E3의 격자 차트 변환을 수정한 후 독립 점수는 C 4.067/5, E3 4.042/5(−0.025)이다. E3 전체 선호는 5/C 3/동률 4, 대표 선호는 3/C 1이다. 참고 사례 선택이 개선한 일정 세부 표시와 다른 페이지의 강조·가독성 저하가 섞여 있으며 전체 점수 개선 조건과 10페이지 필수 품질 조건을 충족하지 못했다. 단일 trial의 결과만으로 참조 선택의 일반적인 효과를 확정하지 않는다.

E2의 점수는 4.150/5로 유지됐으나 설명 문장 재작성 허용을 바꾼 별도 실험이다. 사용자 조건을 자동 완화하거나 E1/E3의 프로파일 효과로 합산하지 않는다. 기본 프로파일은 current-v4로 유지한다.

### 14.9 원본 baseline 경계의 회귀 수정

`chart-furniture.4`의 fallback 공간 예약 복원은 A 5페이지의 제목·범례를 기존 자동 배치로 되돌렸다. 막대의 공통 baseline과 높이/값 비례는 확실했으나 category 반 칸 추정이 frozen anchor를 5px 넘어서 탈락한 경우였다. `chart-furniture.5`에서는 그 증명을 통과한 차트에 한해 유일한 원본 baseline이 모든 막대를 포함하고 추정 양끝과 category spacing의 3% 이내로 일치할 때 원본 baseline span을 사용한다. 임의로 bbox를 clamp하거나 불확실한 축을 강제하지 않는다.

최종 worker→PPTX 검증은 11개가 통과했고 네 관련 모듈 합계는 64개다. 이전 .4 변환·시각 기록은 보존하며 최종 파일은 `chart-fidelity-verified` 변형에 별도로 보관한다.

### 14.10 원본 패널 여백과 category 라벨 공간

`.5` 실제 PowerPoint에서는 A 5페이지의 제목·단일 범례가 복원됐지만 월 라벨의 하단이 잘렸다. frozen chart anchor의 하단과 원본 category text bbox 하단이 모두 734여서 native chart의 라벨 공간에 여유가 없었다. 원본에는 해당 chart를 포함하는 유일한 불투명 패널의 하단 755가 확인됐다.

`chart-furniture.6`은 원본 plot을 증명한 column chart에서 이 패널과 하단 라벨을 확인하고, 추가 영역에 다른 text/shape가 없을 때만 임시 export payload 높이를 489에서 510으로 예약한다. title/plot/legend의 정규화 좌표를 함께 다시 계산해 absolute plot x=111..1077, y=362..695를 유지한다. 원본 SVG·frozen bbox·글꼴·lblOffset·표/차트 데이터는 변경하지 않는다. 패널이 없거나 공간이 점유됐으면 확장하지 않는다.

최종 worker→PPTX 검증은 13개가 통과했고 관련 네 모듈 합계는 66개다. `.6` 결과는 `chart-fidelity-complete` 변형으로 보관하며 이전 변환 결과는 유지한다.

### 14.11 구현·산출물 확인 결과

GPT-6.1 Sol 에이전트 두 명으로 작성 지침·참조 선택과 durable 복원·native chart 변환을 병행했다. 생성 실험의 요청/설정 모델은 모든 arm에서 gpt-6-sol로 고정했으며 실제 provider model까지 별도로 검증하지는 않았다.

`.6` 네 PPTX의 A4 가로 12장씩, 총 48장을 설치된 PowerPoint에서 읽기 전용으로 렌더링했다. 네 덱의 5·12페이지 native chart 8개를 확인했고 A 5페이지의 월 라벨 하단 잘림이 해소됐다. 독립 검토에서도 A/C 5페이지와 E2/E3 5·12페이지의 제목·단일 범례·월 라벨·값/단위/순서가 정상으로 확인됐다. 두 독립 리뷰는 최종 `.6` 경로와 회귀 해소 상태로 갱신했고 기존 디자인 점수와 E3 10페이지 실패를 유지했다. 최종 내보내기 후 실제 게시 source/native sidecar 48장의 불변도 확인했다. 네이티브 표 12개와 차트 8개의 데이터·캔버스·파일 digest 검증이 통과했다.

이는 디자인 품질 전환 승인을 뜻하지 않는다. 원본 품질 게이트 통과는 47/48장이며 E3 10페이지의 라벨 간격 실패가 남아 있다. A 3.975, C 4.067, E2 4.150, E3 4.042의 단일 trial 관측은 기본 전환 기준을 충족하지 않는다. 기본 `current-v4`를 유지하며 새 프로필은 내부 실험 경로로 제한한다. 다른 캔버스의 새 프로필 검증과 통계적 품질/속도 우월성은 이번 결과에 포함하지 않는다.

최종 근거:

- [검증 요약](../artifacts/svg-free-design-quality-recovery-20261002/final-verification.json)
- [생성 프로필 비교](../artifacts/svg-free-design-quality-recovery-20261002/trial-1/final-profile-comparison.html)
- [문장 표현 허용 비교](../artifacts/svg-free-design-quality-recovery-20261002/trial-1/final-wording-comparison.html)
- [내용별 참조 선택 비교](../artifacts/svg-free-design-quality-recovery-20261002/trial-1/final-reference-comparison.html)

### 14.12 후속 SVG v3 구현과 검증 범위

후속 구현은 Sol 이하 에이전트로 작성 품질, durable 계약, native 차트 fallback, 다중 캔버스 검증을 나누어 진행한다. 내부 selector `art-direction-v1@3`와 frozen `{id: art-direction-v1, version: 3}`를 추가하고 기존 current-v4/v1/v2의 입력·해시·복원 계약을 보존한다. v3는 v2와 같은 참조 선택·이미지 수를 유지하며 글자 폭·행높이·관계별 여백을 먼저 확보하고 수리 시 노드·라벨·연결선을 함께 배치한다. 제목과 정보 비중, 표·차트 직접 설명은 내용에 맞게 선택하며 원문·숫자·관계·브랜드·마스터를 변경하지 않는다.

E3 10페이지의 기존 실패는 실제 glyph ink가 1px 교차한 것으로 확인됐다. 새 A4 v3는 게시 원본 12/12장의 품질·canonical digest·PNG digest를 통과했다. 10페이지는 원문 12개 관계와 방향을 보존했고 문제 라벨의 glyph 사이 69px 여백을 확보했다. 이는 단일 새 결과의 공간 문제 해결이며 기존 trial-1의 원본이나 실패를 수정한 결과가 아니다.

기존 좁은 검증은 admission 49개, API header boundary 1개, author/reference 11개, chart fidelity 13개가 통과했다. 실제 제품 idempotency 재제출에서 동일 operation/presentation/job, 원본·frozen 참조·provider receipt 불변과 추가 호출 0회를 확인했고 같은 key의 다른 profile은 409로 거절됐다. 실제 중단 후 재개 검증은 별도의 소유 작업으로 수행하며 provider 결과 불확실성 검사를 우회하지 않는다.

`chart-furniture.7`은 원본 제목·단일 시리즈 범례와 다른 primitive의 공간 분리를 증명할 때만 임시 chart frame 상단을 예약한다. 원본 제목/범례를 편집 text/shape로 유지하고 native plot·값축은 자동 배치한다. worker fixture 2장의 source/sidecar/embedded workbook/category/value/색상 불변과 실제 PowerPoint 표시를 확인했다. 음수 native admission 및 안전한 source 공간을 증명하지 못한 원본의 기존 fallback은 그대로이며 전체 차트 유형의 완전 fidelity를 주장하지 않는다.

다중 캔버스 범위는 지원 preset 8개(16:9, 4:3, 9:16, 9:13, 1:1, 21:9, A4 가로, A4 세로), custom 310×190/190×310/250×250mm 및 비율 경계 400×100/100×400mm다. A4 가로는 동일 본문의 fresh v2/v3 12장 비교, 다른 대표는 표지·구조도·일정·표/차트 통합 4장, 경계는 표지·표/차트 통합 2장으로 중복을 줄인다. 총 새 v3 56장과 비교 v2 12장 계획이며 무한한 custom 크기 전체 인증이나 물리 최소/최대 크기의 가독성 인증으로 해석하지 않는다. 실제 결과와 전환 판정은 후속 증거가 완료된 뒤 별도로 기록한다.

### 14.13 독립 검토에서 발견한 의미 누락과 v4 보완

fresh A4 v2/v3의 실제 PowerPoint 24장 비교 점수는 v2 4.268, v3 4.276(+0.008)이었다. 전체 선호는 v3 6/v2 3/동률 3, 대표 페이지는 v3 2/v2 2였다. 양쪽 12/12장의 canonical 측정 품질과 표·차트 데이터·물리 캔버스·내보내기 전후 원본 보존은 통과했지만, v3 10페이지에서 채널 4개·서비스 5개·외부 연계/저장소 4개의 분류 목록이 누락됐다. 앞선 관계 12개 보존 확인은 전체 의미 보존을 뜻하지 않으며 이 결과는 필수 의미 게이트 실패로 정정한다. v3의 다른 캔버스 생성은 입장 전에 중단했고 기존 source·receipt는 보존했다.

raw active 시간은 v2 1199.860초, v3 253.625초였다. v2 12페이지의 첫 physical call은 약 901.8초 뒤 `public_provider_overload`로 끝나고 회복 호출을 거쳐 완료됐다. 일반 provider receipt의 succeeded 표기만으로 성공한 SVG 작성이라고 판정하지 않으며 checkpoint failure witness와 최종 게시 결과를 함께 사용한다. 이 과부하 교란과 단일 trial로 일반 속도 우위를 주장하지 않는다. provider stream의 완료 wall time을 기존 read timeout 수치로 보장하지도 않는다.

누락된 분류 목록은 원본 slidesMarkdown, outline.content, author의 source_content 입력에 그대로 존재했다. 원인은 입력 축약이 아닌 모델 출력 누락이다. 별도 selector `art-direction-v1@4`와 frozen version 4를 등록하고 v1/v2/v3 recipe·해시·아티팩트를 보존한다. v4는 명시된 분류·목록·정의·범례·문장을 생략하지 않으며 공유 노드를 활용한 관계 구성을 권고한다. 강제 layout ID나 고정 카드 수는 도입하지 않는다.

v4의 좁은 source classification 검출은 명시 comma 목록의 모든 구성원이 원본 관계 endpoint일 때만 적용한다. 분류명과 전체 순서가 같은 가시 text 블록에 있어야 하며 immediate tspan 줄바꿈은 허용한다. 멀리 떨어진 관계 노드와 분류명만 조합하거나 숨긴 글자로 통과시킬 수 없다. 기존 validation_error/repair 경로를 사용해 첫 누락은 재시도하고 반복 누락은 게시를 차단한다. 모든 종류의 의미 보존을 자동 인증하는 검출로 해석하지 않는다.

최종 v4 소유 layer의 기존 좁은 검증은 admission 59개, API header boundary 1개, author/reference 11개, chart fidelity 13개(총 84개)가 통과했다. v4 동일 본문 A4 12장의 실제 의미/PowerPoint 검증 후, 분류 목록 association까지 포함한 13개 캔버스 사례 56장 계획을 판단한다. 기존 v2의 +0.5를 v4 대비 새 고정 전환 기준으로 만들지 않으며, 기존 A/current 파일럿 조건과 단계별 후보 비교는 분리한다.


### 14.14 v5의 분류 연결 오탐 보완과 실제 일정 글자 충돌 검출

- 실제 v4 첫 구조도에는 3개 분류와 13개 구성원이 정렬된 제목·목록으로 이미 보존되어 있었다. 해당 수리는 실제 내용 누락이 아니라 동일 text 요소 조건의 오탐이었다. v5는 동일 요소 또는 가까이 정렬된 유일한 제목·완전한 순서 목록의 연결을 허용한다. 원거리 노드 이름의 단순 합산은 허용하지 않는다.
- v1~v4 계약은 유지하고 v5만 coverage v2와 association_counts를 사용한다. 기존 author 검사 11개, admission 검사 69개와 API 내부 경계 검사 1개를 통과했다.
- v4 일정표에서는 첫 설명의 실제 글자가 첫 막대에 걸렸다. v5는 원본 날짜·기간·의존 문구와 막대의 날짜·기간 비례가 증명된 페이지에 한해 실제 glyph outline을 측정한다. ink_cell 및 line box는 이 판단에 사용하지 않는다.
- 실제 문제 페이지에서 5개 막대와 156개 reliable glyph 중 부분 교차 2개를 검출했다. 원본 SVG·source digest와 기본 wrapper digest는 기존 결과와 일치했다. 첫 실패의 짧은 수리 지시와 두 번째 실패의 거부·checkpoint 저장을 확인했다. 기존 좁은 browser 검사 1개를 통과했다.
- chart-furniture.7의 title/single legend fallback은 기존 chart 검사 13개와 실제 PowerPoint 2장으로 확인했다. 이는 제품 덱 검증과 별도 fixture 증거다. 이 단계의 최신 좁은 검사 합계는 95개이며, 전체 suite 결과를 뜻하지 않는다.
- 새 A4 v5 12장을 먼저 평가한다. 필수 내용·native·실제 PowerPoint 시각 조건 통과 후 13개 대표 캔버스, 총 56장 계획을 진행한다. 무한한 사용자 지정 크기의 전수 시각 검증을 뜻하지 않는다.
- 이전 평가자의 archived 점수를 새 결과와 직접 차감하지 않는다. 기존 A/current PowerPoint도 같은 평가자가 같은 기준으로 다시 평가하며, 단계별 v2 비교와 기본 방식 비교를 구분한다. 기본 승격은 아직 하지 않았다.


### 14.15 v5 최종 대체 페이지의 내용 손실과 v6 보완

- v5 최종 A4 12장은 geometry passed였지만 verified는 11/12였고 구조도 p10은 축약 대체 페이지였다. 최종 대체 SVG/PPTX에는 관계 12개 중 앞 6개만 남고 책임 문장이 빠졌다. 따라서 v5 필수 의미·구조도 게이트는 FAIL이며, 다른 캔버스 입장은 하지 않았다.
- 두 실패 author 원본을 따로 확인한 결과, 각각 분류 3개·구성원 13개·관계 12개·책임 문장이 이미 보존되어 있었다. 분류 body가 여러 연속 text 줄로 나뉜 정상 구성인데 v5가 완전 목록을 한 body 요소로 요구해 오탐했다. 실제 source 누락은 author 원본이 아니라 최종 fallback에서 발생했다.
- v6 coverage/v3는 같은 parent/transform/anchor와 가까운 정렬·제한된 연속 간격·유일한 연결에서 여러 body 줄을 원본 순서의 완전 목록으로 합성할 수 있다. 원거리 노드 합산, 다른 분류 경계 넘기, 재사용과 숨긴 텍스트는 허용하지 않는다. v1~v5 작성 입력은 유지한다.
- 분류 validation_error가 source reflow가 아니어도 정확한 svg_source_classification_missing으로 전달되도록 수정했다. v6은 내용을 줄이는 svg_fallback_page를 금지한다. 기존 bounded repair와 최종 실패를 유지하며 fallback.py와 다른 mode의 원문 처리는 바꾸지 않았다.
- v6 변동 작성 지침에 원본 관계의 12 edge·13 entity와 공유 endpoint의 3개 분기 사실을 전달한다. 좌표·layout ID·노드 위치·카드 개수는 지정하지 않는다. 디자인 개선 효과는 실제 새 덱으로 평가한다.
- v6 일정 guard/v2는 내부 기간 label이 없는 막대도 원본 날짜·기간·행 위치와 유일한 공통 축척으로 증명한다. 기존 v5 guard 계약은 유지한다. 저장된 실제 측정값으로 v4 충돌 glyph 2개가 계속 검출되고, v5 일정의 5개 막대·162개 reliable caption glyph는 교차 0개로 확인됐다. 새 Chrome 실행 증거가 아니라 저장 측정값 재적용 증거다.
- 최신 좁은 검사: admission 79, API 내부 경계 1, author 11, chart 13, 기본 browser 1, terminal 1로 합계 106 PASS. 일부 기존 mock의 누락된 snapshot/aspect_ratio 필드를 보완한 뒤 해당 좁은 검사만 재실행했다. 전체 suite 결과가 아니다.
- 새 A4 v6 12장을 입장시켰다. 같은 평가자의 A/current·v2·v6 실제 PowerPoint 비교와 필수 내용·native·시각 검토를 통과하기 전에는 나머지 캔버스 입장 및 기본 승격을 하지 않는다.

### 14.16 v6 실제 평가와 native 범례 보완

- v6 A4 12장의 필수 원본 정보와 실제 구조도·일정 가독성은 통과했다. 다만 일정 caption이 여러 요소로 나뉘어 자동 guard가 not_applicable이었으며, 이를 자동 측정 교차 0으로 보고하지 않는다. 공유 노드 네트워크는 원본 필수가 아닌 디자인 선호다.
- 같은 평가자·같은 6항목 기준으로 A/current와 v6은 각각 4.337/5, v2는 4.268/5였다. 대표 4장의 선호도는 v6 1장/A 3장으로 자유 생성 대비 큰 품질 회복을 입증하지 못했다. 기본 승격은 보류했다.
- 실제 PowerPoint에서 p5/p12의 단일 series 범례가 4개 월 category 범례로 바뀌는 chart-furniture.7 결함을 발견했다. 데이터가 정확해도 필수 native 시각 조건은 실패로 판정했다.
- chart-furniture.8은 source 단일 범례가 증명된 경우 plot 예약 실패에도 편집 가능한 source 범례를 유지하고 native 범례를 끈다. title 예약과 multi-series/미증명 경로는 유지한다. 기존 chart 검사 14 PASS 및 원본 v6 p12 worker fixture를 실제 PowerPoint로 확인했다.
- v6 원본 12장을 재생성 없이 .8로 내보냈다. 실제 PowerPoint 12장, native table 3개/chart 2개, 값·category·series·원본 SVG/sidecar 보존을 확인했다. 비차트 10장 PNG digest는 이전 .7 결과와 동일했다. 이전 PPTX·PNG·점수는 덮어쓰지 않았다.
- v6은 queue 1.623초, active 581.484초, 실제 호출 14회(작성 12/수리 1/검토 1)였다. 성공 작성 1회가 490.813초여서 단일 실행으로 일반 속도 우위를 주장하지 않는다.

### 14.17 v7 자유 작성과 렌더 후 분류 검증

- v7은 모델에게 제공하는 style view에서 profile carrier를 제거하고 legacy digest를 재구성하여 기존 자유 SVG 입력·참조 sampler로 돌아간다. frozen profile7, staging, 입력 receipt 및 작업·캐시 identity는 구분해서 보존한다. v1~v6 입력은 유지한다.
- 분류 목록의 작성 단계 문자 형식으로 자유 구성을 차단하지 않는다. 렌더 후 실제 가시 text/ink와 source direct solid paint를 사용하여 완전 목록, 명시적 분류 영역, 또는 구성원 전부와 일치하는 세 가지 구분 색 범례를 증명한다. 구성원 순서는 강제하지 않는다.
- 가시성·측정·영역·색 의미가 미증명인 경우 첫 번째는 구성을 보존하는 짧은 수리 지시, 두 번째는 정확한 분류 오류로 거부한다. 숨김/CSS/상속 paint/모호한 중복 노드/원거리 이름 합산을 증거로 인정하지 않는다. geometry 성공을 의미 검증 성공으로 바꾸지 않으며 v7도 손실 대체 페이지를 금지한다.
- 최신 좁은 검사: admission 89, API 경계 1, author 11, chart 14, 기존 기본 browser 1, terminal 1로 합계 117 PASS. author fixture의 aspect_ratio 누락을 수정한 뒤 해당 검사만 다시 수행했다. 저장 A의 범례 없는 분류는 미증명으로 거부하고 저장 v6의 분류 3개·구성원 13개는 실제 목록으로 증명했다. 전체 suite 결과가 아니다.
- 활성 generation/operation·유효 lease가 없는 것을 전역 읽기로 확인한 뒤 API와 generation controller만 재시작했다. .8 export controller는 유지했다. 새 A4 v7 12장을 먼저 평가하며 나머지 44장·controlled resume·기본 승격은 이 시점에 아직 수행하지 않았다.

### 14.18 v7 시간 초과와 범위별 검증 진행

- A4 v7은 operation/job이 public_provider_call_timeout으로 failed, retryable=false에서 종료됐다. 원본 checkpoint는 p2~10의 9장만 완료됐고 p1/p11/p12는 source 없는 inflight다. 일반 usage receipt의 succeeded 표기를 원격 호출 성공이나 최종 게시 성공으로 사용하지 않는다. export/실제 PowerPoint는 0회다.
- 완료 9장의 SVG/PNG/실측 metrics를 별도 보존했다. p10은 실제 표시된 분류 3개·구성원 13개·관계 12개·책임 2문장을 유지했고 association은 measured_explicit_lists로 증명됐다. 실패 덱의 부분 증거이며 12장 수락으로 합산하지 않는다.
- source 없는 불확실 호출은 기존 collect guard로 차단된다. workspace retry는 retryable=false에서 거부하고 기존 recovery revision은 이 timeout을 해소하지 않는다. 같은 A4 새 key 제출이나 SQL reset으로 우회하지 않았다. 단순 동일 key replay 증거와 실제 cache resume 성공은 구분한다.
- paint bbox 모서리를 실제 원/타원/둥근 사각형 내부로 오인할 수 있는 proof 결함을 추가 보완했다. finite source geometry/축 정렬 transform/실측 quad 일치와 전체 label bbox의 실제 shape 내부 포함을 요구한다. 기존 author 모듈 11 PASS를 한 번 실행했다. 완료 9개의 unchanged source/metrics 재적용 결과는 모두 동일하며 curved-shape 실제 생성 증거로 주장하지 않는다.
- 전역 generation/operation·유효 resource lease 0을 확인한 뒤 API/generation controller를 다시 로드하고 v7 코드를 동결했다. old A4 불확실 checkpoint는 보존했다. .8 export controller는 유지했다.
- 남은 서로 다른 캔버스 44장은 별도 입력 범위의 기능 검증으로 진행한다. 기존 큰 품질 향상 pilot 기준 미달과 기본 승격은 별도로 보류한다. 모든 캔버스·실제 재개·A4 최종 품질이 아직 완료됐다는 뜻이 아니다. controlled square는 watcher를 먼저 시작한 뒤 입장하고, 불확실 호출이 없는 running 경계에서만 cancel/retry CAS를 허용한다.

### 14.19 정사각형 및 16:9에서 발견한 실제 경계

- custom-square-v7은 cancel/retry 없이 svg_source_classification_missing으로 자연 실패했다. source/png 4개가 있어도 최종 의미·geometry 통과는 3/4다. 최종 p10은 공유 13노드·12번호 edge·명세·책임 문장을 보존하며 세 가지 legend/node 색도 직접 source fill이 일치했다.
- 분류 검출은 generic CSS paint ownership인 has_owned_paint=false를 SVG rect 제외 근거로 사용해 실제 swatch 3개와 node rect 13개를 모두 버린 오탐이다. 별도로 13개 node label의 실제 font16px는 required16.256px보다 작다. 색 분류 오탐을 고쳐도 geometry 실패를 통과로 바꾸면 안 된다. 첫 분류 수리 지시가 geometry 실패 수치를 누락한 점도 보완 대상으로 확인했다.
- wide-16x9-v7은 게시됐지만 p11에 text overlap과 provider_repair_unavailable이 남아 source quality는 3/4다. 이를 source4/4 통과로 보고하지 않는다. ordinary SVG의 마지막 generic quality 실패를 v7에서도 거부하도록 보완한다. 기존 버전의 게시 동작은 유지한다.
- wide PPTX는 table1/chart1·원본 값·단일 series·4category·원본 보존을 확인했지만, 실제 PowerPoint에서 p12 월 axis 글자 하단이 잘렸다. native 값 성공과 native 시각 실패를 분리한다. source SVG의 월 글자는 정상이며 source 재작성 대신 export 경계를 수정한다.
- controlled watcher는 action0에서 중지했다. 전체 stage의 logical-call 미확정과 runtime reservation reserved/uncertain을 확인하도록 강화했다. 정상 running의 operation admission slot lease는 개별 provider 호출 lease와 다르므로 0을 강제하지 않는다. old A4 unknown3와 custom terminal은 계속 차단한다.
- 두 case의 원인 해결 전에는 추가 matrix 입장을 보류했다. 이전 성공·실패 산출물과 receipt를 덮어쓰지 않으며, 기본값 승격과 품질 향상 주장은 계속 보류한다.

### 14.20 실제 오탐·실패 게시·축 clipping 보완

- v7 source paint proof는 generic HTML/CSS owned-paint 플래그 대신 검증된 canonical SVG direct solid fill과 실측 가시성·불투명도·축·영역·모호성 조건을 사용한다. unchanged custom source/metrics 재적용에서 세 분류와 13구성원의 explicit color legend가 증명됐지만, font16px<16.256px의 geometry 실패는 그대로 유지했다.
- 분류 수리 지시에 compact geometry 실패와 실측 minimum/정수 올림값을 함께 포함했다. v7의 어떤 수리 호출에도 본문 최소 정수값을 보충하되, 최초 legacy 모델 입력과 v1~v6 수리는 유지했다. 정확한 16.256→17px와 초기/frozen identity를 기존 관련 test 1개로 확인했다. 첫 fixture의 stub floor 재설정 순서를 교정한 뒤 해당 한 test만 재실행했다.
- v7 마지막 render의 generic quality 실패를 ordinary SVG에서도 svg_source_quality_failed로 거부한다. source 측정 floor나 실패 결과를 낮춰 통과시키지 않으며 기존 no-lossy-fallback과 연결한다. 기존 author 모듈 11 PASS는 앞선 3점 보완 후 한 번 실행했다.
- wide 일정의 알려진 glyph는 교차 0이지만 qid11 nonspace32개 중 중점 U+00B7 두 개의 reliable ink가 없어 completeProof=false다. 따라서 aggregate overlap은 오탐으로 확정하지 않고 failclosed로 유지한다. global text overlap/probe는 변경하지 않았다.
- chart-furniture.9는 source exact/unique/ordered/uniform category가 증명된 single-series column에서 frame bottom 접촉 및 source-panel 예약 미적용인 경우만 원본 category text를 편집 가능한 객체로 보존하고 native category tick label을 숨긴다. native 축·category/workbook·plot·frozen frame·제목·단일 범례는 유지한다. 분리된 축 텍스트는 데이터 category 변경 시 자동 동기화하지 않는다.
- 기존 chart 14개 PASS와 신규 actual wide 회귀 1개 PASS를 확인했다. 신규 fixture 선언의 sidecar 혼입을 교정한 뒤 그 회귀만 재실행했다. unchanged source worker fixture는 실제 PowerPoint 1장으로 월4개 글자 clipping 해결을 확인했다. 이어 wide source4장을 .9로 재생성 없이 제품 export/PowerPoint 확인해 원본·sidecar/native 값/series 보존 및 native axis 정상 표시를 확인했다. source quality3/4 실패는 별도로 유지한다.
- 활성 작업이 없음을 전역 읽기로 확인한 후 export controller와 generation controller만 순서대로 다시 로드했다. 신규 timeout repurchase action은 아직 0이며, 기존 A4 unknown3 및 모든 archived source/receipt는 보존했다.
- 실제 provider-settings는 codex/CODEX_MODEL=gpt-6-sol이었다. 벤치의 별도 override가 아니라 정상 설정이다. 초기 사용자 6.1 변경 요청에 맞게 현재 지원 목록과 기존 resolver 경계를 확인하고 있으며, 이후 6.1 결과를 앞선 6 Sol 비교에 섞어 속도·품질 효과를 주장하지 않는다.

### 14.21 실제 모델 설정과 새 캔버스 증거

- 기존 admin provider-settings API로 CODEX_MODEL만 gpt-6.1-sol로 변경했다. 재조회에서 해당 값과 다른 설정의 불변을 확인했다. CODEX_REASONING_EFFORT는 medium이다. 이전 6 Sol 결과는 보존하며 새 결과와의 차이를 v7 지침만의 효과로 해석하지 않는다.
- 6.1 및 해당 snapshot alias에만 reasoning effort 호환을 적용했다. none/minimal은 low, ultra는 max로 정규화한다. 다른 모델과 공개 설정 enum은 유지했다. 기존 관련 test 1개가 통과했다. medium은 기존 실행 경로와 같으며 실제 새 생성 요청·provider receipt 모두 6.1 Sol이다.
- standard-4x3-v7과 tall-9x16-v7은 각 4장 source quality passed/verified 4/4, 실제 PowerPoint 4장, 필수 분류·일정·수치, native 표 2개와 차트 1개, source 보존을 통과했다. 표 2개는 일정 work/date/duration 표와 필수 월별 표이다. 분류도는 13개 노드·12개 관계·세 범례·책임 정보 2개를 유지한 공유 network이며 분류 association 3개가 증명됐다. 각 case의 실제 provider 5호출은 모두 6.1 Sol이고 author repair는 0이다.
- wide-16x9-v7의 동일 source를 chart-furniture.9로 다시 내보냈다. 실제 PowerPoint에서 월 축 4개 clipping이 해결됐고 title·단일 legend·값을 유지했다. owned operation/attempt/frozen bundle/runtime manifest·파생 identity digest·artifact SHA 연결의 8조건을 독립 확인했다. 이 export 보완은 해당 기존 source quality 3/4 실패를 성공으로 바꾸지 않는다.
- 시간 초과 A4용 svg-provider-timeout-repurchase/v1 경로를 구현했다. exact owner/CAS/manifest와 종료된 로컬 ledger·lease를 검사하고 unknown checkpoint 3개에만 원본과 소비된 첫 호출을 archive한다. 완료 9개는 recovery action에서 수정하지 않는다. 새 page별 호출은 ordinal 2의 한 번이며 overload도 예산을 소진한다. 기존 logical-call ledger는 수정하지 않는다. 관련 workspace 검사 40개가 통과했고 실제 PostgreSQL 읽기 전용 manifest에서 완료 cache 9개·unknown 3개·추가 author 상한 3개를 확인했다.
- A4의 기존 원격 호출 종료는 확인하지 못했다. 실제 재구매 action은 사용자 승인 전 실행하지 않는다. 이후 기존 classify 단계는 완료 page의 분류 metadata를 재저장할 수 있으므로 전체 재개의 검증 대상은 row 전체의 불변이 아니라 canonical SVG/PNG/input/author/reference bytes 보존과 완료 page provider 재호출 0이다.

### 14.22 새 모델의 디자인 평가와 실행 경계

- 동일 6항목 rubric과 가중치로 원본 PNG 8장과 실제 PowerPoint PNG 8장을 독립 검토했다. standard-4x3-v7의 대표 4장은 절대 4.464/5, tall-9x16-v7은 4.406/5이다. 공유 network와 캔버스별 일정 배치 변화가 확인됐지만 이전 A/current와 모델·캔버스가 다르므로 +0.5 pilot 달성, v7 단독 효과, 모델 단독 효과 또는 점수 간 직접 우열을 주장하지 않는다.
- 개선 후보는 4:3 관계 return connector 추적 비용과 범례 읽기 비용, 세로 일정의 중복 표현·작은 축과 표 글자, 세로 표지의 큰 공백이다. native 차트의 title 중앙 이동·plot 높이 차이도 디자인 판단에 남긴다. 새 필수 template나 고정 layout 제약으로 덮지 않는다.
- tall-9x13-v7도 source quality passed/verified 4/4, 실제 PowerPoint 4장, 필수 facts·native 표 1개/차트 1개·원본 보존을 통과했다. 실제 provider 6호출은 6.1 Sol이며 repair 1회를 사용했다.
- 다음 입장 전 running operation·active reserved logical call·valid provider runtime lease·valid operation slot 모두 0임을 PostgreSQL 읽기 전용으로 확인했다. 관리 script로 API와 generation worker만 재시작했고 export worker와 다른 서비스는 보존했다. 새 timeout recovery/counter와 6.1 effort 호환을 로드했다. custom-portrait는 watcher를 먼저 시작한 별도 정상 cancel/retry 실험이며 A4 원격 종료 미확인 재구매 승인과 분리한다.

### 14.23 관측된 디자인 한계와 정상 재개 실험

- tall-9x13의 동일 rubric 절대 평가는 4.360/5이다. 분류 의미·관계·책임 정보는 보존됐지만 p10은 공유 entity network 대신 12개 독립 관계 행으로 돌아갔다. 앞선 두 canvas의 공유 graph 개선을 모든 canvas에 대한 보장으로 확대하지 않는다.
- 다음 candidate의 최소 개선 제안은 짧은 content intent이다: 관계가 하나의 시스템을 설명한다는 의미와 공유 entity/branch 추적을 우선 고려하고, 일정 날짜·기간·병행을 함께 읽히는 grid로 구성하며 중복 표현은 필요에 따라 선택한다. 원문 facts를 보존하고 고정 layout·좌표·개수를 강제하지 않는다. 이는 품질 잠재력에 관한 추론이며 새 후보의 실제 속도·repair 비용은 미측정이다. v7의 최초 legacy 입력과 기존 산출물은 유지한다.
- custom-portrait 정상 재개 watcher는 완료 checkpoint 3개/author inflight 0/full provider unresolved 0/runtime reservation 0인 창에서 exact-version cancel 1회를 시도했다. API HTTP500으로 중단됐고 자동 재취소·retry는 없었다. PostgreSQL 확인에서 cancel_requested_at NULL, cancel/retry receipt·event 모두 0이며 원래 operation/job이 자연 완료됐다. 이 산출물은 기능 검토에 사용하지만 성공한 cancellation/resume 증거로 계산하지 않는다.
- 기존 wide-v7 ledger의 failed/unavailable 2개는 로컬 reconciliation됐으나 원격 종료/과금 확정 증거가 아니다. 원본·실험 cohort를 보존하고 새 key로 불확실 호출을 우회하지 않는다. A4 추가 호출도 별도 사용자 승인 대기다.

### 14.24 정사각형 오탐과 사용자 지정 세로 결과

- square-1x1-v7은 svg_source_classification_missing으로 종료됐다. 실제 source에는 명시적인 3분류·13개 member·12관계·책임 정보 2개가 있었고 폰트 floor 실패는 없었다. 마지막 canonical SVG와 실제 metrics에서 geometryPassed=true였다.
- 오탐 원인은 외부 연계 범례의 pale-blue pill과 navy 문자색을 동등한 분류 key로 합친 것이었다. 실제 painted legend/enclosure key가 있으면 해당 색만 증거로 사용하고, 없을 때만 문자색 fallback을 사용하도록 한 곳을 수정했다. 경쟁하는 실제 paint key가 2개 이상이면 여전히 실패한다. 기존 동일-parent/축 정렬/full-shape/visibility/member duplicate 일관성 조건과 legacy/v1~v6 동작은 유지했다.
- 기존 관련 test 1개에서 contrasting text + unique painted key 통과, competing painted key 실패를 확인했다. 저장된 square 마지막 source·actual metrics를 메모리에서 재적용한 결과 3분류 모두 explicit_color_legend로 증명됐다. source·metrics 파일 bytes와 실제 failed job은 수정하지 않았고 provider 호출은 0이다. 이 결과 자체는 게시·PPTX 성공이나 복구 완료가 아니다.
- custom-portrait-v7의 자연 완료 4장은 source quality passed/verified 4/4, 실제 PowerPoint·필수 facts·native 표 1개/차트 1개·워크북 exact·원본 보존을 통과했다. p4의 범례와 740 label은 근접하지만 실제 PNG의 visible ink는 3px 간격으로 겹치지 않았다. 디자인 절대 평가는 4.351/5이며 관계 표의 공유 entity 추적과 일정의 중복 정보 밀도는 남는다. cancellation/resume 성공 증거는 계속 미달이다.

### 14.25 정상 권한 복구와 캐시 재측정 계약

- 정상 cancel의 HTTP500은 v2 generation의 captured local-development tenant와 v1 workspace action의 service tenant가 달라 DB deferred admission-authority trigger가 거절한 것이었다. exact same-owner public-generation operation의 cancel/retry에만 기존 검증된 loopback bridge와 captured tenant principal을 재사용했다. 모든 known admission state의 조회는 기존 action state/CAS/blocked guard와 exact receipt replay를 보존하기 위한 것이며, 다른 tenant·owner·operation 접근은 계속 거절한다. DB trigger/RLS/privileges는 변경하지 않았다. 관련 선택 검사 1개가 통과했다.
- svg-source-remeasure/v1 / svg-cache-only-source-remeasure/v1은 서버가 판단한 두 오탐 사유만 지원한다: 4장 classification_legend_paint 또는 2장 native_single_series_title_unit. 대상은 index 1이고 모든 page의 frozen input/style/reference/source/PNG/author/browser 증거, 종료된 로컬 lease, reported+succeeded+reconciled provider ledger를 검증한다. unknown/reserved는 허용하지 않는다. old full quality/row를 archive하며 pending 시점의 failed quality를 성공으로 바꾸지 않는다.
- producer는 완료 page를 수정하지 않고 대상에만 marker를 저장한다. consumer는 fanout 전에 전체 cache witness를 고정하고 cache 손상 시 작성 loop 전에 실패한다. 대상은 기존 실제 render/measure/native/timeline/class/final quality guard로 1회 직접 재측정하며 source와 PNG bytes의 동일성을 강제한다. 수정·추가 작성·수리 요청·실패 재진입은 거절한다. author 0과 별개로 기존 deck VQA 예산의 호출은 가능하다.
- producer 관련 선택 검사 5개와 consumer 관련 선택 검사 1개가 통과했다. PostgreSQL 읽기 전용 manifest는 square/count4와 boundary-wide/count2, boundary-tall/count2 모두 안전 조건을 통과했다. 실제 action·복구·PPTX 성공은 이 준비 단계에서 아직 검증하지 않았다.
- A4 portrait 4장은 source quality verified 4/4, actual PP의 분류·일정·지표·native 표/차트/워크북 exact와 원본 보존을 통과했다. 승인 시점 누락 초기 판정은 동일 SHA의 actual PNG 재열람과 XML의 4개 editable text frame으로 반증·정정했다. 제품 수정이나 재렌더는 없었다. source 디자인 점수는 4.444/5, actual PP는 4.434/5이다.
- ultrawide21x9는 실제 외부분류 명칭의 연계 단어 생략과 author response 미확보로 실패했다. ledger unavailable/failed 2개는 원격 종료 확정 증거가 아니므로 cache-only 재측정·새 key 재생성 대상에서 제외한다. boundary 두 case는 source geometry와 표·chart 값이 모두 보존됐지만 explicit 단위 접미를 native series name으로 증명하는 기존 경계에서 실패했으며 해당 두 단계 proof를 보완 중이다.


### 14.26 재시도 전략 결합과 원본 캐시 보존 (2026-10-02)

- 단일 시리즈 제목의 단위 문구와 짧은 막대 내부 라벨 위치를 기존 native 계약으로 판별하도록 보완했다. 저장된 경계 가로·세로 원본의 native 표/차트 각각 2개가 재판정에서 통과했고, 기존 좁은 검사 1개가 통과했다. 실제 복구 PPTX 검증은 별도다.
- Square 최초 캐시 복구 요청 및 동일 요청 재생은 HTTP 200이었으나, 새 작업에 원본 operationId가 남아 렌더링 전에 generation_strategy_capability_missing으로 실패했다. 추가 작성·렌더링 호출은 없었으며 이 실패를 복구 성공으로 집계하지 않는다.
- 재시도는 원본 operationId와 봉인을 검증한 뒤 새 operationId와 receiptDigest만 다시 결합한다. 전략·capabilities·decision 및 동결 SVG 요청은 보존한다. 독립 읽기 검토와 기존 좁은 검사 5개가 통과했다.
- svg-source-remeasure-dispatch/v1는 위 실패, 원본 봉인/캐시/승인 해시, 추가 provider 호출 0, pending 상태 및 소유권/CAS가 모두 확인된 경우에만 사용한다. 원본 classification archive와 source_operation_id를 유지하고 별도 dispatch lineage를 기록한다. API와 생성 워커에 반영했고 실제 Square 재시도 확인 중이다.
- A4 landscape의 불확실한 원격 호출 3개에 대한 재구매는 사용자 답변 대기다. 새 키로 우회하지 않는다.


### 14.27 실제 캐시 복구 첫 결과 (2026-10-02)

- Square의 별도 dispatch 복구 작업이 실제 PostgreSQL 제품 경로에서 succeeded/published가 되었다. 원본 SVG/PNG/input/author/reference/style, 이전 provider ledger 및 quality archive가 보존되고 4/4 품질 통과, 추가 author 0, 기존 최종 검토 VQA 1이었다. 새 operationId를 결합하는 검증 영수증 변경은 의도된 예외다.
- 별도 cache-remeasure-r1 폴더에서 Square 4장 PPTX와 실제 PowerPoint readback을 완료했다. Native 표 1개/차트 1개, 월 축/단일 시리즈 범례/필수 사실 및 source/sidecar 불변을 확인했다. 독립 시각 평가는 별도로 누적한다.
- 가로 경계 크기 2장도 formal action 및 동일 요청 재생 후 실제 출판/품질 2/2를 통과했다. 추가 author 0, VQA 1이고 원본 및 이전 ledger/archive를 보존했다. PPTX 확인은 별도다.


### 14.28 경계 PPTX와 정상 취소 경로 (2026-10-02)

- 세로 경계 복구 2장도 실제 품질 출판을 통과했고 원본·ledger·archive를 보존했다. 추가 author 0/VQA 1이며 실제 PowerPoint에서 표값·단위·해석을 확인했다. 독립 평가 원본 4.360/실제 4.300으로 native 월 열·헤더 줄바꿈에 따른 가독성 한계가 있다.
- 가로 경계 복구의 원본 SVG 품질 2/2는 통과했으나 실제 PPTX 표 마지막 행이 divider와 첫 해석에 겹쳤다. Root와 독립 평가가 확인했고 mandatory 완료에서 제외했다. 원본 4.350/실제 기존본 3.885이며 실패 산출물을 보존한다. 사용자 지정 canvas의 source fontPt를 고정 0.75로 역변환한 재확대와 native 셀 여백을 내보내기 담당이 보완 중이다.
- 사용자 지정 가로 캔버스의 단일 watcher를 생성 전에 시작했다. 완료 3/4장에서 cancel HTTP 200, 실제 operation cancelled v16, admission cancelled, unresolved logical/runtime/provider lease/slot 0을 확인했다. 표준 retry HTTP 200 이후 최종 결과와 저장된 3장의 재사용은 별도 확인 중이다.
- 이전 사용자 지정 정사각형은 실제 13개 텍스트가 16px<16.256px floor였고 target author ordinal 2를 소진했다. 물리/논리 호출 6개 모두 reported/succeeded/reconciled, unknown 0이다. 기존 job의 3번째 호출·cache 품질 면제 대신 정상 admission으로 별도 GPT-6.1 후속 specimen 1회를 허용했다. 기존 실패·IDs·해시·원본을 보존하고 모델/수정 혼재로 속도·품질 효과를 분리해서 주장하지 않는다.


### 14.29 실제 정상 재개·설정 원복 (2026-10-02)

- 사용자 지정 가로 캔버스는 취소 commit 이후 표준 retry에서 실제 succeeded/published 4/4, 실제 PowerPoint 4장 확인을 완료했다. 취소 전 완료된 3장은 SVG/PNG/input/전체 author 근거 바이트를 보존했다. 미완료 1장은 기존 source/input/author 응답으로 최초 PNG만 만들었다. 추가 author/repair 0, 추가 VQA 1이고 모든 provider logical/physical reported/succeeded, unknown/reserved/uncertain/유효 runtime lease 0이다.
- 재개 전후 quality digest 차이는 composition/classification 파생 메타데이터다. measurement/geometry/passed는 모두 true이며 quality 전체 바이트 불변으로 주장하지 않는다. 새 전략 봉인은 새 작업 ID에 맞고 결정 내용은 보존됐다.
- 독립 실제 PowerPoint 점수는 4.558/5(원본 4.572)다. 공유 13-node/12-relation 그래프와 원문 날짜·기간·선행조건을 결합한 일정 grid를 관측했다. 사례별 구성 개선이며 모델·캔버스 혼재로 V7의 독립 효과나 +0.5 pilot 달성을 주장하지 않는다.
- 마지막 별도 정사각형 검증본 접수 후 임시 bench 설정을 원복했다. API managed 환경 CANVAS_BENCH_ENABLED=false 확인 및 API-only 재시작을 완료했고 생성/내보내기 워커와 다른 서버는 보존했다. 기본 authoring profile은 legacy를 유지한다.
- Native 표 내보내기 수정은 source canvas의 실제 pt_per_px와 원문 cell text bounds에서 증명된 세로 inset을 사용한다. Backend projection/scene/worker adapter만 변경하고 vendor serializer/MANIFEST/runtime .9는 변경하지 않는다. 별도 adapter r1 파일 SHA/작업 PID/원본/최종 PPTX 근거로 적용을 확인할 계획이다.


### 14.30 별도 정사각형 검증본의 실제 가시성 실패 (2026-10-02)

- 정상 admission의 별도 GPT-6.1 specimen 4장은 기존 source quality 4/4를 통과해 출판됐으나, Root·독립 시각 평가에서 p2 관계 설명 3개의 두 번째 줄이 나중에 그려진 opaque node fill에 가려졌다. 원본 SVG와 실제 PPTX에 동일한 문제가 있어 native 변환 손실과 분리한다. 원문 text 존재를 실제 가시성으로 대체하지 않고 mandatory FAIL로 유지한다. 추가 생성은 하지 않는다.
- 기존 사용자 지정 정사각형의 font floor 실패와 이 별도 specimen의 occlusion 실패·IDs·해시·산출물을 모두 보존한다. Source quality 영수증을 사후 성공/실패로 덮어쓰지 않고 별도 가시성 증거를 기록한다.
- 직접 원인은 browser candidatePaintsAt의 SVG rect 제외 및 renderer의 generic occlusion 미게이트다. V7에 한해 canonical source paint order, 기존 solid/finite-axis/rounded-rectangle 판별, 실제 reliable glyph ink를 재사용한 source 가림 guard를 보완한다. 먼저 그려진 노드 배경은 가림으로 판정하지 않는다. 근거가 부족한 glyph/shape는 실제 가림 확정으로 만들지 않고 unproven/failclosed 범위를 표시한다.
- 임시 bench는 계속 꺼져 있고 기본 legacy 유지다. Guard 검증은 기존 좁은 case와 동일 원본의 별도 읽기 전용 local renderer probe 1회로 한정하며 provider/게시/checkpoint 변경은 하지 않는다.
- 기존 기록만 집계한 speed-summary-v7.json은 14 specimen/13 대표 canvas, 요청 60장(대표56+추가4), 저장된 물리 usage84 및 캐시 복구 VQA3을 분리한다. 새로운 속도 benchmark/provider 호출은 없고 모델/수정 혼재·remote outcome unknown을 숨기지 않는다.


### 14.31 Backend export identity의 실제 캐시 재사용 확인 (2026-10-02)

- Backend projection/scene/worker 3파일을 수정하고 기존 좁은 검사 1개를 통과했지만, r1 새 export operation에서 가로·세로 PPTX와 PNG SHA가 기존 .9 산출물과 각각 완전히 같았다. Root ZIP readback에서도 가로 표 native font 16.74/18.83pt와 원래 margins가 동일했다. 새 adapter가 실제 출력에 적용됐다고 주장하지 않는다.
- 직접 원인은 converter_digest가 vendor MANIFEST만 포함해 backend adapter 변경이 engine/cache identity에 없었던 것이다. 실제 재사용 문제가 확인됐으므로 기존 공통 helper에 svg-export-adapter-projection/v1 domain, vendorManifestSHA 및 projection/scene/worker 3개 파일 SHA를 결합한다. 기존 vendor 파일 integrity 검사는 보존한다. Vendor .9/MANIFEST/serializer는 변경하지 않는다.
- API admission identity, frozen export bundle 및 export worker 검증이 같은 helper를 사용한다. 새 API/export 프로세스 이후 새 export admission으로 새 engine identity/bundle을 만들고, 기존 frozen bundle·실패 산출물을 수정하거나 캐시를 삭제하지 않는다. Adapter byte 변경 시 digest 변화와 기존 native font/margin/factor 계약은 동일한 좁은 검사에서 확인했다.
- glyph 가림 guard의 별도 local renderer 1회는 원본과 같은 PNG SHA, 3개 두 번째 줄의 reliable glyph 완전 커버리지를 확보했다. Browser metrics의 source digest는 wrapper binding이고 quality canonical source digest와 분리해야 한다. Wrapper contract와 full glyph containment를 반영한 마지막 검증 및 저장 metrics 재적용을 진행한다. 추가 provider·게시·원본 checkpoint 변경은 없다.


### 14.32 최종 적용·현재 수락 범위 (2026-10-02)

- r2 fresh export admission의 backend adapter digest는 1cb2207fa49a763e0501aaed3bd376c16c68f7e9d2d6eafe8e3254deff8e407d다. 원본/vendor .9는 보존했다. 가로 경계 실제 PPTX는 원래 16.74/18.83pt에서 14.17/15.94pt로 바뀌고 새 SHA가 기존본과 달라 실제 적용을 확인했다. 표 마지막 행·divider·해석 겹침이 해결됐다. 세로 경계 수정본도 전체 내용 가시성/표·차트 데이터 확인을 통과했다.
- 두 r2 operation의 PostgreSQL 읽기 확인: succeeded, 연결된 artifact, export attempt/public identity 각각 1, 저장 bundle SHA, frozen converter digest/version, identity exporter digest/version, compiler runtime version, 최종 output/receipt/download/attempt SHA가 일치한다. Public identity canonical digest와 frozen bundle canonicalExportIdentity는 서로 다른 domain이며 서로 같다고 강제하지 않는다.
- 독립 실제 PowerPoint 점수는 가로 경계 4.350/5(원본4.350), 세로 경계 4.320/5(원본4.360)다. 세로 월 열의 두 줄 표시는 남지만 내용은 모두 읽힌다. 이전 가로 FAIL과 r1 캐시 재사용 근거는 보존했다.
- 신규 source paint guard는 V7만 적용된다. Reliable glyph 전체 bbox가 나중 opaque rect 안에 완전히 포함돼야 확정 가림으로 판정한다. 부분 교차/측정 결손/CSS·paint 모호성은 unproven으로 품질 통과를 차단하며 원래 geometry 판정은 유지한다. Text bbox는 ink 부재 시 후보 선정에만 쓰고 glyph 증거로 대체하지 않는다. 마지막 기존 좁은 검사 1 PASS, 별도 로컬 renderer 총1회/원본동일PNG/provider0, 실제 정사각형 부분 가림 후보6 차단, 정상 그래프 2사례 후보0을 확인했다. 전체 새 guard로 13-case generation을 재실행한 것은 아니다.
- 최신 guard/hook은 API·생성 워커 managed 재시작으로 반영했다. Export worker는 r2를 수행한 프로세스를 보존했다. Bench는 false로 원복했고 기본 legacy를 유지한다.
- 현재 finite matrix는 13대표/계획56장 중 mandatory PASS 9대표/32장이다. 별도 후속 specimen을 포함한 fresh run은14/요청60장이다. 완료되지 않은 4대표는 A4 landscape provider timeout(승인 대기), 16:9 glyph 증거 미완성/불확실 호출, ultra-wide 원문 class 단어 누락/불확실 호출, custom-square 실제 glyph 가림이다. 임의 모든 해상도 무한 집합의 인증이나 전량 성공으로 확대하지 않는다.
- 품질 +0.5 pilot, 모델/설계 독립 속도 향상 및 기본값 전환은 미충족/미입증이다. 수정은 source 안전성·캐시 재개·내보내기 오류를 보완하며 새로운 fixed-layout IR나 템플릿 재제작은 도입하지 않았다. review-gallery.html에서 13대표 SVG/실제 PowerPoint/이전 실패/최신 수정 PPTX/재개 근거/점수·시간 요약을 함께 확인할 수 있다.


## 15. 완성까지 지속하는 후속 작업 (2026-10-03)

사용자는 미완료 작업까지 멀티 에이전트로 끝까지 완성하도록 재지시했다. 중복 비용이 가능한 원격 재호출과 독립 가능한 로컬 개발을 분리한다. 기존 9/13 수락은 중간 결과이며 그대로 보존한다.

- 측정 담당(Sol high): 16:9 p11의 U+00B7 script/language shaping 차이와 완전 glyph 증거를 수정한다. 원본을 바꾸거나 overlap threshold를 완화하지 않는다.
- Source 품질 담당(Sol high): 가려진 정확한 문구·노드 좌표와 원래 class/member를 수정 피드백에 넣고, 기존 SVG remake 미리보기/게시에도 V7 동일 품질 검사를 적용한다. 첫 author 입력과 자유 SVG 방식을 보존한다.
- Durable 담당(Sol high): 실제 4개 작업/lease/logical 호출/소유권/CAS를 새로 확인하고, 완료된 source를 재사용하는 formal recovery 및 기존 slide revision을 담당한다. 새로운 idempotency key로 불확실한 원격 호출을 우회하지 않는다.
- 수락 담당(Sol medium): 기존 trial-2를 고정하고 recovery-20261003 별도 증거에서 실제 source/publication/PPTX/PowerPoint를 확인한다. 내용·표·차트와 이전 실패 기록을 보존한다.

꺼진 managed stack은 관리 스크립트로 시작했다. Startup schema drift check는 UNKNOWN 경고가 있었으나 target 읽기 검사에서 필요한 PostgreSQL 필드는 확인됐다. 글로벌 schema 최신 상태로 확대해서 주장하지 않는다.

### 15.1 확인된 측정·수정 경로 보완

- Glyph collector는 실제 SVG의 lang/xml:lang을 사용하고 미지정 언어를 und로 둔다. Hangul source의 U+00B7만 실제 SVG advance와 isolated canvas advance가 다를 때 ko 측정을 비교하고 기존 1px 자기 일치 기준을 만족한 경우에만 채택한다. 원문을 삭제하거나 overlap 허용치를 늘리지 않는다. 기존 browser 검사 33개를 통과했다.
- 16:9 문제 페이지는 원래 canonical source/PNG를 유지하며 caption glyph 152/152를 측정했다. 통합 source 품질에서 geometry와 timeline이 통과하고 충돌은 0이다. 별도 publication attestation 전에는 verified로 주장하지 않는다. Windows CRLF 증거 파일은 보존하고 LF canonical 별도 파일과 각 hash domain을 기록했다.
- V7 initial author 입력은 보존했다. 수정 피드백은 가려진 정확한 문구와 opaque node 좌표, 원래 분류와 구성원을 전달한다. 기존 SVG preview/publish에도 동일한 glyph/class/paint/timeline guard를 적용하며, model 호출 후 렌더 실패를 자동으로 재구매하지 않도록 기존 workspace admission/accounting 경로를 보완한다. Source 관련 기존 좁은 검사 16개를 통과했다.
- Preview usage는 원래 generation job에 append-only receipt로 연결하되 별도로 admitted preview operation/fence로 제한한다. 원래 admission/logical call/receipt/checkpoint를 덮어쓰지 않고 누적 runtime budget의 사용량만 정상적으로 증가한다. 가상 presentation/job 행은 만들지 않는다.

### 15.2 실제 원본 재측정 게시

- 16:9 원본 재측정 preview 36f5bb7d-1f80-461c-a312-94da2a105fc8 및 publish 4af3b02c-4cb5-4565-ac39-829d76f431c5가 실제 PostgreSQL에서 succeeded를 기록했다. Revision 2에서 4개 canonical SVG/source paragraph/nativeObjects/native summary가 원래 값과 일치하고 품질 passed/verified 4/4다. 두 작업의 logical call 및 usage receipt는 각각 0이다. 실제 PPTX/PowerPoint 수락은 별도로 진행한다.
- 정사각형 첫 preview 9c57903a-1e12-4cbc-b33f-b2847c0fe5e1은 ProviderOutcomeError 이후 실패했다. 물리 receipt의 succeeded는 author finally의 기본값에서 생길 수 있어 유효 응답 성공으로 해석하지 않는다. 후보는 포착되지 않았다. 기존 행은 보존하고 원래 provider 오류와 실제 반환된 응답을 향후 기록하도록 보완한다. 원격 종료가 확정되지 않은 상태에서 자동 재구매하지 않는다.
- 초기 정사각형 429 응답과 재측정 preview의 일반 observation 파일 일부는 helper 재사용으로 덮어써졌다. 원문 파일을 보존했다고 주장하지 않는다. 알려진 429는 요약으로, 게시된 preview/task 증거는 DB에서 읽어 별도 UUID 파일로 보관한다. 이후 helper 관찰/응답은 stage/operation UUID별로 저장한다. Trial-2 기존 수락 manifest는 그대로 유지한다.

### 15.3 수정 후 내보내기와 16:9 최종 수락

- Public v2 export가 generation.result_revision과 요청 sourceRevision의 동일성을 요구해 정상 slide.regenerate 게시 후 revision 2를 presentation_not_found로 거절하는 실제 문제를 수정했다. 원래 owned/tenant generation admission을 이력 권한으로 유지하고, 성공한 publisher의 연속 revision과 현재 잠긴 문서 revision을 확인한다. 실제 canonical SVG·전체 content/native·quality·theme·notes snapshot digest를 admission에 묶고 기존 worker snapshot 준비 전에 검증한다. Source-native integrity의 원래 정확한 revision 검사는 보존한다.
- 해당 수정과 provider 오류 기록의 명명된 좁은 검사 3개가 통과했다. API/생성/export worker만 managed 재시작했고 Next/proxy/PostgreSQL은 보존했다.
- 실제 public-v2 export 0a6581b4-6df3-47c3-8c69-89e2a3a15e89가 sourceRevision 2로 succeeded를 기록했다. PPTX artifact 14a914de-60d0-479c-999d-079eed2d2a58의 SHA256은 0b5e3fa58140d368e730b18e45033d7ad2eb582c727c418b0395d34e378ba6cb다. 실제 PowerPoint 16.0으로 4장을 렌더했으며 Root도 각 PNG를 확인했다. Timeline caption/표/차트 가림·잘림이 없고 표1·차트1, 단일 series, 1~4월, 240/360/520/740 native 데이터가 일치한다. 원본 4장과 native sidecar 및 revision 2는 export 전후 유지됐다. 추가 author/모델 호출은 0이다.
- 현재 대표 수락은 10/13, 36/56장이다. A4 landscape 12장·21:9 4장·custom square 4장이 미완이다. 새 호출 승인 요청 한도는 author A4 3 + ultra 2 + square 1, VQA 최대 2다. 불확실한 기존 호출의 종료를 임의로 성공/취소로 바꾸거나 중복 구매하지 않는다.
- 초기 operation 조회의 operation_authority_unavailable 응답만으로 app binding 부재라고 단정하지 않는다. 같은 오류는 authority 서비스의 다른 거절 분기에서도 반환된다. 최종 export의 실제 거절 원인은 post-publication revision 조회 조건으로 확인했다.

### 15.4 최종 reconciliation 및 승인 대기

- 실제 rev2 export의 PostgreSQL reconciliation에서 succeeded, immutable publication artifact/다운로드 PPTX SHA256 일치, freshness source snapshot와 quality receipt sceneDigest 일치, converter digest 1cb2207fa49a763e0501aaed3bd376c16c68f7e9d2d6eafe8e3254deff8e407d를 확인했다. Export logical call/usage receipt는 0이다.
- 남은 호출은 별도 proposal hash 03431ba5356cfde218a5fa5bfbd923ef184f208cfa0128121a9bce3435b37e4b에 고정했다. A4 manifest는 6924c7017e20a4346336a7f8d4650e8a0d025a371d7b7352a1e83d608119cc35, ultra manifest는 ec02cab5672ecdd6e7be1d32252a4e6bd6752c8d0ae47de303c4af2981febbab다. 각각 author/VQA 3+1, 2+1, 정사각형 author 최대1로 합계6+2이고 모든 dispatchAuthorized는 false다.
- 정사각형 첫 호출 transport는 Codex backend에 store=False로 요청하며 transport idempotency가 확인되지 않았다. 현재 로컬 기록에는 원격 response ID·유효 source·종료 증거가 없어 이를 정상 종료된 호출로 간주하지 않는다. 남은 정사각형 호출도 불확실한 재구매 승인 범위에 포함한다.
- 승인과 무관하게 진행할 구현·검증은 완료했고 goal은 active로 유지한다. 전체 완료로 표기하지 않는다. 승인 후 각 manifest/owner/CAS/비용 한도를 재확인하고 formal 복구 또는 명시적인 preview를 실행한 뒤 새 source/PPTX/PowerPoint 수락을 이어간다.
