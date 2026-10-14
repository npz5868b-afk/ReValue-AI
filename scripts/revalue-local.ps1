[CmdletBinding()]
param(
  [ValidateSet('Start','Stop','Status','Reset','ResetAndStart')]
  [string]$Action = 'Start'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root '.local-run'
$Venv = Join-Path $Root '.venv'
$PidFile = Join-Path $Runtime 'server.pid'
$LogFile = Join-Path $Runtime 'server.log'
$ErrorLogFile = Join-Path $Runtime 'server-error.log'
$DbFile = Join-Path $Runtime 'report_snapshots.sqlite3'
$BrowserResetMarker = Join-Path $Runtime 'browser-reset-pending'
$Port = 8765

function Write-Info([string]$Message) { Write-Host "[ReValue AI] $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "[ReValue AI] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "[ReValue AI] $Message" -ForegroundColor Red }

function Get-Listener {
  return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
}

function Get-ManagedProcess {
  if (-not (Test-Path -LiteralPath $PidFile)) { return $null }
  $raw = (Get-Content -LiteralPath $PidFile -Raw).Trim()
  $managedPid = 0
  if (-not [int]::TryParse($raw, [ref]$managedPid)) { Remove-Item -LiteralPath $PidFile -Force; return $null }
  $process = Get-Process -Id $managedPid -ErrorAction SilentlyContinue
  if ($null -eq $process) { Remove-Item -LiteralPath $PidFile -Force; return $null }
  return $process
}

function Test-DescendantProcess([int]$ChildPid, [int]$AncestorPid) {
  $current = $ChildPid
  for ($depth = 0; $depth -lt 8; $depth++) {
    if ($current -eq $AncestorPid) { return $true }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $current" -ErrorAction SilentlyContinue
    if ($null -eq $process -or -not $process.ParentProcessId) { return $false }
    $current = [int]$process.ParentProcessId
  }
  return $false
}

function Get-FileSha256([string]$Path) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $stream = [System.IO.File]::OpenRead($Path)
  try { return -join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString('x2') }) }
  finally { $stream.Dispose(); $sha.Dispose() }
}

function Open-LocalBrowser {
  $url = "http://127.0.0.1:$Port"
  if (Test-Path -LiteralPath $BrowserResetMarker) {
    $url += '?reset_local_demo=1'
    Remove-Item -LiteralPath $BrowserResetMarker -Force
  }
  Start-Process $url
}

function Stop-ManagedServer {
  $process = Get-ManagedProcess
  if ($null -eq $process) { Write-Info 'No launcher-managed local server is running.'; return }
  $listener = Get-Listener
  if ($null -eq $listener -or $listener.OwningProcess -ne $process.Id) {
    Write-Warn "PID file does not own port $Port. Leaving process $($process.Id) untouched and removing stale PID file."
    Remove-Item -LiteralPath $PidFile -Force
    return
  }
  Write-Info "Stopping launcher-managed server (PID $($process.Id))..."
  Stop-Process -Id $process.Id
  for ($i = 0; $i -lt 30; $i++) {
    if ($null -eq (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 200
  }
  if ($null -ne (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) { throw 'The local server did not stop in time. It was not force-killed.' }
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  Write-Info 'Local server stopped.'
}

$PythonProbe = 'import struct,sys;print(sys.executable);print(sys._base_executable);print(sys.version_info.major,sys.version_info.minor,sep=chr(46));print(struct.calcsize(chr(80))*8)'

function Get-PythonInfo([string]$Command, [string[]]$Prefix = @(), [string]$Source = $Command) {
  try {
    $raw = & $Command @($Prefix + @('-c', $PythonProbe)) 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
    $probe = @($raw | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($probe.Count -lt 4) { return $null }
    $executable = $probe[$probe.Count - 4]
    $baseExecutable = $probe[$probe.Count - 3]
    $version = $probe[$probe.Count - 2]
    $architecture = $probe[$probe.Count - 1]
    if (-not (Test-Path -LiteralPath $executable)) { return $null }
    return [pscustomobject]@{
      Executable = (Resolve-Path -LiteralPath $executable).Path
      BaseExecutable = $baseExecutable
      Version = $version
      Architecture = [int]$architecture
      IsWindowsApps = $executable -match '\\WindowsApps\\'
      Source = $Source
    }
  } catch { return $null }
}

function Select-CompatiblePython([object[]]$Candidates) {
  $supported = @($Candidates | Where-Object {
    $_ -and $_.Version -match '^3\.(10|11|12|13)$' -and $_.Architecture -eq 64
  })
  $native = @($supported | Where-Object { -not $_.IsWindowsApps })
  $pool = if ($native.Count) { $native } else { $supported }
  return @($pool | Sort-Object -Property @{ Expression = { [version]($_.Version + '.0') }; Descending = $true }, @{ Expression = { $_.Source -like 'py -*' }; Descending = $true } | Select-Object -First 1)[0]
}

function Get-CompatiblePython {
  $candidates = [System.Collections.Generic.List[object]]::new()
  $seen = @{}
  function Add-PythonCandidate([object]$Candidate) {
    if ($null -eq $Candidate) { return }
    $key = $Candidate.Executable.ToLowerInvariant()
    if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $candidates.Add($Candidate) }
  }

  # Probe each supported version explicitly. "py -3" only asks for the default
  # major version and can therefore hide an installed 3.11 behind a 3.14 default.
  if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    foreach ($version in @('3.13','3.12','3.11','3.10')) {
      Add-PythonCandidate (Get-PythonInfo -Command 'py.exe' -Prefix @("-$version") -Source "py -$version")
    }
  }

  # A broken or stale py registration must not hide a working native install.
  # This also lets us prefer a real interpreter over a WindowsApps alias.
  foreach ($version in @('313','312','311','310')) {
    $paths = @()
    if ($env:LOCALAPPDATA) { $paths += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe" }
    if ($env:USERPROFILE) { $paths += Join-Path $env:USERPROFILE "AppData\Local\Programs\Python\Python$version\python.exe" }
    if ($env:ProgramFiles) { $paths += Join-Path $env:ProgramFiles "Python$version\python.exe" }
    if (${env:ProgramFiles(x86)}) { $paths += Join-Path ${env:ProgramFiles(x86)} "Python$version\python.exe" }
    foreach ($path in $paths) {
      if ($path -and (Test-Path -LiteralPath $path)) { Add-PythonCandidate (Get-PythonInfo -Command $path -Source 'native install') }
    }
  }

  $selected = Select-CompatiblePython $candidates.ToArray()
  if ($null -ne $selected) {
    Write-Info "Selected Python $($selected.Version) ($($selected.Architecture)-bit): $($selected.Executable) [$($selected.Source)]"
    return $selected
  }
  throw 'Python 3.10, 3.11, 3.12, or 3.13 (64-bit) is required. Install a native 64-bit version from https://www.python.org/downloads/windows/ and rerun START_REVALUE_AI.bat. Python 3.14 is not yet validated for this dependency set.'
}

function Invoke-LocalPython([pscustomobject]$Python, [string[]]$Arguments) {
  & $Python.Executable @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Python command failed: $($Arguments -join ' ')" }
}

function Ensure-Environment {
  $python = Get-CompatiblePython
  $venvPython = Join-Path $Venv 'Scripts\python.exe'
  $recreateVenv = -not (Test-Path -LiteralPath $venvPython)
  if (-not $recreateVenv) {
    $venvInfo = Get-PythonInfo -Command $venvPython -Source 'existing virtual environment'
    $sameBase = $venvInfo -and $venvInfo.BaseExecutable -and ($venvInfo.BaseExecutable -ieq $python.Executable)
    if ($null -eq $venvInfo -or $venvInfo.Version -ne $python.Version -or $venvInfo.Architecture -ne $python.Architecture -or -not $sameBase) { $recreateVenv = $true }
  }
  if ($recreateVenv -and (Test-Path -LiteralPath $Venv)) {
    $backup = "$Venv.incompatible-$(Get-Date -Format 'yyyyMMddHHmmss')"
    Write-Warn "Existing virtual environment is missing or incompatible with Python $($python.Version). Moving it to $backup."
    Move-Item -LiteralPath $Venv -Destination $backup
  }
  if ($recreateVenv) {
    Write-Info "Creating project-local virtual environment with Python $($python.Version)..."
    Invoke-LocalPython $python @('-m','venv',$Venv)
  }
  $requirements = Join-Path $Root 'backend\requirements.txt'
  $stamp = Join-Path $Runtime 'requirements.sha256'
  New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
  $hash = Get-FileSha256 $requirements
  $oldHash = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
  if ($hash -ne $oldHash) {
    Write-Info 'Installing or updating local Python dependencies. This first-time step may need internet access and can take several minutes.'
    & $venvPython -m pip install --upgrade pip | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Could not update pip. Check your internet connection, proxy, or Python installation.' }
    & $venvPython -m pip install -r $requirements | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Could not install requirements. The UI and source remain unchanged; see the error above for the missing package or network issue.' }
    Set-Content -LiteralPath $stamp -Value $hash -NoNewline
  } else { Write-Info 'Project-local dependencies are already prepared.' }
  return $venvPython
}

function Test-Health {
  try { return (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2).status -eq 'ok' } catch { return $false }
}

function Start-Server {
  New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
  $managed = Get-ManagedProcess
  if ($null -ne $managed -and (Test-Health)) { Write-Info "Already running at http://127.0.0.1:$Port (PID $($managed.Id))."; Open-LocalBrowser; return }
  $listener = Get-Listener
  if ($null -ne $listener) { throw "Port $Port is already in use by PID $($listener.OwningProcess). ReValue AI will not stop another program. Close that program or use this port, then try again." }
  $venvPython = Ensure-Environment
  Write-Info 'Starting the real local ReValue backend with project-local SQLite storage...'
  $env:PORT = "$Port"
  $env:REVALUE_REPORT_DB = $DbFile
  $env:REVALUE_REPORT_DATABASE_URL = ''
  $env:REVALUE_PUBLIC_BASE_URL = ''
  $env:PYTHONUNBUFFERED = '1'
  $process = Start-Process -FilePath $venvPython -ArgumentList @('-m','backend.src.server') -WorkingDirectory $Root -RedirectStandardOutput $LogFile -RedirectStandardError $ErrorLogFile -PassThru -WindowStyle Hidden
  Set-Content -LiteralPath $PidFile -Value $process.Id -NoNewline
  for ($i = 0; $i -lt 45; $i++) {
    if (Test-Health) {
      $listener = Get-Listener
      if ($null -eq $listener -or -not (Test-DescendantProcess $listener.OwningProcess $process.Id)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        throw 'The health endpoint responded from a process not created by this launcher. It was left untouched.'
      }
      # On Windows a virtual-environment executable can remain as a wrapper while
      # the base interpreter owns the listening socket. Record the verified child
      # so STOP_REVALUE_AI.bat continues to target only the real local server.
      Set-Content -LiteralPath $PidFile -Value $listener.OwningProcess -NoNewline
      Write-Info "Ready at http://127.0.0.1:$Port"; Write-Host "Local reports are stored only in $Runtime" -ForegroundColor DarkGray; Write-Host 'QR share links use localhost and are intended for this computer unless you deliberately configure a LAN-safe public base URL.' -ForegroundColor DarkGray; Open-LocalBrowser; return
    }
    if ($process.HasExited) { $detail = @($LogFile,$ErrorLogFile | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Get-Content -LiteralPath $_ -Tail 20 }) -join "`n"; Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue; throw "The backend exited during startup.`n$detail" }
    Start-Sleep -Milliseconds 250
  }
  throw "The backend did not pass /health within 12 seconds. Review $LogFile."
}

function Reset-LocalDemo {
  $managed = Get-ManagedProcess
  if ($null -ne $managed) { Write-Warn 'Stop the local server before reset so SQLite can be safely removed.'; $answer = Read-Host 'Stop this launcher-managed server now? [Y/N]'; if ($answer -notmatch '^[Yy]') { throw 'Reset cancelled. No files were changed.' }; Stop-ManagedServer }
  Write-Host 'This removes only ReValue AI local report snapshots and the documented ReValue AI chat key from the localhost browser origin.' -ForegroundColor Yellow
  $answer = Read-Host "Delete $DbFile if present? [Y/N]"
  if ($answer -notmatch '^[Yy]') { Write-Info 'Reset cancelled. No files were changed.'; return }
  $removed = @()
  if (Test-Path -LiteralPath $DbFile) { Remove-Item -LiteralPath $DbFile -Force; $removed += '.local-run\report_snapshots.sqlite3' }
  Get-ChildItem -LiteralPath $Runtime -Filter 'report_snapshots.sqlite3-*' -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force; $removed += $_.Name }
  Set-Content -LiteralPath $BrowserResetMarker -Value 'Clear only ReValue AI localhost chat state on the next launcher-opened page.' -NoNewline
  if ($removed.Count -eq 0) { $removed += 'No local report database existed' }
  Write-Info ('Reset complete: ' + ($removed -join '; '))
  Write-Host 'The next launcher-opened localhost page clears only ReValue AI chat state for that origin.' -ForegroundColor DarkGray
}

if ($MyInvocation.InvocationName -ne '.') {
  switch ($Action) {
    'Start' { Start-Server }
    'Stop' { Stop-ManagedServer }
    'Status' { $managed=Get-ManagedProcess; if ($null -ne $managed -and (Test-Health)) { Write-Info "Running at http://127.0.0.1:$Port (PID $($managed.Id))." } else { Write-Info 'Not running.' } }
    'Reset' { Reset-LocalDemo }
    'ResetAndStart' { Reset-LocalDemo; Start-Server }
  }
}
