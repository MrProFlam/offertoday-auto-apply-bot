@echo off
echo Building OfferTodayBot.exe ...
python -m PyInstaller --clean --onefile --windowed --name OfferTodayBot --collect-all playwright gui_main.py
echo.
if not exist dist\config.yaml copy config.yaml dist\config.yaml >nul
if not exist dist\.env if exist .env copy .env dist\.env >nul
echo Done. The EXE is in the dist\ folder.
echo Put config.yaml and .env next to the EXE (already copied if missing).
pause
