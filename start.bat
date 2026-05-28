@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: 闲鱼监控系统本地启动脚本 (Windows版本)
:: 功能：清理旧构建、安装依赖、构建前端、启动服务

echo ========================================
echo 闲鱼监控系统 - Windows本地启动脚本
echo ========================================

:: 检查Python
echo.
echo [1/6] 检查Python...
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
    echo 解决办法: python -m pip install --upgrade pip
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

:: 检查Playwright
echo [检查] 检查Playwright...
python -m playwright --version >nul 2>&1
if errorlevel 1 (
    echo [警告] 未找到Playwright，即将安装...
    python -m pip install playwright
    if errorlevel 1 (
        echo [错误] Playwright安装失败
        pause
        exit /b 1
    )
)

:: 检查浏览器
echo [检查] 检查浏览器...
reg query "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>&1
if errorlevel 1 (
    reg query "HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>&1
    if errorlevel 1 (
        echo [警告] 未找到Chrome浏览器
        echo 建议安装Chrome浏览器以获得最佳体验
    )
) else (
    echo [检查] Chrome浏览器已找到
)

reg query "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" >nul 2>&1
if errorlevel 1 (
    reg query "HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" >nul 2>&1
    if errorlevel 1 (
        echo [警告] 未找到Edge浏览器
        echo 建议安装Edge浏览器以获得备选方案
    )
) else (
    echo [检查] Edge浏览器已找到
)

:: 安装Playwright浏览器
echo [检查] 检查Playwright浏览器...
python -c "import playwright.sync_api; playwright.sync_api.Playwright.create().install()" >nul 2>&1
if errorlevel 1 (
    echo [信息] 安装Playwright浏览器...
    python -m playwright install chromium
    if errorlevel 1 (
        echo [错误] Playwright浏览器安装失败
        pause
        exit /b 1
    )
    echo [信息] Playwright浏览器安装完成
)

echo [成功] 环境与依赖检查通过

:: 清理旧的dist目录
echo.
echo [2/6] 清理旧的构建产物...
if exist "dist" (
    echo [信息] 删除旧的dist目录
    rmdir /s /q "dist"
) else (
    echo [信息] dist目录不存在，跳过清理
)
echo [成功] 构建产物清理完成

:: 检查并安装Python依赖
echo.
echo [3/6] 检查Python依赖...
if not exist "requirements.txt" (
    echo [错误] requirements.txt文件不存在
    pause
    exit /b 1
)

echo [信息] 正在安装Python依赖...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [错误] Python依赖安装失败
    pause
    exit /b 1
)
echo [成功] Python依赖安装完成

:: 构建前端
echo.
echo [4/6] 构建前端项目...
if not exist "web-ui" (
    echo [错误] web-ui目录不存在
    pause
    exit /b 1
)

cd web-ui

:: 检查node_modules是否存在
if not exist "node_modules" (
    echo [信息] 首次运行，正在安装前端依赖...
    npm install
    if errorlevel 1 (
        echo [错误] 前端依赖安装失败
        cd ..
        pause
        exit /b 1
    )
)

echo [信息] 正在构建前端...
npm run build
if errorlevel 1 (
    echo [错误] 前端构建失败
    cd ..
    pause
    exit /b 1
)

cd ..

if not exist "dist" (
    echo [错误] 前端构建失败，dist目录未生成
    pause
    exit /b 1
)

echo [成功] 前端构建完成，产物已输出到项目根目录 dist/

:: 校验构建产物
echo.
echo [5/6] 校验构建产物...
if exist "dist" (
    echo [成功] 已确认构建产物位于项目根目录 dist/
) else (
    echo [错误] 构建产物校验失败
    pause
    exit /b 1
)

:: 启动后端服务
echo.
echo [6/6] 启动后端服务...
echo.
echo ========================================
echo 服务启动中...
echo 访问地址: http://localhost:8000
echo API文档: http://localhost:8000/docs
echo ========================================
echo.

:: 启动Python应用，使用 || pause 来确保即使Python应用失败也能看到错误信息
python -m src.app || pause

:: 即使Python应用正常退出，也暂停一下以显示状态
echo.
echo ========================================
echo 服务已停止或发生错误
echo ========================================
echo 按任意键退出...
pause >nul

endlocal