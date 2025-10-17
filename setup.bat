@echo off
echo Installing Python dependencies...
pip install -r requirements.txt
pip install pyinstaller

echo Building executable...
pyinstaller --onefile --console --add-data "config.json;." --add-data "content;content" --add-data "logs;logs" scripts\app.py --name Debridarr

echo Setup complete! Run Debridarr.exe to start the application.
pause