# Authored style sample demo topic — `business-plan-v1`

Fixed prompt used to generate every style's sample deck. The company and all
figures are fictitious. Do not edit after the pilot is frozen; a changed prompt
makes decks incomparable across styles.

## Prompt (Korean, 10 slides)

가상의 스마트 물류 스타트업 '루미나 로지스틱스(Lumina Logistics)'의 2027년 사업계획서를 10장으로 작성한다. 회사, 인물, 수치는 모두 가상이며 실제 기업을 언급하지 않는다. 슬라이드 구성은 반드시 아래 순서와 역할을 따른다.

1. 표지 — 회사명, 부제 "2027 사업계획", 날짜 2027년 1월.
2. 목차 — 이후 8개 슬라이드의 제목 목록.
3. 시장 — 국내 중소 이커머스 물류 시장 규모 4.2조 원, 연 성장률 11%, 주요 과제 3가지(당일배송 압박, 인건비 상승, 반품 비용).
4. 제품 — AI 배차 엔진 '루미나 디스패치'의 핵심 기능 3가지(실시간 경로 최적화, 수요 예측, 자동 반품 회수)와 도입 효과(배송 시간 18% 단축, 차량 가동률 23% 향상).
5. 비즈니스 모델 — 월 구독(기본 49만 원, 프로 149만 원)과 건당 수수료 120원의 2단 구조, 2026년 매출 구성 비율(구독 62%, 수수료 38%).
6. 로드맵 — 2027년 분기별 4개 마일스톤(1분기 수도권 확장, 2분기 냉장 물류 지원, 3분기 부산·대구 진출, 4분기 API 마켓플레이스 출시).
7. 팀 — 대표 김서연(전 물류 플랫폼 운영 총괄), CTO 박도현(경로 최적화 연구 10년), COO 이준호(3PL 운영 12년), 정규직 34명.
8. 재무 — 2025년 실적 매출 18억 원과 2026년 추정 41억 원, 2027년 목표 96억 원, 2027년 4분기 월 손익분기 달성 계획. 표 또는 차트로 표현.
9. 리스크 — 리스크 3가지(대형 플랫폼의 자체 배차 개발, 유가 변동, 데이터 규제 강화)와 각 대응 방안.
10. 마무리 — 핵심 요청: 시리즈 A 투자 60억 원 유치, 자금 사용 계획(제품 45%, 영업 30%, 운영 25%), 연락처는 가상 이메일 contact@lumina-logistics.example.

## Fixed request parameters

- strategy: `svg` (server default)
- n_slides: 10
- language: `Korean (한국어)`
- include_title_slide: true, include_table_of_contents: false (the agenda is
  slide 2 of the prompt itself)
- export_as: `pdf`
