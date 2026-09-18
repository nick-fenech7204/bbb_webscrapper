@echo off
REM Double-click this to launch the Streamlit control panel with the
REM console window visible (useful for watching startup errors). Prefer
REM no window at all? Use run_streamlit_silent.vbs instead -- same app,
REM stop it with stop_streamlit.bat since there's no window to close.
REM Activates the venv and starts the app -- your browser opens automatically.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo Starting Streamlit... your browser should open automatically at http://localhost:8501
echo (Close this window to stop the app.)
echo.
streamlit run streamlit_app.py
pause
