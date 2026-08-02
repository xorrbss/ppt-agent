# PPT Master × Template V2 최종 출판 감사

작성일: 2026-08-02
대상 저장소: `https://github.com/hugohe3/ppt-master`
고정 commit: `8858c4960b870f12baf952994fbd0c76ab5ba4ec`

## 결론

Template V2 전용 신규 선택 흐름과 PPT Master 스타일 반영 정책은 유지한다. 그러나 감사한 후보 4종은 권리 증거와 PPTX 패키지 건전성 기준을 통과하지 못했으므로 바이너리 반입, 공개, 기본값 지정은 수행하지 않았다. 기존 V1은 신규 discovery에서 제외하되 기존 문서의 직접 조회·rehydrate·render 호환성은 보존한다.

현재 가장 안전한 제품 결정은 다음과 같다.

- PPT Master 후보 4종: catalog 등록 상태를 유지하되 모두 `blocked`
- 신규 기본 템플릿: 외부 후보의 권리·기술 검증이 끝날 때까지 지정하지 않음
- 사설/custom Template V2 초안 편집: 유지
- 공개 discovery 및 presentation 생성: 통합 release gate를 통과한 revision만 허용
- 외부 PPTX 바이너리·이미지: 저장소에 복사하지 않음

## 후보별 감사 결과

| 순위 | 후보 | 분량/구조 | 시각·편집성 평가 | 출판 판정 |
|---:|---|---|---|---|
| 1 | Global AI Capital | 20장, 947 shape, 181 group, PNG 3개 | 범용 레이아웃 다양성과 디자인 체계가 가장 안정적. 다만 로고·데이터 권리 위험과 일부 overflow가 있음 | BLOCKED |
| 2 | Swiss Grid | 14장, 570 shape, 91 group, PNG 3개 | 그리드와 재사용성이 좋지만 텍스트 중첩·절단 수정량이 큼 | BLOCKED |
| 3 | Glassmorphism | 12장, 762 shape, 160 group, PNG 8개 | 브랜드 데모로 매력적이나 원본 PPTX 변환 실패와 AI 이미지·아이콘 고지 문제가 있음 | BLOCKED |
| 4 | Editorial/Pritzker | 11장, 288 shape, 72 group, PNG 20개 | 편집 가능한 샘플 덱이지만 외부 건축 이미지 의존도가 높아 재배포 위험이 가장 큼 | BLOCKED |

표의 PNG 수는 PPTX 패키지에 실제 내장된 파일 수다. Pritzker upstream 소스 디렉터리에는 raster 25개와 vector 22개가 있어 catalog의 자산 권리 감사 수치와 범위가 다르며, 양쪽 모두 권리 증빙 대상이다.

네 파일 모두 16:9, slide master 1개, layout 11개, theme 1개이며 embedded font는 없다. 그러나 slide-local placeholder, native table, native chart는 모두 0개다. 따라서 “재사용 가능한 master/layout 템플릿”보다는 그룹화된 shape 중심의 편집 가능한 샘플 덱에 가깝다.

catalog의 `template_kind`는 실제 OOXML 구조 판정이 아니라 제품에서 의도한 활용 역할이다. 예를 들어 Swiss의 `layout`은 레이아웃 아이디어 모음이라는 의미이며, 검증된 native master/layout 템플릿이라는 뜻이 아니다. catalog의 capability 값도 importer가 목표로 하는 보존·패치 기능을 나타낼 뿐 현재 후보에서 성공 검증된 기능이 아니다. 현재 네 후보의 artifact import는 모두 실패했으므로 승인 전 capability를 제품 기능으로 노출하지 않는다.

## 패키지·렌더 감사

catalog에 기록된 SHA-256과 byte 크기는 네 후보 모두 원본과 일치했다. 동시에 모든 notesSlide가 존재하지 않는 `ppt/notesMasters/notesMaster1.xml`을 참조했다.

- artifact import: 네 후보 모두 `PartDoesNotExist`로 실패
- LibreOffice PDF 렌더: Swiss, Global AI, Pritzker 성공
- Glass 원본 PPTX: LibreOffice 변환도 실패
- Glass 시각 검토: 동일 commit의 `svg_final` 12장을 명시적 fallback으로만 사용

시각 QA에서 Global AI는 12쪽 카드 하단 절단과 6·15쪽 숫자 레이블 줄갈림, Swiss는 2쪽 중첩·9쪽 수식 줄바꿈 붕괴·10/14쪽 하단 절단, Pritzker는 2·5쪽 중첩/통계 붕괴가 확인됐다. Glass의 SVG 보조 렌더는 중첩·절단이 없었지만 PPTX 자체의 호환성·편집성 증거로 사용하지 않았다.

원본을 로컬에서 수선하면 승인 대상 SHA가 바뀌므로, upstream 수정본을 새 revision으로 받아 처음부터 권리·무결성 감사를 다시 해야 한다.

## 권리 감사

저장소의 MIT 라이선스는 저장소 코드의 사용·수정·배포 근거다. PPTX 내부 제3자 사진, 브랜드 로고, AI 출력물, 인용문과 데이터의 권리를 자동으로 승인하지는 않는다.

- Swiss: AI 이미지 3개 `Pending`, Simple Icons 브랜드 로고 6개, 인용문 원출처 미확인
- Pritzker: 외부 건축 이미지 20개 내장, 사진가·원본 URL·라이선스·허가 없음
- Global AI: AI 이미지 3개 `Pending`, Simple Icons 8개, Tabler 10개, 데이터 재사용 조건 미확인
- Glass: AI 이미지 8개 `Pending`, Phosphor 28개와 필수 MIT 고지 누락

후보 디렉터리에는 후보별 LICENSE/NOTICE/CREDITS/ATTRIBUTION과 자산별 권리 선언이 없다. 그러므로 공개 GitHub 저장소에 있다는 사실만으로 상업적 수정·재배포를 승인할 수 없다.

## 이번 구현에서 강화한 출판 경계

- 승인 레코드만 커밋해 자체 승인할 수 없도록 배포 환경의 독립 검토자 allowlist와 HMAC attestation을 요구
- catalog 후보 SHA-256·크기·preservation mode를 정확한 Template V2 revision의 `assets.native_pptx`에 결속
- discovery, 응답 metadata, 직접 presentation 생성에 같은 release gate 적용
- catalog prompt 생성은 차단하되, 비catalog 사설/custom 초안 편집은 유지
- 새 실패 코드 `template_v2_template_not_published`를 403과 bounded observability dimension으로 등록
- 후보가 실제로 권리·기술 게이트를 통과하기 전에는 기본값으로 노출하지 않음

## 남은 외부 작업

코드만으로 완료할 수 없는 항목이다.

1. 권리자 또는 적법한 제공자에게 후보별 서명된 재배포 허가와 자산별 provenance를 받는다.
2. AI 출력 생성 로그, 외부 사진 라이선스/서면 허가, 로고 정책, 아이콘 NOTICE, 인용·데이터 재사용 조건을 확보한다.
3. notes master 관계가 정상인 upstream PPTX를 새 commit과 새 SHA-256으로 제공받는다.
4. 독립 검토자가 evidence digest를 승인하고 배포 비밀로 HMAC attestation을 생성한다.
5. 정확한 후보 revision을 import하고 실제 저장 객체 digest·OOXML 보존·render/export 회귀를 수행한다.
6. 통합 게이트가 모두 통과한 후보 하나만 기본 템플릿으로 지정한다.

이 전에는 어떤 후보도 `published`, `approved`, `is_default=true`로 바꾸면 안 된다.
