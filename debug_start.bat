@echo off
chcp 65001 >nul
echo ========================================
echo 闲鱼监控系统 - 调试启动脚本
echo ========================================
echo.

echo 正在检查Python...
python --version
if errorlevel 1 (
    echo 错误: 未找到Python
    goto pause_exit
)

echo.
echo 正在检查依赖...
python -c "import fastapi"
if errorlevel 1 (
    echo 错误: FastAPI未安装
    goto pause_exit
)

python -c "import uvicorn"
if errorlevel 1 (
    echo 错误: Uvicorn未安装
    goto pause_exit
)

echo.
echo 正在启动应用...
echo 请稍候，启动过程中显示的日志很重要...
echo 按 Ctrl+C 可以停止服务
echo.

:: 直接启动应用并捕获任何错误
python -m src.app

:pause_exit
echo.
echo ========================================
echo 脚本执行完毕或发生错误
echo ========================================
echo.
pause