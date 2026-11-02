@echo off
rem Double-click this file to update TG Kit after unzipping a newer version.
rem Your chats, tasks and login are kept; the setup only runs if you are not logged in yet.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" -Update
echo.
pause
