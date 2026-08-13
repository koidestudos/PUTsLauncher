@echo off
setlocal
cd /d "%~dp0"

echo === PUTs Launcher — build Windows .exe ===
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm PUTsLauncher.spec

echo.
echo Pronto: dist\PUTsLauncher.exe
echo Mods NAO vao junto — publique os packs no GitHub Releases (+ Modpack).
echo.
pause
