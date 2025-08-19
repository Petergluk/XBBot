REM # XBalanseBot/!RUN_TESTS.bat
REM # v1.1 - 2025-08-17
@echo off
TITLE XBalanseBot Test Runner
chcp 65001 > nul
setlocal

ECHO =================================
ECHO XBalanseBot Test Runner
ECHO %DATE% %TIME%
ECHO =================================
ECHO.

REM --- Шаг 1: Проверка наличия .env файла ---
IF NOT EXIST .env (
    ECHO [!!!] CRITICAL ERROR: .env file not found in the current directory.
    GOTO :END
)

REM --- Шаг 2: Загрузка переменных из .env в окружение этого скрипта ---
ECHO [+] Loading environment variables from .env file...
REM ИЗМЕНЕНИЕ: Добавлена опция "eol=#", чтобы игнорировать строки, начинающиеся с #
FOR /F "usebackq eol=# tokens=1,* delims==" %%A IN (".env") DO (
    set "%%A=%%B"
)
ECHO    Done.
ECHO.

REM --- Шаг 3: Проверка ключевой переменной ---
IF NOT DEFINED POSTGRES_DB_TEST (
    ECHO [!!!] CRITICAL ERROR: POSTGRES_DB_TEST is not defined in your .env file.
    ECHO     The script failed to parse the .env file.
    GOTO :END
)

REM --- Шаг 4: Запуск PostgreSQL в Docker ---
ECHO [+] Starting PostgreSQL container via Docker Compose...
docker-compose up -d
IF %ERRORLEVEL% NEQ 0 (
    ECHO.
    ECHO [!!!] CRITICAL ERROR: Failed to start Docker containers.
    ECHO     Please check if Docker Desktop is running.
    GOTO :END
)
ECHO    Done.
ECHO.

REM --- Шаг 5: Запуск Pytest ---
ECHO =================================
ECHO [+] Running tests with loaded environment variables...
ECHO =================================
ECHO.

REM Запускаем pytest. Теперь он наследует все переменные, которые мы установили выше.
poetry run pytest -v

REM --- Шаг 6: Остановка Docker ---
ECHO.
ECHO =================================
ECHO [+] Shutting down Docker containers...
ECHO =================================
docker-compose down
ECHO    Done.

:END
ECHO.
ECHO [+] Test run finished.
ECHO This window will remain open for inspection.
cmd /k