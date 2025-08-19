REM # XBalanseBot/!RUN.bat
REM # v1.5.2 - 2025-08-16
@echo off
TITLE XBalanseBot Lifecycle Manager
chcp 65001 > nul
setlocal

ECHO =================================
ECHO XBalanseBot Lifecycle Manager
ECHO %DATE% %TIME%
ECHO =================================
ECHO.

REM --- Шаг 1: Запуск PostgreSQL в Docker ---
ECHO [+] Starting PostgreSQL container via Docker Compose...
docker-compose up -d
REM Проверяем, что команда docker-compose выполнилась успешно.
IF %ERRORLEVEL% NEQ 0 (
    ECHO.
    ECHO [!!!] CRITICAL ERROR: Failed to start Docker containers.
    ECHO     Please check if Docker Desktop is running and docker-compose.yml is correct.
    GOTO :END
)
ECHO    Done.
ECHO.

REM --- Шаг 2: Запуск основного скрипта бота ---
ECHO =================================
ECHO [+] Starting the bot using 'poetry run'...
ECHO [i] To stop the bot AND the database, press Ctrl+C in this window.
ECHO =================================
ECHO.
REM Используем `poetry run`, чтобы выполнить команду в правильном окружении
poetry run python main.py
REM Проверяем, не упал ли Python-скрипт с ошибкой.
IF %ERRORLEVEL% NEQ 0 (
    ECHO.
    ECHO [!!!] CRITICAL ERROR: Python script exited with an error.
    ECHO     See the traceback above for details.
    ECHO.
    ECHO [+] Attempting to shut down Docker containers...
    docker-compose down
)

:END
REM --- Завершение ---
ECHO.
ECHO [+] Bot script has finished.
ECHO The Docker containers should have been stopped by the Python script on a clean exit.
ECHO You can check their status with the 'docker ps' command.
ECHO This window will remain open for inspection.
cmd /k
