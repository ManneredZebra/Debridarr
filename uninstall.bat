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

echo Uninstalling Debridarr...

echo Stopping any running Debridarr processes...
taskkill /f /im Debridarr.exe 2>nul
taskkill /f /im python.exe /fi "WINDOWTITLE eq Debridarr*" 2>nul
wmic process where "name='Debridarr.exe'" delete 2>nul
echo Waiting for processes to terminate...
timeout /t 5 /nobreak >nul

echo Removing application files...
if exist "C:\Program Files\Debridarr" rmdir /s /q "C:\Program Files\Debridarr"
if exist Debridarr.exe del Debridarr.exe
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__
if exist *.spec del *.spec

echo Debridarr uninstalled successfully.
echo User data and downloads preserved at C:\ProgramData\Debridarr
timeout /t 2 /nobreak >nul
exit