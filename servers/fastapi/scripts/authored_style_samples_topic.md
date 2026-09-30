# Authored style sample demo topic — `business-plan-v1`

Fixed prompt used to generate every style's sample deck. The company and all
figures are fictitious. Do not edit the prompt section after the pilot is
frozen: its digest is recorded in every manifest (`topicDigest`), so any edit
stales all decks and forces a full regeneration.

The twelve slides are chosen so that each one exercises a different layout
type (big numbers, card row, process, pricing table, comparison matrix, quote,
timeline, chart), which is where styles differ most visibly.

## Prompt (Korean, 12 slides)

가상의 스마트 물류 스타트업 '루미나 로지스틱스(Lumina Logistics)'의 2027년 사업계획서를 정확히 12장으로 작성한다. 회사, 인물, 고객사, 수치는 모두 가상이며 실제 기업을 언급하지 않는다. 슬라이드 구성은 반드시 아래 순서와 역할을 따른다.

1. 표지 — 회사명 '루미나 로지스틱스', 부제 "2027 사업계획", 날짜 2027년 1월, 발표자 대표 김서연.
2. 목차 — 3장부터 12장까지의 제목을 번호와 함께 나열한다.
3. 시장 — 국내 중소 이커머스 물류 시장 규모 4.2조 원과 연 성장률 11%를 큰 숫자로 강조한다.
4. 문제 정의 — 중소 판매자가 겪는 세 가지 문제(당일배송 압박, 인건비 상승, 반품 비용)를 나란한 세 개의 카드로 보여준다.
5. 제품 — AI 배차 엔진 '루미나 디스패치'의 핵심 기능 3가지(실시간 경로 최적화, 수요 예측, 자동 반품 회수)와 도입 효과(배송 시간 18% 단축, 차량 가동률 23% 향상).
6. 작동 방식 — 주문 접수 → AI 배차 → 배송 → 반품 회수의 4단계 흐름을 단계별 설명과 함께 프로세스로 보여준다.
7. 비즈니스 모델 — 월 구독(기본 49만 원, 프로 149만 원)과 건당 수수료 120원의 요금표, 2026년 매출 구성 비율(구독 62%, 수수료 38%).
8. 경쟁 비교 — 루미나, 대형 플랫폼 자체 배차, 수작업 배차의 세 가지를 도입 비용, 반품 회수, 중소 판매자 적합성, 도입 기간의 네 항목으로 비교하는 표.
9. 고객 사례 — 가상 고객사 '한결상회'(생활용품 온라인몰) 운영 팀장의 인용문 한 문장과 도입 6개월 성과(배송 지연 41% 감소, 월 물류비 17% 절감).
10. 로드맵 — 2027년 분기별 4개 마일스톤(1분기 수도권 확장, 2분기 냉장 물류 지원, 3분기 부산·대구 진출, 4분기 API 마켓플레이스 출시)을 타임라인으로.
11. 재무 — 2025년 실적 매출 18억 원, 2026년 추정 41억 원, 2027년 목표 96억 원을 막대 차트로 표현하고, 2027년 4분기 월 손익분기 달성 계획을 덧붙인다.
12. 마무리 — 시리즈 A 투자 60억 원 유치 요청, 자금 사용 계획(제품 45%, 영업 30%, 운영 25%), 연락처는 가상 이메일 contact@lumina-logistics.example.

## Fixed request parameters

- strategy: `svg` (server default)
- canvas: 16:9 (1280×720) — omit `page_size`/`orientation`; this is the CLI's
  default request shape
- n_slides: 12
- language: `Korean (한국어)`
- include_title_slide: true, include_table_of_contents: false (the agenda is
  slide 2 of the prompt itself)
- export_as: `pdf`
- Manifest slide titles, in order: 표지, 목차, 시장, 문제 정의, 제품,
  작동 방식, 비즈니스 모델, 경쟁 비교, 고객 사례, 로드맵, 재무, 마무리
- Order guard (PDF text must contain): slide 1 `루미나`, slide 2 `목차`,
  slide 8 `수작업`, slide 9 `한결상회`, slide 11 `96`, slide 12 `60억`
