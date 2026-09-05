@echo off
REM Double-click this to publish the local site/ folder to your live AWS
REM site -- syncs to S3 and invalidates CloudFront in one step. Needs the
REM AWS CLI installed and configured first (see site/DEPLOY.md).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python scripts\deploy_site.py
pause
