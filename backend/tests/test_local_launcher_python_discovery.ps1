$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $Root 'scripts\revalue-local.ps1')

function Assert-Equal([object]$Actual, [object]$Expected, [string]$Message) {
  if ($Actual -ne $Expected) { throw "$Message. Expected '$Expected', got '$Actual'." }
}

# Regression: py's default can be unsupported 3.14 while py -3.11 resolves a
# native 64-bit interpreter. A WindowsApps alias must not displace it.
$selection = Select-CompatiblePython @(
  [pscustomobject]@{ Executable = 'C:\Python314\python.exe'; BaseExecutable = 'C:\Python314\python.exe'; Version = '3.14'; Architecture = 64; IsWindowsApps = $false; Source = 'py -3' },
  [pscustomobject]@{ Executable = 'C:\Users\judge\AppData\Local\Microsoft\WindowsApps\python3.13.exe'; BaseExecutable = ''; Version = '3.13'; Architecture = 64; IsWindowsApps = $true; Source = 'py -3.13' },
  [pscustomobject]@{ Executable = 'C:\Users\judge\AppData\Local\Programs\Python\Python311\python.exe'; BaseExecutable = 'C:\Users\judge\AppData\Local\Programs\Python\Python311\python.exe'; Version = '3.11'; Architecture = 64; IsWindowsApps = $false; Source = 'py -3.11' }
)
Assert-Equal $selection.Version '3.11' 'The launcher should select py -3.11 over an unsupported 3.14 default'
Assert-Equal $selection.Source 'py -3.11' 'The selected synthetic interpreter should retain its py launcher source'
Assert-Equal $selection.Architecture 64 'The selected interpreter must be 64-bit'

$live = Get-CompatiblePython
Assert-Equal $live.Version '3.11' 'This host should select the installed Python 3.11 interpreter'
Assert-Equal $live.Architecture 64 'This host should select a 64-bit Python interpreter'
if (-not (Test-Path -LiteralPath $live.Executable)) { throw "Selected executable does not exist: $($live.Executable)" }

Write-Host "launcher Python discovery tests passed: synthetic default-3.14/native-3.11 selection; live $($live.Version) $($live.Architecture)-bit $($live.Executable)"
