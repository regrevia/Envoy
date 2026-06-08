@echo off
setlocal EnableExtensions
pushd "%~dp0"
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell was not found on PATH.
  popd
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_env.ps1"
set "EXIT_CODE=%errorlevel%"
popd
endlocal & exit /b %EXIT_CODE%
