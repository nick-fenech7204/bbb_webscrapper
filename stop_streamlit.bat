@echo off
REM Stops the Streamlit control panel, however it was started (this
REM window, run_streamlit.bat, or the silent run_streamlit_silent.vbs --
REM the last one has no window of its own to close).
REM
REM Safe to run even if a batch scrape is currently in progress: the
REM batch subprocess Streamlit launches runs fully detached, its own
REM process group with no shared console (see streamlit_app.py's own
REM module docstring) -- specifically so that closing/killing Streamlit
REM never takes an in-progress batch down with it. This only stops the
REM web control panel itself.
taskkill /F /T /IM streamlit.exe >nul 2>&1
if %errorlevel%==0 (
    echo Streamlit stopped.
) else (
    echo Streamlit wasn't running.
)
pause
