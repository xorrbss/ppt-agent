# SVG 모드 구현 결과 (2026-09-16)

Authored 기본값을 유지하고 SVG를 전체 문서 생성 전략으로 추가했다. 페이지 수를 3장으로 제한하지 않는다. 기존 Authored 스타일 YAML과 카탈로그를 공유하며 SVG 작성 계약과 스타일 어댑터를 적용한다.

## 구현

- 업로드 모드 선택, 아웃라인/생성 요청, 서버 프로필 `svg-v1`, 격리 미리보기 연결.
- `svg_content`와 `source_format` 배타 저장, 스타일 스냅샷, 페이지 체크포인트, 원본/캡처 해시, 완료 페이지 재사용.
- 정적 SVG 허용 목록, 원본 이미지 검증, Chromium 텍스트 경계·겹침·글꼴 검사, 최초 생성 후 최대 한 번 보정.
- 페이지 다시 만들기 미리보기/반영, 이력 저장, revision CAS, 문서 변경 시 시스템 페이지 번호 갱신.
- 기존 격리 내보내기 작업 및 아티팩트 권한 경로를 통한 native PPTX와 이미지 기반 PDF.
- 고정 slide-master 변환기와 라이선스/파일 해시. 격리 환경에서 호스트 홈 및 dotenv 탐색을 하지 않는 패치를 MANIFEST에 명시.
- 두 가산적 DB migration: SVG 원본/체크포인트, SVG 아티팩트 종류.

## 실제 확인한 범위

증거 디렉터리: `artifacts/svg-mode-implementation-20260916/`.

- 요청·스냅샷·OSB/현대백화점 스타일 어댑터·금지 SVG 입력·Chromium 캡처·native 변환의 좁은 실행 확인.
- 프런트 프로필 및 기능 분기 테스트 10개 통과.
- PostgreSQL migration 적용. OSB 스타일 2페이지를 실제 provider 및 durable generation 경로로 생성하여 저장 완료.
- API 재열기에서 SVG 프로필, SVG 원본 2개, 페이지 다시 만들기 capability 확인.
- 격리 작업을 통한 PPTX/PDF 완료와 권한 있는 다운로드, 다운로드 SHA-256 대조.
- PowerPoint 2페이지 렌더, native 텍스트 39개 확인. 사본에서 텍스트와 도형 색을 수정하고 저장·재열기해 유지 확인.
- PDF 2페이지, 960 × 540 pt 확인.
- 실제 페이지 다시 만들기 미리보기와 반영 후 revision 2, 후보 SVG 일치, 비대상 페이지 불변 확인.

통합 과정에서 provider driver 등록, provider semantic slot, 완료 publication의 public fence 전달, 비동기 task 완료 투영, 아티팩트 허용 종류, worker 입력 staging, 변환기의 사용자 홈 탐색 문제를 수정했다. 수정 전 만들어진 검증 문서 한 건의 완료 task 투영은 저장된 성공 상태를 근거로 복구했다. 이 복구를 새 코드의 자동 완료 검증으로 간주하지 않는다.

## 품질 및 지원 범위

- 품질 상태는 `review_required`이다. 일부 글꼴 확인 경고가 남았다. API/파일 생성 성공을 시각 품질 또는 HTML 대비 우위로 간주하지 않는다.
- 에디터는 SVG 미리보기와 페이지 재생성을 제공한다. 웹 화면의 개별 SVG 요소 직접 편집은 제공하지 않으며, native 요소 편집은 내보낸 PowerPoint에서 확인했다.
- SVG 문법은 검증·변환 가능한 정적 하위 집합이다. 임의 CSS, 스크립트, 외부 참조, 복잡한 효과는 허용하지 않는다.
- 회사 표준 pin, exact reconstruction, 원본 restyle, release-v1 품질 인증, 명시적 글꼴 포함은 현재 SVG 경로에서 지원하지 않는다.
- 모든 스타일·장문 문서·이미지 혼합·세로 캔버스의 품질 인수를 완료한 것은 아니다. 현대백화점은 이번 제품 통합 검증에서 전체 문서를 재생성하지 않았다.
- 전체 테스트/빌드/E2E는 수행하지 않았다. 변경 경계의 좁은 검사와 실제 기능 호출로 확인했다.
