@echo off
chcp 65001 >nul
title 墨参 MoShen · 小说写作助手

echo ========================================
echo   墨参 MoShen · 小说写作助手 (桌面版)
echo ========================================
echo.

cd /d "d:\Trae Workspace\moshen\backend"

echo [1/3] 检查 Python 环境...
set "PYTHON_PATH=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_PATH%" (
    echo 尝试使用系统 Python...
    set "PYTHON_PATH=python"
)
"%PYTHON_PATH%" --version
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

echo.
echo [2/3] 安装依赖...
"%PYTHON_PATH%" -m pip install -r requirements.txt -q

echo.
echo [3/3] 启动桌面应用...
echo.
echo 首次使用请在应用中点击"设置"配置模型 API Key
echo 关闭窗口即退出程序
echo.

"%PYTHON_PATH%" desktop.py

pause
