# Compliance verification record (2026-08-13 KST)

## 확인 사실

- 작업 시작 전 `git status --short`: `?? docs/authored-only-vnext-architecture-design-20260813.md`. 이 기존 미추적 파일은 읽거나 수정하지 않았다.
- 공식 remote `git ls-remote https://github.com/genspark-ai/genoffice.git HEAD`: `dc4d7e5927864498913b7ba42d0da06cc7cf628e`.
- 로컬 sparse clone `git rev-parse HEAD`: 같은 SHA.
- origin: `https://github.com/genspark-ai/genoffice.git`; clone worktree clean.
- materialized workspace: `packages/pptx-engine`만 존재. `apps`, `ee`, 다른 `packages/*`는 materialized되지 않음.
- engine runtime 직접 의존성: `fast-xml-parser`, `jszip`. engine `src`의 bare import 집합도 동일.
- LICENSE exact-copy SHA-256: `68e32334df324ef4fc79fad3a26487e5abac36055a484bb6c8d7ecf9d8350885`.
- NOTICE exact-copy SHA-256: `ff1ad79ed52b0f5c1d0e10a9370c22c0c0e6ee83074b6a9b644b25ab7b129f94`.
- SPDX 최소 SBOM: engine 1개 + lockfile production closure 21개 = 22 package, dependency relationship 25개.
- 실제 후보의 격리 engine intake: upstream `package.json`, `tsconfig.json`, `src/**`와 파일 목록 및 SHA-256 차이 0.

## 실행 결과

정상 검사:

```text
node docs/experiments/authored-vnext/03-ooxml-roundtrip/compliance/check-compliance.mjs
RESULT PASS (17 checks)
```

금지 경로/의존성 음성 검사: 시스템 temp 후보에 `ee/marker.txt`와 `dependencies.electron`을 넣고 같은 검사기를 `--candidate`로 실행했다.

```text
FAIL candidate contains denied app/ee/branding paths: ee, ee/marker.txt
FAIL candidate dependency boundary violations: package.json: dependencies.electron=1.0.0
FAIL candidate must contain exactly one @genoffice/pptx-engine intake; found 0
RESULT FAIL (3 violations)
NEGATIVE_EXIT=1
```

temp 후보는 경로가 시스템 temp root 아래인지 정규화·확인한 후 삭제했다. 전체 제품 테스트 스위트는 실행하지 않았다.

## 적용한 가정

- 이 intake는 비배포 PoC이며 `packages/pptx-engine` source와 그 production npm closure만 평가한다.
- root workspace manifest/lock/config는 pin 검증과 격리 실행 metadata이고 제품 앱 source로 간주하지 않는다.
- `package-lock.json`의 resolved version/license 선언을 최소 SBOM의 기준으로 삼는다.

## 미검증·남은 제한

- 외부 SPDX validator와 법률 검토는 실행하지 않았다.
- 각 npm tarball의 실제 license text/hash를 별도로 내려받아 검증하지 않았다.
- upstream 전체 도구인 `tools/gen-third-party-notices.mjs`는 경계상 실행하지 않았다. release용 third-party notice는 별도 생성·검토가 필요하다.
- remote HEAD 일치는 위 확인 시각의 관측값이다. exact SHA pin 자체만 재현 가능한 불변 입력으로 사용한다.
- 이 검사는 라이선스·경계 evidence이며 PPTX roundtrip 기능, PowerPoint repair dialog 또는 OOXML 보존성을 검증하지 않는다.
