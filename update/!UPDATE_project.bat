@echo off
REM ===================================================
REM == Разбор файла от ИИ на структуру проекта       ==
REM ==                                               ==
REM == ИСПОЛЬЗОВАНИЕ:                                ==
REM == 1. Поместите файл с ответом ИИ в эту папку.   ==
REM == 2. Переименуйте его в 'ai_output.txt'.        ==
REM == 3. Запустите этот скрипт.                     ==
REM ===================================================

TITLE Project Splitter

SET "INPUT_FILE=ai_output.txt"

IF NOT EXIST "%INPUT_FILE%" (
    echo [ERROR] File not found: %INPUT_FILE%
    echo.
    echo Please make sure the file 'ai_output.txt' exists in this directory.
) ELSE (
    echo.
    echo [INFO] Found and splitting file: %INPUT_FILE%
    echo.
    python split_ai_code.py "%INPUT_FILE%"
)

echo.
echo [DONE] Task completed. Press any key to close this window.
pause > nul