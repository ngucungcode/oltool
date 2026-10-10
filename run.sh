#!/usr/bin/env bash
set -e

if [ ! -d ".venv" ]; then
    echo "⚠️ Chưa tìm thấy môi trường ảo .venv! Đang tự động chạy install.sh..."
    bash install.sh
fi

.venv/bin/python cli.py "$@"
