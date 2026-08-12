# -*- mode: python ; coding: utf-8 -*-
"""
墨参 MoShen · PyInstaller 打包配置
将 Python 后端打包为独立可执行文件 moshen-server.exe
"""

import os
from pathlib import Path

block_cipher = None

# 后端目录
backend_dir = Path('backend')

a = Analysis(
    [str(backend_dir / 'server.py')],
    pathex=[str(backend_dir)],
    binaries=[],
    datas=[
        # 提示词模板
        (str(backend_dir / 'prompts'), 'prompts'),
        # 前端静态文件
        ('frontend', 'frontend'),
        # .env.example
        (str(backend_dir / '.env.example'), '.'),
    ],
    hiddenimports=[
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'fastapi.middleware.cors',
        'fastapi.staticfiles',
        'fastapi.responses',
        'sse_starlette',
        'sse_starlette.sse',
        'pydantic',
        'pydantic_settings',
        'httpx',
        'chardet',
        'aiofiles',
        'core',
        'core.config',
        'core.resource_path',
        'core.llm_provider',
        'core.prompt_loader',
        'core.context_manager',
        'core.file_parser',
        'engines',
        'engines.dialogue_manager',
        'engines.intent_router',
        'engines.intervention',
        'knowledge',
        'knowledge.project_kb',
        'knowledge.rules_kb',
        'knowledge.novel_analyzer',
        'routes',
        'routes.chat',
        'routes.project',
        'routes.files',
        'main',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='moshen-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # 保留控制台以便调试
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='moshen-server',
)
