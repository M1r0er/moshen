<div align="center">

# 墨参 MoShen

**本地优先的小说创作助手：整理你的资料，指出你的问题，不代你动笔**

[![version](https://img.shields.io/github/v/tag/M1r0er/moshen?label=version&color=blue)](https://github.com/M1r0er/moshen/tags)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![platform](https://img.shields.io/badge/platform-Windows-blue)]()
[![python](https://img.shields.io/badge/Python-3.10%2B-blue)]()
[![electron](https://img.shields.io/badge/Electron-33-47848F)]()

</div>

## 项目定位

墨参是一间属于你自己的编辑室。它把你散落的设定、大纲、角色、伏笔与参考资料收进同一个项目，在你动笔前后提供结构、逻辑、文风与节奏上的判断，并把你的决定记录下来，供下一章延续。

它不产出正文。正文只有在你于写作页手动保存时才会写入磁盘；模型拿到的上下文用于讨论、诊断与整理，不会替你写下一章。对话中有两类写入：一类是约定标记触发的直接写入，范围限定在设定、大纲与知识库；另一类是待确认提案，模型把整理好的内容交给你，点「加入」之后才归入设定、大纲、知识库或爽爆点库。两者都会标注来源，且不会覆盖你手工维护的内容。

### 与一键生成型写作工具的区别

| 维度 | 墨参 | 一键生成型 AI 写作工具 |
|---|---|---|
| 正文来源 | 由作者书写 | 由模型生成 |
| AI 的角色 | 编辑、审稿、资料整理 | 代笔 |
| 事实来源 | 区分作者事实与 AI 推断，来源可查 | 模型输出即事实 |
| 数据位置 | 本机工作区文件，模型与 Key 自配 | 多为云端托管 |
| 创作主导权 | 在作者 | 在模型 |

## 项目背景

让模型直接写完一章并不困难，代价却出现在后面。模型在海量平均语料上训练，输出会趋向"最平均"的表达，人物与桥段容易趋向套路；长篇小说依赖几十万字前后一致的设定与伏笔，纯生成很难长期维持；当写作被交出去之后，作者对作品的判断力与个人风格也一并交了出去。

墨参把模型放在编辑的位置上。它帮你核对前后矛盾、追踪尚未回收的伏笔、梳理人物关系、诊断文风里的"AI 味"，在你偏离设定时提出质疑；这一章究竟怎么写，仍然由你决定。

## 功能

界面共 9 个页面。

| 页面 | 能做什么 |
|---|---|
| 对话 | 讨论大纲、人物、设定、伏笔与逻辑；流式输出；输入意图自动路由到合适的职能模型；发现问题时给出 L1 建议 / L2 质询 / L3 否决；可提出待确认提案，由你点选后归入设定、大纲、知识库或爽爆点 |
| 写作 | 富文本正文编辑器，自动保存；单章与全文伏笔检测；章节创作流程管理 |
| 伏笔 | 伏笔台账：记录埋设与回收、按状态与时间排序，未回收的排在前面 |
| 星图 | 从稿件抽取角色 / 世界 / 势力 / 地点 / 事件 / 道具六类实体及关系；重新分析采用**增量更新**（AI 只提交变更补丁，逐字段更新、可随剧情增删，不会整份重写已有设定）；也可手动新增、编辑、删除条目与关系；3D 关系图支持拖拽旋转、拖拽节点、滚轮缩放，右键节点或连线可直接编辑；条目档案可手动补充；手动维护的内容不会被 AI 覆盖；可选人物肖像生成 |
| 设定 | 多级设定目录树、AI 优化、从文档提取设定、导出 txt / md / html / docx |
| 大纲 | 节点与连线式大纲，区分主线与支线 |
| 爽爆点 | 大纲页的子页面：按剧情段 / 副本登记爽点、爆点、钩子等，记录每个点用了多少次，附审美疲劳提示，并可用 AI 检查已有正文修正使用次数 |
| 知识库 | 本地文件分析整理、按关键词生成参考文档、从对话保存、重新分析 |
| 灵感 | 指定灵感文件夹，浏览并读取其中的常见文本与 Word 文档 |
| 文件 | 上传 txt / md / docx；文风诊断、逻辑检查、冲突值计算、全面诊断；拆书蒸馏 |

## 创作流程与门禁

写作页把"规划 → 草稿 → 审稿 → 修稿 → 定稿"落成显式的章节状态，并记录每次变迁：

- 状态只向前推进，回退需要显式重置；
- 草稿不能跳过审稿直接定稿；
- 已定稿章节只读，改动前必须重置；
- 修改已审稿或已修稿的正文，该章会退回草稿并提示重新审稿。

## 快速开始

### 安装版

安装包由 `npm run build:electron` 生成，产物为 `release/` 下的 `墨参 MoShen-Setup-<版本>.exe`，双击安装即可。运行不需要本机安装 Node.js 或 Python——后端已随安装包打包，由 Electron 在启动时拉起。

### 源码运行

需要 Node.js 18+ 与 Python 3.10+。

```bash
npm install
npm run dev
```

`start.bat` 等价于开发调试入口。窗口打开后，Python 后端在后台启动。

### 构建

```bash
# 1. 打包 Python 后端（PyInstaller → dist/moshen-server）
npm run build:python

# 2. 免安装版（→ release/win-unpacked）
npx electron-builder --win --dir --publish never

# 3. NSIS 安装包（→ release/）
npm run build:electron
```

## 模型配置

墨参使用任意 OpenAI 兼容接口，DeepSeek、Claude、GPT、Qwen、Ollama 等均可接入。配置按职能分配模型，保存在 `~/.moshen/.env`。

| 角色 | 用途 | 建议 |
|---|---|---|
| TEXT_MASTER | 文学分析、文风诊断 | 文字能力最强的模型 |
| STRUCTURE_ANALYST | 结构分析、逻辑检查、干预评估 | 推理能力强的模型 |
| KNOWLEDGE_BUILDER | 拆书、知识提取 | 快速低成本模型 |
| DIALOGUE_PARTNER | 日常对话、创意讨论 | 均衡模型 |
| NOVEL_ANALYZER | 星图实体与关系抽取 | 推理能力强的模型 |
| IMAGE_GENERATOR | 人物肖像的绘画提示词（图像接口需兼容 `/images/generations`） | 可复用聊天 API |

只配置一组即可使用：未单独指定时按降级链自动挑选，`auto` 模式还会结合输入长度与意图，在同一渠道的多个模型之间选择。设置页可以管理多组 API 渠道。

## 语言

界面语言与项目写作语言相互独立，各自切换并保存。

界面语言只影响按钮与标签的文案；写作语言会写进系统提示词，决定模型用什么语言创作。你粘贴的作者原文与参考资料在任何语言设置下都保持原样，不被翻译或改写。系统提示词中的语言约定、结构化输出协议与作者事实优先级属于不可覆盖的合同，用户配置与写作模板都改不动它们。

当前内置简体中文与 English 两套界面文案，覆盖导航与主要状态提示，其余页面文案仍在补齐。

## 数据、隐私与边界

| 数据 | 位置 |
|---|---|
| 项目资料（设定、大纲、伏笔、正文、对话、报告、星图、爽爆点） | 你选择的工作区目录，纯文件存储（JSON / Markdown / HTML） |
| 模型配置与 API Key | `~/.moshen/.env` |
| 工作区、灵感文件夹、语言与写作偏好 | `~/.moshen/workspace.json` |

几条约定：

- 墨参不提供模型额度，也不托管稿件；请求发往你自己配置的服务商。
- 文件写入采用原子替换（临时文件 + fsync + 重命名），读取-修改-写入序列串行执行，避免文件损坏与并发覆盖。
- 设定、大纲与伏笔记录带来源标注 `author` / `ai`；助手不能覆盖你手工维护的内容，遇到冲突会提示，而不是悄悄改写。
- 对话中的写入分两类：标记触发的直接写入只覆盖设定、大纲、知识库；其余内容走待确认提案，需你点「加入」才会落地。
- 项目被重新打开后，旧窗口持有的会话租约立即失效，其写入会被拒绝，避免过期上下文污染新会话。
- 上下文按预算与优先级注入，被截断或省略的部分会显式告知模型与用户，不做静默丢弃。

## 项目结构

```
moshen/
├── electron/
│   └── main.js                    # Electron 主进程（窗口管理 + Python 子进程）
├── backend/
│   ├── server.py                  # 后端服务启动器（Electron 模式入口）
│   ├── desktop.py                 # 桌面入口（PyWebView 备选方案）
│   ├── main.py                    # FastAPI 应用与会话租约校验
│   ├── core/                      # 核心层
│   │   ├── config.py              # 多模型配置与渠道
│   │   ├── llm_provider.py        # LLM 调用（流式、断流重试、结束原因）
│   │   ├── prompt_loader.py       # 提示词加载
│   │   ├── context_manager.py     # 四层上下文（请求级）
│   │   ├── context_builder.py     # 分节预算组装与缺口说明
│   │   ├── safe_io.py             # 原子写与写锁
│   │   ├── session.py             # 项目会话租约
│   │   ├── file_parser.py         # 文件解析（含 .docx）
│   │   ├── resource_path.py       # 资源路径解析（开发 / 打包）
│   │   └── utils.py               # 通用工具
│   ├── engines/                   # 引擎层
│   │   ├── dialogue_manager.py    # 对话编排
│   │   ├── intent_router.py       # 意图路由
│   │   └── intervention.py        # 主动干预评估
│   ├── knowledge/                 # 知识层
│   │   ├── project_kb.py          # 项目知识库
│   │   ├── flow.py                # 章节创作流程状态机
│   │   ├── plot_points.py         # 爽爆点库与使用次数统计
│   │   ├── rules_kb.py            # 创作规范库
│   │   ├── novel_analyzer.py      # 可选拆书引擎
│   │   └── relation_graph_render.py
│   ├── analysis/                  # 星图分析层
│   │   ├── base.py                # 分析器基类（模板方法）
│   │   ├── relation_graph_analyzer.py
│   │   ├── foreshadowing_analyzer.py
│   │   ├── chapter_split.py
│   │   ├── portrait.py
│   │   └── progress.py
│   ├── routes/                    # 路由层
│   │   ├── chat.py                # 对话
│   │   ├── writing.py             # 写作与流程门禁
│   │   ├── flow.py                # 创作流程
│   │   ├── foreshadowing.py       # 伏笔
│   │   ├── relations.py           # 星图
│   │   ├── settings_writer.py     # 设定
│   │   ├── outline.py             # 大纲
│   │   ├── plot_points.py         # 爽爆点与 AI 使用次数检查
│   │   ├── knowledge.py           # 知识库
│   │   ├── files.py               # 文件与诊断
│   │   ├── workspace.py           # 工作区与偏好
│   │   ├── project.py             # 项目管理
│   │   ├── conversation.py        # 对话历史
│   │   └── session.py             # 会话租约
│   └── prompts/                   # 提示词模板
│       ├── system_persona/        # 助手人格
│       └── system_contract/       # 不可覆盖的系统合同
├── frontend/
│   ├── index.html                 # Vue 3 前端（单文件）
│   ├── vue.global.js              # Vue 3 运行时
│   └── vendor/three.min.js        # three.js（关系图可视化）
├── moshen.spec                    # PyInstaller 打包配置
├── package.json                   # Electron + electron-builder 配置
├── start.bat                      # 开发模式启动脚本
└── README.md
```

## 技术栈

- 桌面：Electron + electron-builder
- 后端：Python 3.10+ / FastAPI / uvicorn / PyInstaller
- 前端：Vue 3（单文件 HTML）+ three.js
- 模型：OpenAI 兼容接口，流式输出，具备断流重试与结束原因识别
- 存储：工作区文件，原子写 + 写锁

## 已知限制

- 不生成正文，也不提供"一键成书"；选题、创意与事实核查仍由你负责。
- 星图分析、文风诊断与拆书蒸馏的质量取决于所配模型，结论需要你复核。
- 人物肖像需要兼容 `/images/generations` 的接口，未配置时该项不可用。
- 界面文案仅覆盖简体中文与 English，且英文覆盖尚不完整。
- 安装包尚未代码签名，Windows 可能显示发布者信誉提示。
- 当前只提供 Windows 构建。

## 参与开发

欢迎提交 issue 与 PR。提交前请确认：

1. 后端可编译：`python -m compileall backend`
2. 前端内联脚本语法正确；
3. 若改动版本号，需同步 `package.json`、`backend/main.py`（FastAPI 与 `/api/health`）、`frontend/index.html` 三处。

## 致谢

架构与产品思路受以下开源项目启发：

- [tianming-skill](https://github.com/zy-zmc/tianming-skill) by 子夜
- [harnessNovel](https://github.com/XTmingyue/harnessNovel) by XTmingyue
- [AI-Novel-Writer](https://github.com/EthanYoQ/AI-Novel-Writer) by EthanYoQ

## 许可证

[MIT](LICENSE)
