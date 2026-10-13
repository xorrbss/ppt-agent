# 기준 PPT 대비 SVG 디자인 개선 구현 상태

작성일: 2026-10-12. 기준 설계: [품질 복구 설계](svg-baseline-quality-restoration-design-20261011.md).

## 현재 결과

독립 문맥의 Sol high 에이전트 2명이 작성 입력·디자인 검토를, Sol medium 에이전트 1명이 읽기 전용 근거와 호출 계획을 담당했다. root는 정책 고정·계획·체크포인트 연결과 통합 검증을 담당했다. 작업 워크트리는 `C:\project\PPT-agent\wt-svg-quality-baseline-20261012`, 기준 커밋은 `f9c9443aefbd0272f7fc5b60e1d774ba887dd8b9`다.

| 영역 | 구현한 동작 | 제한 |
| --- | --- | --- |
| 첫 작성 입력 | 신규 정책에서 실제 원문 근거·관계·참고 사용 이유·이미지 입력 순번과 인접 역할을 연결 | 실제 디자인 향상은 아직 확인하지 않음 |
| 참고 편중 | known whole-page 계약의 원문 text/title을 사용하고 ID·좌표·식별되지 않은 JSON을 numeric/native 승격 근거에서 제외 | 과거 수치 강조50건 모두가 잘못된 판단이었다고 확정하지 않음 |
| 정상 화면 재작성 | 기존 passed를 보존하며 근거 있는 디자인 목표를 기존 평가 축에서 수정·선택에 연결 | 새 정책에만 적용, 취향·미판정으로 자동 개선 확정하지 않음 |
| 전후 비교 | 기존 호출 안에서 익명 A/B와 근거 있는 축별 개선·동등·악화·미판정을 처리 | 실제 제공자 스키마 수용과 판독 결과는 미확인 |
| 최종 검토 | 정확히 같은 덱의 원 영수증을 재사용하고 혼합 덱은 실제 producer 근거가 일치하는 로컬 묶음만 재사용, 전역 갱신 | 불확실한 근거로 producer를 새로 만들거나 과거 failed를 통과로 변경하지 않음 |
| 신규 요청 계획 | `svg-quality-first@2`와 최종 검토 control을 신규 요청에 고정하고 실제 예약 예산에 포함 | 금전 상한·과거 작업·v1 요청을 변경하지 않음 |
| 검토 비용 연결 | fresh v2도 존재하는 실제 workspace operation ID를 usage scope에 전달 | 실제 ledger 계상은 아직 실행하지 않음 |
| 원본·호환성 | 기존 v1/v7/v8 고정 요청의 작성·판정·계획을 유지하고 사용자 표지·출력 비율 조건 보존 | 모든 기존 경로의 전체 테스트를 수행했다는 뜻은 아님 |

새 IR·레이아웃 엔진·고정 패턴 순환·이미지 2개 제한·새 DB 테이블·추가 계획 LLM 호출은 만들지 않았다. 현재 구현 파일은 작성/참고/의도3개, 검토/프롬프트/수정4개, 정책/준비/체크포인트3개와 기존 단위 테스트3개다.

## 확인한 입력 차이

기준은 revision5의 12페이지, free-svg, OSB 스타일, profile7, 1280×720이다. 대표1·6·9·11·12쪽 SVG/PNG의 실제 파일과 영수증/웹 참조가 각각5/5 연결됐다. 기준에 표지의 국소 remake와 국소 보정이 있어 순수 v7 첫 출력으로 보지 않는다. 원래 모델은 정상 읽기 범위에서 확인되지 않았다.

80페이지의 저장 요청은 표지 false·목차 false·instructions 없음이다. 모든 역할이 content인 것을 표지 누락 버그로 재분류하지 않았다. 초기 참고 basis는 수치 강조50·비교24·명시 관계6이다. 이 분포만으로 획일화 원인이나 v8 퇴행을 확정하지 않는다.

기준9쪽에는 native table 8×6,12쪽에는 column chart 6 categories×1 series가 있다. 대표5쪽의 실제 source 이미지 입력은 모두0이다. 11쪽의 개념 화면은 실제 화면 캡처 시험을 대신하지 못한다.

## 수행한 좁은 검증

기존 `test_author_slide_svg_asset_offer.py`의2개 node, `test_authored_vision_advisory.py`의1개 node, `test_svg_generation_admission.py`의2개 node를 하나의 pytest 명령으로 확인했다. 매개변수 사례를 포함해 **11개 통과, 실패0, 10.91초**다.

범위는 v2 원문/이미지/참고 입력 연결과 legacy 유지, metadata 숫자 배제, 익명 비교와 scope 연결, N=1/2/5/6/12/80의 신규 검토 계획과 legacy 보존, 최종 control 변조/타입 거절이다. 실제 제공자·normal API·PPTX·Office·디자인·성능 수용 검증을 대신하지 않는다. 전체 테스트·빌드·린트를 실행하지 않았다.

80페이지 신규 계획은 regular physical36 + final18 = 최대54 리뷰 슬롯이며 둘의 ordinal이 겹치지 않는다. 2~5페이지는 regular2 + final최대3 =5이며, 최종의 로컬1+전역1+필요한 포맷 재시도1을 예약한다. 한 페이지는 regular1이고 혼합 최종 control은 없다. v1 계산을 소급 변경하지 않는다.

## 준비한 실제 시험

`C:\project\PPT-agent\delivery-backups\svg-quality-baseline-20261012`에 요청과 실제 실행 근거를 저장했다. 기준1·6·9·11·12의 source 순서와 같은 스타일·16:9·한국어·표지 true·목차 false를 사용했다. 정상 API로 신규 5페이지를 생성했고 실제 author 5회, PNG 5개, native 표 1개와 차트 1개를 확인했다. 원문 5개는 기준의 저장 source와 동일하지만, 기준의 별도 native 병합 정보와 개념도 표현까지 같은 입력으로 제공한 것은 아니다.

현재 관리 설정은 Codex / gpt-6.1-sol / medium이고 author model override는 없다. 활성 가격 revision은 `subscription-local-20260930-gpt61`, 해당 catalog input/output 요율은0이다. 이는 관리 카탈로그의 계상 값이며 구독 사용량이나 실제 외부 청구의 무료 증거로 확대하지 않는다. 신규 N5 admission의 initial24+retry6=최대30은 논리적 허용 상한이며 실제 예상 호출 수·새 권한·기존80페이지의 승인 예산을 뜻하지 않는다.

사용자의 완료까지 진행하라는 지시에 따라 실행 서버의 API와 관련 워커에 새 코드를 적용했다. 첫 초안의 실제 PNG 비교는 **D3 미채택**이다. 구성도 제작 지침 노출, 화면 개념도 축소, 표의 공통 적용 범위 표현 문제가 남았다. 표의 병합 의미는 재사용 markdown에 명시되지 않았으며 기준의 별도 native payload에는 존재한다. 수치·셀 값 보존을 의미·시각 보존 성공으로 판단하지 않는다.

생성 후 검토 진입에서 fresh/partial operation binding과 v2 partial 최종 검토 슬롯 중복 계산을 발견해 각각 `4c7debbfe`, `9970778df`로 수정했다. 기존 좁은 검증은 각각 3건, 6건 통과했다. 기존 실패 작업은 유지하고 정상 recovery API로 author 5개를 재사용했다. 실제 VQA 1회는 ledger succeeded와 usage로 확인했으나 schema/coverage 판독 실패로 선택·저장이 완료되지 않았다. 원 review terminal event를 별도 보존한 근거는 없으며, 작성 5회의 terminal 증거와 구분한다. 이후 v2 full 스키마·프롬프트·파서의 조건과 안전한 거절 사유 보존을 맞췄으며 기존 좁은 검증 2건을 통과했다. 실제 실패 원응답이 없어 해당 실패의 단일 원인을 확정하지 않는다.

v2 작성 입력에서는 제작·출처 구역을 독자 내용과 구분하고, 원본이 지원하는 가로 병합은 기존 native 추론을 사용하도록 규칙 충돌을 해소했다. 기존 작성 검증 1건을 실행 서버의 Python 환경으로 통과했다. 별도 워크트리 환경에서는 `regex`가 없어 수집에 실패했으며 의존성을 변경하지 않았다. 기준의 실제 native 공통 적용 범위와 화면 개념도 정보를 보강한 별도 canonical 5페이지 요청을 정상 API로 실행했다. 기존 요청과 실패 기록은 변경하지 않는다.

canonical 작업 `f50aa23c-e491-4ae0-80da-5429e74c8e02`에서는 작성 5회와 구조 수정 4회의 provider 성공을 확인했으며 미종결 호출은 0건이다. 실제 4개 PNG 중 표 공통 적용 범위와 구성도 구분은 복원됐지만 최종 선택과 전체 품질 판정은 미완료다. 로컬 품질 실패 원인은 출처 UUID 숫자 조각과 기준 페이지 번호를 본문 숫자로 강제한 것이었다. 측정된 8개 시도에서 실제 독자용 숫자 누락은 0건이었다. v2 수치 검사 producer와 render caller를 독자 구역 기준으로 맞췄고 기존 좁은 검증 1건을 7.67초에 통과했다. 원문·이전 실패 영수증은 수정하지 않았다. 기준11은 sealed terminal 뒤 로컬 측정이 없어 기존 복구 계획에서 409로 차단됐다. 유효한 v2 작성 영수증과 입력·소유자·본문 해시·캔버스 연결을 검증한 경우 기존 known-source 경로로 투영하여 새 자식 작업에서 측정하도록 보강했다. 원본은 그대로 두며 이전 quality·PNG를 새 측정 증거로 재사용하지 않는다. 실제 DTO와 공식 seal을 사용한 기존 좁은 검증 3건이 6.33초에 통과했다. 정상 API 복구 실행은 다음 단계이며 전체 12페이지 생성과 PPTX 검증은 아직 진행하지 않았다.

## 남은 작업

실제 복구 계획에서 최신 기준11 raw2의 지원되지 않는 SVG 속성이 500을 일으켰다. 이를 임의 삭제하거나 SVG 계약을 완화하지 않았다. 정적 부적격일 때 같은 입력의 완료된 이전 응답을 원 작성 영수증과 정규화 해시, 이전 최종 SVG·측정 해시에 각각 맞춰 선택하는 경로를 추가했다. 모든 후보가 부적격이면 409로 차단한다. 기존 단일 검증 3건이 9.00초에 통과했고, 정상 API 재실행 결과는 아직 미확인이다. 원 응답·실패 품질·부모 상태는 보존한다.

1. 대표5의 정상 생성과 실제 SVG/PNG/PPTX 결과를 기준과 비교한다. 원래12페이지와 다른 덱 문맥·기준의 원 모델 미확정 한계를 보고한다.
2. 통과한 경우 기준12 전체를 같은 요청 조건으로 검증한다. 동등하면 유지로, 부족하면 미완료로 보고한다.
3. 실제 단계 시간·수정 수·호출·대기와 ledger 비용을 측정하고, 수정 전 최종 검증/출력 시간 예약을 실측 근거로 보완한다. 현재 슬롯 고정이나 단위 검증 시간을 실제 생성 성능 향상으로 보고하지 않는다.
4. 실제 캡처 사례와 긴 덱을 승인 범위에서 검증하고 웹 적용·기존 정책의 실제 호환성을 확인한다.

`compiled-spec-memory` 스킬은 현재 도구와 관련 경로에서 찾지 못해 자동 기록을 수행하지 않았다. 이 문서 작성이나 좁은 검증을 Spec 자동 기록 성공으로 표시하지 않는다.

## 2026-10-13 최신 완료 증거

위의 실패·미완료·미확인 기록은 각 단계의 역사로 보존한다. 이후 정상 생성·소유자 export·최종 PPTX review 및 현재 artifact 품질 연결이 아래 실제 증거로 완료됐다.

- 현재 구현·런타임 HEAD: `bb6ee8ea004d639ede947b91f1e36f98f7b6c81c`.
- 정상 presentation `6a027c7a-700a-4036-bcd8-d394c764939e`, revision 1, 12쪽. 최종 `canonical-twelve-editable.pptx`는 113291 bytes, SHA-256 `c4ebff7a5340fdf5de447fa2735c0ecfc1e9a743c43da5da7b60ab6d5f7086ae`.
- 실제 Office 12쪽 렌더와 native 객체 3개(표 2개·차트 1개)의 편집 가능·데이터 일치를 확인했다. 정상 최종 review `9b87d29e-d41d-4c8d-8994-97d644969705`는 실제 VQA 1회, 검토 12쪽, issue 0, remaining 0이다.
- 정상 소유자 timeline GET HTTP 200에서 `currentArtifactFidelity=passed`를 확인했다. 동일 artifact `60be0c32-083c-49b5-a85f-8a118969f1d7`, digest, source revision 1, export `5412df28-4881-41ae-baeb-ab094614a064`, 최종 review의 실제 producer 연결을 확인했다. 앞선 standalone consumer의 현재 읽기 미확인은 이 후속 정상 소유자 API 증거로 해소됐다.
- export 당시 역사적 QualityReceipt의 `blocked`와 원본 실패 증거는 변경하지 않았다. 이 완료 기록은 위 정상 12쪽 전달 범위이며, 모든 source·모델 비교 벤치나 전체 release PASS를 주장하지 않는다. 올바른 URL `http://localhost:5000/presentation?id=6a027c7a-700a-4036-bcd8-d394c764939e`에서 실제 DOM `data-testid=svg-quality-status`의 현재 PPTX 검토 통과 표시를 확인했다. `location.search`의 id 일치, 실제 presentation GET HTTP 200·동일 id·12쪽, timeline HTTP 200도 확인했다. 이전 notfound는 `presentation_id`라는 잘못된 쿼리명 때문이며 인증 원인이 아니다. 화면 증거: [실제 품질 UI](C:/project/PPT-agent/delivery-backups/svg-quality-baseline-20261012/quality-ui-current-artifact-fidelity-verified-20261013.png). 마지막 scrollshot timeout은 위 확인 결과와 별도이다.
- [전달 상태](C:/project/PPT-agent/delivery-backups/svg-quality-baseline-20261012/canonical-twelve-delivery-status-20261013.md), [정상 소유자 API 증거](C:/project/PPT-agent/delivery-backups/svg-quality-baseline-20261012/canonical-twelve-current-artifact-fidelity-api-proof.json), [통합 증거](C:/project/PPT-agent/delivery-backups/svg-quality-baseline-20261012/current-artifact-fidelity-integration-proof.json), [전달 검증](C:/project/PPT-agent/delivery-backups/svg-quality-baseline-20261012/canonical-twelve-delivery-verification.json).

## 2026-10-13 5·7·8쪽 보완본의 현재 상태

앞 절의 revision 1 결과와 PPTX는 당시의 정상 완료 기록이며 보존했다. 이후 같은 presentation의 5쪽 문장 종결, 7쪽 조사 줄바꿈, 8쪽 순차 관계를 고쳐 revision 4의 12쪽을 게시했다. 나머지 9쪽의 SVG·PNG와 원문, native 객체 계약을 보존했다. 실제 5·7·8쪽 PNG는 독립 검토와 root 확인을 거쳤다.

- 정상 source review `0a2f8032-de53-4e4f-b454-2d36998c6fbe`는 12쪽을 검토했고 issue 0, qualityRecovery passed, blocking 0, unreviewed 0, 실제 VQA 1회와 자동 재시도 0회를 기록했다.
- 정상 export `9df9a2a7-e8ee-44aa-bf8f-92b295298340`의 새 PublicArtifact는 `3193540b-7829-4be6-ab8b-db02024b51c1`이다. 새 [편집 가능 PPTX](C:/project/PPT-agent/delivery-backups/svg-quality-baseline-20261012/polish-20261013/polished-twelve-editable-revision-4.pptx)는 113537 bytes, SHA-256 `975f7b85796cd1c02dda90da5d458eacca1441e722b2d072b98e0d73c3337560`이다.
- 실제 Office에서 새 PPTX의 12쪽을 렌더했다. 원본과 새 파일의 8·9쪽 표 셀/병합, 12쪽 차트 시리즈·범주·값이 같고, 변경하지 않은 9쪽 PNG 해시가 같다. 원본 PPTX 해시 `c4ebff7a5340fdf5de447fa2735c0ecfc1e9a743c43da5da7b60ab6d5f7086ae`도 그대로다.
- 새 PublicArtifact에 묶은 final_pptx review `5b54fbbb-ca41-4214-b319-9f936f20a871`는 실제 VQA 논리 호출 1회를 시작했으나 공급자 결과·사용량이 미확정(`public_provider_result_unknown`)인 상태로 실패했다. 비용 미확정 1건이며 입력·출력 토큰, 실제 비용, 사용 영수증이 없다. 전송 전 실패라고 확정할 증거가 없으므로 임의 재요청·영수증 보정 없이 보존한다. **새 PPTX의 final review 및 currentArtifactFidelity passed는 확인되지 않았다.**

새 파일의 현재 전달 상태와 남은 승인 범위는 [보완본 전달 상태](C:/project/PPT-agent/delivery-backups/svg-quality-baseline-20261012/polish-20261013/polished-twelve-delivery-status-20261013.md)에 기록했다. 위 source·export·Office 완료와 final review 미확정은 서로 다른 검증 단계다.
