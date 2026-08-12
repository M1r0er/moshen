# 墨参 MoShen · 小说写作助手

> 辅助而非代笔——让 AI 当编辑，不当枪手。

## 快速开始

### 1. 启动桌面应用
双击 `start.bat`，程序会自动安装依赖并以桌面窗口形式打开（无需浏览器）。

也可手动启动：
```bash
cd backend
python desktop.py
```

### 2. 配置模型
应用打开后，点击左侧"设置"按钮，填写至少一组模型的 API Key（推荐 DeepSeek 或 Claude）。

## 核心功能

### 对话工作台
- 与助手对话讨论大纲、人物、设定、伏笔、逻辑等
- 流式输出，实时显示
- L1建议/L2质询/L3否决 三级主动干预

### 文件管理
- 上传 .txt/.md 稿件或参考小说
- 一键分析：文风诊断、逻辑检查、冲突值计算、全面诊断
- 拆书蒸馏（可选）：学习参考小说的叙事模式

### 项目仪表盘
- 知识库文件管理（世界观/角色/伏笔/大纲/文风样本）
- 诊断报告查看

## 拆书蒸馏（可选功能）
拆书是可选的增强功能，不使用拆书本软件也能完整运行。
用途：学习某个作者的节奏把控、行文风格、剧情设计。

流程：导入 .txt → 章节拆分 → 单章事实卡 → 故事情节单元 → 叙事模式抽象

## 技术栈
- 后端：Python 3.10+ / FastAPI / httpx
- 前端：Vue 3（CDN，单文件 HTML）
- 桌面：PyWebView（原生窗口，无需浏览器）
- LLM：OpenAI 兼容接口（DeepSeek/Claude/GPT/Qwen 等）

## 项目结构
```
moshen/
├── backend/
│   ├── desktop.py           # 桌面应用入口（PyWebView）
│   ├── main.py              # FastAPI 入口
│   ├── core/                # 核心层
│   │   ├── config.py        # 多模型配置
│   │   ├── llm_provider.py  # LLM 调用
│   │   ├── prompt_loader.py # 提示词加载
│   │   ├── context_manager.py # 四层上下文
│   │   └── file_parser.py   # 文件解析
│   ├── engines/             # 引擎层
│   │   ├── dialogue_manager.py  # 对话管理器
│   │   ├── intent_router.py     # 意图路由
│   │   └── intervention.py      # 主动干预引擎
│   ├── knowledge/           # 知识层
│   │   ├── project_kb.py    # 项目知识库
│   │   ├── rules_kb.py      # 创作规范库
│   │   └── novel_analyzer.py # 可选拆书引擎
│   ├── routes/              # 路由层
│   │   ├── chat.py          # 对话接口
│   │   ├── project.py       # 项目管理
│   │   └── files.py         # 文件管理
│   └── prompts/             # 提示词模板
│       └── system_persona/  # 助手人格
├── frontend/
│   └── index.html           # Vue 3 前端
├── workspace/               # 项目工作区
└── start.bat                # 启动脚本
```

## 四角色模型配置
| 角色 | 用途 | 推荐模型 |
|------|------|---------|
| TEXT_MASTER | 文学分析、文风诊断 | 文字能力最强的模型 |
| STRUCTURE_ANALYST | 结构分析、逻辑检查 | 推理能力强的模型 |
| KNOWLEDGE_BUILDER | 拆书、知识提取 | 快速低成本模型 |
| DIALOGUE_PARTNER | 日常对话、创意讨论 | 均衡模型 |

只需配置一组即可使用，系统自动降级。

## 致谢
本项目架构设计基于对以下开源项目的深度源码学习：
- [tianming-skill](https://github.com/zy-zmc/tianming-skill) by 子夜
- [harnessNovel](https://github.com/XTmingyue/harnessNovel) by XTmingyue
