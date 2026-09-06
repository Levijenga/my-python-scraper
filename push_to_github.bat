@echo off
cd /d "%~dp0"
echo Staging and pushing to GitHub...
"C:\Program Files\Git\cmd\git.exe" add .
"C:\Program Files\Git\cmd\git.exe" commit -m "Update scrapers and configuration"
"C:\Program Files\Git\cmd\git.exe" push origin main
echo.
echo Done! Pushed to GitHub successfully.
pause
