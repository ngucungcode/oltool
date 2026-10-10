@echo off
chcp 65001 >nul
echo ==========================================================
echo   OLTool - Trinh cai dat tu dong (Windows)
echo ==========================================================

REM Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [X] Loi: Chua cai dat Python tren may tinh!
        echo Hay tai va cai dat Python tu https://www.python.org/
        echo Luu y: Tich chon "Add Python to PATH" khi cai dat.
        pause
        exit /b 1
    ) else (
        set PY_CMD=py
    )
) else (
    set PY_CMD=python
)

echo Tim thay Python: %PY_CMD%

REM Create venv
if not exist ".venv" (
    echo [*] Dang khoi tao moi truong ao (.venv)...
    %PY_CMD% -m venv .venv
) else (
    echo [*] Moi truong ao .venv da ton tai.
)

REM Install packages
echo [*] Dang cai dat thu vien...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

REM Install Playwright Chromium
echo [*] Dang tai trinh duyet Chromium cho Playwright...
.venv\Scripts\python.exe -m playwright install chromium

REM Copy config if missing
if not exist "config.json" (
    if exist "config.example.json" (
        copy config.example.json config.json >nul
        echo [*] Da tao file config.json mau.
    )
)

echo ==========================================================
echo CAI DAT HOAN TAT!
echo Buoc 1: Mo file config.json dien tai khoan va API Key AI.
echo Buoc 2: Chay file run.bat de bat dau.
echo ==========================================================
pause
