@echo off
chcp 65001 >nul
cd /d D:\Phase2_CFD_ML
set PYTHONIOENCODING=utf-8

echo.
echo ╔══════════════════════════════════════════════════╗
echo ║       UrbanWind CFD — 城市微风场建模前端        ║
echo ╚══════════════════════════════════════════════════╝
echo.
echo   启动中...

:: 后台启动服务器（新窗口）
start "UrbanWind CFD Server" python -m frontend.main

:: 等服务器就绪后自动打开浏览器
echo   等待服务器就绪...
:wait
timeout /t 1 /nobreak >nul
powershell -Command "try {$r=Invoke-WebRequest 'http://127.0.0.1:8765/api/health' -TimeoutSec 2; exit 0} catch {exit 1}" >nul 2>&1
if %errorlevel% neq 0 goto wait

:: 打开浏览器
start http://127.0.0.1:8765

echo   ✓ 已启动！浏览器已打开 http://127.0.0.1:8765
echo.
echo   ─────────────────────────────────────────────
echo   关闭方式: 在 UrbanWind CFD Server 窗口按 Ctrl+C
echo   或者直接关掉那个终端窗口
echo   ─────────────────────────────────────────────
echo.
echo   按任意键关闭此面板（不影响服务器）...
pause >nul
