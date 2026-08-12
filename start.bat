@echo off
title 墨参 MoShen · 小说写作助手

echo ========================================
echo   墨参 MoShen · 小说写作助手 (Electron)
echo ========================================
echo.

cd /d "d:\Trae Workspace\moshen"

echo [1/3] 检查 Node.js 环境...
set "NODE_PATH=C:\Program Files\nodejs\node.exe"
if not exist "%NODE_PATH%" (
    echo 错误：未找到 Node.js，请先安装 Node.js 18+
    pause
    exit /b 1
)

echo [2/3] 检查依赖...
if not exist "node_modules\electron" (
    echo 首次运行，正在安装依赖...
    "%NODE_PATH%" "C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js" install
    if errorlevel 1 (
        echo 依赖安装失败，请检查网络连接
        pause
        exit /b 1
    )
)

echo [3/3] 启动桌面应用...
echo.

set "PYTHON_PATH=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
"node_modules\electron\dist\electron.exe" . --dev

pause