$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python run.py --module monitor --gsc .\gsc_exports --max-pages 200 --export-backlog --no-report
