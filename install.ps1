# PowerShell Installer for OLTool
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  OLTool - Trình cài đặt tự động (PowerShell)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Check Python
$pyCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pyCmd = "py"
} else {
    Write-Host "❌ Lỗi: Chưa tìm thấy Python trên hệ thống!" -ForegroundColor Red
    Write-Host "👉 Hãy tải và cài đặt Python từ https://www.python.org/ (Tích chọn 'Add to PATH')"
    exit 1
}

Write-Host "🔍 Đã tìm thấy Python: $pyCmd" -ForegroundColor Green

# Create venv
if (-not (Test-Path ".venv")) {
    Write-Host "📦 Đang khởi tạo môi trường ảo Python (.venv)..." -ForegroundColor Yellow
    & $pyCmd -m venv .venv
} else {
    Write-Host "📦 Môi trường ảo .venv đã tồn tại." -ForegroundColor Green
}

$venvPy = ".venv\Scripts\python.exe"

# Upgrade pip and install requirements
Write-Host "📥 Đang cài đặt thư viện từ requirements.txt..." -ForegroundColor Yellow
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt

# Install Playwright browser
Write-Host "🌐 Đang tải trình duyệt Chromium cho Playwright..." -ForegroundColor Yellow
& $venvPy -m playwright install chromium

# Copy config if not exists
if (-not (Test-Path "config.json") -and (Test-Path "config.example.json")) {
    Copy-Item "config.example.json" "config.json"
    Write-Host "⚙️ Đã tạo file config.json mẫu." -ForegroundColor Green
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "🎉 CÀI ĐẶT THÀNH CÔNG!" -ForegroundColor Green
Write-Host "👉 Bước 1: Mở file config.json và điền tài khoản Onluyen & API Key."
Write-Host "👉 Bước 2: Chạy .\run.ps1 hoặc .\run.bat để bắt đầu."
Write-Host "==========================================================" -ForegroundColor Cyan
