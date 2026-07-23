@echo off
chcp 65001 >nul
setlocal

set "SCRIPT=%~dp0github_raw_tui.py"

if not exist "%SCRIPT%" (
    echo Cannot find: "%SCRIPT%"
    echo Make sure this launcher is in the same folder as github_raw_tui.py.
    echo.
    pause
    exit /b 1
)

where py >nul 2>nul
if "%errorlevel%"=="0" (
    py -3 "%SCRIPT%"
    set "EXIT_CODE=%errorlevel%"
    goto done
)

where python >nul 2>nul
if "%errorlevel%"=="0" (
    python "%SCRIPT%"
    set "EXIT_CODE=%errorlevel%"
    goto done
)

echo Python was not found.
echo Install Python 3 or add it to PATH, then run this launcher again.
set "EXIT_CODE=1"

:done
echo.
if not "%EXIT_CODE%"=="0" echo Program exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
