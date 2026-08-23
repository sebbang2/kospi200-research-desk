param(
  [string]$UniverseCsv,
  [int]$Delay = 1.2
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if ([string]::IsNullOrWhiteSpace($UniverseCsv)) {
  $UniverseCsv = Join-Path $root 'kospi200_universe.csv'
}
if (-not (Test-Path -LiteralPath $UniverseCsv)) {
  throw "Universe CSV not found: $UniverseCsv. Run bootstrap_kospi200.py first."
}

if (Test-Path -LiteralPath $bundledPython) {
  $pythonPath = $bundledPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $pythonPath = (Get-Command python).Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  $pythonPath = (Get-Command py).Source
} else {
  throw "Python 3 is required. Install Python or add it to PATH."
}

if ([IO.Path]::GetFileName($pythonPath) -eq "py.exe") {
  & $pythonPath -3 "$root\refresh_research.py" --universe $UniverseCsv --output-dir $root --delay $Delay
} else {
  & $pythonPath "$root\refresh_research.py" --universe $UniverseCsv --output-dir $root --delay $Delay
}
