param(
  [int]$Port = 8765
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
  throw "Python 3 is required. Install Python or add it to PATH."
}

Start-Process "http://localhost:$Port/index.html"
Write-Host "KOSPI200 Research Desk: http://localhost:$Port/index.html"
if ($python.Name -eq "py.exe") {
  & $python.Source -3 "$root\dashboard_server.py" --port $Port
} else {
  & $python.Source "$root\dashboard_server.py" --port $Port
}
