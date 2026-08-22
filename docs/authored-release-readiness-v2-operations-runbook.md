# Authored release readiness v2 운영 런북

## 범위와 권한

v2는 release profile, scoped capability receipt, renderer attestation, kill switch를 **표시**하는 계약이다. UI나 Next.js가 serializer readiness 또는 promotion을 승인하지 않는다. `promotionReady=true`는 서버가 발행한 상태일 뿐이며, 아래 운영 증거와 별도 승인 없이 배포를 허용하지 않는다. `shadow` 또는 `blocked` receipt는 release promotion authority를 줄 수 없지만, non-promotable Hybrid 진단 export 자체를 자동으로 금지하지는 않는다. 해당 export admission과 receipt는 서버가 최종 결정한다.

v1 endpoint는 호환 경로다. v2 route가 404/410/501인 경우에만 UI가 v1로 내려가며, v1의 `promotionReady`, `fullNativeParityReady`, `independentVnextGenerationReady`는 계속 false다. 인증 오류·5xx·형식 오류는 fallback하지 않고 fail-closed한다.

## 4단계 운영 절차

1. **Migration preflight**: additive migration head, DB backup 복구 가능성, N/N-1 read compatibility, rollback rehearsal receipt를 기록한다. schema downgrade나 production migration 실행은 이 스크립트가 수행하지 않는다.
2. **Renderer attestation**: external worker, non-root/read-only/tmpfs/network-none isolation, 만료 전 attestation digest를 서버가 projection에 넣었는지 확인한다. local-native와 development는 `unattested`이며 release 증거가 아니다.
3. **Capability/kill switch**: profile JSON의 `promotionCapabilityIds`와 `boundedNativeCapabilityIds`, `profileDefinitionDigest`, release authority receipts를 확인한다. 서버가 ready authority를 발행한 경우에만 `releaseProjectionDigest`와 export authority claim의 ordered receipt pins를 exact match로 확인한다. 비승격 상태에서 authority receipts가 비어 있고 digest/claim이 `null` 또는 absent인 것은 예상된 fail-closed 상태다. active switch는 신규 admission만 중단하고 기존 읽기·검증된 Fidelity artifact는 보존한다. UI 표시는 switch를 해제할 수 없다.
4. **Alert/rollback**: renderer attestation expiry, receipt expiry, identity/ownership/asset publication failure, z-band/CJK/chart regression, active-job drain 실패를 alert로 둔다. hard integrity incident는 즉시 kill switch → 신규 lease 중단 → in-flight drain → known-good app release 복귀 → read/export receipt 재검증 순서로 처리한다.

## 명령과 증거

`node scripts/authored-release-readiness-operations.mjs <migration-preflight|rollback-drain|alert-evaluate> <redacted-evidence.json>`은 읽기 전용 evidence gate다. evidence는 `authored-release-readiness-evidence/v1`이어야 하며, success도 `ready-for-approved-operation`일 뿐 promotion은 false다. secret, deck content, asset URL을 evidence에 기록하지 않는다.

Bitbucket YAML 구성 검증은 `node scripts/verify-authored-release-required-checks.mjs`로 수행한다. 실제 branch merge check 확인은 Bitbucket 읽기 권한을 가진 `BITBUCKET_API_TOKEN`으로 manual pipeline 또는 `--live`를 실행해야 한다. token 부재, HTTP 오류, branch restriction 부재는 모두 blocked이며 CI 성공으로 취급하지 않는다.

## Rollback 종료 조건

- active kill switch receipt와 incident ID가 있다.
- 새 release lease가 0이고 기존 in-flight 작업은 terminal/checkpoint 상태로 drain됐다.
- schema downgrade 없이 N-1 read/export와 immutable manifest digest가 확인됐다.
- renderer attestation/required-check/alert 상태가 다시 관측될 때까지 promotion은 false다.
