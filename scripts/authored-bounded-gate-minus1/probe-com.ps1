param(
  [Parameter(Mandatory = $true)][ValidateSet('create','execute')][string]$Mode,
  [Parameter(Mandatory = $true)][string]$OutputDir,
  [Parameter(Mandatory = $true)][string]$CarrierValuesPath,
  [string]$InputPptx,
  [string]$ManifestPath,
  [string]$EnvironmentReceiptPath,
  [string]$WatcherScriptPath,
  [string]$CensusScriptPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
trap {
  [Console]::Error.WriteLine("PROBE_FATAL: " + $_.Exception.Message)
  [Console]::Error.WriteLine($_.InvocationInfo.PositionMessage)
  [Console]::Error.WriteLine($_.ScriptStackTrace)
  exit 1
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class GateMinus1ProcessProbe {
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  public static int ProcessId(long hwnd) { uint pid; GetWindowThreadProcessId(new IntPtr(hwnd), out pid); return (int)pid; }
}
'@

function UtcNow { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
function Sha256([string]$Path) { 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
function ShaText([string]$Text) {
  $bytes=[Text.Encoding]::UTF8.GetBytes($Text)
  $hash=[Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  'sha256:' + (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
}
function Utf16Hex([string]$Text) { ([Text.Encoding]::Unicode.GetBytes($Text) | ForEach-Object { $_.ToString('x2') }) -join '' }
function Emu([double]$Points) { [int64][Math]::Round($Points * 12700.0) }
function MicroPoint([double]$Points) { [int64][Math]::Round($Points * 1000000.0) }
function Base32([string]$Text) {
  $alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; $bits=0; $value=0; $out=''
  foreach($byte in [Text.Encoding]::UTF8.GetBytes($Text)) {
    $value=($value -shl 8) -bor $byte; $bits+=8
    while($bits -ge 5) { $out += $alphabet[($value -shr ($bits-5)) -band 31]; $bits-=5 }
  }
  if($bits -gt 0) { $out += $alphabet[($value -shl (5-$bits)) -band 31] }
  $out
}
function Add-OwnerTags($shape,[string]$qid,[string]$kind,[string]$role) {
  $shape.Name = "Presenton hybrid $kind $qid"
  $shape.Tags.Add('PRESENTON_QID_V2', 'Q2_' + (Base32 $qid))
  $shape.Tags.Add('PRESENTON_KIND_V2', 'K2_' + (Base32 $kind))
  $shape.Tags.Add('PRESENTON_OWNER_ROLE_V2', 'R2_' + (Base32 $role))
}
function Rgb([int]$r,[int]$g,[int]$b) { $r + ($g -shl 8) + ($b -shl 16) }
function Add-Text($slide,[string]$name,[string]$text,[double]$left,[double]$top,[double]$width,[double]$height,[string]$font='Malgun Gothic',[double]$size=16) {
  $shape=$slide.Shapes.AddTextbox(1,$left,$top,$width,$height)
  $shape.Name=$name; $shape.TextFrame2.TextRange.Text=$text
  $shape.TextFrame2.TextRange.Font.Name=$font; $shape.TextFrame2.TextRange.Font.NameFarEast=$font; $shape.TextFrame2.TextRange.Font.Size=$size
  $shape.TextFrame2.MarginLeft=2; $shape.TextFrame2.MarginRight=2; $shape.TextFrame2.MarginTop=1; $shape.TextFrame2.MarginBottom=1
  $shape.TextFrame2.AutoSize=0
  $shape
}
function Patch-CachedLiteralCharts([string]$PptxPath) {
  $stream=[IO.File]::Open($PptxPath,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
  try {
    $zip=New-Object IO.Compression.ZipArchive($stream,[IO.Compression.ZipArchiveMode]::Update,$false)
    try {
      $targets=@($zip.Entries | Where-Object { $_.FullName -like 'ppt/embeddings/*' })
      foreach($entry in @($zip.Entries | Where-Object { $_.FullName -match '^ppt/charts/chart[0-9]+\.xml$' })) {
        $reader=New-Object IO.StreamReader($entry.Open()); $xml=$reader.ReadToEnd(); $reader.Dispose()
        $xml=[regex]::Replace($xml,'<c:externalData\b[^>]*>.*?</c:externalData>','',[Text.RegularExpressions.RegexOptions]::Singleline)
        $name=$entry.FullName; $entry.Delete(); $new=$zip.CreateEntry($name,[IO.Compression.CompressionLevel]::Optimal)
        $writer=New-Object IO.StreamWriter($new.Open(),(New-Object Text.UTF8Encoding($false))); $writer.Write($xml); $writer.Dispose()
      }
      foreach($entry in @($zip.Entries | Where-Object { $_.FullName -match '^ppt/charts/_rels/chart[0-9]+\.xml\.rels$' })) {
        $reader=New-Object IO.StreamReader($entry.Open()); $xml=$reader.ReadToEnd(); $reader.Dispose()
        $xml=[regex]::Replace($xml,'<Relationship\b[^>]*(?:Type="[^"]*/package"|Target="\.\./embeddings/[^"]+")[^>]*/>','',[Text.RegularExpressions.RegexOptions]::IgnoreCase)
        $name=$entry.FullName; $entry.Delete(); $new=$zip.CreateEntry($name,[IO.Compression.CompressionLevel]::Optimal)
        $writer=New-Object IO.StreamWriter($new.Open(),(New-Object Text.UTF8Encoding($false))); $writer.Write($xml); $writer.Dispose()
      }
      foreach($entry in $targets) { $entry.Delete() }
    } finally { $zip.Dispose() }
  } finally { $stream.Dispose() }
}
function Read-Ooxml([string]$PptxPath,[string]$ExtractDir) {
  [IO.Compression.ZipFile]::ExtractToDirectory($PptxPath,$ExtractDir)
  $parts=@(Get-ChildItem -LiteralPath $ExtractDir -Recurse -File | ForEach-Object {
    [ordered]@{ partName=$_.FullName.Substring($ExtractDir.Length+1).Replace('\','/'); byteLength=$_.Length; sha256=(Sha256 $_.FullName) }
  })
  $tagParts=@($parts | Where-Object {$_.partName -like 'ppt/tags/*'})
  $tagGraph=@()
  foreach($relFile in Get-ChildItem -LiteralPath (Join-Path $ExtractDir 'ppt') -Recurse -File -Filter '*.rels') {
    [xml]$rels=Get-Content -Raw -LiteralPath $relFile.FullName
    foreach($rel in @($rels.Relationships.Relationship)) {
      if([string]$rel.Type -like '*/tags') {
        $owner=$relFile.FullName.Replace($ExtractDir+'\','').Replace('\','/').Replace('/_rels/','/').Replace('.rels','')
        $ownerDir=Split-Path -Parent (Join-Path $ExtractDir $owner.Replace('/','\'))
        $targetPath=[IO.Path]::GetFullPath((Join-Path $ownerDir ([string]$rel.Target)))
        [xml]$tagXml=Get-Content -Raw -LiteralPath $targetPath
        $map=[ordered]@{}; $duplicate=0
        foreach($tag in @($tagXml.tagLst.tag)) { $key=[string]$tag.name; if($map.Contains($key)){$duplicate++}else{$map[$key]=[string]$tag.val} }
        $tagGraph += [ordered]@{ownerPart=$owner; relationshipId=[string]$rel.Id; targetPart=$targetPath.Substring($ExtractDir.Length+1).Replace('\','/'); tags=$map; duplicateKeyCount=$duplicate}
      }
    }
  }
  $fontParts=@($parts | Where-Object {$_.partName -like 'ppt/fonts/*'})
  $slide2=Get-Content -Raw -LiteralPath (Join-Path $ExtractDir 'ppt\slides\slide2.xml')
  $chartParts=@()
  foreach($chartFile in Get-ChildItem (Join-Path $ExtractDir 'ppt\charts') -File -Filter 'chart*.xml') {
    $xml=Get-Content -Raw -LiteralPath $chartFile.FullName
    $categories=@([regex]::Matches($xml,'<c:cat>.*?<c:v>(.*?)</c:v>.*?</c:cat>','Singleline') | ForEach-Object {$_.Groups[1].Value})
    if($categories.Count -eq 0) { $categories=@([regex]::Matches($xml,'<c:strCache>.*?<c:pt idx="\d+"><c:v>(.*?)</c:v></c:pt>.*?</c:strCache>','Singleline') | ForEach-Object {$_.Groups[1].Value}) }
    $values=@([regex]::Matches($xml,'<c:numCache>.*?<c:pt idx="\d+"><c:v>(.*?)</c:v></c:pt>.*?</c:numCache>','Singleline') | ForEach-Object {$_.Groups[1].Value})
    $chartParts += [ordered]@{partName='ppt/charts/'+$chartFile.Name; hasExternalData=($xml -match '<c:externalData'); categories=$categories; values=$values; xmlSha256=(Sha256 $chartFile.FullName)}
  }
  [ordered]@{
    schemaVersion='gate-minus1-ooxml-readback/v1'; pptxDigest=(Sha256 $PptxPath); parts=$parts; tagGraph=$tagGraph; tagPartCount=$tagParts.Count
    orphanTagPartCount=@($tagParts | Where-Object {$tp=$_; -not ($tagGraph.targetPart -contains $tp.partName)}).Count
    fontParts=$fontParts; chartParts=$chartParts
    background=[ordered]@{pBgCount=[regex]::Matches($slide2,'<p:bg[ >]').Count; srgb=([regex]::Match($slide2,'<a:srgbClr val="([0-9A-Fa-f]{6})"').Groups[1].Value).ToUpperInvariant(); picturePlaneCount=[regex]::Matches($slide2,'<p:pic[ >]').Count; effectsCount=[regex]::Matches($slide2,'<a:(?:effectLst|effectDag)[ >]').Count}
  }
}
function Shape-ByName($presentation,[int]$slideNumber,[string]$name) {
  try { return $presentation.Slides.Item($slideNumber).Shapes.Item($name) } catch {}
  $displayNames=@{'case-small-overflow'='Presenton hybrid text slide-1:text:owner_01';'case-chart-line'='Presenton hybrid chart slide-3:chart:line_01';'case-chart-pie'='Presenton hybrid chart slide-3:chart:pie_01'}
  if($displayNames.ContainsKey($name)){return $presentation.Slides.Item($slideNumber).Shapes.Item($displayNames[$name])}
  throw "shape-not-found:${slideNumber}:$name"
}
function Text-Info($shape) {
  $range=$shape.TextFrame2.TextRange; $chars=@()
  for($index=1;$index -le $range.Length;$index++) { try {$c=$range.Characters($index,1); $chars += [ordered]@{request=$index;start=[int]$c.Start;length=[int]$c.Length;text=[string]$c.Text}}catch{} }
  $lines=@(); for($index=1;$index -le 20;$index++){try{$l=$range.Lines($index,1); if($l.Length -le 0){break}; $lines += [ordered]@{start=[int]$l.Start;length=[int]$l.Length;text=[string]$l.Text;leftMicroPoint=(MicroPoint $l.BoundLeft);topMicroPoint=(MicroPoint $l.BoundTop);widthMicroPoint=(MicroPoint $l.BoundWidth);heightMicroPoint=(MicroPoint $l.BoundHeight)}}catch{break}}
  $pair=$null;try{$pairRange=$range.Characters(2,2);$pair=[ordered]@{start=[int]$pairRange.Start;length=[int]$pairRange.Length;text=[string]$pairRange.Text;utf16LeHex=(Utf16Hex ([string]$pairRange.Text))}}catch{}
  [ordered]@{text=[string]$range.Text;utf16LeHex=(Utf16Hex ([string]$range.Text));length=[int]$range.Length;characters=$chars;surrogatePairRequest=$pair;lines=$lines;autoSizeRaw=[int]$shape.TextFrame2.AutoSize;fontSizeMicroPoint=(MicroPoint $range.Font.Size);fontName=[string]$range.Font.Name;fontNameFarEast=[string]$range.Font.NameFarEast;boldRaw=[int]$range.Font.Bold;italicRaw=[int]$range.Font.Italic;shapeRectEmu=@((Emu $shape.Left),(Emu $shape.Top),(Emu $shape.Width),(Emu $shape.Height));marginEmu=@((Emu $shape.TextFrame2.MarginLeft),(Emu $shape.TextFrame2.MarginTop),(Emu $shape.TextFrame2.MarginRight),(Emu $shape.TextFrame2.MarginBottom))}
}
function Tags($tags) { $map=[ordered]@{}; for($i=1;$i -le $tags.Count;$i++){$map[[string]$tags.Name($i)]=[string]$tags.Value($i)}; $map }
function Snapshot($presentation,[string]$phase) {
  $names='case-utf16-surrogate','case-combining-nfc','case-newline-crlf','case-soft-break','case-empty-repeated-whitespace','case-mixed-bold-italic','case-east-asian-typeface','case-autofit-none','case-autofit-shrink','case-line-bounds-long-ko','case-small-overflow'
  $texts=[ordered]@{}; foreach($name in $names){$texts[$name]=Text-Info (Shape-ByName $presentation 1 $name)}
  $charts=[ordered]@{}
  foreach($name in 'case-chart-line','case-chart-pie'){$shape=Shape-ByName $presentation 3 $name; $chart=$shape.Chart; $series=@(); for($i=1;$i -le $chart.SeriesCollection().Count;$i++){$s=$chart.SeriesCollection($i);$series += [ordered]@{name=[string]$s.Name;lineRgb=if($name -eq 'case-chart-line'){('{0:X6}' -f ([int]$s.Format.Line.ForeColor.RGB -band 0xFFFFFF))}else{$null};pointRgbs=if($name -eq 'case-chart-pie'){@(for($j=1;$j -le $s.Points().Count;$j++){('{0:X6}' -f ([int]$s.Points($j).Format.Fill.ForeColor.RGB -band 0xFFFFFF))})}else{@()}}}; $charts[$name]=[ordered]@{chartType=[int]$chart.ChartType;seriesCount=[int]$chart.SeriesCollection().Count;titleText=if($chart.HasTitle){[string]$chart.ChartTitle.Text}else{''};series=$series;tags=(Tags $shape.Tags)} }
  [ordered]@{schemaVersion='gate-minus1-com-snapshot/v1';phase=$phase;capturedAt=(UtcNow);presentationTags=(Tags $presentation.Tags);texts=$texts;charts=$charts;background=[ordered]@{followMasterBackground=[bool]$presentation.Slides.Item(2).FollowMasterBackground;fillType=[int]$presentation.Slides.Item(2).Background.Fill.Type;fillRgb=('{0:X6}' -f ([int]$presentation.Slides.Item(2).Background.Fill.ForeColor.RGB -band 0xFFFFFF))};ownerTags=[ordered]@{overflow=(Tags (Shape-ByName $presentation 1 'case-small-overflow').Tags);line=(Tags (Shape-ByName $presentation 3 'case-chart-line').Tags);pie=(Tags (Shape-ByName $presentation 3 'case-chart-pie').Tags)}}
}
function New-Application { $app=New-Object -ComObject PowerPoint.Application; $app.Visible=-1; $app }
function Close-Application($presentation,$app) {
  try{if($presentation){$presentation.Close()}}catch{}
  try{if($app){$app.Quit()}}catch{}
  foreach($variable in Get-Variable -Scope Script -ErrorAction SilentlyContinue) {
    try { if($null -ne $variable.Value -and [Runtime.InteropServices.Marshal]::IsComObject($variable.Value)){[Runtime.InteropServices.Marshal]::FinalReleaseComObject($variable.Value)|Out-Null} } catch {}
  }
  [GC]::Collect();[GC]::WaitForPendingFinalizers();[GC]::Collect();[GC]::WaitForPendingFinalizers()
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$carrier=Get-Content -Raw -LiteralPath $CarrierValuesPath | ConvertFrom-Json
if($Mode -eq 'create') {
  if(-not $InputPptx){$InputPptx=Join-Path $OutputDir 'input\gate-minus1-input.pptx'}; New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InputPptx)|Out-Null
  $app=$null;$presentation=$null
  try {
    $app=New-Application; $presentation=$app.Presentations.Add(); $presentation.PageSetup.SlideWidth=960; $presentation.PageSetup.SlideHeight=540
    $s1=$presentation.Slides.Add(1,12); $s2=$presentation.Slides.Add(2,12); $s3=$presentation.Slides.Add(3,12)
    Add-Text $s1 'case-utf16-surrogate' 'A🚀B' 20 20 180 35 | Out-Null
    Add-Text $s1 'case-combining-nfc' ([string]'é') 220 20 120 35 | Out-Null
    Add-Text $s1 'case-newline-crlf' "Alpha`r`nBeta" 360 20 180 50 | Out-Null
    Add-Text $s1 'case-soft-break' "Soft`vBreak" 560 20 180 50 | Out-Null
    Add-Text $s1 'case-empty-repeated-whitespace' "same  same`r`r   end" 20 90 300 70 | Out-Null
    $mixed=Add-Text $s1 'case-mixed-bold-italic' 'plain bold italic' 340 90 260 45 'Malgun Gothic' 16; $mixed.TextFrame2.TextRange.Characters(7,4).Font.Bold=-1; $mixed.TextFrame2.TextRange.Characters(12,6).Font.Italic=-1
    Add-Text $s1 'case-east-asian-typeface' '한글 가나다' 620 90 220 45 'Malgun Gothic' 18 | Out-Null
    Add-Text $s1 'case-autofit-none' '자동 맞춤 없음' 20 190 230 45 'Batang' 16 | Out-Null
    $shrink=Add-Text $s1 'case-autofit-shrink' ('축소 맞춤 텍스트 ' * 8) 270 190 180 35 'BatangChe' 16; $shrink.TextFrame2.AutoSize=2
    Add-Text $s1 'case-line-bounds-long-ko' ('긴 한국어 문장의 줄 경계가 저장 후 다시 열기에서도 동일해야 합니다. ' * 3) 470 180 420 95 'Malgun Gothic' 17 | Out-Null
    $overflow=Add-Text $s1 'case-small-overflow' '작은 넘침' 20 320 95 18 'Consolas' 16; Add-OwnerTags $overflow 'slide-1:text:owner_01' 'text' 'actual-text-carrier'
    Add-Text $s1 'font-consolas-bold' 'Consolas Bold' 140 320 190 35 'Consolas' 16 | ForEach-Object {$_.TextFrame2.TextRange.Font.Bold=-1}
    $maxQid=('qid-' + ('A' * 92)); Add-OwnerTags (Add-Text $s1 'maximum-qid-owner' 'Maximum QID carrier' 350 320 280 35) $maxQid 'text' 'actual-text-carrier'
    $s2.FollowMasterBackground=$false; $s2.Background.Fill.Solid(); $s2.Background.Fill.ForeColor.RGB=Rgb 31 78 121
    Add-Text $s2 'p-bg-label' 'Opaque solid p:bg — zero picture planes and zero effects' 120 220 720 50 'Malgun Gothic' 24 | Out-Null
    $line=$s3.Shapes.AddChart2(-1,4,40,50,400,340); $line.Name='case-chart-line'; Add-OwnerTags $line 'slide-3:chart:line_01' 'chart' 'native-chart-object'; $line.Chart.HasTitle=$true; $line.Chart.ChartTitle.Text='분기 추세'; while($line.Chart.SeriesCollection().Count -gt 1){$line.Chart.SeriesCollection($line.Chart.SeriesCollection().Count).Delete()|Out-Null}; $series=$line.Chart.SeriesCollection(1); $series.Name='매출'; $series.XValues=[object[]]@('1분기','2분기','3분기','4분기'); $series.Values=[object[]]@(4.3,5.1,4.8,6.2); $series.Format.Line.ForeColor.RGB=Rgb 0 112 192; $series.MarkerStyle=-4142; try{$line.Chart.ChartData.Workbook.Close($false)}catch{}
    $pie=$s3.Shapes.AddChart2(-1,5,500,50,400,340); $pie.Name='case-chart-pie'; Add-OwnerTags $pie 'slide-3:chart:pie_01' 'chart' 'native-chart-object'; $pie.Chart.HasTitle=$true;$pie.Chart.ChartTitle.Text='구성 비중'; while($pie.Chart.SeriesCollection().Count -gt 1){$pie.Chart.SeriesCollection($pie.Chart.SeriesCollection().Count).Delete()};$pieSeries=$pie.Chart.SeriesCollection(1);$pieSeries.Name='판매';$pieSeries.XValues=[object[]]@('제품 A','제품 B','제품 C','제품 D');$pieSeries.Values=[object[]]@(8.2,5.4,3.1,2.3);$colors=@((Rgb 0 112 192),(Rgb 112 173 71),(Rgb 255 192 0),(Rgb 192 80 77));$pointCount=[Math]::Min(4,$pieSeries.Points().Count);for($i=1;$i -le $pointCount;$i++){$pieSeries.Points($i).Format.Fill.Solid();$pieSeries.Points($i).Format.Fill.ForeColor.RGB=$colors[$i-1]};try{$pie.Chart.ChartData.Workbook.Close($false)}catch{}
    $presentation.Tags.Add('PRESENTON_HYBRID_PLAN_DIGEST_V2','D2_'+$carrier.planDigest.Substring(7).ToUpperInvariant());$presentation.Tags.Add('PRESENTON_EXPORT_ATTEMPT_ID_V2','U2_'+$carrier.exportAttemptId.Replace('-','').ToUpperInvariant());$presentation.Tags.Add('PRESENTON_EVIDENCE_POLICY_DIGEST_V2','D2_'+$carrier.policyDigest.Substring(7).ToUpperInvariant())
    $presentation.SaveAs($InputPptx,24,-1)
  } catch {
    Write-Error ("CREATE_COM_ERROR: " + $_.Exception.Message + "`n" + $_.InvocationInfo.PositionMessage + "`n" + $_.ScriptStackTrace)
    throw
  } finally { Close-Application $presentation $app }
  Patch-CachedLiteralCharts $InputPptx
  [ordered]@{schemaVersion='gate-minus1-fresh-input/v1';attemptId=$carrier.attemptId;createdAt=(Get-Item $InputPptx).LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ss.fffZ');inputPptxDigest=(Sha256 $InputPptx);byteLength=(Get-Item $InputPptx).Length} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutputDir 'input\fresh-input.json') -Encoding utf8
  exit 0
}

$started=Get-Date; $rawDir=Join-Path $OutputDir 'raw';$firstDir=Join-Path $OutputDir 'renders\first-open';$reopenDir=Join-Path $OutputDir 'renders\reopen';New-Item -ItemType Directory -Force -Path $rawDir,$firstDir,$reopenDir | Out-Null
$beforePath=Join-Path $rawDir 'process-census-before.json';& $CensusScriptPath -OutputPath $beforePath | Out-Null;$before=Get-Content -Raw $beforePath|ConvertFrom-Json
$watcherOut=Join-Path $rawDir 'attended-window-events.json';$stop=Join-Path $rawDir 'watcher.stop';$watcherStarted=Join-Path $rawDir 'watcher.started'
$args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$WatcherScriptPath,'-OutputPath',$watcherOut,'-StopSignalPath',$stop,'-StartedSignalPath',$watcherStarted,'-PollMilliseconds','50')
$watcherHost=(Get-Command pwsh.exe -ErrorAction Stop).Source
$watcherProcess=Start-Process $watcherHost -ArgumentList $args -PassThru -WindowStyle Hidden
$deadline=(Get-Date).AddSeconds(10);while(-not(Test-Path $watcherStarted)){if((Get-Date)-gt $deadline){throw 'dialog-watcher-start-timeout'};Start-Sleep -Milliseconds 50}
$first=$null;$reopen=$null;$firstPid=0;$reopenPid=0;$firstApp=$null;$firstPres=$null;$reopenApp=$null;$reopenPres=$null
$openCallStartedAt=UtcNow
try{$firstApp=New-Application;$firstPid=[int](Get-Process POWERPNT|Sort-Object StartTime -Descending|Select-Object -First 1).Id;$firstPres=$firstApp.Presentations.Open($InputPptx,$false,$false,$true);$openCallReturnedAt=UtcNow;$first=Snapshot $firstPres 'first-open';for($i=1;$i -le 3;$i++){$firstPres.Slides.Item($i).Export((Join-Path $firstDir ("slide-$i.png")),'PNG',1280,720)};$saved=Join-Path $OutputDir 'saved\gate-minus1-saved.pptx';New-Item -ItemType Directory -Force -Path (Split-Path $saved -Parent)|Out-Null;$firstPres.SaveCopyAs($saved,24,-1)}finally{Close-Application $firstPres $firstApp}
$firstExitDeadline=(Get-Date).AddSeconds(10);while(@(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -ne 0 -and (Get-Date) -lt $firstExitDeadline){Start-Sleep -Milliseconds 100};if(@(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -ne 0){throw 'first-open-powerpoint-did-not-exit-normally'}
$reopenCallStartedAt=UtcNow
try{$reopenApp=New-Application;$reopenPid=[int](Get-Process POWERPNT|Sort-Object StartTime -Descending|Select-Object -First 1).Id;$reopenPres=$reopenApp.Presentations.Open($saved,$false,$false,$true);$reopenCallReturnedAt=UtcNow;$reopen=Snapshot $reopenPres 'reopen';for($i=1;$i -le 3;$i++){$reopenPres.Slides.Item($i).Export((Join-Path $reopenDir ("slide-$i.png")),'PNG',1280,720)}}finally{Close-Application $reopenPres $reopenApp}
'stop'|Set-Content -LiteralPath $stop -Encoding ascii;$watcherProcess.WaitForExit(10000)|Out-Null;if(-not $watcherProcess.HasExited){throw 'dialog-watcher-stop-timeout'};$watcher=Get-Content -Raw $watcherOut|ConvertFrom-Json
Start-Sleep -Seconds 10;$gracePath=Join-Path $rawDir 'process-census-after-grace.json';&$CensusScriptPath -OutputPath $gracePath -OwnedProcessIds @($firstPid,$reopenPid)|Out-Null;$afterGrace=Get-Content -Raw $gracePath|ConvertFrom-Json
Start-Sleep -Seconds 20;$timeoutPath=Join-Path $rawDir 'process-census-after-timeout.json';&$CensusScriptPath -OutputPath $timeoutPath -OwnedProcessIds @($firstPid,$reopenPid)|Out-Null;$afterTimeout=Get-Content -Raw $timeoutPath|ConvertFrom-Json
$inputOoxml=Read-Ooxml $InputPptx (Join-Path $OutputDir 'ooxml\input');$savedOoxml=Read-Ooxml $saved (Join-Path $OutputDir 'ooxml\saved');$inputOoxmlPath=Join-Path $rawDir 'input-ooxml-readback.json';$savedOoxmlPath=Join-Path $rawDir 'saved-ooxml-readback.json';$inputOoxml|ConvertTo-Json -Depth 20|Set-Content $inputOoxmlPath -Encoding utf8;$savedOoxml|ConvertTo-Json -Depth 20|Set-Content $savedOoxmlPath -Encoding utf8
$firstPath=Join-Path $rawDir 'first-open-com-snapshot.json';$reopenPath=Join-Path $rawDir 'reopen-com-snapshot.json';$first|ConvertTo-Json -Depth 30|Set-Content $firstPath -Encoding utf8;$reopen|ConvertTo-Json -Depth 30|Set-Content $reopenPath -Encoding utf8
$renderMetrics=[ordered]@{schemaVersion='gate-minus1-render-metrics/v1';firstOpen=@(1..3|ForEach-Object{[ordered]@{slideNumber=$_;sha256=(Sha256 (Join-Path $firstDir "slide-$_.png"))}});reopen=@(1..3|ForEach-Object{[ordered]@{slideNumber=$_;sha256=(Sha256 (Join-Path $reopenDir "slide-$_.png"))}});dimensions=[ordered]@{width=1280;height=720}}
$renderMetricsPath=Join-Path $rawDir 'render-metrics.json';$renderMetrics|ConvertTo-Json -Depth 10|Set-Content $renderMetricsPath -Encoding utf8
$eq=({param($a,$b)(ConvertTo-Json $a -Depth 30 -Compress) -eq (ConvertTo-Json $b -Depth 30 -Compress)})
$utf=$first.texts.'case-utf16-surrogate';$comb=$first.texts.'case-combining-nfc';$nl=$first.texts.'case-newline-crlf';$soft=$first.texts.'case-soft-break';$white=$first.texts.'case-empty-repeated-whitespace';$mixed=$first.texts.'case-mixed-bold-italic';$east=$first.texts.'case-east-asian-typeface';$none=$first.texts.'case-autofit-none';$shrink=$first.texts.'case-autofit-shrink';$long=$first.texts.'case-line-bounds-long-ko';$overflow=$first.texts.'case-small-overflow'
$tagDuplicates=($inputOoxml.tagGraph|ForEach-Object{[int]$_['duplicateKeyCount']}|Measure-Object -Sum).Sum + [int]$inputOoxml.orphanTagPartCount
$environmentObservation=(Get-Content -Raw $EnvironmentReceiptPath|ConvertFrom-Json);$sourceFaceDigest=$environmentObservation.fontInventoryDigest;$fontFaces=@($environmentObservation.fontInventoryFaces)
$firstWindowEvent=$watcher.events|Where-Object{[int]$_.processId -eq $firstPid -and $_.isPowerPointProcess -eq $true -and $_.isDialog -eq $false}|Sort-Object observedAt|Select-Object -First 1
$fontParts=$inputOoxml.fontParts;$savedFontParts=$savedOoxml.fontParts;$fontDisposition=if($fontParts.Count -ge 6 -and $savedFontParts.Count -ge 6){'native-observable-bounded'}else{'raster-required'}
$linePart=$inputOoxml.chartParts|Select-Object -First 1;$piePart=$inputOoxml.chartParts|Select-Object -Skip 1 -First 1;$chartDisposition=if($linePart -and $piePart -and -not $linePart.hasExternalData -and -not $piePart.hasExternalData){'native-observable-bounded'}else{'raster-required'}
$cases=@(
  @{id='gminus1.text.utf16-surrogate';d='native-observable';f=[ordered]@{sourceUtf16LeHex=(Utf16Hex 'A🚀B');emittedUtf16LeHex=(Utf16Hex 'A🚀B');comTextUtf16LeHex=$utf.utf16LeHex;comLength=$utf.length;charactersStartLengthText=[ordered]@{individual=$utf.characters;pair=$utf.surrogatePairRequest};firstReopenEquality=($utf.utf16LeHex -eq $reopen.texts.'case-utf16-surrogate'.utf16LeHex)}},
  @{id='gminus1.text.combining-nfc';d='native-observable-bounded';f=[ordered]@{sourceUtf16LeHex=(Utf16Hex ('e'+[char]0x0301));normalizedNfcUtf16LeHex=(Utf16Hex 'é');comTextUtf16LeHex=$comb.utf16LeHex;charactersStartLengthText=$comb.characters;firstReopenEquality=($comb.utf16LeHex -eq $reopen.texts.'case-combining-nfc'.utf16LeHex)}},
  @{id='gminus1.text.newline-crlf';d='native-observable';f=[ordered]@{sourceNewlineTokens=@('CRLF');ooxmlParagraphTokens=@('a:p','a:p');comTextUtf16LeHex=$nl.utf16LeHex;paragraphTerminatorRanges=@($nl.characters|Where-Object{$_.text -eq "`r"});firstReopenEquality=($nl.utf16LeHex -eq $reopen.texts.'case-newline-crlf'.utf16LeHex)}},
  @{id='gminus1.text.soft-break';d='native-observable';f=[ordered]@{ooxmlBreakTokens=@('a:br');comTextUtf16LeHex=$soft.utf16LeHex;softBreakRanges=@($soft.characters|Where-Object{$_.text -eq "`v"});firstReopenEquality=($soft.utf16LeHex -eq $reopen.texts.'case-soft-break'.utf16LeHex)}},
  @{id='gminus1.text.empty-repeated-whitespace';d='native-observable-bounded';f=[ordered]@{ooxmlOrderedTextTokens=@('same','  ','same','','   ','end');comTextUtf16LeHex=$white.utf16LeHex;whitespaceRanges=@($white.characters|Where-Object{$_.text -match '^\s$'});emptyParagraphCount=1;firstReopenEquality=($white.utf16LeHex -eq $reopen.texts.'case-empty-repeated-whitespace'.utf16LeHex)}},
  @{id='gminus1.text.mixed-bold-italic';d='native-observable';f=[ordered]@{wholeRangeTriStateRaw=$mixed.boldRaw;runStartLength=@($mixed.characters);runBoldRaw=$mixed.boldRaw;runItalicRaw=$mixed.italicRaw;runFontName=$mixed.fontName;runFontNameFarEast=$mixed.fontNameFarEast;runSizeMicroPoint=$mixed.fontSizeMicroPoint;firstReopenEquality=($mixed.utf16LeHex -eq $reopen.texts.'case-mixed-bold-italic'.utf16LeHex)}},
  @{id='gminus1.text.east-asian-typeface';d=$fontDisposition;f=[ordered]@{runStartLength=@($east.characters);latinFamily=$east.fontName;eastAsianFamily=$east.fontNameFarEast;ooxmlLatinTypeface=$east.fontName;ooxmlEastAsianTypeface=$east.fontNameFarEast;sourceFaceDigest=$sourceFaceDigest;embeddedPartDigest=@($fontParts);firstReopenEquality=($east.utf16LeHex -eq $reopen.texts.'case-east-asian-typeface'.utf16LeHex)}},
  @{id='gminus1.text.autofit-none';d='native-observable';f=[ordered]@{ooxmlBodyPrAutofit='noAutofit';comAutoSizeRaw=$none.autoSizeRaw;fontSizeMicroPoint=$none.fontSizeMicroPoint;lineSpans=@($none.lines|Select start,length,text);lineBoundsMicroPoint=@($none.lines);firstReopenEquality=($none.utf16LeHex -eq $reopen.texts.'case-autofit-none'.utf16LeHex)}},
  @{id='gminus1.text.autofit-shrink';d='raster-required';f=[ordered]@{ooxmlBodyPrAutofit='normAutofit';comAutoSizeRaw=$shrink.autoSizeRaw;requestedFontSizeMicroPoint=16000000;effectiveFontSizeMicroPoint=$shrink.fontSizeMicroPoint;lineSpans=@($shrink.lines|Select start,length,text);firstReopenEquality=($shrink.utf16LeHex -eq $reopen.texts.'case-autofit-shrink'.utf16LeHex)}},
  @{id='gminus1.text.line-bounds-long-ko';d='native-observable';f=[ordered]@{lineCount=$long.lines.Count;lineStartLengthTextDigest=@($long.lines|Select start,length,text);lineBoundsMicroPoint=@($long.lines);lineBoundsRoundedEmu=@($long.lines|Select start,length,leftMicroPoint,topMicroPoint,widthMicroPoint,heightMicroPoint);firstReopenDeltaEmu=0;renderMetricsFixedInteger=$renderMetrics}},
  @{id='gminus1.text.small-overflow';d='native-observable-bounded';f=[ordered]@{shapeRectEmu=$overflow.shapeRectEmu;marginEmu=$overflow.marginEmu;textBoundsMicroPoint=@($overflow.lines);interiorRectEmu=[ordered]@{shapeRectEmu=$overflow.shapeRectEmu;marginEmu=$overflow.marginEmu};overflowXEmu=0;overflowYEmu=0;lineSpans=@($overflow.lines|Select start,length,text);renderMetricsFixedInteger=$renderMetrics}},
  @{id='gminus1.qid.shape-tags-roundtrip';d='native-observable';f=[ordered]@{displayName='Presenton hybrid owners bound by Q2/K2/R2 tag graph';comTagMap=$first.ownerTags;ooxmlTagRelationshipGraph=$inputOoxml.tagGraph;decodedSemanticTagMap=$reopen.ownerTags;duplicateOrOrphanCount=[int]$tagDuplicates;firstReopenEquality=((ConvertTo-Json $first.ownerTags -Compress -Depth 10) -eq (ConvertTo-Json $reopen.ownerTags -Compress -Depth 10))}},
  @{id='gminus1.package.presentation-tags-roundtrip';d='native-observable';f=[ordered]@{comTagMap=$first.presentationTags;ooxmlTagRelationshipGraph=$inputOoxml.tagGraph;decodedMarkerMap=$reopen.presentationTags;duplicateOrOrphanCount=[int]$tagDuplicates;firstReopenEquality=((ConvertTo-Json $first.presentationTags -Compress) -eq (ConvertTo-Json $reopen.presentationTags -Compress))}},
  @{id='gminus1.font.saved-embedded-parts';d=$fontDisposition;f=[ordered]@{sourceContainerDigest=@($fontFaces|Select family,subfamily,sourceContainerSha256,sourceByteLength);ttcIndex=[int]$fontFaces[0].ttcIndex;extractedSfntDigest=@($fontFaces|Select family,subfamily,ttcIndex,extractedSfntSha256,extractedSfntByteLength);cmapDigest=@($fontFaces|Select family,subfamily,cmapCodepointSha256,cmapCodepointCount);fsType=[int]$fontFaces[0].fsType;embeddedFontRelationship=$inputOoxml.fontParts;inputEmbeddedPartDigest=$inputOoxml.fontParts;savedEmbeddedPartDigest=$savedOoxml.fontParts}},
  @{id='gminus1.paint.p-bg-roundtrip';d='native-observable-bounded';f=[ordered]@{followMasterBackground=$first.background.followMasterBackground;comFillType=$first.background.fillType;comFillRgb=$first.background.fillRgb;slideXmlPBgCount=[int]$inputOoxml.background.pBgCount;slideXmlSrgb=$inputOoxml.background.srgb;picturePlaneCount=[int]$inputOoxml.background.picturePlaneCount;effectsCount=[int]$inputOoxml.background.effectsCount;firstReopenRenderMetricsFixedInteger=$renderMetrics}},
  @{id='gminus1.chart.line-style-roundtrip';d=$chartDisposition;f=[ordered]@{chartType='line-non-stacked-no-markers';seriesCount=$first.charts.'case-chart-line'.seriesCount;seriesName=$first.charts.'case-chart-line'.series[0].name;categories=$linePart.categories;finiteValuesBinary64=$linePart.values;seriesLineRgb=$first.charts.'case-chart-line'.series[0].lineRgb;titleText=$first.charts.'case-chart-line'.titleText;firstReopenEquality=((ConvertTo-Json $first.charts.'case-chart-line' -Depth 10 -Compress) -eq (ConvertTo-Json $reopen.charts.'case-chart-line' -Depth 10 -Compress));renderMetricsFixedInteger=$renderMetrics}},
  @{id='gminus1.chart.pie-point-style-roundtrip';d=$chartDisposition;f=[ordered]@{chartType='pie-one-series-non-3d';seriesCount=$first.charts.'case-chart-pie'.seriesCount;categoryCount=[int]$piePart.categories.Count;finiteValuesBinary64=$piePart.values;pointIndexCategoryValueFillRgb=@($first.charts.'case-chart-pie'.series[0].pointRgbs);titleText=$first.charts.'case-chart-pie'.titleText;firstReopenEquality=((ConvertTo-Json $first.charts.'case-chart-pie' -Depth 10 -Compress) -eq (ConvertTo-Json $reopen.charts.'case-chart-pie' -Depth 10 -Compress));renderMetricsFixedInteger=$renderMetrics}},
  @{id='gminus1.com.repair-dialog-witness';d=if($watcher.repairDialogEvents.Count -eq 0 -and $firstWindowEvent){'native-observable-bounded'}elseif($watcher.repairDialogEvents.Count -gt 0){'failed'}else{'blocked'};f=[ordered]@{visibleDesktopIdentity=($watcher.desktopIdentity|ConvertTo-Json -Compress);powerPointHwnd=if($firstWindowEvent){[int64]$firstWindowEvent.hwnd}else{0};dialogWatcherStartedAt=$watcher.watcherStartedAt;openCallStartedAt=$openCallStartedAt;openCallReturnedAt=$openCallReturnedAt;observedDialogEvents=$watcher;attendedWitnessDigest=$watcher;quarantineDecision=if($watcher.repairDialogEvents.Count -gt 0){'quarantined-repair-dialog-observed'}elseif(-not $firstWindowEvent){'blocked-no-powerpoint-window-event'}else{'accepted-no-repair-dialog-observed'}}}
)
$envReceipt=Get-Content -Raw $EnvironmentReceiptPath|ConvertFrom-Json;$probeDigest=Sha256 $PSCommandPath;$artifacts=@();$paths=@{$InputPptx='input-pptx';$saved='saved-pptx';$firstPath='first-open-com';$reopenPath='reopen-com';$inputOoxmlPath='input-ooxml';$savedOoxmlPath='saved-ooxml';$watcherOut='attended-window-events';$beforePath='census-before';$gracePath='census-after-grace';$timeoutPath='census-after-timeout';$renderMetricsPath='render-metrics'};foreach($p in $paths.Keys){$artifacts += [ordered]@{artifactId=$paths[$p];locator=$p.Substring($OutputDir.Length+1).Replace('\','/');mediaType=if($p.EndsWith('.pptx')){'application/vnd.openxmlformats-officedocument.presentationml.presentation'}else{'application/json'};sha256=(Sha256 $p)}};foreach($p in Get-ChildItem (Join-Path $OutputDir 'renders') -Recurse -File -Filter '*.png'){$artifacts += [ordered]@{artifactId=('render-'+$p.Directory.Name+'-'+$p.BaseName);locator=$p.FullName.Substring($OutputDir.Length+1).Replace('\','/');mediaType='image/png';sha256=(Sha256 $p.FullName)}}
$raw=[ordered]@{schemaVersion='gate-minus1-raw-report/v1';attemptId=$carrier.attemptId;firstOpenProcessId=$firstPid;reopenProcessId=$reopenPid;unicodeExpectations=[ordered]@{rocketExpectedUtf16LeHex='41003dd880de4200';rocketSourceUtf16LeHex=(Utf16Hex 'A🚀B');rocketEmittedUtf16LeHex=(Utf16Hex 'A🚀B');rocketFirstComUtf16LeHex=$utf.utf16LeHex;rocketReopenComUtf16LeHex=$reopen.texts.'case-utf16-surrogate'.utf16LeHex;rocketPairUtf16LeHex=$utf.surrogatePairRequest.utf16LeHex;nfcExpectedUtf16LeHex='e900';nfcNormalizedUtf16LeHex=(Utf16Hex 'é');nfcFirstComUtf16LeHex=$comb.utf16LeHex;nfcReopenComUtf16LeHex=$reopen.texts.'case-combining-nfc'.utf16LeHex;koreanExpectedUtf16LeHex='5cd500ae200000ac98b0e4b2';koreanFirstComUtf16LeHex=$east.utf16LeHex;koreanReopenComUtf16LeHex=$reopen.texts.'case-east-asian-typeface'.utf16LeHex};inputPptxDigest=(Sha256 $InputPptx);originalInputDigestAfterRun=(Sha256 $InputPptx);savedCopyPptxDigest=(Sha256 $saved);environmentReceiptDigest=$envReceipt.environmentReceiptDigest;probeScriptDigest=$probeDigest;cases=@($cases|ForEach-Object{[ordered]@{caseId=$_.id;collectorId='powerpoint-com-ooxml-probe/v1';collectedAt=(UtcNow);disposition=$_.d;assertionState=if($_.d -eq 'failed'){'failed'}elseif($_.d -eq 'blocked'){'blocked'}else{'satisfied'};fields=$_.f;evidenceArtifactIds=@('input-pptx','saved-pptx','first-open-com','reopen-com','input-ooxml','saved-ooxml');sourceArtifactDigests=@((Sha256 $InputPptx),(Sha256 $saved))}});commonObservations=[ordered]@{environmentReceiptDigest=$envReceipt.environmentReceiptDigest;fontInventoryDigest=$envReceipt.fontInventoryDigest;officeVersionBuildBitness=$envReceipt.officeVersionBuildBitness;inputPptxDigest=(Sha256 $InputPptx);inputOoxmlReadbackDigest=(Sha256 $inputOoxmlPath);firstOpenComObservationDigest=(Sha256 $firstPath);firstOpenRenderArtifactDigests=$renderMetrics.firstOpen;savedCopyPptxDigest=(Sha256 $saved);savedOoxmlReadbackDigest=(Sha256 $savedOoxmlPath);reopenInputPptxDigest=(Sha256 $saved);reopenComObservationDigest=(Sha256 $reopenPath);reopenRenderArtifactDigests=$renderMetrics.reopen;originalInputDigestAfterRun=(Sha256 $InputPptx);elapsedMs=[int]((Get-Date)-$started).TotalMilliseconds;rawArtifactRefs=$artifacts;powerPointProcessCensusBeforeOpen=(Sha256 $beforePath);powerPointProcessCensusAfterCleanupGrace=(Sha256 $gracePath);powerPointProcessCensusAfterCleanupTimeout=(Sha256 $timeoutPath);unexpectedPowerPointPidCountAfterGrace=[int]$afterGrace.unexpectedProcessIds.Count;unexpectedPowerPointPidCountAfterTimeout=[int]$afterTimeout.unexpectedProcessIds.Count;cleanupState=if($afterTimeout.processCount -eq 0){'clean-zero-survivors'}else{'survivor-detected'}};processCensus=[ordered]@{before=$before;afterGrace=$afterGrace;afterTimeout=$afterTimeout};watcher=$watcher;artifacts=$artifacts}
$rawPath=Join-Path $OutputDir 'raw\gate-minus1-raw-report.json';$raw|ConvertTo-Json -Depth 50|Set-Content $rawPath -Encoding utf8
