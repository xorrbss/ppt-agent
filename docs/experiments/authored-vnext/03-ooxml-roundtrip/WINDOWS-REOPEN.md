# Windows reopen/read-back 검증

이 절차는 원본 PPTX를 Office 프로세스에 직접 전달하지 않는다. 매 실행마다 임시 복사본을 만들고, 원본 SHA-256을 실행 전후 비교한다. PowerPoint COM에는 저장 호출이 없으며 읽기 전용·비표시로 연다. LibreOffice는 격리된 임시 사용자 프로필에서 별도 출력 PPTX로 변환하고 그 ZIP/OOXML 패키지를 다시 읽는다.

## 자동 검증

PowerPoint와 LibreOffice를 함께 검증한다.

```powershell
$tool = ".\docs\experiments\authored-vnext\03-ooxml-roundtrip\tools\windows-reopen.ps1"
& $tool `
  -InputPath "C:\safe-corpus\synthetic-90.pptx" `
  -Engine Both `
  -OutputJson ".\docs\experiments\authored-vnext\03-ooxml-roundtrip\runs\synthetic-90.windows.json"
```

LibreOffice만 검증할 때는 좁은 wrapper를 사용할 수 있다.

```powershell
& ".\docs\experiments\authored-vnext\03-ooxml-roundtrip\tools\libreoffice-readback.ps1" `
  -InputPath "C:\safe-corpus\synthetic-90.pptx" `
  -OutputJson ".\docs\experiments\authored-vnext\03-ooxml-roundtrip\runs\synthetic-90.libreoffice.json"
```

기본 실행 파일은 확인 대상 설치 경로로 고정되어 있다.

- PowerPoint: `C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE`
- LibreOffice: `C:\Program Files\LibreOffice\program\soffice.exe`

PowerPoint 프로세스가 이미 실행 중이면 harness는 기존 사용자 세션에 붙거나 이를 종료하지 않고 `blocked-existing-process`로 중단한다. 기본 120초를 넘긴 worker는 종료한다. 이때 실행 직전에 없던 PowerPoint 프로세스도 worker의 직접 자식으로 확인된 PID만 종료하며, 소유 관계를 확인할 수 없는 프로세스는 건드리지 않고 JSON에 남긴다. 원인 조사에 임시 입력·LibreOffice 변환 출력이 필요하면 `-KeepWorkingDirectory`를 지정한다.

## JSON 판정

- `overallStatus=passed`: 요청한 엔진의 자동 read-back이 성공했고 원본 및 임시 입력 SHA-256이 유지되었다.
- `source.unchanged=true`: 고객 파일이 아닌 로컬 원본의 실행 전후 바이트가 동일하다.
- `engines[].open.expectedExecutableMatched=true`: PowerPoint COM이 지정된 `POWERPNT.EXE` 경로의 프로세스를 시작했음을 뜻한다.
- `engines[].open.slideCount`: PowerPoint가 읽은 슬라이드 수다. package slide part 수와 다르면 실패한다.
- `engines[].output.packageReadBack`: LibreOffice 변환 결과가 ZIP/OOXML로 다시 열리고 필수 presentation part를 포함하는지 나타낸다. 입력과 출력 slide part 수가 다르면 실패한다.
- `repairDialog.zeroClaimSupported=false`: 자동화만으로 `repair dialog=0`을 주장할 수 없다는 fail-closed 결과다.

LibreOffice 출력은 호환성 신호일 뿐 byte-preserving no-op 결과가 아니다. part SHA-256, relationship closure, unknown payload 보존 여부는 별도 package inventory/diff harness 결과로 판정해야 한다.

## PowerPoint `repair dialog=0` 수동 witness

비표시 COM은 UI가 없고 PowerPoint COM 모델에는 신뢰할 수 있는 “복구됨” 플래그가 없다. 따라서 자동 성공은 read-back만 증명하며 `repair dialog=0`은 다음 수동 절차를 별도로 완료해야 한다.

1. 원본 SHA-256을 기록하고 원본을 로컬 임시 디렉터리에 복사한다.
2. 네트워크·동기화 대상이 아닌 임시 복사본을 Windows PowerPoint UI에서 연다.
3. “PowerPoint에서 콘텐츠에 문제가 있음을 발견했습니다”, 복구/수리, 읽을 수 없는 콘텐츠 관련 대화상자가 나타나는지 관찰한다.
4. 대화상자가 전혀 없고 모든 슬라이드가 열린 경우에만 witness 결과를 `repair dialog=0`으로 기록한다. 발생했다면 문구와 시각을 기록하고 실패 처리한다.
5. 저장하지 않고 닫는다. 원본 및 임시 복사본 SHA-256을 다시 계산해 변경이 없는지 확인한다.

자동 JSON, 수동 witness 기록, package diff 결과는 서로 대체하지 않는다. 고객 파일은 외부 서비스나 네트워크 위치로 전송하지 않는다.

## 공식 동작 근거

- Microsoft의 [`Presentations.Open`](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentations.open) 문서는 `ReadOnly=msoTrue`가 읽기 전용 열기, `WithWindow=msoFalse`가 비표시 열기임을 명시한다.
- Microsoft의 [`Application.AutomationSecurity`](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.application.automationsecurity) 문서는 프로그래밍 방식 열기 직전에 `msoAutomationSecurityForceDisable`을 설정하면 매크로를 비활성화한다고 설명한다. harness는 이전 값을 보관하고 닫기 전에 복원한다.
- LibreOffice의 [공식 시작 매개변수](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)는 `--headless`, `--norestore`, `--convert-to`, `--outdir`, 격리 프로필용 `-env:UserInstallation=...`의 의미를 정의한다.
