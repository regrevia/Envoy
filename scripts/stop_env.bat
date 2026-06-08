@echo off
setlocal EnableExtensions
pushd "%~dp0.."

echo Stopping Envoy services...
where docker >nul 2>nul
if errorlevel 1 (
  echo Docker was not found on PATH.
) else (
  docker info >nul 2>nul
  if errorlevel 1 (
    echo Docker is not running. Skipping docker compose down.
  ) else (
    echo Running docker compose down...
    docker compose down
  )
)

echo Stopping backend/frontend processes...
for %%P in (uvicorn.exe python.exe pythonw.exe node.exe npm.exe next.exe cmd.exe) do (
  taskkill /F /IM %%P /T >nul 2>&1
)

echo Done.
popd
endlocal
exit /b 0
