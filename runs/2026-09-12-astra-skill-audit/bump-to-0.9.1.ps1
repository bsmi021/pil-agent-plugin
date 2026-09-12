$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$oldVersion = '0.9.0'
$newVersion = '0.9.1'

$fixedTargets = @(
    'plugin.json',
    'pyproject.toml',
    '.codex-plugin\plugin.json',
    '.claude-plugin\plugin.json',
    '.claude-plugin\marketplace.json',
    'tests\test_character_sheet_review.py',
    'tests\test_components.py',
    'tests\test_silhouette.py',
    'uv.lock'
)

$scriptTargets = Get-ChildItem (Join-Path $repositoryRoot 'scripts') -Filter 'pil_*.py' |
    Where-Object {
        [System.IO.File]::ReadAllText($_.FullName).Contains("TOOL_VERSION = `"$oldVersion`"")
    }

if ($scriptTargets.Count -ne 30) {
    throw "Expected 30 public tool version carriers, found $($scriptTargets.Count)."
}

$targets = @($fixedTargets | ForEach-Object { Join-Path $repositoryRoot $_ }) + @($scriptTargets.FullName)
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

foreach ($target in $targets) {
    $text = [System.IO.File]::ReadAllText($target)
    $updated = $text.Replace("`"$oldVersion`"", "`"$newVersion`"")
    if ($updated -eq $text) {
        throw "No $oldVersion version literal found in $target."
    }
    [System.IO.File]::WriteAllText($target, $updated, $utf8NoBom)
}

Write-Output "Updated $($targets.Count) version-carrying files from $oldVersion to $newVersion."
