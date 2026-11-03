@echo off
where py >nul 2>nul
if errorlevel 1 (
  python "%~dp0run.py" %*
) else (
  py -3.12 "%~dp0run.py" %*
)
if errorlevel 1 exit /b 1
