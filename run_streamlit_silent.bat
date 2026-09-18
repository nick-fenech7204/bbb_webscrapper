@echo off
REM Not meant to be double-clicked directly -- run_streamlit_silent.vbs
REM launches this with no visible window. Same command as run_streamlit.bat,
REM but output goes to logs\streamlit.log instead of a console, since a
REM hidden window has no console to write to. Stop the app with
REM stop_streamlit.bat.
cd /d "%~dp0"
if not exist logs mkdir logs
call .venv\Scripts\activate.bat
streamlit run streamlit_app.py >> logs\streamlit.log 2>&1
