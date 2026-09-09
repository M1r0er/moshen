# 墨参 MoShen · 小说写作助手

> 辅助而非代笔——让 AI 当编辑，不当枪手。

## 快速开始

### 普通用户（安装版）
下载 `release/` 目录下的安装包（`墨参 MoShen Setup x.x.x.exe`）双击安装，安装完成后通过桌面快捷方式或开始菜单启动即可。

**无需运行 `start.bat`，也无需本机安装 Node.js / Python**——Python 后端已随安装包一起打包，启动时由 Electron 自动拉起。

### 配置模型
应用打开后，点击顶部「设定」标签页，填写至少一组模型的 API Key（推荐 DeepSeek 或 Claude）。

### 开发者（源码运行）
`start.bat` 仅用于开发调试，需本机已安装 Node.js 18+ 与 Python 3.10+。
双击 `start.bat`，或手动执行：
```bash
npm run dev
```
Electron 窗口会自动打开，后端 Python 服务在后台启动。

### 构建安装包
```bash
# 1. 打包 Python 后端为独立可执行文件
npm run build:python

# 2. 打包 Electron 安装包
npm run build:electron
```
构建产物在 `release/` 目录下（该目录已被 `.gitignore` 忽略）。

## 核心功能

界面共 9 个标签页：对话、写作、伏笔、星图、设定、大纲、知识库、灵感、文件。

### 对话工作台
- 与助手对话讨论大纲、人物、设定、伏笔、逻辑等
- 流式输出，实时显示
- L1建议 / L2质询 / L3否决 三级主动干预

### 写作
- 按大纲分章起草与续写，正文直接落盘到项目目录

### 伏笔
- 伏笔台账管理：埋设 / 回收 / 逾期追踪

### 星图（关系图谱）
- 从稿件中抽取角色 / 世界 / 势力 / 地点 / 事件 / 道具六类实体及其关系
- 交互式 3D 关系图谱可视化（three.js）
- 人物肖像绘制（需兼容 OpenAI `/images/generations` 接口的模型）

### 设定 / 大纲 / 知识库 / 灵感
- 世界观、角色、势力等设定与大纲的结构化管理
- 知识库文件管理（世界观/角色/伏笔/大纲/文风样本）
- 灵感收集，支持读取 Word 文档

### 文件管理
- 上传 .txt/.md/.docx 稿件或参考小说
- 一键分析：文风诊断、逻辑检查、冲突值计算、全面诊断
- 拆书蒸馏（可选）：学习参考小说的叙事模式

## 拆书蒸馏（可选功能）
拆书是可选的增强功能，不使用拆书本软件也能完整运行。
用途：学习某个作者的节奏把控、行文风格、剧情设计。

流程：导入 .txt → 章节拆分 → 单章事实卡 → 故事情节单元 → 叙事模式抽象

## 技术栈
- 桌面：Electron + electron-builder
- 后端：Python 3.10+ / FastAPI / PyInstaller
- 前端：Vue 3（本地文件，单文件 HTML）+ three.js（关系图谱可视化）
- LLM：OpenAI 兼容接口（DeepSeek/Claude/GPT/Qwen 等）

## 项目结构
```
moshen/
├── electron/
│   └── main.js              # Electron 主进程（窗口管理 + Python 子进程）
├── backend/
│   ├── server.py            # 后端服务启动器（Electron 模式入口）
│   ├── desktop.py           # 桌面入口（PyWebView 备选方案）
│   ├── main.py              # FastAPI 应用
│   ├── core/                # 核心层
│   │   ├── config.py        # 多模型配置
│   │   ├── llm_provider.py  # LLM 调用
│   │   ├── prompt_loader.py # 提示词加载
│   │   ├── context_manager.py # 四层上下文
│   │   ├── file_parser.py   # 文件解析（含 .docx）
│   │   ├── resource_path.py # 资源路径解析（开发/打包）
│   │   └── utils.py         # 通用工具函数
│   ├── engines/             # 引擎层
│   │   ├── dialogue_manager.py  # 对话管理器
│   │   ├── intent_router.py     # 意图路由
│   │   └── intervention.py      # 主动干预引擎
│   ├── knowledge/           # 知识层
│   │   ├── project_kb.py    # 项目知识库
│   │   ├── rules_kb.py      # 创作规范库
│   │   ├── novel_analyzer.py # 可选拆书引擎
│   │   └── relation_graph_render.py # 关系图谱渲染数据
│   ├── analysis/            # 星图分析层
│   │   ├── base.py                  # 分析器基类（模板方法）
│   │   ├── relation_graph_analyzer.py # 实体与关系抽取
│   │   ├── foreshadowing_analyzer.py  # 伏笔分析
│   │   ├── chapter_split.py           # 章节拆分
│   │   ├── portrait.py                # 人物肖像生成
│   │   └── progress.py                # 分析进度追踪
│   ├── routes/              # 路由层（11 个模块）
│   │   ├── chat.py          # 对话
│   │   ├── project.py       # 项目管理
│   │   ├── files.py         # 文件管理
│   │   ├── workspace.py     # 工作区
│   │   ├── knowledge.py     # 知识库
│   │   ├── settings_writer.py # 设定写入
│   │   ├── conversation.py  # 会话历史
│   │   ├── outline.py       # 大纲
│   │   ├── writing.py       # 写作
│   │   ├── foreshadowing.py # 伏笔
│   │   └── relations.py     # 星图关系图谱
│   └── prompts/             # 提示词模板
│       └── system_persona/  # 助手人格
├── frontend/
│   ├── index.html           # Vue 3 前端（单文件）
│   ├── vue.global.js        # Vue 3 运行时
│   └── vendor/
│       └── three.min.js     # three.js（关系图谱可视化）
├── moshen.spec              # PyInstaller 打包配置
├── package.json             # Electron + electron-builder 配置
├── start.bat                # 开发模式启动脚本（仅开发者使用）
└── README.md
```

## 多角色模型配置
| 角色 | 用途 | 推荐模型 |
|------|------|---------|
| TEXT_MASTER | 文学分析、文风诊断 | 文字能力最强的模型 |
| STRUCTURE_ANALYST | 结构分析、逻辑检查 | 推理能力强的模型 |
| KNOWLEDGE_BUILDER | 拆书、知识提取 | 快速低成本模型 |
| DIALOGUE_PARTNER | 日常对话、创意讨论 | 均衡模型 |
| NOVEL_ANALYZER | 星图实体与关系抽取 | 推理能力强的模型 |
| IMAGE_GENERATOR | 人物肖像绘制 prompt 生成 | 复用聊天 API |

只需配置一组即可使用，系统自动降级。

## 致谢
本项目架构设计基于对以下开源项目的深度源码学习：
- [tianming-skill](https://github.com/zy-zmc/tianming-skill) by 子夜
- [harnessNovel](https://github.com/XTmingyue/harnessNovel) by XTmingyue
