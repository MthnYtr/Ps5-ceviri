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
echo Hangi kaynak icin altyazi alanini yeniden secmek istiyorsunuz?
echo   1 = Capture karti
echo   2 = Ekran (Remote Play)
set /p K="Secim (1/2): "
if "%K%"=="2" (
  python app.py --source screen --select-roi
) else (
  python app.py --source card --select-roi
)
pause
