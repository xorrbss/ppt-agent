# Authored renderer runtime boundary trace

## 조사 범위와 판정 규칙

- 조사 기준: `main` / `b387a34735482d4f6dfbf2b955fbf19fd0d2315e`
- 조사일: 2026-08-13 (Asia/Seoul)
- 시작 dirty snapshot: `?? docs/authored-only-vnext-architecture-design-20260813.md` — 기존 사용자 파일이며 이 조사에서 수정하지 않았다.
- 범위: FastAPI `slide_capture`, Next authored-hybrid Chrome runner, `V1ContentRender`에서 도달하는 authored preview, Electron main/preload/IPC/window navigation.
- 방법: 소스 정적 추적과 공식 문서 대조를 수행하고, 메인 실험이 local-only canary로 생성한 baseline JSON 두 개를 직접 검토했다. 이 하위 작업 자체는 hostile payload를 실행하지 않았고 외부 exfiltration, metadata endpoint, crash/host-stability case는 어느 실험에서도 실행하지 않았다.
- 아래에서 **확인 사실**은 현재 소스가 직접 보장하는 동작, **회귀 가설**은 옵션 제거 시 예상되는 호환성 영향, **미검증**은 격리된 negative test가 필요한 항목이다.

## 결론 요약

현재 경로는 authored-only GA의 `host read 0 / external·loopback·metadata egress 0 / child·IPC 0 / secret log 0 / resource escape 0`을 정적으로 입증하지 못한다. 오히려 다음 P0 차단 증거가 있다.

1. FastAPI authored renderer는 기본적으로 재사용 Chromium을 `--no-sandbox`로 띄우고, 실패 시 역시 `--no-sandbox`인 `file://` one-shot renderer로 후퇴한다. 어느 경로에도 요청 차단, CSP 또는 OS egress deny가 없다.
2. Next hybrid runner에는 local deny proxy와 정적 preflight가 있지만 여전히 `--no-sandbox`와 `--allow-file-access-from-files`를 사용한다. preflight는 AST/DOM allowlist가 아니라 정규식 기반이며, runner 및 extractor는 preflight 없이 직접 호출 가능한 public 함수다.
3. 일반 authored preview의 `sandbox=""`는 script를 차단하지만 subresource network deny는 아니다. smart preview는 `sandbox="allow-scripts"`와 외부 CDN script를 사용하고, Tailwind runtime 준비 뒤에는 authored HTML을 Electron preload가 있는 상위 Next 문서에 `innerHTML`로 직접 삽입한다.
4. Electron 주 창은 `webSecurity:false`, `sandbox:false`이며 preload bridge를 노출한다. bridge에는 전체 user config 조회/변경, app-data/temp 파일 읽기, 파일 이동, 파일 쓰기, 로그 쓰기, export child-process 유발, 임의 URL metadata `BrowserWindow` 생성이 포함된다. IPC handler에는 sender/origin 검증이 없다.
5. Electron이 띄운 FastAPI/Next/export child는 `process.env`를 상속한다. API key와 DB URL이 FastAPI child에 명시적으로 전달되고, Next 및 export child에도 부모 환경 전체가 전달된다. renderer Chrome도 별도 `env` 없이 spawn되어 Next 환경을 상속한다.

따라서 현재 gate 판정은 FastAPI one-shot과 Next low-level runner의 `host read 0`, FastAPI one-shot의 `loopback egress 0`, 정적으로 reachable한 `external egress` 및 Electron `child/IPC 0`가 **FAIL**이다. 실제 외부·metadata egress, secret log, process/resource escape는 안전 격리 실험 전까지 **INCONCLUSIVE**다.

### 전달받아 직접 검토한 local-only 실행 증거

- FastAPI one-shot `_render_html_to_png_subprocess`는 renderer가 만든 work directory 밖의 별도 임시 PNG를 `file://`로 실제 읽었다. canary pixel `[217,35,172]`, 1/1 성공이다. 같은 render에서 `127.0.0.1` sink로 1회 GET이 도달하고 응답 자원의 pixel `[22,163,74]`가 캡처됐다: [current-fastapi-baseline.json](C:/project/PPT-agent/ppt-agent/docs/experiments/authored-vnext/04-renderer-security/results/current-fastapi-baseline.json:9).
- Next low-level `runAuthoredHybridChrome`도 work directory 밖의 별도 임시 PNG를 실제 읽어 동일 canary RGBA `[217,35,172,255]`를 캡처했다. 반면 authored loopback resource는 runner deny proxy 때문에 sink request/response가 0이었다: [current-next-baseline.json](C:/project/PPT-agent/ppt-agent/docs/experiments/authored-vnext/04-renderer-security/results/current-next-baseline.json:8).
- 두 실행 모두 standalone Chrome에서 Node/Electron IPC surface는 관찰되지 않았다. process creation/OS syscall, 실제 외부 DNS/HTTP, metadata-shaped target은 안전 정책상 실행하지 않아 `INCONCLUSIVE`다.
- 이 결과는 FastAPI one-shot과 Next **low-level runner**의 증거다. FastAPI 기본 reusable CDP, Next 정상 export의 preflight 결합, browser preview, Electron은 이 두 JSON으로 동적 판정할 수 없다.

## 런타임별 경계 지도

| 경로 | 입력 위치 | 프로세스/권한 | file 경계 | network 경계 | IPC/process 경계 | 정적 P0 판정 |
|---|---|---|---|---|---|---|
| FastAPI reusable authored Chrome | in-memory authored HTML | FastAPI 권한의 Chromium child, `--no-sandbox`, CDP loopback | `about:blank`에 `Page.setDocumentContent`; OS read-only/tmpfs/chroot 없음 | 차단 없음; CDP가 `127.0.0.1:<random>`에 열림 | subprocess tree, remote debugging enabled | FAIL/INCONCLUSIVE |
| FastAPI one-shot fallback | temp `slide.html` | FastAPI 권한의 Chromium child, `--no-sandbox` | `file://<temp>/slide.html`; host temp와 일반 filesystem namespace | proxy/request interception/CSP 없음 | timeout 후 tree-kill만 있음 | FAIL/INCONCLUSIVE |
| Next authored-hybrid | temp `slide.html` | Next 권한의 Chromium child, `--no-sandbox` | `file://`; `--allow-file-access-from-files` | local drop proxy + loopback proxy 강제; OS egress deny 아님 | bounded stdout/stderr, timeout/tree cleanup | file: FAIL, net: 방어 있으나 P0 미입증 |
| 일반 authored preview | iframe `srcDoc` | 브라우저/Electron renderer 내 frame | `sandbox=""`; script 비활성 | subresource egress 차단 없음 | script가 막혀 직접 bridge 호출은 차단 | net: FAIL |
| smart authored preview | iframe 및 상위 DOM | 브라우저/Electron renderer | iframe은 `allow-scripts`; 준비 후 parent DOM `innerHTML` | CDN 3개 + authored subresource 가능 | Electron이면 parent에 preload bridge 존재 | FAIL |
| Electron app window | local Next origin | `webSecurity:false`, `sandbox:false`, preload | app-data/temp read 및 write/move IPC | SOP 비활성; session request deny 없음 | broad IPC bridge, sender 검증 없음 | FAIL |
| Electron metadata window | renderer 제공 URL | hidden BrowserWindow + preload + `webSecurity:false` | URL이 `file:`인지 검증하지 않음 | 임의 URL `loadURL`; navigation controller 미부착 | 같은 IPC bridge를 remote/file page에 제공 가능 | FAIL |

## 1. FastAPI `slide_capture.py`

### 실행 경로와 옵션 — 확인 사실

- Chrome executable은 `CHROME_PATH`, `PUPPETEER_EXECUTABLE_PATH`, `CHROMIUM_PATH` 순으로 읽고 설치 경로를 탐색한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1037).
- authored render는 기본값 `AUTHORED_CHROME_REUSE=1`로 reusable 경로를 우선하고 실패하면 one-shot으로 후퇴한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1604), [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1693).
- reusable Chromium 옵션은 `--headless=new`, `--disable-gpu`, **`--no-sandbox`**, `--remote-debugging-port=0`, `--remote-allow-origins=*`, 임시 `--user-data-dir`이다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1155).
- CDP endpoint는 `http://127.0.0.1:<DevToolsActivePort>`이며 `/json/new`, websocket, `/json/close`를 `aiohttp`로 사용한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1134), [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1334). random port이지만 network namespace 분리나 인증은 없다.
- hostile HTML은 `about:blank` target에 `Page.setDocumentContent`로 들어간다. 이후 font readiness와 injected runtime script를 `Runtime.evaluate`하고 screenshot을 캡처한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1373). 이 경로에는 URL/element allowlist, request interception, CSP 설정이 없다.
- one-shot authored fallback은 input을 임시 `slide.html`에 기록한 뒤 **`--no-sandbox`** Chromium에 `pathlib.Path(...).as_uri()` 즉 `file://` URL로 넘긴다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1501), [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1521).
- pixel contrast probe도 별도 **`--no-sandbox`** Chromium과 `file://` document를 사용한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1558).
- generic `/pdf-maker` deck capture 역시 `--no-sandbox`이며 전달받은 `base_url`로 직접 이동한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1241).
- `subprocess.Popen`에 `env`, uid/gid, rlimit, cwd confinement를 지정하지 않는다. 따라서 FastAPI 프로세스의 OS identity와 환경을 상속한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:53), [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1177).
- timeout 시 Windows `taskkill /F /T`, POSIX process-group `SIGKILL`로 child tree를 정리한다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:35). render concurrency 기본 4와 cache limit은 폭주 완화이지만 DOM/image/font byte cap이나 OS CPU/memory/pid limit은 아니다: [slide_capture.py](C:/project/PPT-agent/ppt-agent/servers/fastapi/utils/slide_capture.py:1659).

### file/network/process 영향

- `file://` one-shot 문서에서 authored script/event가 실행되는 것을 막는 코드가 없다. explicit `--allow-file-access-from-files`는 없지만 file-origin이 어떤 host path와 scheme을 실제로 읽을 수 있는지는 플랫폼/Chrome 버전별 격리 실험이 필요하다.
- absolute HTTP(S), loopback, link preload, CSS `@import`/`url()`, SVG/object/iframe의 browser request를 막는 코드가 없다. 따라서 egress 0은 정적으로 **FAIL**이다.
- reusable CDP의 `--remote-allow-origins=*`는 DevTools websocket origin 검사를 넓힌다. endpoint는 loopback이나 별도 network namespace가 없으므로 local peer threat는 미검증이다.
- Chromium child 자체가 FastAPI 환경을 상속한다. 웹 JS가 곧바로 `process.env`를 읽는다는 뜻은 아니지만, renderer/browser compromise의 blast radius에는 FastAPI와 같은 host filesystem/network identity 및 inherited environment가 포함된다.

## 2. Next authored-hybrid Chrome

### 기존 방어 — 확인 사실

- preflight는 HTML 2 MiB, embedded image/font 각각 8 MiB를 제한한다: [security.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/security.ts:4).
- `<script>`, frame/object/embed/applet, form control, link/base/foreignObject, refresh, event handler/srcdoc/srcset/ping, 위험 protocol, CSS import/image-set/escape/expression을 정규식으로 거부한다. CSS URL과 URL attribute는 fragment 또는 magic이 확인된 raster `data:`만 허용한다: [security.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/security.ts:82).
- 정상 hybrid export는 Google Fonts collector 후 `htmlForPreflight`를 검사하고 실패 시 image fallback으로 종료한 뒤에만 Chrome extraction을 호출한다: [export.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/export.ts:252).
- Google Fonts network trust boundary는 HTTPS exact host `fonts.googleapis.com` / `fonts.gstatic.com`, query/path, redirect hop을 검증하고 cookie/authorization/referrer 없이 manual redirect로 fetch한다: [google-fonts-url-policy.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/google-fonts-url-policy.ts:9), [google-fonts-fetch.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/google-fonts-fetch.ts:278). 이것은 allowlisted egress이지 zero-egress는 아니다.
- Chrome runner는 `127.0.0.1` random port의 TCP server를 만들고 모든 socket을 즉시 destroy한다: [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:52).
- Chrome에 `--proxy-server=<local deny>`, `--proxy-bypass-list=<-loopback>`를 주어 Chromium의 implicit loopback bypass도 제거한다: [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:540). 따라서 HTTP(S) 및 loopback browser requests는 proxy에서 drop될 의도다.
- stdout/stderr 합계는 stream별 최대 64 MiB, 기본 timeout은 20초이며 timeout/완료 후 process tree와 unique profile process를 정리한다: [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:12), [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:327), [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:342).

### 남은 경계 결함 — 확인 사실

- runner는 여전히 **`--no-sandbox`**, **`--allow-file-access-from-files`**, `file://<workdir>/slide.html`을 사용한다: [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:527).
- TCP proxy는 HTTP(S) transport에 대한 application-layer 완화일 뿐 `file:`, browser/process exploit, DNS가 proxy 이전에 발생하는지 여부, OS 수준 socket syscall을 봉쇄하지 않는다. egress deny가 실패해도 별도 audit sink가 없어서 시도/deny evidence가 남지 않는다.
- preflight는 parser/AST/DOM allowlist가 아니라 원문 정규식 검사다. parser differential 및 mutation 후 DOM은 이 정적 조사에서 증명하지 못했다.
- `runAuthoredHybridChrome`와 `extractAuthoredSlideDom`은 public이며 함수 내부에서 preflight를 강제하지 않는다: [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:512), [extractor.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/extractor.ts:88). `renderTemplateV2SourceHtml`은 runner를 직접 호출한다: [source-render.mjs](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/export-fidelity/source-render.mjs:3).
- instrumentation은 authored CSP meta를 제거해 first-party inline analyser를 실행하고, `baseUrl`로 `http:`, `https:`, `file:`을 허용한다: [html-instrumentation.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/html-instrumentation.ts:86), [html-instrumentation.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/html-instrumentation.ts:124). 정상 export preflight와 결합하면 제한되지만 direct caller의 defense-in-depth는 아니다.
- runner `spawn`에는 `env`가 없어 Next 프로세스 환경 전체를 Chromium에 상속한다: [chrome-runner.ts](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome-runner.ts:342).

## 3. Next preview iframe 및 `V1ContentRender`

### 일반 authored 경로 — 확인 사실

- `V1ContentRender`는 authored slide에서 `preferAuthoredHtml`이 false면 PNG를 렌더한다. true이고 driver가 `smart`가 아니면 `AuthoredSlidePreview`에 persisted `html_content`를 넘긴다: [V1ContentRender.tsx](<C:/project/PPT-agent/ppt-agent/servers/nextjs/app/(presentation-generator)/components/V1ContentRender.tsx:131>).
- `AuthoredSlidePreview`는 raw `srcDoc`를 `sandbox=""`, `referrerPolicy="no-referrer"`, `loading="lazy"` iframe에 넣는다: [AuthoredSlidePreview.tsx](<C:/project/PPT-agent/ppt-agent/servers/nextjs/app/(presentation-generator)/components/AuthoredSlidePreview.tsx:22>). empty sandbox는 script/forms/top-navigation 등의 capability를 주지 않지만 image/font/CSS 같은 subresource network를 zero-egress로 만들지는 않는다.
- 이 경로에는 CSP나 request interceptor가 없다. 따라서 script가 비활성이라도 hostile `<img>`, CSS `url()` 등 local/external request attempt는 가능하며 gate상 egress 0은 **FAIL**이다.

### smart 경로 — 확인 사실

- `V1ContentRender`는 `preferAuthoredHtml`이며 server-issued profile의 `driver_id === "smart"`이면 `SmartHtmlEditor`를 사용한다: [V1ContentRender.tsx](<C:/project/PPT-agent/ppt-agent/servers/nextjs/app/(presentation-generator)/components/V1ContentRender.tsx:140>).
- `SmartHtmlSlide`는 authored body에 Tailwind CDN과 chart script 두 개, font URL을 더한 `srcDoc`을 만든다: [SmartHtmlSlide.tsx](<C:/project/PPT-agent/ppt-agent/servers/nextjs/app/(presentation-generator)/components/SmartHtmlSlide.tsx:21>), [SmartHtmlSlide.tsx](<C:/project/PPT-agent/ppt-agent/servers/nextjs/app/(presentation-generator)/components/SmartHtmlSlide.tsx:34>). 이 iframe은 `sandbox="allow-scripts"`로 authored script/event도 실행할 수 있고 CSP/referrerPolicy가 없다: [SmartHtmlSlide.tsx](<C:/project/PPT-agent/ppt-agent/servers/nextjs/app/(presentation-generator)/components/SmartHtmlSlide.tsx:105>).
- Tailwind runtime이 준비되면 `SmartHtmlEditor`는 iframe이 아니라 상위 Next document의 `container.innerHTML = html`로 authored markup을 직접 삽입하고 여러 node를 `contenteditable`로 만든다: [SmartHtmlEditor.tsx](<C:/project/PPT-agent/ppt-agent/servers/nextjs/app/(presentation-generator)/components/SmartHtmlEditor.tsx:174>). `innerHTML`로 삽입된 `<script>` 자체는 일반 브라우저에서 자동 실행되지 않지만 event handler, subresource, iframe/srcdoc/object 등 active DOM의 실행/요청 surface는 제거되지 않는다.
- Electron에서 상위 Next document에는 아래 preload bridge가 존재한다. 따라서 smart DOM 안에서 실행을 얻은 authored event/child frame이 parent global에 도달하는 chain은 P0 IPC negative test의 최우선 대상이다.

## 4. Electron window, preload, IPC, process

### 주 창 옵션 — 확인 사실

- 주 창의 모든 presentation window option은 `webSecurity:false`, `contextIsolation:true`, `nodeIntegration:false`, **`sandbox:false`**, preload를 사용한다: [main.ts](C:/project/PPT-agent/ppt-agent/electron/app/main.ts:196), [main.ts](C:/project/PPT-agent/ppt-agent/electron/app/main.ts:218).
- Linux에서는 packaged `chrome-sandbox`가 root-owned mode 4755가 아니거나 stat에 실패하면 application-wide `no-sandbox` switch를 추가한다: [main.ts](C:/project/PPT-agent/ppt-agent/electron/app/main.ts:53).
- 주 창은 `http://127.0.0.1:<nextjsPort>`를 trusted origin으로 정하고 로드한다: [constants.ts](C:/project/PPT-agent/ppt-agent/electron/app/utils/constants.ts:6), [main.ts](C:/project/PPT-agent/ppt-agent/electron/app/main.ts:553).
- `window-security`는 main-frame `will-navigate`/`will-redirect`와 popup을 same app origin/presentation path로 제한하고 외부 HTTP(S)는 OS browser로 보낸다: [window-security.ts](C:/project/PPT-agent/ppt-agent/electron/app/utils/window-security.ts:97), [window-security.ts](C:/project/PPT-agent/ppt-agent/electron/app/utils/window-security.ts:154). 이것은 subresource request, fetch/XHR, iframe resource 또는 file read 정책이 아니다.
- repository search에서 production Electron session의 `webRequest.onBeforeRequest`, `setPermissionRequestHandler`, `will-attach-webview`나 enforced CSP는 발견되지 않았다. `webviewTag`는 명시하지 않아 Electron default false에 의존한다.

### preload가 renderer에 주는 capability — 확인 사실

- `window.env`에 FastAPI/Next URL뿐 아니라 `TEMP_DIRECTORY`, `NEXT_PUBLIC_USER_CONFIG_PATH`를 노출한다: [preloads/index.ts](C:/project/PPT-agent/ppt-agent/electron/app/preloads/index.ts:4).
- `window.electron`에는 `getUserConfig`/`setUserConfig`, `readFile`, `fileDownloaded`, `exportPresentation`, `getSlideMetadata`, `uploadImage`, log write/clear 등이 노출된다: [preloads/index.ts](C:/project/PPT-agent/ppt-agent/electron/app/preloads/index.ts:13).
- `contextIsolation:true`는 preload 내부 Electron object를 분리하지만 `contextBridge.exposeInMainWorld`로 명시적으로 내보낸 메서드는 loaded page가 호출할 수 있다. 즉 authored content가 상위 page JS execution을 획득하면 이 bridge는 capability boundary다.

### IPC handler 경계 — 확인 사실

- 모든 handler는 `ipcMain.handle`의 event sender/origin을 검증하지 않고 `_`로 버린다. setup 시 export, user config, metadata, read-file, settings, upload, log, API handler가 모두 등록된다: [ipc/index.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/index.ts:12).
- `get-user-config`는 persisted config 전체를 반환하고 `set-user-config`는 저장/런타임 refresh한다: [user_config_handlers.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/user_config_handlers.ts:5). API keys가 user config에 저장되는 것은 main startup의 `setUserConfig` 입력에서 확인된다: [main.ts](C:/project/PPT-agent/ppt-agent/electron/app/main.ts:503).
- `read-file`은 `realpath`로 symlink/`..`를 방어하고 app-data/temp roots에 제한한다. 그러나 그 roots에는 user config path가 포함되므로 host arbitrary read는 막아도 secret-bearing app data read capability는 남는다: [read_file.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/read_file.ts:5).
- `file-downloaded`는 renderer가 준 source path를 base-dir 검증 없이 Downloads로 `rename`한다: [export_handlers.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/export_handlers.ts:50).
- `upload-image`는 size/magic 검사 없이 renderer Buffer를 app user-data uploads에 쓰고 `file://` URL을 반환한다: [upload_image.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/upload_image.ts:7).
- log bridge는 arbitrary renderer string을 `nextjs.log`에 append하고 로그 삭제도 제공한다: [log_handler.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/log_handler.ts:6). renderer가 secret을 먼저 획득할 수 있는지와 결합한 secret-log negative test가 필요하다.
- `export-presentation`은 renderer 입력으로 fixed export child process를 유발하고, export child에 `...process.env`를 넘긴다: [export_handlers.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/export_handlers.ts:60), [export_handlers.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/export_handlers.ts:121), [export_handlers.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/export_handlers.ts:332). 임의 binary 선택은 아니지만 `child/IPC 0` 기준에는 위배되는 reachable process side effect다.
- `get-slide-metadata`는 renderer 제공 `url`을 검증 없이 hidden BrowserWindow에 로드한다. 이 창은 `webSecurity:false`와 같은 preload를 가지며 `windowSecurity.attach`를 호출하지 않는다: [slide_metadata.ts](C:/project/PPT-agent/ppt-agent/electron/app/ipc/slide_metadata.ts:88). 따라서 arbitrary HTTP(S)/file URL load, remote/file content에 preload bridge 노출, 장시간 wait가 가능한 별도 P0 경계다.

### child 환경과 host 경계 — 확인 사실

- Electron은 FastAPI child에 OpenAI/Google/Anthropic/custom/Bedrock 등 API key, DB URL, app-data/temp/user config 경로를 명시적으로 전달한다: [main.ts](C:/project/PPT-agent/ppt-agent/electron/app/main.ts:277).
- server launcher는 FastAPI와 Next child 모두 `env: { ...process.env, ...env }`로 실행하며 stdout/stderr를 host log file에 append한다: [servers.ts](C:/project/PPT-agent/ppt-agent/electron/app/utils/servers.ts:97), [servers.ts](C:/project/PPT-agent/ppt-agent/electron/app/utils/servers.ts:171).
- export child도 부모 환경 전체를 받는다. 어떤 child에도 non-root drop, read-only root, tmpfs, mount namespace, network namespace, cgroup/job resource cap 같은 zero-egress harness 설정은 없다.

## 5. 위험 옵션 blast radius와 제거 회귀 가설

### `--no-sandbox` (FastAPI, Next hybrid, Linux Electron fallback)

**확인된 blast radius**

- Chromium의 renderer/utility OS sandbox를 전부 끈다. hostile HTML 처리 중 browser/renderer exploit이 성공하면 동일 host identity의 filesystem, network, process/env를 향한 추가 OS sandbox가 없다.
- FastAPI와 Next renderer는 host child이고 inherited environment를 받으므로 container/OS 외부 격리가 없다면 compromise boundary가 곧 service account/desktop user boundary다.
- Electron Linux fallback은 main/presentation/metadata window 전체의 process sandbox까지 application-wide로 약화한다.

**제거 회귀 가설**

- Linux root container, user namespace가 금지된 host, packaged `chrome-sandbox` ownership/mode가 잘못된 설치에서는 Chromium startup이 실패할 수 있다. 현재 Electron 코드 주석과 fallback 조건이 이 호환성 문제를 직접 나타낸다.
- Windows/macOS 및 올바른 Linux sandbox 설치에서는 render semantic 회귀보다 launch/setup 회귀가 주 위험이다. `chrome://sandbox` 또는 process-level sandbox assertion이 있는 격리 CI matrix가 필요하다.
- 해결 방향은 flag 유지가 아니라 non-root 실행 + user namespace/seccomp 또는 올바른 `chrome-sandbox` 배포다. machine-wide permission 변경은 이 harness의 허용 범위가 아니다.

### `--allow-file-access-from-files` (Next hybrid)

**확인된 blast radius**

- `file://` authored document가 다른 local file origin에 접근할 수 있도록 범위를 넓힌다. local deny proxy는 이 scheme을 관찰하거나 차단하지 못한다.
- `normalizeAuthoredHybridBaseUrl`이 `file:`을 받아들이며 public extractor는 preflight를 강제하지 않아, direct caller가 local base와 relative asset을 결합할 수 있다.

**제거 회귀 가설**

- 정상 authored export는 preflight 후 self-contained data raster/trusted collected font를 사용하므로 이 flag 의존성이 낮아야 한다.
- 반면 기존 Chrome test는 외부 temp directory의 `style.css`를 `file:` base URL로 읽어 style drift를 검증한다. flag 제거 시 이 direct-extractor test와 local relative stylesheet 호환성이 깨질 가능성이 높다: [chrome.test.mjs](C:/project/PPT-agent/ppt-agent/servers/nextjs/lib/authored-hybrid/chrome.test.mjs:704).
- Template V2 direct runner가 local relative asset을 입력으로 받을 수 있는지는 caller corpus로 확인해야 한다. production authored gate와 QA-only local fixture를 분리하는 것이 바람직하다.

### Electron `webSecurity:false`

**확인된 blast radius**

- Electron 공식 정의상 same-origin policy를 끄고 `allowRunningInsecureContent`를 true로 만든다. 따라서 renderer XSS/authored DOM execution과 preload bridge의 결합 위험이 커지고 cross-origin response 접근·mixed content 제한이 약화된다.
- main navigation policy는 top-level만 다루므로 이 설정으로 약화된 subresource/fetch/frame 경계를 복구하지 않는다.

**제거 회귀 가설**

- `upload-image`가 `file://` URL을 반환하고 UI는 HTTP loopback origin이므로 local file image 표시가 차단될 가능성이 있다.
- cross-origin API 호출이 숨어 있다면 CORS 오류가 드러난다. 현재 의도된 full flow는 same-origin proxy이므로 이는 proxy/API route를 바로잡아야 할 회귀 신호다.
- metadata `capturePage`의 remote image 렌더 자체는 CORS와 별개일 수 있으나 file/mixed resources는 영향받을 수 있어 targeted visual tests가 필요하다.

### Electron `sandbox:false` 및 preload

**확인된 blast radius**

- main/presentation renderer에서 Chromium OS-level process sandbox를 명시적으로 끈다. `nodeIntegration:false`와 `contextIsolation:true`는 유지되지만 renderer exploit에 대한 OS confinement가 사라진다.
- preload는 `process.env`와 IPC를 사용하며, unsandboxed preload가 broad bridge를 제공한다.

**제거 회귀 가설**

- Electron sandboxed preload는 제한된 Electron/Node API만 제공한다. 현재 preload의 `contextBridge`, `ipcRenderer`, 제한된 `process` 사용이 실제 packaged build에서 동작하는지 확인해야 한다.
- `process.env`로 읽는 public URL/path 값이 sandboxed preload에서 보이지 않으면 immutable `additionalArguments` 또는 main IPC bootstrap으로 옮겨야 한다.
- Linux application-wide `no-sandbox`가 남아 있으면 per-window `sandbox:true`만으로는 목표를 달성했다고 볼 수 없다.

## 6. P0 gate에 넘길 정적 판정

| 기준 | 현재 정적 판정 | 근거 | 필요한 isolated negative test |
|---|---|---|---|
| host read 0 | **FAIL** (측정 경로); host 전체 범위는 미검증 | FastAPI one-shot과 Next low-level runner가 workdir 밖 temp canary read 성공; Electron app-data/temp read capability | 정상 preflight/reusable/Electron별 canary와 host root deny audit |
| external egress 0 | **FAIL** | FastAPI에 deny 없음; SmartHtmlSlide CDN; Google Fonts allowlisted fetch | local DNS/HTTP sink + OS egress deny, 실제 외부 목적지는 사용 금지 |
| loopback/metadata egress 0 | **FAIL/INCONCLUSIVE** | preview/FastAPI deny 없음; metadata window arbitrary URL; Next proxy는 loopback drop 의도 | 127.0.0.1 sink와 metadata-shaped local address를 별도 계측 |
| child/IPC 0 | **FAIL** | Electron bridge와 export/metadata IPC; FastAPI/Next Chromium child | hostile authored event/srcdoc에서 IPC counter만 증가시키는 canary |
| secret log 0 | **INCONCLUSIVE** | full config read + arbitrary log write capability; server stdout/stderr host log | synthetic secret만 사용해 JSON/JUnit에서 stdout/stderr/log redaction 확인 |
| resource escape 0 | **INCONCLUSIVE** | timeout/output/concurrency cap은 있으나 OS memory/CPU/pid/file/network limit 없음 | cgroup/job/container 전용 oversized/timeout cases; host에서 직접 실행 금지 |

## 7. 공식 보안 기준

- [Electron Security](https://www.electronjs.org/docs/latest/tutorial/security): process sandbox 활성화, `webSecurity` 비활성화 금지, restrictive CSP, 모든 IPC sender 검증, `file://` 회피, untrusted content에 Electron API 미노출을 권고한다.
- [Electron BrowserWindow](https://www.electronjs.org/docs/latest/api/browser-window): `webSecurity:false`가 same-origin policy를 끄고 insecure content 실행을 허용하며, `sandbox`가 renderer의 Chromium OS sandbox와 Node engine을 제어한다고 명시한다.
- [Electron Context Isolation](https://www.electronjs.org/docs/latest/tutorial/context-isolation): context isolation은 preload와 page의 JS context를 분리하지만 `contextBridge`로 명시적으로 노출한 API는 page에서 호출 가능하다.
- [Chromium Linux Sandboxing](https://chromium.googlesource.com/chromium/src/+/0e94f26e8/docs/linux_sandboxing.md): `--no-sandbox`가 모든 sandboxing을 끄며 renderer를 제한된 privilege로 처리하기 위한 layer를 설명한다.
- [Chromium AppArmor/userns guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md): `--no-sandbox`가 critical security features를 끄므로 open web 처리에 사용하지 말라고 명시한다.

## 미검증 및 안전 제한

- 이 하위 작업 자체는 payload를 실행하지 않았다. 다만 직접 검토한 메인 실험 JSON은 local file canary와 `127.0.0.1` HTTP sink를 사용했으며 그 결과는 위에 실행 증거로 분리했다.
- 실제 외부 DNS/HTTP, metadata-shaped target, Electron IPC invocation, secret log injection은 수행하지 않았다.
- real metadata IP, cloud metadata endpoint, 외부 DNS/HTTP endpoint는 접촉하지 않았다.
- crash, OOM, fork bomb, decompression bomb, oversized font/image/DOM은 host 안정성 때문에 실행하지 않았다. non-root/read-only/tmpfs/cgroup-or-job/network-namespace가 검증된 harness에서만 실행해야 한다.
- proxy bypass, sandboxed iframe과 Electron `webSecurity:false`의 세부 상호작용, Chrome file-origin read 가능 범위는 플랫폼별 dynamic test 없이는 단정하지 않는다.
- Electron main/preload/IPC의 capability chain은 source상 존재하지만 authored payload에서 어떤 branch와 user gesture로 도달하는지는 안전한 local-only canary로 재현해야 한다.
