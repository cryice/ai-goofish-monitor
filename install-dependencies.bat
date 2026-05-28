@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: 闲鱼监控系统依赖安装脚本 (Windows版本)

echo ========================================
echo 闲鱼监控系统 - Windows依赖安装脚本
echo ========================================

:: 检查Python
echo.
echo [1/4] 检查Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请确保已安装Python 3.10+并添加到PATH环境变量
    echo.
    echo 解决办法:
    echo 1) 下载并安装Python 3.10+:
    echo    https://www.python.org/downloads/
    echo 2) 安装时请勾选"Add Python to PATH"
    echo 3) 验证安装: python --version
    pause
    exit /b 1
)

:: 检查Python版本
for /f "tokens=*" %%i in ('python -c "import sys; print(sys.version_info[:2])"') do set PYTHON_VERSION=%%i
if "!PYTHON_VERSION!" lss "(3, 10)" (
    echo [错误] Python版本过低，需要Python 3.10+，当前版本: !PYTHON_VERSION!
    pause
    exit /b 1
)

:: 检查pip
echo [检查] 检查pip...
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到pip
    pause
    exit /b 1
)

:: 检查Node.js
echo [检查] 检查Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Node.js，请确保已安装Node.js并添加到PATH环境变量
    echo.
    echo 解决办法:
    echo 1) 下载并安装Node.js (LTS版本):
    echo    https://nodejs.org/
    echo 2) 验证安装: node --version
    pause
    exit /b 1
)

:: 检查npm
echo [检查] 检查npm...
npm --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到npm
    pause
    exit /b 1
)

echo [成功] 环境检查通过

:: 安装Python依赖
echo.
echo [2/4] 安装Python依赖...
if not exist "requirements.txt" (
    echo [错误] requirements.txt文件不存在
    pause
    exit /b 1
)

echo [信息] 正在安装Python依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] Python依赖安装失败
    pause
    exit /b 1
)
echo [成功] Python依赖安装完成

:: 安装Playwright浏览器
echo.
echo [3/4] 安装Playwright浏览器...
python -m playwright install chromium
if errorlevel 1 (
    echo [错误] Playwright浏览器安装失败
    pause
    exit /b 1
)
echo [成功] Playwright浏览器安装完成

:: 安装前端依赖
echo.
echo [4/4] 安装前端依赖...
if not exist "web-ui" (
    echo [错误] web-ui目录不存在
    pause
    exit /b 1
)

cd web-ui

echo [信息] 正在安装前端依赖...
npm install
if errorlevel 1 (
    echo [错误] 前端依赖安装失败
    cd ..
    pause
    exit /b 1
)

cd ..

echo [成功] 所有依赖安装完成！

echo.
echo ========================================
echo 依赖安装完成！
echo 现在您可以运行 start.bat 来启动应用。
echo ========================================

endlocal