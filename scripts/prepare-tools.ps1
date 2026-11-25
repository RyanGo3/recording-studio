param([switch]$ForceDownload)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$ToolDir = Join-Path $ProjectDir "tools"
$LicenseDir = Join-Path $ProjectDir "licenses\ffmpeg"
$Version = "8.1.2"
$ArchiveName = "ffmpeg-$Version-essentials_build.zip"
$Url = "https://github.com/GyanD/codexffmpeg/releases/download/$Version/$ArchiveName"
$FallbackUrl = "https://www.gyan.dev/ffmpeg/builds/packages/$ArchiveName"
$PinnedArchiveHash = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
$PinnedExeHash = "1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e"
$PinnedProbeHash = "b49ccc7c6547b141ad5a2f6ec69cc04323d7133d7704d70b331b904c63eecb07"
$Archive = Join-Path $ToolDir $ArchiveName
$Dest = Join-Path $ToolDir "ffmpeg.exe"
$ProbeDest = Join-Path $ToolDir "ffprobe.exe"
New-Item -ItemType Directory -Path $ToolDir, $LicenseDir -Force | Out-Null
if ((Test-Path -LiteralPath $Dest) -and -not $ForceDownload) {
    $VersionLines = @(& $Dest -version)
    $VersionLine = $VersionLines[0]
    if ($LASTEXITCODE -ne 0 -or $VersionLine -notmatch "ffmpeg version $([regex]::Escape($Version))-essentials_build") {
        throw "Existing tools\ffmpeg.exe has an unexpected version; check it before continuing."
    }
    if ((Get-FileHash -LiteralPath $Dest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $PinnedExeHash) {
        throw "Existing tools\ffmpeg.exe does not match the pinned SHA-256."
    }
    Write-Host "Verified existing $Dest"
    if (Test-Path -LiteralPath $ProbeDest) {
        if ((Get-FileHash -LiteralPath $ProbeDest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $PinnedProbeHash) {
            throw "Existing tools\ffprobe.exe does not match the pinned SHA-256."
        }
        Write-Host "Verified existing $ProbeDest"
        exit 0
    }
}
if ((Test-Path -LiteralPath $Dest) -and $ForceDownload) {
    throw "tools\ffmpeg.exe already exists. Save it elsewhere before continuing."
}
if (-not (Test-Path -LiteralPath $Archive)) {
    $Downloaded = $false
    foreach ($CandidateUrl in @($Url, $FallbackUrl)) {
        for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
            $Pending = Join-Path $ToolDir ("$ArchiveName." + [guid]::NewGuid().ToString("N") + ".download")
            try {
                Invoke-WebRequest -Uri $CandidateUrl -OutFile $Pending -UseBasicParsing -TimeoutSec 180
                $PendingHash = (Get-FileHash -LiteralPath $Pending -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($PendingHash -ne $PinnedArchiveHash) { throw "Downloaded FFmpeg archive has an unexpected SHA-256." }
                Move-Item -LiteralPath $Pending -Destination $Archive
                $Downloaded = $true
                $Url = $CandidateUrl
                break
            }
            catch {
                Write-Warning "FFmpeg download attempt $Attempt failed: $($_.Exception.Message)"
                if ($Attempt -lt 3) { Start-Sleep -Seconds (2 * $Attempt) }
            }
        }
        if ($Downloaded) { break }
    }
    if (-not $Downloaded) { throw "Unable to download the pinned FFmpeg archive from either upstream source." }
}
$Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $PinnedArchiveHash) { throw "FFmpeg SHA-256 verification failed. Check the retained download." }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    foreach ($ToolName in @("ffmpeg", "ffprobe")) {
        $Exe = @($Zip.Entries | Where-Object { $_.FullName -match "/bin/$ToolName\.exe$" })
        if ($Exe.Count -ne 1) { throw "Unexpected $ToolName path inside the ZIP archive." }
        $ToolTarget = Join-Path $ToolDir "$ToolName.exe"
        if (Test-Path -LiteralPath $ToolTarget) {
            $EntryStream = $Exe[0].Open()
            $Hasher = [System.Security.Cryptography.SHA256]::Create()
            try {
                $ExpectedHash = [BitConverter]::ToString($Hasher.ComputeHash($EntryStream)).Replace("-", "").ToLowerInvariant()
            }
            finally { $EntryStream.Dispose(); $Hasher.Dispose() }
            if ((Get-FileHash -LiteralPath $ToolTarget -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
                throw "Existing $ToolName.exe does not match the pinned archive; no files were overwritten."
            }
        }
        else {
            [IO.Compression.ZipFileExtensions]::ExtractToFile($Exe[0], $ToolTarget, $false)
        }
    }
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
    ffprobe_sha256 = (Get-FileHash -LiteralPath $ProbeDest -Algorithm SHA256).Hash.ToLowerInvariant()
    source_url = "https://github.com/FFmpeg/FFmpeg/tree/n$Version"
    build_source_url = "https://github.com/GyanD/codexffmpeg"
}
$Metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ToolDir "ffmpeg-provenance.json") -Encoding UTF8
Write-Host "Verified FFmpeg $Version -> $Dest"
