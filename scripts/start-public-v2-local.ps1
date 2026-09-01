[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $repo 'app_data\public-v2-local'
$profilePath = Join-Path $runtime 'local-profile.json'
if (-not (Test-Path -LiteralPath $profilePath)) {
    throw 'A retained local-profile.json with the exact provider pricing catalog is required.'
}
$profile = Get-Content -Raw -LiteralPath $profilePath | ConvertFrom-Json
$env:PUBLIC_ARTIFACT_STORE_BACKEND = 'local-development'
$env:PUBLIC_V2_RC_PROVIDER_READY = '1'
$env:PUBLIC_PROVIDER_PRICING_REVISION = $profile.pricingCatalog.revision
$env:PUBLIC_PROVIDER_PRICING_CATALOG = $profile.pricingCatalog | ConvertTo-Json -Depth 12 -Compress
foreach ($name in @('PUBLIC_V2_RC_DATABASE_URL', 'PUBLIC_V2_RC_API_DATABASE_URL', 'PUBLIC_V2_RC_CONTROLLER_DATABASE_URL', 'PUBLIC_V2_RC_OBSERVATION_DATABASE_URL')) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
$dataArgs = @{}
if ($profile.PSObject.Properties.Name -contains 'appDataDirectory') { $dataArgs['AppDataDirectory'] = $profile.appDataDirectory }
& (Join-Path $PSScriptRoot 'start-public-v2-rc.ps1') -Serve -UseOwnedPostgres -PersistentRoot $runtime -ProviderConfigPath (Join-Path $runtime 'app-data\userConfig.json') @dataArgs
