#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "  OLTool - Trình cài đặt tự động (Linux / macOS)"
echo "=========================================================="

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Lỗi: Chưa tìm thấy Python 3 trên hệ thống!"
    echo "👉 Vui lòng cài đặt Python >= 3.10 từ https://www.python.org hoặc qua package manager."
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "🔍 Đã tìm thấy Python: $PY_VER"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "📦 Đang khởi tạo môi trường ảo Python (.venv)..."
    python3 -m venv .venv
else
    echo "📦 Môi trường ảo .venv đã tồn tại."
fi

# Upgrade pip and install requirements
echo "📥 Đang cài đặt thư viện từ requirements.txt..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Install Playwright browser dependencies
echo "🌐 Đang tải trình duyệt Chromium cho Playwright..."
.venv/bin/python -m playwright install chromium

# Copy config if not exists
if [ ! -f "config.json" ] && [ -f "config.example.json" ]; then
    cp config.example.json config.json
    echo "⚙️ Đã tạo file config.json mẫu. Vui lòng chỉnh sửa tài khoản trước khi chạy."
fi

# Set permissions
chmod +x run.sh install.sh 2>/dev/null || true

echo ""
echo "=========================================================="
echo "🎉 CÀI ĐẶT THÀNH CÔNG!"
echo "👉 Bước 1: Mở file 'config.json' và điền tài khoản Onluyen & API Key."
echo "👉 Bước 2: Chạy lệnh './run.sh' để bắt đầu làm bài."
echo "=========================================================="
