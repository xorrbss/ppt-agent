$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$probePath=Join-Path $PSScriptRoot 'probe-com.ps1'
$tokens=$null
$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($probePath,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count -ne 0){throw "probe-parse-errors:$($parseErrors.Count)"}
$functionAst=$ast.Find({
  param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Read-ChartSeriesCacheValues'
},$true)
if($null -eq $functionAst){throw 'chart-cache-reader-function-missing'}
Invoke-Expression $functionAst.Extent.Text

[xml]$chart=@'
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
  <c:chart><c:plotArea><c:lineChart><c:ser>
    <c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>매출</c:v></c:pt></c:strCache></c:strRef></c:tx>
    <c:cat><c:strLit>
      <c:pt idx="3"><c:v>4분기</c:v></c:pt><c:pt idx="1"><c:v>2분기</c:v></c:pt>
      <c:pt idx="0"><c:v>1분기</c:v></c:pt><c:pt idx="2"><c:v>3분기</c:v></c:pt>
    </c:strLit></c:cat>
    <c:val><c:numLit>
      <c:pt idx="2"><c:v>4.8</c:v></c:pt><c:pt idx="0"><c:v>4.3</c:v></c:pt>
      <c:pt idx="3"><c:v>6.2</c:v></c:pt><c:pt idx="1"><c:v>5.1</c:v></c:pt>
    </c:numLit></c:val>
  </c:ser></c:lineChart></c:plotArea></c:chart>
</c:chartSpace>
'@
$series=$chart.SelectSingleNode("//*[local-name()='ser']")
$categories=@(Read-ChartSeriesCacheValues $series @('cat','xVal'))
$values=@(Read-ChartSeriesCacheValues $series @('val','yVal'))
if(($categories -join '|') -ne '1분기|2분기|3분기|4분기'){throw "category-cache-order:$($categories -join '|')"}
if(($values -join '|') -ne '4.3|5.1|4.8|6.2'){throw "value-cache-order:$($values -join '|')"}
if($categories.Count -ne $values.Count){throw "category-value-cardinality:$($categories.Count):$($values.Count)"}
Write-Output 'probe-com-chart-cache: PASS (4 categories, 4 values)'
