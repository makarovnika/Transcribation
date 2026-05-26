@echo off
REM run.bat — ручной запуск транскрибатора в текущем окне.
REM
REM Двойной клик: откроется консоль, поднимется сервис, останется висеть.
REM Закрытие окна = смерть процесса.
REM
REM Для постоянного фонового запуска используй bin\windows\install-service.ps1.

cd /d "%~dp0\..\.."

if not exist ".venv\Scripts\python.exe" (
    echo [!!] venv не найден. Сначала: bin\windows\setup.ps1
    pause
    exit /b 1
)

echo === Запуск транскрибатора на http://127.0.0.1:7860 ===
echo Ctrl+C чтобы остановить.
echo.

.venv\Scripts\python.exe -u app.py
pause
