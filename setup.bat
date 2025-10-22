@echo off
echo Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo Building executable...
cd /d "%~dp0"
python -m PyInstaller --onefile --noconsole scripts\tray_app.py --name Debridarr --distpath .

echo Creating user data folders...
set DEBRIDARR_DIR=%LOCALAPPDATA%\Debridarr
mkdir "%DEBRIDARR_DIR%" 2>nul
mkdir "%DEBRIDARR_DIR%\logs" 2>nul
mkdir "%DEBRIDARR_DIR%\content" 2>nul
mkdir "%DEBRIDARR_DIR%\content\sonarr\magnets" 2>nul
mkdir "%DEBRIDARR_DIR%\content\sonarr\completed_magnets" 2>nul
mkdir "%DEBRIDARR_DIR%\content\sonarr\completed_downloads" 2>nul
mkdir "%DEBRIDARR_DIR%\content\radarr\magnets" 2>nul
mkdir "%DEBRIDARR_DIR%\content\radarr\completed_magnets" 2>nul
mkdir "%DEBRIDARR_DIR%\content\radarr\completed_downloads" 2>nul
mkdir "%DEBRIDARR_DIR%\content\in_progress" 2>nul

echo Creating config file...
echo { > "%DEBRIDARR_DIR%\config.json"
echo   "real_debrid_api_token": "YOUR_API_TOKEN_HERE" >> "%DEBRIDARR_DIR%\config.json"
echo } >> "%DEBRIDARR_DIR%\config.json"

echo Setup complete! Please edit %LOCALAPPDATA%\Debridarr\config.json with your Real Debrid API token.
if exist Debridarr.exe (
    echo Starting Debridarr in system tray...
    start Debridarr.exe
) else (
    echo ERROR: Debridarr.exe was not created. Check the PyInstaller output above for errors.
)
pause