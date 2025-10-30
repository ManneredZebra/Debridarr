@echo off

:: Check for administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Running with administrator privileges...
) else (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)
echo Installing Python dependencies...
cd /d "%~dp0"
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pip install pyinstaller

echo Building executable...
cd /d "%~dp0"
py -m PyInstaller --onefile --noconsole --icon=icon.png scripts\tray_app.py --name Debridarr --distpath .

echo Installing to Program Files...
set INSTALL_DIR=C:\Program Files\Debridarr
mkdir "%INSTALL_DIR%" 2>nul
mkdir "%INSTALL_DIR%\bin" 2>nul
copy Debridarr.exe "%INSTALL_DIR%\bin\" >nul
copy icon.png "%INSTALL_DIR%\" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy to Program Files. Please run as Administrator.
    pause
    exit /b 1
)
del Debridarr.exe

echo Creating data folders...
set CONTENT_DIR=C:\ProgramData\Debridarr
mkdir "%CONTENT_DIR%" 2>nul
mkdir "%CONTENT_DIR%\logs" 2>nul
mkdir "%CONTENT_DIR%\sonarr\magnets" 2>nul
mkdir "%CONTENT_DIR%\sonarr\in_progress" 2>nul
mkdir "%CONTENT_DIR%\sonarr\completed_magnets" 2>nul
mkdir "%CONTENT_DIR%\sonarr\completed_downloads" 2>nul
mkdir "%CONTENT_DIR%\radarr\magnets" 2>nul
mkdir "%CONTENT_DIR%\radarr\in_progress" 2>nul
mkdir "%CONTENT_DIR%\radarr\completed_magnets" 2>nul
mkdir "%CONTENT_DIR%\radarr\completed_downloads" 2>nul

echo Creating config file...
if not exist "%CONTENT_DIR%\config.yaml" (
    py create_config.py
) else (
    echo Config file already exists, skipping.
)

echo Setup complete! Please edit C:\ProgramData\Debridarr\config.yaml with your Real Debrid API token.
if exist "%INSTALL_DIR%\bin\Debridarr.exe" (
    echo Starting Debridarr in system tray...
    start "" "%INSTALL_DIR%\bin\Debridarr.exe"
    timeout /t 2 /nobreak >nul
    echo Opening Web UI...
    start http://127.0.0.1:3636
    timeout /t 1 /nobreak >nul
    exit
) else (
    echo ERROR: Debridarr.exe was not created. Check the PyInstaller output above for errors.
    pause
)