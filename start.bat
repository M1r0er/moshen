@echo off
title ī�� MoShen �� С˵д������

echo ========================================
echo   ī�� MoShen �� С˵д������ (Electron)
echo ========================================
echo.

cd /d "d:\Trae Workspace\moshen"

echo [1/3] ��� Node.js ����...
set "NODE_PATH=C:\Program Files\nodejs\node.exe"
if not exist "%NODE_PATH%" (
    echo ����δ�ҵ� Node.js�����Ȱ�װ Node.js 18+
    pause
    exit /b 1
)

echo [2/3] �������...
if not exist "node_modules\electron" (
    echo �״����У����ڰ�װ����...
    "%NODE_PATH%" "C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js" install
    if errorlevel 1 (
        echo ������װʧ�ܣ�������������
        pause
        exit /b 1
    )
)

echo [3/3] ��������Ӧ��...
echo.

set "PYTHON_PATH=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
"node_modules\electron\dist\electron.exe" . --dev

pause