# 기존 SVG 문서의 release-v1 READY 발급·검증

2026-10-18 KST 기준, 기존 A4 문서 `7a93f776-5688-51c0-a52f-978360c87639`의 revision 2·4쪽 원본을 유지한 채 새 `release-v1` export receipt의 `releaseReadiness=ready`를 확인했다. 필수 영역 `authoring`, `geometryFont`, `visualReview`, `nativeCoverageOoxml`은 모두 `passed`다.

이는 [2026-10-17 후속 보고](ppt-svg-quality-followup-20261017.md) 이후의 별도 결과다. 과거 receipt의 `blocked`, 실패·UNKNOWN operation, identity·artifact 및 ledger 기록은 소급 변경하지 않았다. 새 receipt만 READY다.

## 실제 결과와 계보

| 단계 | 식별자 | 확인 결과 |
|---|---|---|
| 기존 source QA | `355a5486-49bc-4ab2-aae7-cc289b3abd35` | 현재 r2의 4쪽 검토 근거 유지 |
| 최종 코드의 interactive export | `fc9af948-5f07-4c58-9703-495d449ede58` | 새 PPTX 생성 성공 |
| 해당 PPTX의 final QA | `06139f08-7c0c-4d4b-b929-f44030cb0fcf` | 실제 최종 PPTX 4쪽·0 findings, VQA 1회 보고 |
| 새 release-v1 export | `c0ebc945-932b-4a69-b145-28107211ad91` | 성공, 필수 4영역 passed, READY |
| 새 공개 artifact | `768aed22-52fc-4928-8b86-928890e2cb4e` | 인증된 웹 다운로드와 receipt SHA 일치 |

최종 파일 SHA-256은 `5cac516a077fa22f66b2fc5ba601d316919f9d85ddeb6912f9d1c4ba5282207f`다. interactive와 release 파일은 동일 bytes이며, release 관측에서 `workerExecuted=false`, `cacheReuse=true`를 확인했다. 첫 fresh 시도 `7dee169a-1195-4424-a1bd-462a1cd74697`와 artifact `0fa39c8f-2d22-4376-b79b-e56f2a54336a`의 blocked 근거도 보존했다.

## 변경 범위와 검증

제품 변경은 `servers/fastapi/services/svg_export_identity.py` 한 파일이다. release 요청은 정책 이외의 현재 입력이 정확히 같은 interactive-v1 또는 정책 미지정 identity의 원 physical producer를 검증하여 동일 파일을 재사용한다. 현재 converter·verifier, source·native objects·폰트·품질 입력, owner·tenant·revision, artifact SHA·storage·size와 operation fence 검사를 유지한다. 반복 release도 원 physical producer에 직접 연결하고 원본 readback·render 증거를 보존한다. 새 receipt에서는 현재 source QA와 해당 artifact의 final review를 다시 판정하며 release gate를 완화하지 않는다.

`svg_export_quality.py`와 기존 header 검증 계약은 변경하지 않았다. 기존 테스트 파일 두 개를 보완했으며, root의 좁은 검증은 **16 passed, 8 deselected**다. trace는 `C:/project/PPT-agent/delivery-backups/svg-release-ready-20261018/privatepytest-release-predecessor-20261018-flat-origin.txt`에 보존했다.

실제 제품 경로는 인증된 timeline·artifact 다운로드 및 hash, 실제 PowerPoint 4쪽 확인, native 원문·문단 보존으로 검증했다. 원본 4쪽과 revision 2는 그대로이며, source 본문·slide ID·notes·content의 전후 동일성을 확인했다. 과거 operation·artifact·identity는 범위를 한정한 전후 행 비교로 보존을 확인했고, PowerPoint 확인 중 source와 기존 문서도 보존됐다.

## 증거 경로

아래 경로는 로컬 증거 보관 위치다.

| 증거 | 파일 |
|---|---|
| 최종 종합 근거·필수 영역·계보·동일 bytes·보존 검사 | `C:/project/PPT-agent/delivery-backups/svg-release-ready-20261018/release-completion-proof.json` |
| 인증된 웹 timeline·다운로드 hash | `C:/project/PPT-agent/delivery-backups/svg-header-completion-20261017/release-ready-v1-20261018-web-artifact-proof.json` |
| 실제 PowerPoint 4쪽·source 및 기존 문서 보존 | `C:/project/PPT-agent/delivery-backups/svg-release-ready-20261018/release-ready-office/office-proof.json` |
| native 원문·문단 보존 | `C:/project/PPT-agent/delivery-backups/svg-release-ready-20261018/release-ready-native-paragraph-proof.json` |

## 결과의 범위

이 결과는 위 문서·revision·artifact와 현재 로컬 실행 환경에 한정한다. 선택 영역 performance는 `not_assessed`다. `identityAndReuseMs` 약 359ms는 이번 한 번의 재사용 관측이며 전체 생성 성능, 디자인 향상 또는 다른 문서의 범용 release 인증을 뜻하지 않는다.
