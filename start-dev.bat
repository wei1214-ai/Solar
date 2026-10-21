@echo off
REM ---------------------------------------------------------------------------
REM SolarSync dev launcher: starts the FastAPI backend and the Next.js frontend.
REM ---------------------------------------------------------------------------
setlocal

if not exist "backend\.venv" (
  echo [1/2] Creating backend virtual environment...
  python -m venv backend\.venv
  call backend\.venv\Scripts\activate
  pip install -r backend\requirements.txt
) else (
  call backend\.venv\Scripts\activate
)

if not exist "frontend\node_modules" (
  echo [2/2] Installing frontend dependencies...
  pushd frontend
  call npm install
  popd
)

if not exist "frontend\.env.local" (
  copy /Y "frontend\.env.local.example" "frontend\.env.local" >nul
)

start "SolarSync API" cmd /k "cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"
start "SolarSync Web" cmd /k "cd frontend && npm run dev"

echo.
echo SolarSync API  : http://127.0.0.1:8000/docs
echo SolarSync Web  : http://localhost:3000
endlocal
