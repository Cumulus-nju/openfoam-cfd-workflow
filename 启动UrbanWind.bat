@echo off
chcp 65001 >nul
cd /d D:\Phase2_CFD_ML
echo.
echo ╔══════════════════════════════════════════════╗
echo ║     UrbanWind CFD — 城市微风场建模前端      ║
echo ╚══════════════════════════════════════════════╝
echo.
echo   启动中... 浏览器将自动打开 http://127.0.0.1:8765
echo.
echo   按 Ctrl+C 停止服务器
echo.
set PYTHONIOENCODING=utf-8
python -m frontend.main
pause
