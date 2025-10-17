@echo off
echo Uninstalling Debridarr...

echo Stopping any running Debridarr processes...
taskkill /f /im Debridarr.exe 2>nul

echo Removing application files...
if exist dist\Debridarr.exe del dist\Debridarr.exe
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__
if exist *.spec del *.spec

echo Removing user data...
set DEBRIDARR_DIR=%LOCALAPPDATA%\Debridarr
if exist "%DEBRIDARR_DIR%" (
    echo Removing %DEBRIDARR_DIR%...
    rmdir /s /q "%DEBRIDARR_DIR%"
)

echo Debridarr uninstalled successfully.
pause