$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $root 'output\vertical-slice.pptx'
$roundtripDir = Join-Path $root 'output\powerpoint-final'
$roundtrip = Join-Path $roundtripDir 'vertical-slice.powerpoint-roundtrip.pptx'
$render = Join-Path $roundtripDir 'vertical-slice.powerpoint.png'
$factsPath = Join-Path $roundtripDir 'powerpoint-verification.json'
New-Item -ItemType Directory -Force $roundtripDir | Out-Null

foreach ($target in @($roundtrip, $render, $factsPath)) {
    if (Test-Path -LiteralPath $target) {
        $resolved = (Resolve-Path -LiteralPath $target).Path
        if (-not $resolved.StartsWith($roundtripDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe PowerPoint verification target: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Force
    }
}

$powerPoint = $null
$presentation = $null
$reopened = $null

function Get-DeckFacts($deck) {
    $designs = @()
    for ($designIndex = 1; $designIndex -le $deck.Designs.Count; $designIndex++) {
        $design = $deck.Designs.Item($designIndex)
        $masterShapeNames = @()
        for ($shapeIndex = 1; $shapeIndex -le $design.SlideMaster.Shapes.Count; $shapeIndex++) {
            $masterShapeNames += [string]$design.SlideMaster.Shapes.Item($shapeIndex).Name
        }
        $layoutNames = @()
        for ($layoutIndex = 1; $layoutIndex -le $design.SlideMaster.CustomLayouts.Count; $layoutIndex++) {
            $layoutNames += [string]$design.SlideMaster.CustomLayouts.Item($layoutIndex).Name
        }
        $designs += [ordered]@{
            index = $designIndex
            masterName = [string]$design.SlideMaster.Name
            masterShapeNames = $masterShapeNames
            layoutNames = $layoutNames
        }
    }

    $slide = $deck.Slides.Item(1)
    $shapes = @()
    for ($shapeIndex = 1; $shapeIndex -le $slide.Shapes.Count; $shapeIndex++) {
        $shape = $slide.Shapes.Item($shapeIndex)
        $placeholderType = $null
        try { $placeholderType = [int]$shape.PlaceholderFormat.Type } catch {}
        $item = [ordered]@{
            name = [string]$shape.Name
            placeholderType = $placeholderType
            leftPt = [math]::Round([double]$shape.Left, 3)
            topPt = [math]::Round([double]$shape.Top, 3)
            widthPt = [math]::Round([double]$shape.Width, 3)
            heightPt = [math]::Round([double]$shape.Height, 3)
            hasTextFrame = [bool]$shape.HasTextFrame
        }
        if ($shape.HasTextFrame) {
            $frame = $shape.TextFrame2
            $boundHeight = [double]$frame.TextRange.BoundHeight
            $availableHeight = [double]$shape.Height - [double]$frame.MarginTop - [double]$frame.MarginBottom
            $lineCount = $null
            try { $lineCount = [int]$frame.TextRange.Lines().Count } catch {}
            $item.text = [string]$frame.TextRange.Text
            $item.fontName = [string]$frame.TextRange.Font.Name
            $item.marginLeftPt = [math]::Round([double]$frame.MarginLeft, 3)
            $item.marginRightPt = [math]::Round([double]$frame.MarginRight, 3)
            $item.marginTopPt = [math]::Round([double]$frame.MarginTop, 3)
            $item.marginBottomPt = [math]::Round([double]$frame.MarginBottom, 3)
            $item.boundWidthPt = [math]::Round([double]$frame.TextRange.BoundWidth, 3)
            $item.boundHeightPt = [math]::Round($boundHeight, 3)
            $item.availableHeightPt = [math]::Round($availableHeight, 3)
            $item.lineCount = $lineCount
            $item.verticallyClippedByBounds = $boundHeight -gt ($availableHeight + 0.5)
        }
        $shapes += $item
    }
    $furniture = @(
        'furniture:top-rail',
        'furniture:brand',
        'furniture:footer-rule',
        'furniture:title-accent',
        'furniture:footer'
    )
    $custom = @($designs | Where-Object { $_.masterName -eq 'Authored vNext Master' })
    $title = @($shapes | Where-Object { $_.placeholderType -eq 1 })
    return [ordered]@{
        slideCount = [int]$deck.Slides.Count
        designCount = [int]$deck.Designs.Count
        designs = $designs
        customMasterCount = $custom.Count
        customMasterFurnitureMissing = @($furniture | Where-Object { $_ -notin $custom.masterShapeNames })
        customMasterLayoutNames = @($custom.layoutNames)
        slideLayoutName = [string]$slide.CustomLayout.Name
        slideShapes = $shapes
        furnitureBodyDuplicateCount = @($shapes | Where-Object { $_.name -in $furniture }).Count
        titleShapeCount = $title.Count
        titleVerticallyClipped = [bool]($title | Where-Object { $_.verticallyClippedByBounds })
        titleLineCount = if ($title.Count -eq 1) { $title[0].lineCount } else { $null }
        titleFontName = if ($title.Count -eq 1) { $title[0].fontName } else { $null }
        titleMarginsPt = if ($title.Count -eq 1) { [ordered]@{ left = $title[0].marginLeftPt; right = $title[0].marginRightPt; top = $title[0].marginTopPt; bottom = $title[0].marginBottomPt } } else { $null }
    }
}

try {
    $powerPoint = New-Object -ComObject PowerPoint.Application
    $powerPoint.DisplayAlerts = 1
    $presentation = $powerPoint.Presentations.Open($source, $true, $false, $false)
    $openFacts = Get-DeckFacts $presentation
    # ppSaveAsOpenXMLPresentation = 24
    $presentation.SaveAs($roundtrip, 24)
    $presentation.Close()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($presentation)
    $presentation = $null

    $reopened = $powerPoint.Presentations.Open($roundtrip, $true, $false, $false)
    $reopenFacts = Get-DeckFacts $reopened
    $reopened.Slides.Item(1).Export($render, 'PNG', 1280, 720)

    $accepted = (
        $openFacts.slideCount -eq 1 -and
        $reopenFacts.slideCount -eq 1 -and
        $reopenFacts.customMasterCount -eq 1 -and
        $reopenFacts.customMasterFurnitureMissing.Count -eq 0 -and
        @($reopenFacts.customMasterLayoutNames) -contains 'Standard Title' -and
        @($reopenFacts.customMasterLayoutNames) -contains 'Long Title' -and
        $reopenFacts.slideLayoutName -eq 'Long Title' -and
        $reopenFacts.furnitureBodyDuplicateCount -eq 0 -and
        $reopenFacts.titleShapeCount -eq 1 -and
        -not $reopenFacts.titleVerticallyClipped -and
        $reopenFacts.titleLineCount -eq 3 -and
        $reopenFacts.titleFontName -eq 'Malgun Gothic' -and
        [math]::Abs($reopenFacts.titleMarginsPt.left - 7.2) -lt 0.01 -and
        [math]::Abs($reopenFacts.titleMarginsPt.right - 7.2) -lt 0.01 -and
        [math]::Abs($reopenFacts.titleMarginsPt.top - 3.6) -lt 0.01 -and
        [math]::Abs($reopenFacts.titleMarginsPt.bottom - 3.6) -lt 0.01 -and
        (Test-Path -LiteralPath $render)
    )
    $facts = [ordered]@{
        status = if ($accepted) { 'pass' } else { 'fail' }
        sourcePptxSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()
        application = [ordered]@{
            name = [string]$powerPoint.Name
            version = [string]$powerPoint.Version
            build = [string]$powerPoint.Build
        }
        openMode = 'read-only, untitled=false, withWindow=false'
        initialOpen = $openFacts
        saveAsOpenXmlPresentation = $true
        reopenedRoundtrip = $reopenFacts
        renderedPng = $render
        repairDialogObservation = 'not directly observable in a windowless run; open-save-reopen completed without COM error'
    }
    $facts | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 -LiteralPath $factsPath
    $facts | ConvertTo-Json -Depth 12
    if (-not $accepted) { exit 1 }
}
finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($presentation)
    }
    if ($null -ne $reopened) {
        $reopened.Close()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($reopened)
    }
    if ($null -ne $powerPoint) {
        $powerPoint.Quit()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
