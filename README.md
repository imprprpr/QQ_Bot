# AnimaBot · QQ 角色聊天机器人

基于 **NoneBot2 + OneBot V11** 的 QQ 角色陪伴机器人，支持多角色对话、图片理解、ComfyUI 生图、GPT-SoVITS 语音和情景表情包。当前版本 **0.2.1**。

每个用户与每个角色分别保留上下文和长期信息；图片任务在后台排队，出图期间仍可聊天。项目带有仅限本机访问的管理页，可查看运行记录、任务结果与模型 token 用量。

## 技术栈

| 层级 | 技术 | 用途 |
|---|---|---|
| 语言与并发 | Python 3.10+、asyncio | 异步消息处理、会话锁、后台生图队列 |
| 机器人框架 | NoneBot2、nonebot-adapter-onebot | 接收 OneBot V11 事件、回复 QQ 消息 |
| Web 服务 | FastAPI、Uvicorn | OneBot 接入、本机管理 API 和网页 |
| 聊天与图片理解 | OpenAI Python SDK、OpenAI 兼容 API | 填写 API Key、Base URL、模型 ID；可共用多模态 API 或配置独立视觉服务 |
| 图片生成 | ComfyUI HTTP API | 提交 API 工作流、轮询任务、下载生成图片 |
| 语音合成 | GPT-SoVITS HTTP API | 根据角色回复生成语音，失败时回退文字 |
| 持久化 | SQLite（Python sqlite3） | 用户/角色状态、消息档案、运行阶段与生图任务 |
| 表情素材 | 本地图片库、JSON 标签、nekos.best API | 根据角色、情绪和场景筛选表情 |
| 管理界面 | HTML、CSS、原生 JavaScript | 查看任务、编辑记忆、导入表情与重发图片 |
| HTTP 与图片处理 | aiohttp、httpx、Pillow | 异步服务调用、测试、图片校验与处理 |
| 配置与依赖 | python-dotenv、Poetry | 环境变量配置、依赖声明 |
| 测试 | unittest、AsyncMock、本机 HTTP / ASGI 服务 | 无真实 QQ、模型费用或 GPU 任务的回归测试 |

**聊天数据使用 SQLite。** JSON 用于角色卡、ComfyUI 工作流和本地表情标签，不用来替代聊天数据库。当前为单进程设计，尚未适配 PostgreSQL 或多实例共享数据库。

## 主要功能

- **角色卡**：支持 JSON 与含角色元数据的 PNG，兼容部分 Tavern V2 字段和关键词世界书。
- **角色独立记忆**：按机器人、私聊/群聊、群号、用户及角色隔离，切换角色后恢复原记录。
- **显式长期信息**：用户可添加、查看和删除记忆；管理员可编辑阶段摘要。当前不自动提取事实或生成摘要。
- **图片理解**：支持主模型直接处理图片，或由独立视觉服务生成描述；多模态模式保留有限图片历史。
- **上下文生图**：结合角色设定、最近对话和上一张图的描述生成完整提示词；也支持 `/draw` 直接生图。
- **后台图片队列**：默认最多8个等待或运行任务，GPU逐个执行；不发送固定的“正在生成图片”提示。
- **语音与情景表情**：支持语音回复，本地精选表情优先，缺少匹配时尝试外部动漫动图。
- **运行管理**：查看消息、模型阶段、token 用量、ComfyUI `prompt_id` 与发送结果，手动重发已有图片。
- **群聊触发**：群内需要真正 @ 机器人；普通群消息不进入模型。主动问候仅私聊可选，默认关闭。

## 架构

```text
QQ / OneBot V11 协议端
          │ 反向 WebSocket
          ▼
NoneBot2 消息与命令路由
          ├─ 用户 / 角色状态 ───────────── SQLite
          ├─ 聊天与图片理解 ───────────── OpenAI 兼容 API（API Key）
          │                               可选独立视觉 API
          ├─ 表情选择 ─────────────────── 本地素材 / nekos.best
          ├─ 语音合成 ─────────────────── GPT-SoVITS
          └─ 后台生图队列 ─────────────── ComfyUI
                                          │
                              保存图片 → 发送到原 QQ 会话

本机管理页 → 运行记录、角色记忆、表情库、已有图片重发
```

## 部署前准备

1. Python 3.10 或以上；下述环境准备示例使用 Windows PowerShell。
2. 独立运行且支持 OneBot V11 的 QQ 协议端。本仓库不包含 QQ 登录程序。
3. 支持 OpenAI Chat Completions 协议的模型服务、API Key 和对应模型 ID。
4. 如需生图，运行 ComfyUI 并安装工作流要求的模型及节点。
5. 如需语音，运行 GPT-SoVITS 并准备你有权使用的参考音频。

机器人启动脚本不会自动启动 QQ 协议端、ComfyUI、代理或 GPT-SoVITS。

## 安装与启动

```powershell
git clone https://github.com/imprprpr/QQ_Bot.git
cd QQ_Bot
Copy-Item .env.example .env
```

已有 `.env` 时不要覆盖。依赖可以使用 Poetry 安装：

```powershell
poetry install --no-root
# 完成下面的配置后启动
poetry run python bot.py
```

也可使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "nonebot2[fastapi,httpx]>=2.3,<3" "nonebot-adapter-onebot>=2.4.3,<3" "openai>=1.14,<3" "aiohttp>=3.9.3,<4" "python-dotenv>=1.0.1,<2" "Pillow>=10.4,<13" "httpx>=0.27,<1"
.\.venv\Scripts\python.exe bot.py
```

以下 `python` 命令都应在安装了项目依赖的同一环境执行；使用 Poetry 时加 `poetry run`，使用上述虚拟环境时替换为 `.\.venv\Scripts\python.exe`。

### 1. 配置 QQ 连接

`.env` 默认仅监听本机：

```dotenv
HOST=127.0.0.1
PORT=8080
COMMAND_START=["/"]
```

将同机 OneBot V11 协议端的反向 WebSocket 地址设为：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

端口或跨机器部署需要相应调整。不要将本机管理接口直接暴露到公网；协议端的认证设置也应与 NoneBot 配置一致。

### 2. 填写模型 API

在 `.env` 中填写以下三项即可接入聊天。示例使用 DeepSeek 兼容地址，其他服务填写各自的接口地址和模型 ID：

```dotenv
LLM_API_KEY=你的API密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

只填写服务的 API 基础地址，不要追加 `/chat/completions`。不同服务对 `/v1` 等路径的要求可能不同，按服务商提供的 OpenAI 兼容示例填写。模型必须实际支持 Chat Completions 协议；原生协议地址不能直接混用。

可选参数：

```dotenv
LLM_TIMEOUT=90
LLM_MAX_TOKENS=500
LLM_TEMPERATURE=0.7
LLM_EXTRA_BODY={}
LLM_PROXY=
```

`LLM_PROXY` 可填写本机 HTTP 代理地址，留空时使用客户端默认网络设置；显式代理不会用于本机回环模型地址。模型特有参数可通过 `LLM_EXTRA_BODY` 传 JSON 对象，需以对应服务的支持范围为准。密钥仅放本机 `.env`，不提交到仓库。

**看图方式一：主模型本身支持多模态。** 同一个 API Key 和接口即可看图：

```dotenv
LLM_VISION_ENABLED=true
HISTORY_IMAGES=4
```

主模型必须支持 OpenAI 格式的 `image_url` 图片内容；纯文本模型不要开启。该模式不调用独立视觉服务，每次请求最多附带最近4张可用图片。图片缓存有期限和容量限制，过期后提示重新发送。

**看图方式二：纯文本主模型 + 独立视觉模型。** 默认 `LLM_VISION_ENABLED=false`，看图时先生成客观描述，再交给聊天模型：

```dotenv
LLM_VISION_ENABLED=false
VISION_API_KEY=视觉服务密钥
VISION_BASE_URL=https://your-vision-provider.example/v1
VISION_MODEL=实际支持看图的模型ID
VISION_TEMPERATURE=0.3
VISION_EXTRA_BODY={}
```

`VISION_API_KEY`、`VISION_BASE_URL`、`VISION_MODEL` 留空分别沿用聊天服务对应配置。因此同一服务、同一密钥下只换视觉模型时，只需填写 `VISION_MODEL`。纯文本主模型没有搭配可用视觉模型时，文字聊天仍可使用，看图会返回无法识别的说明。

```powershell
# 仅检查必填配置和地址格式，不联网
python tools/check_api.py
# 可选：真实短文本调用，会产生模型调用费用
python tools/check_api.py --request
```

### 3. 配置 ComfyUI

```dotenv
COMFYUI_SERVER=http://127.0.0.1:8188
DRAW_TIMEOUT=600
DRAW_QUEUE_LIMIT=8
DRAW_POSITIVE_NODE=3
DRAW_STEPS=22
DRAW_WIDTH=640
DRAW_HEIGHT=960
DRAW_CFG=4.5
DRAW_SAMPLER=euler_ancestral
DRAW_SCHEDULER=normal
# 如需覆盖固定画质与风格标签：
# DRAW_BASE_TAGS=masterpiece, best quality
```

工作流位于 [`src/plugins/chat_and_draw/workflow_api.json`](src/plugins/chat_and_draw/workflow_api.json)，必须是 **API 格式**。随仓库分发的是工作流配置，不包含模型权重。

当前工作流引用 `anima_aestheticV11.safetensors`、`qwen_3_06b_base.safetensors`、`qwen_image_vae.safetensors`、`anima-highres-aesthetic-boost.safetensors`，还包含检测器/SAM节点。需准备对应文件，并安装提供 `ImpactWildcardProcessor`、`ImpactSwitch`、`ToDetailerPipe`、`Power Lora Loader (rgthree)`、`Seed (rgthree)`、`easy int` 等节点的扩展。先在自己的 ComfyUI 中导入并运行工作流，确认依赖齐全。

| 可调内容 | 位置 |
|---|---|
| 固定画质/画风标签 | `.env` 中 `DRAW_BASE_TAGS`，默认值在 `comfyui.py` |
| 角色外貌、服装和对话设定 | `characters/*.json` |
| 主模型、LoRA、负面词及图像链路 | `workflow_api.json` |
| 工作流字段映射 | `comfyui.py` 的 `build_workflow()` |

当前字段映射：正向词节点 `3`、随机种子 `37`、步数 `39`、CFG `41`、采样器 `5`、宽度 `23`、高度 `31`。换工作流时必须同步核对映射，不能只修改 JSON 文件名。

超时或机器人退出只结束本地等待，不会取消 ComfyUI 已接受的任务。重启后旧任务标记为中断，不自动重新生成。

### 4. 配置语音与表情（可选）

```dotenv
TTS_API_URL=http://127.0.0.1:9880/tts
TTS_REF_AUDIO=
TTS_PROMPT_TEXT=
TTS_PROMPT_LANG=zh
STICKER_ENABLED=true
STICKER_USER_AGENT="MyQQBot (contact: your-contact)"
STICKER_COOLDOWN=30
STICKER_PROXY=
LOCAL_STICKER_DIR=data/sticker_library
```

`TTS_REF_AUDIO` 是 GPT-SoVITS 服务可读取的参考音频路径；参考音频和权重不随仓库分发。OneBot 端也需能访问生成语音使用的本地文件 URI。

外部表情使用 [nekos.best](https://docs.nekos.best/getting-started/api-endpoints.html)，无需 API 密钥，但需按服务要求设置实际应用名和联系标识。外部接口只接收类别，不接收对话正文。`STICKER_PROXY` 留空时沿用 `LLM_PROXY`。

本地素材可从管理页导入，并设置情绪、角色文件 ID、适用和避免的关键词。支持 GIF/PNG/JPEG/WebP，每张不超过5MiB，最多500张。无本地匹配时再尝试外部接口；`STICKER_ENABLED=false` 仅关闭外部接口，本地素材仍可使用。

## 使用命令

私聊直接发送；群聊请先使用 QQ 的实际 @ 功能提及机器人。

| 命令 | 功能 |
|---|---|
| `/cards` | 查看已安装角色 |
| `/switch ling` | 切换并恢复对应角色的记忆 |
| `/memory` | 查看当前角色的长期信息及阶段摘要 |
| `/remember 我喜欢海边` | 添加当前用户与角色的长期信息 |
| `/forget 1` / `/forget all` | 删除指定或全部长期信息条目 |
| `/reset` | 重置当前角色上下文、长期信息、摘要和图片描述 |
| `/draw 1girl, solo, smiling` | 绕过聊天模型，直接调用 ComfyUI |
| `/proactive on` / `/proactive off` | 开启或关闭私聊主动问候，默认关闭 |
| `/help` | 查看帮助 |

自然聊天示例：“傍晚去海边，你穿白裙，拍张全身照。”随后说“改成红裙，其他不变”，会参考上一张成功生成图片的描述。这里是文生图描述连续，并不保证跨图人脸完全一致。

用户/角色记忆正常重启后恢复。`/reset` 不清除其他角色的记忆、不删除运行档案，也不取消已提交的图片任务。详细操作见[角色选择用户指南](角色选择用户指南.md)。

## 本机管理页

启动机器人后，另开项目终端：

```powershell
python tools/manage.py
```

打开输出的地址，默认 [http://127.0.0.1:8080/manage](http://127.0.0.1:8080/manage)，输入终端显示的令牌。默认令牌保存在 `data/admin-token`；可用至少32字符的 `BOT_ADMIN_TOKEN` 覆盖。不要分享令牌。

- 查看最近50次聊天运行与图片任务、模型/发送阶段和消息明细。
- 查看服务返回的实际 token 用量；不估算货币费用，也不补算服务未返回的用量。
- 编辑最近200个用户角色状态的长期信息和人工阶段摘要。
- 预览任务图片，手动重发已有图片到原机器人、原私聊或群聊，不重新生成。
- 导入、标记、查看和删除本地表情。

页面与接口限制本机回环访问，接口还校验管理令牌和浏览器 Origin。令牌不写入浏览器持久存储。QQ接口返回成功只代表协议端接受，不代表用户已读。

## 目录与数据

```text
bot.py                         启动 NoneBot 和 OneBot 适配器
characters/                    角色卡
src/plugins/chat_and_draw/
  __init__.py                  命令与消息路由
  sessions.py                  用户/角色状态和会话锁
  llm.py                       API Key 客户端、多模态消息与动作解析
  comfyui.py / workflow_api.json  工作流字段映射及生图调用
  jobs.py / delivery.py        后台图片任务与独立发送
  runtime.py                   运行、消息、用量和任务记录
  sticker_library.py / stickers.py  本地及外部表情
  media.py / image_history.py  语音与图片历史缓存
  admin.py / manage.html      本机管理 API 与页面
  tavern.py                    角色卡及世界书
  config.py                   配置和路径
ref_audio/                     自备 TTS 参考音频
tools/                        API 配置检查与管理令牌工具
tests/                        离线回归测试
```

默认 SQLite 文件为 `data/sessions.sqlite3`。`sessions` 保存会话选择与开关，`role_states` 保存角色历史/记忆，`runs`、`run_events`、`messages`、`tasks` 保存运行记录。

模型近期上下文默认最多30条/16000字符；长期信息最多30条、每条300字，人工摘要最多4000字。运行档案与模型上下文分开，不会将完整档案全部塞入模型。输入档案每条最多50000字符，媒体记类型标记；命令管理操作和定时问候尚未全部纳入运行档案。

| 路径 | 内容 |
|---|---|
| `data/artifacts/` | 后台任务生成图片，可从管理页重发 |
| `data/failed_images/` | 同步兼容路径发送失败时保留的图片 |
| `data/image_cache/` | 有保留期限和容量限制的输入图片缓存 |
| `data/tts_cache/` | TTS 输出缓存 |
| `data/sticker_library/` | 本地表情图片与 JSON 标签 |

运行数据库、任务图片和记录目前需自行备份与清理。只运行一个机器人进程；并发会话由进程内锁管理，SQLite 操作在线程中执行，不支持多进程共用数据库。首次启动新版本会迁移旧的当前角色状态，已被旧版本清除的历史无法恢复。

`.env`、API 密钥、运行数据、参考音频、本地备份和缓存均不纳入 Git。角色卡、图片、音频、模型权重及节点扩展需遵守各自授权。

## 测试与排障

```powershell
python -m unittest discover -s tests -v
```

0.2.1 已通过91项离线测试，覆盖角色记忆隔离、旧数据迁移、并发队列、事件去重、API Key 配置、多模态消息、用量记录、ComfyUI HTTP 往返、发送失败恢复以及管理接口访问限制。测试采用临时数据库、模拟 QQ 和本机 HTTP / ASGI 服务，不会调用真实模型、发送真实 QQ 消息或使用 GPU。

| 现象 | 检查方向 |
|---|---|
| 聊天超时，未出现 ComfyUI 接受记录 | 先检查 API Key、接口地址、模型 ID、代理和 `LLM_TIMEOUT` |
| ComfyUI 拒绝任务或无图 | 检查工作流模型、节点、字段映射；先手动运行工作流 |
| 图片已生成但 QQ 未收到 | 在管理页查看发送阶段和任务图片，修复协议端后重发 |
| 群聊没有回应 | 确认真正 @ 了机器人，OneBot WebSocket 已连接 |
| 语音失败 | 检查 GPT-SoVITS、参考音频路径与 OneBot 文件可访问性 |
| 表情包无法发送 | 检查本地情绪标签或外部 User-Agent/代理配置 |

真实部署仍需依次验收文字、角色切换、重启记忆、看图、语音、生成图片和群聊 @。更多版本说明见[升级说明](升级说明-0.2.md)。
