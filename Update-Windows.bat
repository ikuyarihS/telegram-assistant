@echo off
rem Double-click this file to update Telegram Assistant (called TG Kit up to 0.2.5) after
rem unzipping a newer version. Your chats, tasks and login are kept; the setup only runs if
rem you are not logged in yet. The old "TG Kit" icons are replaced by "Telegram Assistant" ones.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" -Update
echo.
pause
