@echo off
echo Uninstalling Debridarr...

echo Stopping any running Debridarr processes...
taskkill /f /im Debridarr.exe 2>nul

echo Removing application files...
if exist Debridarr.exe del Debridarr.exe
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__
if exist *.spec del *.spec

echo Debridarr application uninstalled successfully.
echo User data preserved at %LOCALAPPDATA%\Debridarr
pause