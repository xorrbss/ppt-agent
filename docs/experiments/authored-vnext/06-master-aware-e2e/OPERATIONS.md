# 운영, 재개, 충돌, rollback 계약

## Published master pin

작업 시작 시 `masterId`, `masterRevision`, `masterDigest`, `layoutRegistryDigest`, `fontManifestDigest`를 한 묶음으로 pin한다. 모든 slide checkpoint와 core bridge receipt가 이 값을 그대로 가져야 한다. 동일 `operationId`의 retry/resume에서 하나라도 바뀌면 `authored_vnext_stale_master`로 중단한다.

master head가 새 revision으로 이동한 경우 기존 checkpoint를 새 master에 암묵적으로 적용하지 않는다. 새 `operationId`와 새 source/outline 계획으로 재생성해야 한다.

## Body-only regenerate

1. 현재 deck/slide revision과 digest를 읽어 publication fence를 만든다.
2. title, layout, master furniture를 복제한 뒤 body slot만 바꾼 detached candidate를 저장한다.
3. DOM 구조, OCR source coverage, title-fit/clipping, furniture duplicate를 검사한다.
4. candidate를 포함한 전체 deck에 fonts/colors/frame/pageNo/layout/slot/source consistency gate를 실행한다.
5. 모든 gate가 통과할 때만 publication fence로 CAS append한다.

후보가 거절되거나 CAS가 충돌하면 기존 head와 immutable revisions는 바뀌지 않는다. 후보 증거는 원인 분석용이며 active deck이 아니다.

## Rollback

rollback은 과거 파일을 덮어쓰거나 head를 과거 번호로 되감지 않는다. 대상 immutable revision의 내용을 새 revision으로 복제해 CAS publish하며 `parentRevisionDigest`는 rollback 직전 head를 가리킨다. 따라서 감사 로그에서 변경과 복구가 모두 보인다.

rollback 전에는 다음을 확인한다.

- target revision/digest가 동일 deck에 존재한다.
- target master digest가 rollback 요청에 pin된 digest와 같다.
- publication fence가 현재 head와 일치한다.
- 복원된 deck도 현재 quality policy를 통과한다.

## 장애 대응

| 신호 | publish 여부 | 조치 |
|---|---:|---|
| `authored_vnext_generation_conflict` | 아니요 | 최신 head를 읽고 사용자 변경과 병합 후 새 fence로 재시도 |
| `authored_vnext_candidate_rejected` | 아니요 | candidate gate issue와 OCR/DOM/title-fit 증거 점검 |
| `authored_vnext_stale_master` | 아니요 | 새 master로 전체 outline 재계획; 기존 checkpoint 재사용 금지 |
| `authored_vnext_core_bridge_export_failed` | 아니요 | 동일 pinned checkpoint로 제한 재시도; digest 변경 시 중단 |
| `authored_vnext_rollback_conflict` | 아니요 | target과 current head를 다시 조회하고 운영자 의도 재확인 |

운영 환경에서 generation service가 브라우저나 PowerPoint 프로세스에 직접 attach하거나 종료해서는 안 된다. export는 core bridge port만 사용하고, 고객 데이터 또는 provider 호출은 별도의 명시적 승인과 정책 경계가 없는 한 수행하지 않는다.
