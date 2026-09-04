@echo off
REM Double-click this to open a terminal in the project, venv already
REM activated, ready for any CLI command (run_search.py, batch_scrape_metros.py, etc).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo.
echo BBB Scraper -- venv activated, you're in the project folder.
echo.
echo Common commands:
echo   python scripts\run_search.py --help
echo   python scripts\run_search.py --list-metros
echo   python scripts\batch_scrape_metros.py --help
echo   streamlit run streamlit_app.py
echo.
cmd /k
