[CmdletBinding()]
param(
    [ValidateSet('All', 'Bootstrap', 'Dependencies', 'SourceDependencies', 'Configure', 'Build', 'Test', 'Package', 'Validate', 'Hardware')]
    [string]$Stage = 'All',
    [ValidateRange(1, 64)][int]$Jobs = 2,
    [string]$HardwarePackage = '',
    [switch]$ResumeHardwareCapture
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$env:LANG = 'C.UTF-8'
$Repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$Work = Join-Path $Repo '.build\windows-arm64'
$Msys = Join-Path $Work 'msys64'
$Logs = Join-Path $Work 'logs'
New-Item -ItemType Directory -Force $Work, $Logs | Out-Null
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:PATH"
$env:PATHEXT = ".COM;.EXE;.BAT;.CMD;$env:PATHEXT"
$env:MSYSTEM = 'CLANGARM64'
$env:CHERE_INVOKING = '1'
$env:ARDOUR_WINDOWS_REPO = $Repo
$env:ARDOUR_BUILD_JOBS = "$Jobs"

function Install-Msys {
    if (Test-Path "$Msys\usr\bin\bash.exe") { return }
    # The MSYS shell may be x64; CLANGARM64 compilers and application binaries
    # are native ARM64. Do not confuse the bootstrap host with the build target.
    $Release = Invoke-RestMethod 'https://api.github.com/repos/msys2/msys2-installer/releases/latest'
    $Asset = @($Release.assets | Where-Object { $_.name -match '^msys2-base-x86_64-.*\.tar\.xz$' })[0]
    if (!$Asset) { throw 'Official MSYS2 base archive was not found.' }
    $Archive = Join-Path $Work $Asset.name
    if ($Asset.digest -notmatch '^sha256:([a-fA-F0-9]{64})$') {
        throw 'Official MSYS2 archive SHA256 digest was not found.'
    }
    $Expected = $Matches[1]
    if (!(Test-Path $Archive)) {
        Write-Host "Downloading $($Asset.name)"
        (New-Object Net.WebClient).DownloadFile($Asset.browser_download_url, $Archive)
    }
    if ((Get-FileHash -Algorithm SHA256 $Archive).Hash -ne $Expected) {
        throw 'MSYS2 bootstrap SHA256 mismatch; remove the archive and retry.'
    }
    & "$env:SystemRoot\System32\tar.exe" -xf $Archive -C $Work
    if ($LASTEXITCODE) { throw 'MSYS2 archive extraction failed.' }
    $Release.tag_name | Set-Content (Join-Path $Work 'msys2-release.txt')
}

function Invoke-BuildStage([string]$Name) {
    $Log = Join-Path $Logs "$Name.log"
    if (Test-Path $Log) {
        $PreviousTime = (Get-Item $Log).LastWriteTimeUtc.ToString('yyyyMMdd-HHmmss-fffffff')
        Copy-Item $Log (Join-Path $Logs "$Name-$PreviousTime.log")
    }
    Write-Host "=== $Name (log: $Log)"
    $Run = Join-Path $Work 'run'
    New-Item -ItemType Directory -Force $Run | Out-Null
    $Script = Join-Path $Run "$Name-$([Guid]::NewGuid()).sh"
    Copy-Item (Join-Path $PSScriptRoot 'build.sh') $Script
    $Script = $Script.Replace('\', '/')
    $Info = New-Object Diagnostics.ProcessStartInfo
    $Info.FileName = "$Msys\usr\bin\bash.exe"
    $Info.Arguments = '--login "' + $Script + '" ' + $Name
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    $Info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $Info.StandardErrorEncoding = $Info.StandardOutputEncoding
    $Process = New-Object Diagnostics.Process
    $Process.StartInfo = $Info
    $Writer = New-Object IO.StreamWriter($Log, $false, (New-Object Text.UTF8Encoding($false)))
    $Writer.AutoFlush = $true
    try {
        if (!$Process.Start()) { throw "Could not start $Name" }
        $OutTask = $Process.StandardOutput.ReadLineAsync()
        $ErrTask = $Process.StandardError.ReadLineAsync()
        while ($null -ne $OutTask -or $null -ne $ErrTask) {
            if ($null -ne $OutTask -and $OutTask.IsCompleted) {
                $Line = $OutTask.GetAwaiter().GetResult()
                if ($null -eq $Line) { $OutTask = $null } else {
                    $Writer.WriteLine($Line)
                    Write-Output $Line
                    $OutTask = $Process.StandardOutput.ReadLineAsync()
                }
            }
            if ($null -ne $ErrTask -and $ErrTask.IsCompleted) {
                $Line = $ErrTask.GetAwaiter().GetResult()
                if ($null -eq $Line) { $ErrTask = $null } else {
                    $Writer.WriteLine($Line)
                    Write-Output $Line
                    $ErrTask = $Process.StandardError.ReadLineAsync()
                }
            }
            if (($null -eq $OutTask -or !$OutTask.IsCompleted) -and
                ($null -eq $ErrTask -or !$ErrTask.IsCompleted)) {
                Start-Sleep -Milliseconds 20
            }
        }
        $Process.WaitForExit()
        $Result = $Process.ExitCode
    } finally {
        $Writer.Dispose()
        $Process.Dispose()
    }
    if ($Result) { throw "Windows ARM64 stage '$Name' failed (exit $Result); see $Log" }
}

if ($Stage -in @('All', 'Bootstrap')) {
    Install-Msys
    Invoke-BuildStage 'Bootstrap'
}
if ($Stage -eq 'Bootstrap') { exit }
if (!(Test-Path "$Msys\usr\bin\bash.exe")) { throw 'Run -Stage Bootstrap first.' }
if ($Stage -eq 'Hardware') {
    $Package = if ($HardwarePackage) { [IO.Path]::GetFullPath($HardwarePackage) } else {
        Join-Path $Repo 'dist\Ardour-arm64'
    }
    if (!(Test-Path (Join-Path $Package 'bin\Ardour.exe'))) {
        throw 'Run -Stage Package and -Stage Validate before hardware testing.'
    }
    $env:PATH = "$(Join-Path $Package 'bin');$Msys\clangarm64\bin;$env:SystemRoot\System32;$env:SystemRoot"
    $Python = Join-Path $Msys 'clangarm64\bin\python.exe'
    $ReportCopy = Join-Path $Repo 'dist\Ardour-arm64-hardware-validation.json'
    $Arguments = '"' + (Join-Path $PSScriptRoot 'hardware_h2n.py') +
        '" --package "' + $Package + '" --work "' + $Work +
        '" --report-copy "' + $ReportCopy + '"'
    if ($ResumeHardwareCapture) { $Arguments += ' --resume-capture' }
    $HardwareProcess = Start-Process -FilePath $Python -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
    if ($HardwareProcess.ExitCode) {
        throw "H2n hardware validation failed (exit $($HardwareProcess.ExitCode)); see $Work\hardware-h2n"
    }
    exit
}
if ($Stage -eq 'All') {
    foreach ($Name in @('Dependencies', 'Configure', 'Build', 'Test', 'Package', 'Validate')) {
        Invoke-BuildStage $Name
    }
} else {
    Invoke-BuildStage $Stage
}
