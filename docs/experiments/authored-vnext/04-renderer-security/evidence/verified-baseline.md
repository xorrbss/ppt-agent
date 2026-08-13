# 현행 renderer security baseline

실행일: 2026-08-13 (Asia/Seoul)

## 확인 사실

- FastAPI one-shot authored renderer는 임시 HTML을 `file://`로 열며
  `--no-sandbox`를 전달한다
  (`servers/fastapi/utils/slide_capture.py:1501-1545`). 재사용 Chrome도
  `--no-sandbox`로 시작한다 (`:1155-1171`).
- Next hybrid 저수준 runner는 `file://`, `--no-sandbox`,
  `--allow-file-access-from-files`를 함께 사용한다. HTTP 계열 요청은 매 실행
  127.0.0.1 drop proxy로 보낸다
  (`servers/nextjs/lib/authored-hybrid/chrome-runner.ts:527-565`). 공개 hybrid
  진입 전 preflight는 active markup, file/network URL, CSS import/url 및 크기
  초과를 fail closed한다
  (`servers/nextjs/lib/authored-hybrid/security.ts:82-159`).
- 일반 authored preview는 빈 iframe sandbox를 사용하므로 스크립트 실행은
  금지되지만, sandbox만으로 CSS/image/font의 네트워크 로드는 막지 않는다
  (`AuthoredSlidePreview.tsx:37-45`). Smart preview는
  `sandbox="allow-scripts"`이며 원격 Tailwind와 Chart bootstrap 및 전달받은
  font URL을 삽입한다 (`SmartHtmlSlide.tsx:21-55,105-111`).
- Electron 주 창은 `contextIsolation:true`, `nodeIntegration:false`이지만
  `webSecurity:false`, `sandbox:false`, preload 활성 상태다
  (`electron/app/main.ts:196-215`). 개발/기본 start 명령도 Electron 전체에
  `--no-sandbox`를 전달한다 (`electron/package.json:28-31`).
- preload는 user config 조회/설정, 허용 디렉터리 파일 읽기, export, metadata,
  image, log IPC를 main world에 노출한다
  (`electron/app/preloads/index.ts:13-40`). 적어도 `get-user-config`와
  `write-nextjs-log` handler는 sender frame을 검증하지 않는다
  (`electron/app/ipc/user_config_handlers.ts`, `electron/app/ipc/log_handler.ts`).

Electron 공식 보안 checklist도 process sandbox 활성화, `webSecurity` 유지,
제한 CSP, IPC sender 검증, `file://` 회피 및 untrusted content에 Electron API를
노출하지 않을 것을 권고한다:
<https://www.electronjs.org/docs/latest/tutorial/security>.
Process sandbox의 목적과 `sandbox:false` 위험은 다음 공식 문서에 설명되어
있다: <https://www.electronjs.org/docs/latest/tutorial/sandbox/>.

## 실행 결과

모든 동적 요청은 임시 파일과 ephemeral `127.0.0.1` sink로만 수행했다.
외부 DNS/HTTP 및 metadata 주소는 요청하지 않았다.

| 경로 | host file read | loopback | privileged renderer surface | 판정 |
|---|---:|---:|---:|---|
| FastAPI one-shot fallback | 1/1 성공 | 1 request, 1 response | Node/Electron API 없음 | P0 FAIL |
| Next low-level runner (preflight 우회 직접 호출) | 1/1 성공 | sink request 0 | Node/Electron API 없음 | P0 FAIL |
| Next public preflight + deny proxy focused test | file/network payload 정적 거절 | bypass Chrome 요청 0 | 해당 없음 | 9/9 PASS |
| Preview iframe | 미실행 | 미실행 | 미실행 | INCONCLUSIVE |
| Electron | 미실행 | 미실행 | 미실행 | INCONCLUSIVE |

원시 결과:

- `results/current-fastapi-baseline.json`
- `results/current-next-baseline.json`

실행한 focused test:

```text
cd servers/nextjs
node --test lib/authored-hybrid/security.test.mjs
# tests 9, pass 9, fail 0, skipped 0
```

## 추론

- FastAPI는 현재 renderer 경계 자체가 host file과 loopback을 차단하지 않는다.
  따라서 authored HTML이 신뢰된 생성 결과라는 가정이 깨지면 둘 다 즉시
  P0 기준을 위반한다.
- Next는 공개 preflight와 deny proxy가 HTTP 요청을 막는 방어층이지만,
  저수준 renderer 자체에는 host-file capability가 남아 있다. preflight의
  parser differential/새 URL surface/직접 호출 회귀가 곧 host read로 이어질
  수 있으므로 `--allow-file-access-from-files` 제거와 `file://` 폐기가 별도
  gate여야 한다.
- Electron에서 authored iframe이 top-level preload API에 실제로 도달한다고
  확인한 것은 아니다. 다만 top-level DOM XSS, navigation/policy 우회 또는
  `webSecurity:false`와 결합한 origin isolation 실패가 생기면 exposed IPC의
  blast radius가 config secret read/write, app-data read, log write 및 process
  spawning export handler까지 확대된다.

## 미검증 및 안전 제한

- 실제 외부 DNS/HTTP, link-local/cloud metadata target은 정책상 실행하지
  않았다. 관련 baseline 수치는 `inconclusive`이며 0으로 간주하지 않는다.
- process creation, IPC invocation, Electron iframe escape, browser crash exploit,
  fork bomb, memory exhaustion, decompression bomb, oversized real font/image는
  호스트 안정성 때문에 실행하지 않았다.
- OS syscall/network namespace 추적과 container hardened 동적 실행은 이 Windows
  호스트에 Docker CLI가 없어 수행하지 못했다. 정적 self-check와 CI 격리
  실행을 별도 gate로 둔다.
