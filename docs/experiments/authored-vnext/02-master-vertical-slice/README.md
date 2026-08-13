# 02 — master-aware long-title vertical slice

한 canonical input에서 다음 산출물을 만든다.

- `output/vertical-slice.html`
- `output/vertical-slice.preview.png`
- `output/vertical-slice.pptx`
- `output/manifest.json`
- `output/verification-report.json`
- `output/reproducibility-report.json`
- `output/powerpoint-final/powerpoint-verification.json`

## 구성

- `deckmaster-contract.schema.json`: master furniture, standard/long-title layout, ownership, font manifest, digest schema
- `canonical-input.json`: DeckMasterContract + SlideSpec v2 한 장과 15-case stress corpus
- `build.mjs`: HTML/PNG/artifact-tool PPTX 생성 및 좁은 OOXML adapter
- `verify.py`: package relationship/read-back, duplicate, clipping, preview 비교
- `verify-reproducibility.mjs`: 두 독립 빌드의 digest/byte 재현성 비교
- `verify-powerpoint.ps1`: 안전한 PowerPoint open→SaveAs→reopen→PNG harness
- `decision-note.md`: 재사용/adapter/폐기 및 호환성 판정

## 실행

프레젠테이션 workspace dependency loader가 반환한 bundled Node/Python과 node modules를 사용한다. 이 작업 환경에서는 아래 경로였다.

```powershell
$target = 'C:\project\PPT-agent\ppt-agent\docs\experiments\authored-vnext\02-master-vertical-slice'
$node = 'C:\Users\ibiz\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$python = 'C:\Users\ibiz\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

Set-Location $target
& $node .\build.mjs --out .\output
& .\verify-powerpoint.ps1 -InputPptx .\output\vertical-slice.pptx -OutputDir .\output\powerpoint-final
& $python .\verify.py
```

PowerPoint harness는 기존 `POWERPNT` 프로세스가 있으면 fail-closed한다. 사용자 인스턴스에 attach하거나 종료하지 않는다.

LibreOffice는 전용 user profile과 output directory를 사용한 뒤 `pdftocairo -png -singlefile -r 96`으로 보조 PNG를 만든다. 현재 검증 report는 `output/libreoffice-final3/vertical-slice.libreoffice.png`를 읽는다.

재현성 확인:

```powershell
& $node .\build.mjs --out .\tmp\repro-final-a
& $node .\build.mjs --out .\tmp\repro-final-b
& $node .\verify-reproducibility.mjs .\tmp\repro-final-a .\tmp\repro-final-b .\output\reproducibility-report.json
```

## 판정 읽는 법

`verification-report.json`의 `status=pass`는 package structural error 0, master/layout/placeholder graph 일치, furniture body duplicate 0, HTML/PowerPoint clipping 0을 뜻한다. 창 없는 PowerPoint 실행에서는 repair 대화상자 UI 자체를 관찰할 수 없으므로, COM open-save-reopen 성공을 별도 근거로 기록한다.

`reproducibility-report.json`은 canonical/semantic digest와 HTML/PNG bytes가 재현됨을 보장한다. artifact-tool 내부 UUID 때문에 PPTX bytes는 현재 동일하지 않다.
