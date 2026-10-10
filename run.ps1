# PowerShell Launcher for OLTool
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$venvPy = ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "⚠️ Chưa tìm thấy môi trường ảo .venv! Đang tự động chạy install.ps1..." -ForegroundColor Yellow
    & .\install.ps1
}

& $venvPy cli.py $args
