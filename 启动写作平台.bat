@echo off
chcp 65001 >nul
title ai-xiaoshuo · 番茄 AI 写作平台（端口 8788）
cd /d "%~dp0"

echo ============================================
echo   ai-xiaoshuo · 番茄 AI 写作平台
echo   （以 ai-xiaoshuo-skill 为核心）
echo ============================================
echo.

if exist "%~dp0.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%~dp0.env") do (
    if not "%%a"=="" if not "%%b"=="" set "%%a=%%b"
  )
)
if "%PORT%"=="" set "PORT=8788"
if "%HOST%"=="" set "HOST=127.0.0.1"

netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [!] 端口 %PORT% 已被占用 —— 服务可能已在运行。
    echo     浏览器打开： http://%HOST%:%PORT%
    pause
    exit /b
)

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
where "%PY%" >nul 2>nul || goto :nopy

echo [√] 启动中...（本窗口不要关）
echo     地址： http://%HOST%:%PORT%
echo.
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start \"\" http://%HOST%:%PORT%"
"%PY%" -m uvicorn app.main:app --host %HOST% --port %PORT%
echo.
echo 服务已退出。
pause
exit /b

:nopy
echo [X] 找不到 Python，请安装 3.10+ 或修改脚本里的路径。
pause
