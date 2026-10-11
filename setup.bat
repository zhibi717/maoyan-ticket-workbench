@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto error
echo Setup complete. Double-click Start.bat.
pause
exit /b 0
:error
echo Setup failed. Python 3.13 and Internet access are required.
pause
exit /b 1
