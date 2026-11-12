@echo off
setlocal
cd /d "%~dp0web" || exit /b 1
call pnpm.cmd dev --hostname 127.0.0.1
