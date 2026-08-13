# DeckMasterContract + long-title SlideSpec v2 vertical slice 결정 기록

작성일: 2026-08-13  
범위: 이 디렉터리의 한 장짜리 PoC와 그 검증 증거만 다룬다. 제품 전체 아키텍처 설계는 다루지 않는다.

## 결론

한 canonical input에서 HTML, 1280×720 PNG, 실제 PowerPoint master/layout/theme/placeholder 관계를 가진 PPTX를 생성했다. 패키지 read-back, Microsoft PowerPoint 16.0 open→SaveAs→reopen, PowerPoint PNG export, LibreOffice 26.2.5.2 PDF render를 모두 통과했다.

합격 판정은 다음과 같다.

| 기준 | 결과 | 직접 증거 |
| --- | --- | --- |
| repair 유발 구조 | package relationship/XML/CRC 오류 0; PowerPoint open-save-reopen 성공 | `output/verification-report.json`, `output/powerpoint-final/powerpoint-verification.json` |
| master/layout inventory | custom master 1, `Standard Title`/`Long Title` 2개 일치 | `output/manifest.json`, `output/verification-report.json` |
| 실제 master 관계 | slide1 → slideLayout3 → slideMaster2 → master-local theme3 | `output/verification-report.json` |
| placeholder 관계 | title idx 0, body idx 1; layout/slide 대응 | `output/verification-report.json` |
| furniture body 중복 | 0; 공통 furniture 5개는 custom master에만 존재 | `output/verification-report.json`, PowerPoint COM inventory |
| title clipping | HTML 0, PowerPoint 0; PowerPoint 3줄, bound 136.99pt / available 180.3pt | `output/browser-metrics.json`, PowerPoint COM inventory |
| digest 재현성 | canonical/master/font/content/spec digest 2회 모두 동일 | `output/reproducibility-report.json` |
| preview↔PPT 차이 | PowerPoint MAE 20.5637, LibreOffice MAE 23.2544 기록 | `output/verification-report.json` |

창 없는 PowerPoint 실행에서는 repair 대화상자 자체를 볼 수 없다. 따라서 “대화상자 0회 관찰”이라고 주장하지 않고, package structural error 0과 실제 PowerPoint open-save-reopen 무오류를 분리해 근거로 삼았다.

## canonical 계약

- `deckmaster-contract.schema.json`은 master furniture, standard/long-title layout, `locked`/`modelWritable`, font manifest, digest를 닫힌 schema로 정의한다.
- `canonical-input.json`의 furniture 5개는 `locked=true`, `modelWritable=false`이고, layout placeholder와 slide body는 `locked=false`, `modelWritable=true`이다.
- OOXML master furniture에는 `a:spLocks`의 `noMove`, `noResize`, `noEditPoints`, `noGrp`를 기록하고 description에도 ownership을 봉인했다.
- long-title placeholder는 title idx 0/body idx 1, 35pt, 최대 3줄, PowerPoint 기본 여백과 같은 좌우 9.6px(7.2pt), 상하 4.8px(3.6pt)을 사용한다.
- title은 canonical 3줄을 HTML과 PPTX에 동일한 강제 줄바꿈으로 전달하고 `a:noAutofit`을 사용한다. PowerPoint가 임의 축소해 digest와 preview를 바꾸지 않도록 한 선택이다.
- theme major/minor font와 `Hang`, `Jpan`, `Hans`, `Hant` mapping은 `Malgun Gothic`으로 고정한다. HTML fallback 순서는 `Malgun Gothic`, `Noto Sans CJK KR`, `Noto Sans KR`, `Arial Unicode MS`, `Arial`, `sans-serif`이다. Windows에서 실제 resolve한 regular/bold/semilight 6.68 파일의 경로·크기·SHA-256, installed/cloud/embedded 상태도 font manifest와 digest에 포함하고 빌드 시 다시 읽어 검증한다.

## 줄바꿈과 stress corpus

UAX #14 rev.55 / Unicode 17.0.0을 버전 고정했다. UAX #14는 break opportunity를 정하고 실제 폭에 맞는 최종 break 선택은 상위 line-fitting 계층의 책임이므로, 이 PoC는 `ko-space-first-with-id-fallback-v1` tailoring과 최종 강제 줄바꿈을 canonical digest 입력으로 명시한다.

stress corpus는 15개다. 한국어·영어·한영 혼합·숫자/퍼센트/괄호를 각각 1~3줄로 포함하고, `「」『』（）[]`, `、。,.!?…・ー々ゃゅょ`, combining mark, emoji ZWJ를 추가했다. 각 expected line은 닫는 구두점 행두와 여는 구두점 행말을 금지하는 검사에 통과해야 한다. PPTX에는 presentation-level `p:kinsoku lang="ko-KR"`, paragraph `eaLnBrk="1"`, `latinLnBrk="0"`, `hangingPunct="0"`를 명시했다.

표준 근거:

- [Unicode UAX #14 rev.55 / Unicode 17.0.0](https://www.unicode.org/reports/tr14/tr14-55.html)
- [Unicode 17.0.0 LineBreak data](https://www.unicode.org/Public/17.0.0/ucd/LineBreak.txt)
- [Unicode 17.0.0 LineBreakTest](https://www.unicode.org/Public/17.0.0/ucd/auxiliary/LineBreakTest.txt)
- [W3C Japanese Layout Requirements](https://www.w3.org/TR/2020/NOTE-jlreq-20200811/)
- [Microsoft PresentationML package structure](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document)
- [Microsoft slide master guidance](https://learn.microsoft.com/en-us/office/open-xml/presentation/working-with-slide-masters)
- [Microsoft Open XML `p:kinsoku`](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.presentation.kinsoku?view=openxml-3.0.1)
- [Microsoft Windows font fallback](https://learn.microsoft.com/en-us/globalization/fonts-layout/fonts)

## 현행 코드 재사용 경계

### 직접 재사용

- SlideSpec v1.1의 strict schema, immutable revision, content/spec digest, provenance, `RenderRequest(renderer/fonts/assets/source)` 패턴: `servers/fastapi/models/slidespec_v1.py:363`, `:374`, `:703`, `:785`.
- render 환경을 spec/content/font/browser/adjacent digest로 봉인하는 패턴: `servers/fastapi/services/slidespec_authored_executor.py:235`.
- PPTX ZIP/OPC read-write와 roundtrip parse: `servers/nextjs/lib/authored-hybrid/pptx-archive.ts:55`, `:158`.
- CJK fallback, font layout, browser capture, native text plan과 package inspection의 좁은 유틸리티 경계.

### adapter로 재사용

- `@oai/artifact-tool` facade는 custom master/layout/slide/theme의 기본 골격 생성에 사용한다.
- artifact-tool 2.8.43에서 master에 여러 shape를 연속 추가하면 반환 id가 모두 `1`이고 마지막 shape만 남으며, layout placeholder에서도 같은 last-only 현상이 재현됐다. public setter나 shape id API도 없었다. 그래서 export 후 기존 `pptx-archive.ts` adapter로 누락 furniture/placeholder, kinsoku, theme script mapping, 고정 core timestamp만 보강한다. 근거는 `agent-probe/master-multi-shape-report.txt`와 `agent-probe/master-shape-id-api-facts.json`이다.
- post-processing은 지정된 실험 디렉터리의 harness에만 있고 제품 소스는 변경하지 않았다.

### 이 vertical slice에서 폐기

- historical full-slide-image PPTX는 master-aware/editable 결과의 최종 경로로 쓰지 않는다. 현행 hybrid README도 fidelity mode가 historical full-slide-image exporter임을 명시한다: `servers/nextjs/lib/authored-hybrid/README.md:40`.
- authored-hybrid assembler 자체는 이미 생성된 fidelity PPTX skeleton의 selected slide를 재구성하는 경계이므로 custom master authoring기로 사용하지 않는다: `servers/nextjs/lib/authored-hybrid/pptx-assembler.ts:483`.
- adaptive `SlideModel.content`의 얕은 자유형 구조는 master ownership, placeholder inheritance, font manifest, deterministic digest를 표현하기에 부족하므로 v2 canonical로 승격하지 않는다.
- builder 안의 direct-from-scratch OOXML 함수는 실행하지 않는 fallback 실험 코드이며 제품 후보에서는 폐기한다. 실제 산출물은 artifact-tool 생성 후 좁은 package adapter를 거친 것이다.

## preview와 PowerPoint의 차이

브라우저 PNG와 PowerPoint PNG는 모두 1280×720이고 제목 3줄/clipping 0이다. PowerPoint는 Malgun Gothic으로 resolve됐고, 좌우 7.2pt·상하 3.6pt 여백을 COM에서 확인했다. 그래도 browser/Office shaping과 rasterization 차이 때문에 전체 이미지 MAE는 20.5637이다. LibreOffice는 1281×720을 1280×720으로 비교용 resize했고 MAE는 23.2544다. 이 수치는 기록용이며 pixel-identical gate는 아니다.

## 재현성과 남은 제약

2회 독립 생성에서 canonical/master/font/content/spec digest와 HTML/PNG bytes는 동일했다. PPTX bytes는 artifact-tool 내부 UUID 때문에 동일하지 않다. 따라서 이 PoC가 보장하는 것은 canonical/semantic digest 재현성이지 PPTX ZIP byte-for-byte 재현성이 아니다.

실제 Windows PowerPoint에서 font resolve, layout inventory, clipping, open-save-reopen, PNG export까지 검증했다. 창 없는 모드라 repair 대화상자 UI는 직접 관찰하지 못했다. LibreOffice 결과는 보조 증거이며 PowerPoint 대체 판정이 아니다.

## 출처·라이선스·NOTICE

제품 또는 PoC에 GenOffice ee 및 PPTist AGPL 코드·자료를 열람·복사·포함하지 않았다. tracked/product tree 검색에서도 해당 문자열은 없었다. pre-existing untracked architecture 문서는 이 실험 범위 밖이라 수정하지 않았다.

| 구성요소 | 고정 revision | 사용 범위 | LICENSE/NOTICE 증거 |
| --- | --- | --- | --- |
| 이 저장소 | `b387a34735482d4f6dfbf2b955fbf19fd0d2315e` | schema/harness 및 기존 archive adapter | Apache-2.0 `LICENSE` SHA-256 `72DAA21C...FC3D01A`; `NOTICE` SHA-256 `5F394C43...D9AEB61A` |
| Playwright | 1.62.1, tag commit [`26a9e470a7b3c7822084b09fb7f13902c5f37b51`](https://github.com/microsoft/playwright/commit/26a9e470a7b3c7822084b09fb7f13902c5f37b51) | installed Chrome 151.0.7922.137 capture | Apache-2.0 `LICENSE` SHA-256 `45873D00...0220EB`; `NOTICE` `6D602191...F51C81D`; `ThirdPartyNotices.txt` `B17AC0BD...7ABD8E` |
| `@oai/artifact-tool` | 2.8.43; bundled package는 source commit을 노출하지 않음 | PPTX facade | private/proprietary `LICENSE.md` SHA-256 `30B7A1B1...A271CD`; NOTICE 없음. 오픈소스로 주장하지 않음 |
| LibreOffice | 26.2.5.2, build commit `cd7284b4cbbfeb507e630c1aac019f4157393acb` | reopen/render 보조 검증기 | installed `LICENSE.html` `D64906F9...98F1F`, `license.txt` `4A4424CC...AC344`, `NOTICE` `8D34210E...EF7B` |
| Poppler `pdftocairo` | 26.02.0, tag commit [`30927d02c1fd6dc02aae2e32334bb82315b24cbb`](https://gitlab.com/freedesktop-sdk/mirrors/freedesktop/poppler/poppler/-/commit/30927d02c1fd6dc02aae2e32334bb82315b24cbb) | LibreOffice PDF→PNG 보조 변환 | `COPYING.gpl2` `AB15FD52...8666C6`, `COPYING.adobe` `4D367131...17B39` |

이 실험 디렉터리에는 위 외부 패키지의 source를 vendoring하지 않았다. `node_modules`는 Codex bundled workspace runtime을 가리키는 로컬 junction이며 Git 산출물에 포함되지 않는다.
