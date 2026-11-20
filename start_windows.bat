@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   SalesPulse - Sales Analytics Website
echo ============================================

if not exist "venv\Scripts\python.exe" (
    echo [1/3] Creating a project virtual environment...
    py -m venv venv
    if errorlevel 1 goto :error
) else (
    echo [1/3] Existing project virtual environment found.
)

echo [2/3] Installing the lightweight requirements (Flask only)...
"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not defined SECRET_KEY (
    echo Creating a temporary local session secret for this run...
    for /f "delims=" %%K in ('py -c "import secrets; print(secrets.token_hex(32))"') do set "SECRET_KEY=%%K"
)

echo [3/3] Starting SalesPulse...
echo Open http://127.0.0.1:5000 in your browser.
"venv\Scripts\python.exe" app.py
if errorlevel 1 goto :error
goto :end

:error
echo.
echo Setup or startup failed. Read the error above.
echo This project no longer requires Pandas or NumPy.
pause
exit /b 1

:end
pause
endlocal
