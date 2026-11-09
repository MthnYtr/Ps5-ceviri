@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Conda ortaminin adi (farkli bir isim verdiysen burayi degistir)
set ENV=ps5ceviri
call conda activate %ENV%
if errorlevel 1 (
  echo "%ENV%" conda ortami acilamadi. Anaconda Prompt'ta "conda init cmd.exe" calistirip tekrar deneyin.
  pause
  exit /b 1
)
REM PS5 -> kart -> PC -> TV. Altyazi goruntunun ustunde. ESC = cikis, T = altyazi ust/alt
python app.py --source card --mode tv
pause
