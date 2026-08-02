@echo off
setlocal
cd /d "%~dp0"

echo === PUTs Launcher — build Windows .exe ===
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm PUTsLauncher.spec

echo.
echo Pronto: dist\PUTsLauncher.exe
echo Copie a pasta "mods" para o mesmo diretorio do .exe antes de distribuir.
echo.
pause
