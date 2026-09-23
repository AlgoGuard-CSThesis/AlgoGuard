$ErrorActionPreference = 'Stop'

# Keep the Stage 5A service smoke tests independent of unfinished 5B migrations.
$repoRoot = Split-Path -Parent $PSScriptRoot
$stackRoot = Join-Path $repoRoot '.local/supabase-5a'
$supabaseRoot = Join-Path $stackRoot 'supabase'
$functionRoot = Join-Path $supabaseRoot 'functions/smoke_test'

New-Item -ItemType Directory -Path $functionRoot -Force | Out-Null
$configText = Get-Content -LiteralPath (Join-Path $repoRoot 'supabase/config.toml') -Raw
if ($configText -notmatch '(?m)^project_id = "AlgoGuard"\r?$') {
    throw 'Expected the AlgoGuard project ID in supabase/config.toml.'
}
$configText = $configText.Replace('project_id = "AlgoGuard"', 'project_id = "AlgoGuard5A"')
$configText = $configText.Replace('sql_paths = ["./seed.sql"]', 'sql_paths = []')
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $supabaseRoot 'config.toml'), $configText, $utf8)
Copy-Item -LiteralPath (Join-Path $repoRoot 'supabase/functions/smoke_test/index.ts') `
    -Destination (Join-Path $functionRoot 'index.ts') -Force

Write-Output 'Prepared .local/supabase-5a with the project configuration and smoke function.'
Write-Output 'Run: npx supabase start --workdir .local/supabase-5a'
