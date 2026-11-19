param([switch]$ForceDownload)
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$ToolDir = Join-Path $ProjectDir "tools"
$LicenseDir = Join-Path $ProjectDir "licenses\ffmpeg"
$Version = "8.1.2"
$ArchiveName = "ffmpeg-$Version-essentials_build.zip"
$Url = "https://www.gyan.dev/ffmpeg/builds/packages/$ArchiveName"
$PinnedArchiveHash = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
$PinnedExeHash = "1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e"
$Archive = Join-Path $ToolDir $ArchiveName
$Dest = Join-Path $ToolDir "ffmpeg.exe"
New-Item -ItemType Directory -Path $ToolDir, $LicenseDir -Force | Out-Null
if ((Test-Path -LiteralPath $Dest) -and -not $ForceDownload) {
    $VersionLine = (& $Dest -version | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or $VersionLine -notmatch "ffmpeg version $([regex]::Escape($Version))-essentials_build") {
        throw "Existing tools\ffmpeg.exe has an unexpected version; check it before continuing."
    }
    if ((Get-FileHash -LiteralPath $Dest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $PinnedExeHash) {
        throw "Existing tools\ffmpeg.exe does not match the pinned SHA-256."
    }
    Write-Host "Reusing $Dest"
    exit 0
}
if (Test-Path -LiteralPath $Dest) {
    throw "tools\ffmpeg.exe already exists. Save it elsewhere before continuing."
}
$Checksum = ([string](Invoke-WebRequest -Uri "$Url.sha256" -UseBasicParsing).Content).Trim().Split()[0].ToLowerInvariant()
if ($Checksum -notmatch '^[0-9a-f]{64}$') { throw "Invalid upstream SHA-256 response." }
if ($Checksum -ne $PinnedArchiveHash) { throw "Upstream SHA-256 differs from the pinned release." }
if (-not (Test-Path -LiteralPath $Archive)) {
    Invoke-WebRequest -Uri $Url -OutFile $Archive -UseBasicParsing
}
$Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $Checksum) { throw "FFmpeg SHA-256 verification failed. Check the retained download." }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    $Exe = @($Zip.Entries | Where-Object { $_.FullName -match '/bin/ffmpeg\.exe$' })
    if ($Exe.Count -ne 1) { throw "Unexpected FFmpeg path inside the ZIP archive." }
    [IO.Compression.ZipFileExtensions]::ExtractToFile($Exe[0], $Dest, $false)
    foreach ($Entry in $Zip.Entries) {
        if ($Entry.Name -match '^(LICENSE|COPYING|README)') {
            $Target = Join-Path $LicenseDir $Entry.Name
            if (-not (Test-Path -LiteralPath $Target)) {
                [IO.Compression.ZipFileExtensions]::ExtractToFile($Entry, $Target, $false)
            }
        }
    }
}
finally { $Zip.Dispose() }
$Metadata = [ordered]@{
    version = $Version
    binary_url = $Url
    archive_sha256 = $Actual
    exe_sha256 = (Get-FileHash -LiteralPath $Dest -Algorithm SHA256).Hash.ToLowerInvariant()
    source_url = "https://github.com/FFmpeg/FFmpeg/tree/n$Version"
    build_source_url = "https://github.com/GyanD/codexffmpeg"
}
$Metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ToolDir "ffmpeg-provenance.json") -Encoding UTF8
Write-Host "Verified FFmpeg $Version -> $Dest"
