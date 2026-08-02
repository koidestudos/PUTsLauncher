@echo off
cd /d "%~dp0"
if exist "PUTsLauncher.exe" (
  start "" "PUTsLauncher.exe"
  exit /b 0
)
echo EXE nao encontrado. Rodando via Python...
python main.py
if errorlevel 1 pause
