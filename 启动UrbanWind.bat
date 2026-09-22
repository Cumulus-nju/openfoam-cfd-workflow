@echo off
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

rem 优先使用项目内虚拟环境，找不到再退回系统 python
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" launch.py
if errorlevel 1 (
    echo.
    echo [错误] 启动失败。若提示缺少依赖，请先执行：
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
)
endlocal
