"""
墨参 · 版本号单一来源
后端所有需要版本号的位置（FastAPI 应用版本、/api/health、更新说明弹窗、诊断信息）统一引用此处。
前端与打包产物中的版本号见 frontend/index.html 的 .sidebar-version 与 package.json。
"""

APP_VERSION = "0.11.5"
