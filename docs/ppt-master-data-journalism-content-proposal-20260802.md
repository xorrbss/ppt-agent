# PPT Master Data Journalism 콘텐츠 제안

작성일: 2026-08-02

## 결과

`ppt-master-data-journalism` 후보를 실제 데이터가 채워진 20장 AI Capital 2026 제안 덱으로 제작했다. 기존 시각 구조는 유지하면서 샘플 문구를 한국어 중심의 데이터 저널리즘 서사로 교체했고, 출처가 불명확한 이미지·로고·직접 인용은 사용하지 않았다.

산출물은 `docs/artifacts/ppt-master-data-journalism/ai-capital-2026-data-journalism.pptx`에 있다.

## 20장 구성

1. AI Capital 2026 표지
2. Executive Snapshot
3. 6개 파트 목차
4. 민간 AI 투자와 생성형 AI 비중
5. 초대형 투자 라운드 증가
6. 미국·중국·영국 투자 격차
7. 생성형 AI 투자 지역 집중
8. OpenAI·Anthropic 대형 라운드
9. 신규 투자 유치 AI 기업 수
10. 자본→컴퓨트→전력→도입→가치 전환
11. Alphabet CapEx 구성
12. 데이터센터 전력 수요와 계통 병목
13. 공급자 경제성 섹션 브리지
14. NVIDIA 매출 전환
15. 조직 도입과 에이전트 초기 단계
16. Stargate 인프라 계획
17. 가치·생산성 섹션 브리지
18. 미국 생성형 AI 소비자 잉여
19. 4대 리스크 매트릭스
20. 여섯 가지 투자 판단

## 화면 노출 제안

현재 신규 템플릿 목록은 정적 카탈로그 상태, 권리 증빙, 네이티브 원본 보관, publication preflight를 모두 통과한 항목만 노출한다. 따라서 이 덱은 지금 바로 사용자 화면에 공개하지 않고 운영자 검토 자료로 사용한다.

노출 순서는 다음과 같다.

1. 검토자가 이 PPTX의 재배포·수정·상업 이용 범위를 문서로 승인한다.
2. 승인 문서 digest와 신뢰된 승인자 attestation을 rights manifest에 기록한다.
3. 승인된 동일 PPTX를 template ID/revision 범위의 native storage에 반입한다.
4. DB의 `TemplateV2.assets.native_pptx`에 SHA-256, 크기, layout source parts와 publishable preflight 증거를 기록한다.
5. read-only candidate preflight를 실행한다.
6. 카탈로그 상태를 `published`로 바꾸고 rollout allowlist에 ID를 추가한다.
7. 목록 API와 TemplateSelection 화면에서 노출·선택·생성을 확인한다.

권리 승인이나 운영 attestation을 임의로 만들지 않는 한 1~4단계는 자동 완료할 수 없다. 콘텐츠 제작, 템플릿 충실도 검사와 전 슬라이드 렌더 검수는 완료했다.

## 승인 진행 상태

정확한 공개 대상은 기존 upstream 샘플이 아니라 외부 미디어를 제거한 SHA-256
`5ea1bd7f13ebaa324be3b60167bb259c7049d13c33865c0abaa2306a17d67e37`의
20장 덱이다. 독립 검토자가 해시, 라이선스 연결, 신규 작성 콘텐츠에 대한 권한을
검증할 수 있도록 다음 요청 패킷을 준비했다.

- `servers/fastapi/templates/v2/evidence/ppt-master-data-journalism/approval-request.v1.md`
- `servers/fastapi/templates/v2/evidence/ppt-master-data-journalism/review-packet.v1.json`

요청 패킷은 승인 증거 자체가 아니며, 독립 승인자 식별자와 배포 환경 HMAC 서명이
없는 상태에서는 catalog를 `published`로 전환하지 않는다.
