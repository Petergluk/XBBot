@echo off
REM ===================================================
REM == Сборка проекта в один текстовый файл          ==
REM == Запускает 'python split_ai_code.py gather'    ==
REM ===================================================

TITLE Project Gather

echo.
echo [INFO] Starting project gathering process...
echo.

python split_ai_code.py gather

echo.
echo [DONE] Task completed. Press any key to close this window.
pause > nul