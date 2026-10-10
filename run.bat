@echo off
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
    echo [!] Chua cai dat moi truong ao. Dang khoi chay install.bat...
    call install.bat
)

.venv\Scripts\python.exe cli.py %*
if %errorlevel% neq 0 (
    echo.
    echo Chuong trinh dung lai voi ma loi %errorlevel%.
    pause
)
