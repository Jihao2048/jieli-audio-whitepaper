@echo off
rem Launch the graphical converter.
rem Everything is resolved relative to this script so the folder can be
rem moved anywhere without editing paths.

setlocal
set HERE=%~dp0

rem Prefer pythonw so no console window lingers behind the GUI.
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%HERE%wav_tool\gui.py" %*
    goto :eof
)

rem Fall back to python if pythonw is unavailable.
where python.exe >nul 2>nul
if %errorlevel%==0 (
    python.exe "%HERE%wav_tool\gui.py" %*
    goto :eof
)

echo.
echo Python was not found on PATH.
echo Install Python 3.8 or newer with the tkinter component, then try again.
echo.
pause
