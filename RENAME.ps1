<#
.SYNOPSIS
    Renames CHATIFY / NOVA / NYRO to NOVIS (case-matched) inside text files under a folder.

.DESCRIPTION
    Case mapping:
      CHATIFY, NOVA, NYRO   -> NOVIS
      chatify, nova, nyro   -> novis
      Chatify, Nova, Nyro   -> Novis

    Default: dry run. Prints every changed line (before and after) and writes nothing.
    -Apply: writes the changes. Originals are backed up to a sibling folder
            named NOVIS_backup_<timestamp> unless -NoBackup is given.

    Whole words only by default ("innovation" is untouched).
    -Substring also matches inside identifiers such as ChatifyAI or nova_api.

    Skipped automatically: this script, .git folders, binary files,
    and files that are not valid UTF-8 (so they are never corrupted).

.EXAMPLE
    .\Convert-NovisRename.ps1                 # dry run, shows what would change
    .\Convert-NovisRename.ps1 -Apply          # apply changes with backup
#>
param(
    [string]$Root = 'C:\Users\Renewfy\Links\NOVIS',
    [switch]$Apply,
    [switch]$Substring,
    [switch]$NoBackup
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).ProviderPath.TrimEnd('\')

$names   = 'CHATIFY|NOVA|NYRO|chatify|nova|nyro|Chatify|Nova|Nyro'
$pattern = if ($Substring) { $names } else { "(?<![A-Za-z])(?:$names)(?![A-Za-z])" }
$rx      = [regex]::new($pattern)   # case-sensitive

$evaluator = [System.Text.RegularExpressions.MatchEvaluator]{
    param($m)
    $v = $m.Value
    if     ($v -cmatch '^[A-Z]+$') { 'NOVIS' }
    elseif ($v -cmatch '^[a-z]+$') { 'novis' }
    else                           { 'Novis' }
}

# Strict UTF-8: throws on invalid bytes, so non-UTF-8 files are skipped, not damaged
$utf8 = New-Object System.Text.UTF8Encoding($false, $true)

function Test-IsBinary([byte[]]$Bytes) {
    $len = [Math]::Min($Bytes.Length, 8000)
    for ($i = 0; $i -lt $len; $i++) {
        if ($Bytes[$i] -eq 0) { return $true }
    }
    return $false
}

$self          = $PSCommandPath
$rootPrefixLen = $Root.Length + 1
$files = Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
    Where-Object { $_.FullName -ne $self -and $_.FullName -notmatch '\\\.git\\' }

$backupDir    = $null
$filesScanned = 0
$filesChanged = 0
$linesChanged = 0
$replacements = 0
$skipped      = 0

Write-Host "Root:  $Root"
Write-Host "Mode:  $(if ($Apply) {'APPLY (files will be written)'} else {'DRY RUN (nothing written)'})  |  Match: $(if ($Substring) {'substring'} else {'whole word'})"
Write-Host ""

foreach ($file in $files) {
    $filesScanned++

    try {
        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        if (Test-IsBinary $bytes) { continue }
        $text = $utf8.GetString($bytes)
    }
    catch {
        Write-Warning "Skipped (unreadable or not UTF-8): $($file.FullName)"
        $skipped++
        continue
    }

    if (-not $rx.IsMatch($text)) { continue }

    $lineNo = 0
    foreach ($line in ($text -split "`n")) {
        $lineNo++
        if ($rx.IsMatch($line)) {
            $linesChanged++
            $replacements += $rx.Matches($line).Count
            $newLine = $rx.Replace($line, $evaluator)
            Write-Host "$($file.FullName):$lineNo" -ForegroundColor Cyan
            Write-Host "  - $($line.Trim())"       -ForegroundColor DarkGray
            Write-Host "  + $($newLine.Trim())"    -ForegroundColor Green
        }
    }

    $filesChanged++
    if (-not $Apply) { continue }

    $newText = $rx.Replace($text, $evaluator)

    if (-not $NoBackup) {
        if (-not $backupDir) {
            $backupDir = Join-Path (Split-Path $Root -Parent) ("NOVIS_backup_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
            New-Item -ItemType Directory -Path $backupDir | Out-Null
        }
        $dest = Join-Path $backupDir $file.FullName.Substring($rootPrefixLen)
        New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $dest -Force
    }

    [System.IO.File]::WriteAllBytes($file.FullName, $utf8.GetBytes($newText))
}

Write-Host ""
Write-Host "================ SUMMARY ================" -ForegroundColor Yellow
Write-Host "Files scanned       : $filesScanned"
Write-Host "Files with matches  : $filesChanged"
Write-Host "Lines changed       : $linesChanged"
Write-Host "Replacements        : $replacements"
Write-Host "Skipped files       : $skipped"
if ($backupDir) { Write-Host "Backup folder       : $backupDir" }
if (-not $Apply) { Write-Host "Dry run only. Add -Apply to write these changes." -ForegroundColor Yellow }