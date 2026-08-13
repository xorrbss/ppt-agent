# GenOffice `pptx-engine` compliance boundary

이 디렉터리는 GenOffice 전체 제품을 들여오기 위한 설계가 아니라, 지정 commit의 Apache core `packages/pptx-engine`만 격리 평가하기 위한 compliance evidence다.

## 확인된 pin과 provenance

- 공식 저장소: <https://github.com/genspark-ai/genoffice>
- exact pin: [`dc4d7e5927864498913b7ba42d0da06cc7cf628e`](https://github.com/genspark-ai/genoffice/commit/dc4d7e5927864498913b7ba42d0da06cc7cf628e)
- 확인 시각: `2026-08-13T15:44:46.6165083+09:00`
- 확인 방법: 공식 원격에 `git ls-remote https://github.com/genspark-ai/genoffice.git HEAD`를 실행하고 로컬 sparse clone의 `git rev-parse HEAD`와 비교했다. 둘 다 exact pin이었다.
- 패키지 선언: [`packages/pptx-engine/package.json`](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/package.json)의 `@genoffice/pptx-engine@0.1.0`, `Apache-2.0`, `private: true`.
- 원본 라이선스: [LICENSE](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/LICENSE), [NOTICE](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/NOTICE).

`LICENSE.genoffice.txt`와 `NOTICE.genoffice.txt`는 pin의 byte-for-byte 복제본이다. `upstream-pin.json`에 SHA-256을 고정했고 검사기가 upstream blob과 로컬 복제본을 모두 다시 해시한다. NOTICE 안의 앱·폰트·Unicode 설명까지 원문 그대로 남아 있으나, 이 PoC는 그 앱·폰트·Unicode 데이터나 `LICENSE-UNICODE.txt`를 배포 대상으로 채택하지 않는다.

## 허용/차단 경계

허용되는 upstream source workspace는 `packages/pptx-engine` 하나다. root의 `package.json`, `package-lock.json`, TypeScript/Vitest 설정과 라이선스 파일은 sparse clone 재현 및 의존성 검증용 metadata일 뿐, 제품 앱 도입 허가가 아니다.

다음은 fail-closed로 차단한다.

- `apps/`, `ee/`, 다른 `packages/*` workspace 물리화
- 로고·branding asset과 Electron `.asar`
- `@genoffice/pptx-engine` 외 `@genoffice/*` 의존성
- Electron, agent-core, ai-provider, OpenAI/Anthropic 제품 의존성
- engine 밖으로 나가는 상대 import 또는 `fast-xml-parser`, `jszip` 외 bare runtime import
- 후보에 정확히 하나 존재해야 하는 engine intake의 파일 목록 또는 byte hash drift
- exact SHA, 공식 remote, sparse allowlist, LICENSE/NOTICE 해시, SBOM closure 중 하나라도 drift한 상태

검사 실행:

```powershell
node docs/experiments/authored-vnext/03-ooxml-roundtrip/compliance/check-compliance.mjs
```

다른 clone 또는 intake 후보를 검사하려면 경로를 명시한다.

```powershell
node docs/experiments/authored-vnext/03-ooxml-roundtrip/compliance/check-compliance.mjs `
  --upstream .codex-tmp/genoffice-dc4d7e592786 `
  --candidate docs/experiments/authored-vnext/03-ooxml-roundtrip
```

검사기는 네트워크를 호출하지 않는다. remote HEAD 재확인은 별도의 read-only `git ls-remote` 단계이며, remote HEAD가 바뀌어도 이 평가의 exact SHA pin은 자동 이동하지 않는다.

## 최소 SBOM과 NOTICE 한계

`sbom.spdx.json`은 SPDX 2.3 형식으로 engine과 `package-lock.json`에서 계산한 production dependency closure만 담는다. dev/test package와 GenOffice 앱은 포함하지 않는다. 이 SBOM은 PoC 경계 증거이지 release용 third-party notice의 대체물이 아니다. upstream NOTICE가 언급하는 `tools/gen-third-party-notices.mjs`는 전체 저장소 도구이므로 이 격리 intake에 도입하거나 실행하지 않았다.

배포 전에는 별도 release 경로에서 실제 번들 기준 third-party license text와 Apache-2.0 제4조 의무를 검토해야 한다. Apache-2.0 제6조에 따라 GenOffice/Mainfunc 이름은 provenance 설명 외 제품명·로고·endorsement에 사용하지 않는다.

## Kill criteria

다음 중 하나면 adapter intake/배포를 중단한다.

1. commit/remote/라이선스 복제본 검증 실패 또는 upstream commit 재작성 징후
2. `ee`, Electron/AI 앱, 다른 workspace, branding asset이 후보에 등장
3. engine runtime import 또는 lockfile production closure가 allowlist/SBOM과 불일치
4. copyleft/unknown license가 새로 유입됐는데 배포 방식과 의무를 검토하지 않음
5. 실제 배포물의 third-party notice·source modification notice·NOTICE 전달 방법이 정해지지 않음
6. `packages/pptx-engine`를 넘는 기능이 필요해져 현재 Apache core 격리 경계로 해결할 수 없음

검사가 통과해도 법률 자문이나 최종 배포 승인으로 해석하지 않는다.
