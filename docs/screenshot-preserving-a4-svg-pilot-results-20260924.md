# A4 SVG 재구성 파일럿 결과 — 2026-09-24

## 현재 범위

권장 순서의 전체 위험 분류와 대표 출력 파일럿을 수행했다. 제품 생성 경로, 사용자 검토 UI, 전체 28장 처리는 아직 구현 완료가 아니다. 결과는 수동 영역 주석과 에이전트 시각 전사를 사용하는 로컬 검증본이다.

## 수행과 확인

| 항목 | 확인 범위 |
|---|---|
| 원본 전체 분류 | 28장: 표지 1, 화면 중심 20, 구분장 1, 효과·수치 6 |
| 가로 파일럿 | 원본 3·8·11·22·24장, 순서 유지, A4 SVG/PPTX/PDF |
| 세로 파일럿 | 원본 24장, A4 SVG/PPTX/PDF, 별도 세로 배치 |
| 가로 PPTX | 편집 가능한 텍스트 도형 127개, 이미지 52개 |
| 세로 PPTX | 편집 가능한 텍스트 도형 48개, 이미지 10개 |
| 텍스트 readback | 생성 SVG 대비 공백을 정규화한 문자 다중집합의 누락·추가 없음 |
| 이미지 readback | 생성 SVG의 crop PNG와 PPTX 이미지 픽셀 일치 |
| 이미지 표시 비율 | PPTX 그림 객체 62개의 crop·도형 크기를 확인, 상대 오차 1% 초과 0개 |
| PDF | 각각 5장·1장 및 실제 A4 페이지 크기 확인, 페이지 이미지 방식 |
| 자산 연결 | canonical slot SVG와 sealed 자산 manifest를 기존 materialization/admission 경로에 전달 |
| 최종 산출물 계보 | 입력 SVG·canonical SVG·저장 materialized SVG의 digest 기록 |

PPTX의 표는 개별 셀 텍스트·사각형으로 편집할 수 있으며, OOXML native table 객체는 0개다. 이 수치를 표 전체의 native table 지원으로 보고하지 않는다. 텍스트 다중집합 확인은 문장 순서·정답 전사·가시성의 증명이 아니다.

## 파일럿에서 수정한 실제 문제

- 로컬 tessdata의 TSV config 누락으로 일반 텍스트가 반환되어 OCR 토큰이 0개가 되던 처리.
- 원본 3장 이미지의 약 0.444 픽셀 여백을 무조건 잘림으로 거부하던 처리.
- A4 재배치에서 캡처가 늘어날 수 있던 image 비율 처리.
- PDF admission의 SVG2 href·정수 image 크기 요구와 자산 분리 경로 연결.
- 흰 표 머리글이 배경에 가려지거나 표 셀 배경이 아이콘을 덮던 배치 순서.
- 실제 글꼴 폭·여백·한글 단어 기준 줄바꿈, 세로 제목과 합계 숫자의 고아줄.
- 8장 로고의 첫 글자를 자르던 crop 경계.

8장의 작은 보조 화면은 원본에서도 하단이 설명 띠에 가려져 있다. 보이는 원본 픽셀만 보존하며 가려진 UI를 생성하지 않는다. 재배치 후 그 절단 경계가 보일 수 있으므로 이 상태를 원본 화면 전체 복원으로 설명하지 않는다.

## 검증 한계

- 자동 영역 검출 성능은 검증하지 않았다. 대표 주석은 수동 작성했다.
- 에이전트 전사는 사용자 승인이나 원문 전수 정확도 보증이 아니다. 검토 필요 상태를 유지한다.
- 전체 OCR과 영역 OCR의 교차 확인에는 같은 로컬 엔진을 사용한다. 완전성에 대한 독립 증명이 아니다.
- 알려진 OCR 미포함 7건은 원본 대조 결과 선·테두리 오탐 6건과 설명 영역 bbox 미포함 1건이었다. 제외 근거와 bbox를 보완했지만 partial_overlap·ambiguous 진단은 별도 검토 대상이다.
- 로컬 CAS fixture는 바이트·배치·식별자의 일관성을 확인한다. 실제 DB owner 권한, committed lifecycle, generation fencing 및 publication admission은 검증하지 않았다.
- 브라우저와 PDF의 제한된 시각 확인은 PowerPoint 앱에서의 전체 시각 수용을 대체하지 않는다.
- 전체 28장, 모든 세로 페이지, 앱에서 생성·저장·재개·내보내기까지의 제품 흐름은 미검증이다.

## 후속 제품 연결 순서

1. 승인된 raster 분석 pin과 crop ArtifactRef를 SourceSlot에 연결하는 브리지.
2. 기존 WorkspaceOperation revision/CAS와 WorkspaceArtifact 생존 참조를 사용하는 분석 저장.
3. 동일 대화 안에서 원본 대조·수정·승인·재개를 지원하는 검토 UI.
4. 기존 native 경로와 분리된 명시적 raster reconstruction admission 및 생성 연결.
5. 원본 순서를 유지한 28장 전체 적용과 페이지별 내용·가독성 수용 확인.

대표 출력의 성공만으로 위 후속 작업을 완료 처리하지 않는다.

## 증거와 변경 파일

- `artifacts/raster-reconstruction-pilot-20260924/page-risk-audit.json`
- `artifacts/raster-reconstruction-pilot-20260924/pilot-summary.json`
- `scripts/raster_reconstruction_pilot.py`
- `scripts/raster_reconstruction_pilot_export.py`
- `docs/screenshot-preserving-a4-svg-reconstruction-design-20260924.md`

원문 전사·주석·진단 로그는 로컬 비공개 작업 폴더에 보관한다. 사용자용 검토본은 Downloads의 Samsung_Display_A4_SVG_Pilot_20260924 폴더에 제공한다.
