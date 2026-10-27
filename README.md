<a id="top"></a>

# AnimaBot · QQ 角色聊天机器人

基于 **NoneBot2 + OneBot V11** 的 QQ 角色陪伴机器人，支持多角色对话、图片理解、ComfyUI / 云 API 生图、GPT-SoVITS / Genie-TTS / Google Chirp 3 HD 语音和情景表情包。当前版本 **0.4.0**。

每个用户与每个角色分别保留上下文和长期信息；图片任务在后台排队，出图期间仍可聊天。项目带有仅限本机访问的管理页，可查看运行记录、任务结果与模型 token 用量。

<p align="center">
  <a href="#tech-stack">技术栈</a> · <a href="#features">特性速览</a> · <a href="#quick-start">安装部署</a> · <a href="#model-api">模型配置</a> · <a href="#image-generation">图片生成</a> · <a href="#voice-stickers">语音与表情</a><br>
  <a href="#commands">命令索引</a> · <a href="#group-chat">群聊参与</a> · <a href="#proactive">主动问候</a> · <a href="#bot-interaction">机器人斗嘴</a> · <a href="#personas">人设库与QQ分配</a> · <a href="#memory">角色记忆</a> · <a href="#code-analysis">代码分析</a> · <a href="#admin">管理界面</a> · <a href="#troubleshooting">常见排障</a><br>
  <a href="#architecture">架构</a> · <a href="#data">数据持久化</a> · <a href="#cloud-server">云服务器</a> · <a href="#systemd">systemd 后台管理</a> · <a href="#documentation">文档索引</a>
</p>

<a id="tech-stack"></a>

## 技术栈

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white"></a>
  <a href="https://nonebot.dev/"><img alt="NoneBot2 2.3+" src="https://img.shields.io/badge/NoneBot2-2.3%2B-EA5252?style=flat-square"></a>
  <a href="https://onebot.dev/"><img alt="OneBot V11" src="https://img.shields.io/badge/OneBot-V11-009688?style=flat-square"></a>
  <a href="https://fastapi.tiangolo.com/"><img alt="Web FastAPI" src="https://img.shields.io/badge/Web-FastAPI-009688?style=flat-square&amp;logo=fastapi&amp;logoColor=white"></a>
  <a href="https://www.sqlite.org/"><img alt="Database SQLite" src="https://img.shields.io/badge/Database-SQLite-003B57?style=flat-square&amp;logo=sqlite&amp;logoColor=white"></a>
  <a href="https://python-poetry.org/"><img alt="Dependencies Poetry" src="https://img.shields.io/badge/Dependencies-Poetry-60A5FA?style=flat-square&amp;logo=poetry&amp;logoColor=white"></a>
</p>
<p align="center">
  <img alt="LLM OpenAI-compatible API" src="https://img.shields.io/badge/LLM-OpenAI--compatible_API-412991?style=flat-square">
  <a href="https://github.com/Comfy-Org/ComfyUI"><img alt="Image ComfyUI or Cloud API" src="https://img.shields.io/badge/Image-ComfyUI_%2F_Cloud_API-2088FF?style=flat-square"></a>
  <img alt="TTS GPT-SoVITS, Genie, Cloud API" src="https://img.shields.io/badge/TTS-GPT--SoVITS_%2F_Genie_%2F_Cloud_API-FF8C42?style=flat-square">
  <img alt="HTTP aiohttp and httpx" src="https://img.shields.io/badge/HTTP-aiohttp_%2F_httpx-2C5BB4?style=flat-square">
</p>

<details>
<summary>展开查看组件用途与依赖说明</summary>

| 层级 | 技术 | 用途 |
|---|---|---|
| 语言与并发 | Python 3.10+、asyncio | 异步消息处理、会话锁、后台生图队列 |
| 机器人框架 | NoneBot2、nonebot-adapter-onebot | 接收 OneBot V11 事件、回复 QQ 消息 |
| Web 服务 | FastAPI、Uvicorn | OneBot 接入、本机管理 API 和网页 |
| 聊天与图片理解 | OpenAI Python SDK、OpenAI 兼容 API | 填写 API Key、Base URL、模型 ID；可共用多模态 API 或配置独立视觉服务 |
| 图片生成 | ComfyUI / 云图片 API | 可配置本机工作流或独立云服务，结果进入原有任务与发送流程 |
| 语音合成 | GPT-SoVITS / Genie-TTS / Google Chirp 3 HD HTTP API | 按角色选择音色，失败时回退文字 |
| 持久化 | SQLite（Python sqlite3） | 用户/角色状态、消息档案、运行阶段与生图任务 |
| 表情素材 | 本地图片库、JSON 标签、nekos.best API | 根据角色、情绪和场景筛选表情 |
| 管理界面 | HTML、CSS、原生 JavaScript | 查看任务、编辑记忆、导入表情与重发图片 |
| HTTP 与图片处理 | aiohttp、httpx、Pillow | 异步服务调用、测试、图片校验与处理 |
| 配置与依赖 | python-dotenv、Poetry | 环境变量配置、依赖声明 |
| 测试 | unittest、AsyncMock、本机 HTTP / ASGI 服务 | 无真实 QQ、模型费用或 GPU 任务的回归测试 |

</details>

**聊天数据使用 SQLite。** JSON 用于角色卡、ComfyUI 工作流和本地表情标签，不用来替代聊天数据库。当前为单进程设计，尚未适配 PostgreSQL 或多实例共享数据库。

<a id="features"></a>

## 主要功能

- **角色卡**：支持 JSON 与含角色元数据的 PNG，兼容部分 Tavern V2 字段和关键词世界书。
- **角色独立记忆**：按机器人、私聊/群聊、群号、用户及角色隔离，切换角色后恢复原记录。
- **手动与自动记忆**：保留 `/remember` 手动条目；自动从有线索的用户消息提取事件、偏好和事实，逐条保存原文引文、来源与时间，支持更正与删除。阶段摘要仍由管理员编辑。
- **自然接话**：放宽机械字数和强制追问约束，区分承接与收尾；角色可以有不同意见，用户说晚安时自然告别。
- **图片理解**：支持主模型直接处理图片，或由独立视觉服务生成描述；多模态模式保留有限图片历史。
- **上下文生图**：结合角色设定、最近对话和上一张图的描述生成完整提示词；也支持 `/draw` 直接生图。
- **后台图片队列**：默认最多8个等待或运行任务，GPU逐个执行；不发送固定的“正在生成图片”提示。
- **语音与情景表情**：支持语音回复，本地精选表情优先，缺少匹配时尝试外部动漫动图。
- **运行管理**：查看消息、模型阶段、token 用量、ComfyUI `prompt_id` 与发送结果，手动重发已有图片。
- **群聊参与**：@ 直接回应、昵称限频回应、根据近期公开上下文低概率参与。普通消息在本地筛选，不逐条请求模型；私聊定时问候默认开启。

<a id="architecture"></a>

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
          ├─ 语音合成 ─────────────────── GPT-SoVITS / Genie / 云 TTS
          └─ 后台生图队列 ─────────────── ComfyUI / 云图片 API
                                          │
                              保存图片 → 发送到原 QQ 会话

本机管理页 → 运行记录、角色记忆、表情库、已有图片重发
```

<a id="prerequisites"></a>

## 部署前准备

1. Python 3.10 或以上；下述环境准备示例使用 Windows PowerShell。
2. 独立运行且支持 OneBot V11 的 QQ 协议端。本仓库不包含 QQ 登录程序。
3. 支持 OpenAI Chat Completions 协议的模型服务、API Key 和对应模型 ID。
4. 如需生图，配置云图片 API，或运行 ComfyUI 并安装工作流要求的模型及节点。
5. 如需语音，配置云 TTS，或运行 GPT-SoVITS / Genie-TTS 并准备你有权使用的音色与参考音频。

机器人启动脚本不会自动启动 QQ 协议端、ComfyUI、代理或 GPT-SoVITS。

<a id="quick-start"></a>

## 安装与启动

```powershell
git clone https://github.com/imprprpr/QQ_Bot.git
cd QQ_Bot
Copy-Item .env.example .env
```

已有 `.env` 时不要覆盖。依赖可以使用 Poetry 安装：

本机专用设置可写入 `.env.local`，其优先级高于 `.env` 和同名环境变量；该文件及 `.local/` 目录均不纳入 Git。公开版本默认使用下面的 API Key 配置。本机特殊服务可通过 `LLM_CLIENT_FACTORY` 指定私有 Python 文件，文件提供 `create_client(vision=False)`，返回兼容 `chat.completions.create()` 与异步 `close()` 的客户端；日志名称可用 `LLM_BACKEND_LABEL` 设置。私有适配器及其依赖由部署者单独维护，不随仓库发布。

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

<a id="qq-connection"></a>

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

<a id="model-api"></a>

### 在 NapCat 中切换模型提供商

安装仓库自带的 `napcat-plugin-anima-peer-manager` 1.1.0 后，在 NapCat“扩展页面”打开“AnimaBot 模型提供商”。
选择预设或自定义兼容接口，填写 API Key，拉取模型列表，选定模型后“保存并启用”。支持多个配置、手动模型 ID、
恢复部署默认。仅适用于 OpenAI Chat Completions 兼容接口，不包含订阅登录或其他原生协议；生图和 TTS 接口独立。

Bot 的 `BOT_MODEL_CONFIG_FILE` 需填写插件 `ctx.configPath + '.models.json'` 的宿主机绝对路径。
每次新请求读取选择，正在进行的请求使用原配置。密钥仅存服务器私有文件且不回显；文件不可公开或提交 Git。
跨容器的运行用户和共享组权限见[插件说明](deploy/napcat/napcat-plugin-anima-peer-manager/README.md)。

### 限流保护

聊天请求收到429后使用带随机抖动的指数退避（8秒起，指数部分最高120秒），尊重服务端 `Retry-After`，
并为相同端点共享冷却。每次调用最多重试一次，排队、认证、冷却与网络请求均受总超时预算约束。
同端点串行恢复，队列最多等待10秒，避免群聊、私聊和后台请求集中冲击上游。冷却超过剩余预算时停止请求，
明确提示限流；后台主动消息静默跳过，不伪装成角色台词。冷却状态保存在进程内，正常恢复成功后清零，重启后重新建立。
该机制减少重复请求，但不能消除上游容量不足、额度耗尽或服务故障。

页面管理的兼容接口对直接聊天预留至少1024输出token；未明确@的群聊仍保持原短回复预算。
上游返回 `finish_reason=length` 时，普通聊天可发送已生成文字并说明截断，禁止执行其中完整或不完整的媒体指令；
没有正文或代码分析被截断时明确提示输出限制，不再笼统误报为服务无效。

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
VISION_MAX_TOKENS=2048
VISION_EXTRA_BODY={}
```

入站 GIF 和动画 WebP 会先转换成最多3格 PNG 关键帧拼图，再交给模型；采样限于前120帧，不能保证覆盖整段动画。静态 JPEG/PNG/WebP 保持原格式。QQ `image` 与 `mface` 消息都会读取，必要时通过协议端的 `get_image` 获取缓存地址。商城表情的兼容依据见 [NapCat 消息格式](https://napneko.github.io/develop/msg)。

独立视觉接口的输出预算默认 `VISION_MAX_TOKENS=2048`。若返回截断、内容过滤或空正文，会记录失败及完成原因，不把残缺结果当作有效描述。纯图片消息在识别失败时直接发送明确的服务端失败说明，跳过聊天模型编造原因及旧事件回忆；附带文字的问题仍可按文字继续处理。图片读取失败不推断是用户网络或清晰度问题；可在管理页检查 `image-input`、`image-fetch` 和 `vision` 阶段。

`VISION_API_KEY`、`VISION_BASE_URL`、`VISION_MODEL` 留空分别沿用聊天服务对应配置。因此同一服务、同一密钥下只换视觉模型时，只需填写 `VISION_MODEL`。纯文本主模型没有搭配可用视觉模型时，文字聊天仍可使用，看图会返回无法识别的说明。

```powershell
# 仅检查必填配置和地址格式，不联网
python tools/check_api.py
# 可选：真实短文本调用，会产生模型调用费用
python tools/check_api.py --request
```

<a id="image-generation"></a>

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

<a id="cloud-images"></a>

#### 使用云端图片服务

本机默认保持 `DRAW_BACKEND=comfyui`。无 GPU 的云服务器可选择 `DRAW_BACKEND=api`，保留原来的后台队列、角色上下文、图片保存与失败重发。支持返回 `data[0].b64_json` 的 OpenAI 风格 `/images/generations` 服务：

```dotenv
DRAW_ENABLED=true
DRAW_BACKEND=api
DRAW_API_BASE_URL=https://图片服务的API根地址/v1
DRAW_API_KEY=你的服务端密钥
DRAW_API_MODEL=服务支持的图像模型
DRAW_API_SIZE=1024x1024
DRAW_TIMEOUT=120
```

`DRAW_API_MODEL` 必须是支持图片输出的模型，不能填写普通聊天模型。响应图片会校验格式和大小，不下载服务响应里的任意 URL。当前链路使用文本画面描述重新生成图片，没有把参考图片直接传给图像接口，也不加载 ComfyUI 检查点或 LoRA。

对不同鉴权/协议的服务，可在部署私有配置中设置 `DRAW_CLIENT_FACTORY`，指向可信 Python 文件，提供 `async generate_image(prompt) -> bytes`。该文件由部署者维护，不能包含在发布包或提交凭据。路径错误会明确失败，不静默调用其他计费服务。Google Cloud 图像服务可以通过这样的适配器接入；应按最新官方生命周期选择仍支持的图像模型，单独核对权限和费用，不能假设专用赠金一定覆盖生图。

云图片接口默认 `DRAW_API_STYLE=photography`：统一成年日系美少女写真，精致小脸与灵动眼神、清透肤质和适度美颜、柔光与写真构图；角色自拍明确为成年女性。角色卡的 `image_profile` 提供稳定外貌、气质和默认衣着，随绘图任务当前角色选择，聊天与 `/draw` 均应用。场景、服装和动作以本轮要求为准，不把角色强行加入风景或物品图。原有 ComfyUI 工作流保持原提示词逻辑。

“玲”的摄影形象为黑色长发、深棕色眼睛、甜美俏皮的成年偶像写真气质；Amelia 为金发蓝眼、灵动傲娇的气质。可编辑各角色卡的 `image_profile` 调整。`DRAW_API_STYLE=custom` 可关闭默认摄影包装，交由自己的适配器处理。固定提示词改善画风和特征一致性，但不保证同一张脸；当前文生图没有人物参考图身份锁定。

云端图片默认每次请求一张，遇到超时不自动再次生成，避免结果不确定时重复计费。失败可在管理界面检查阶段记录后决定是否重试。`/draw` 会使用所选后端，不再固定绑定 ComfyUI。

<a id="model-diagnostics"></a>

#### 模型请求分阶段诊断与有界重试

`http_transport.py` 提供与服务商无关的 JSON HTTP 适配工具，供私有模型适配器及图片 API 使用。它记录 `model-http` 阶段：认证耗时、DNS/连接建立（含 TLS）、请求发出、响应头/正文时间、HTTP 状态、重试次数和总耗时。日志不记录提示词、请求体、认证头或完整项目 URL。

使用该传输层的聊天适配器默认首次尝试最多 35 秒、最后一次重试可使用总时限的剩余预算，总共最多 2 次，仍受 `LLM_TIMEOUT` 总时限限制。仅连接故障、超时、408/429/部分 5xx 可重试，带随机退避并尊重 `Retry-After`；401 最多触发一次凭据刷新。权限错误、无效请求、无效响应及模型内容拒绝不会反复提交。图片请求单独保持不自动重试。适配器内部已有重试时不要再叠加另一层 SDK 重试。

连接或认证完成前失败，可以定位到相应链路；请求发出后等待响应头的时间包含服务端排队/计算和返回网络路径，单凭客户端日志不能完全分离二者。应与同一接口的轻量请求及 DNS/TCP/TLS 探测对照，而不是把所有超时都解释为 DNS 或服务器内存不足。

模型暂态错误最多重试一次；最后一次重试可以使用总超时的剩余时间，避免两次短超时浪费总预算。
服务故障提示不写入角色历史，也不会为该提示合成语音。此机制不能保证上游拥堵时始终成功。

<a id="voice-stickers"></a>

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

也可使用云端 Google Chirp 3 HD。先在 Google Cloud 项目中启用 **Cloud Text-to-Speech API** 和结算，再配置仅供服务端使用的 API key，并将其 API 限制设为 Cloud Text-to-Speech API：

```dotenv
TTS_ENABLED=true
TTS_BACKEND=google-chirp3
GOOGLE_TTS_API_KEY=你的服务端密钥
GOOGLE_TTS_VOICE=cmn-CN-Chirp3-HD-Kore
GOOGLE_TTS_SPEAKING_RATE=1
GOOGLE_TTS_VOLUME_GAIN_DB=0
TTS_DELIVERY=base64
```

`TTS_DELIVERY=base64` 通过 OneBot 传递音频，适合 NapCat 在 Docker 中运行的情况；音频仍保存到 `data/tts_cache/` 并按 TTL 清理。失败时回退文字；每次合成限制 4,500 UTF-8 字节，超长输入回退文字。保持默认 `TTS_BACKEND=gpt-sovits` 即可继续使用本机参考音频服务。`TTS_PROXY` 可单独指定 HTTP 代理。

角色音色映射保存在 `data/tts_voices.json`，键名使用 `/角色` 对应的角色卡文件名（不含扩展名）。示例：

```json
{
  "ling": {"name": "cmn-CN-Chirp3-HD-Kore", "speaking_rate": 1.0, "volume_gain_db": 0},
  "amelia": {"name": "cmn-CN-Chirp3-HD-Leda", "speaking_rate": 0.95, "volume_gain_db": 0}
}
```

映射文件修改后下一次合成生效；未配置的角色使用全局默认。语速范围为 `0.25–2.0`，音量增益为 `-96–16 dB`，建议从 `0 dB` 开始。这里绑定的是 Google 预设声音，不能通过填写动漫角色名复刻角色，也不能直接导入 GPT-SoVITS 权重。可在 [Google 官方音色页](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd)试听；语速等控制以服务当前支持范围为准。

自有部署也可用 `TTS_HEADERS_FACTORY` 指定可信本地 Python 文件，提供同步 `headers(force=False) -> dict` 方法；文件负责认证，仓库不会读取或分发部署凭据。不要在角色卡、日志或版本库中保存密钥。

<a id="genie-tts"></a>

#### 用 Genie-TTS 在 CPU 服务器复用 GPT-SoVITS 模型

[Genie-TTS](https://github.com/High-Logic/Genie-TTS) 为兼容版本的 GPT-SoVITS 权重提供转换和 ONNX 推理。先在独立本机环境中转换模型、保存参考音频及准确文本，再上传到服务器；权重、音频和私有配置不要提交到仓库。按上游文档确认具体模型版本，不能把任意 `.pth` 或 `.onnx` 文件当作通用模型。

推荐独立安装到 `/home/ubuntu/genie-tts/.venv`，将 [服务适配器](deploy/genie/server.py) 复制到 `/home/ubuntu/genie-tts/server.py`。固定并记录上游提交版本；运行环境无需安装用于转换的 PyTorch。按上游要求准备 `GenieData`，中文部署应准备并验证中文 RoBERTa 资源。`voices.json` 示例：

```json
{
  "default": {
    "model_dir": "/home/ubuntu/genie-tts/models/my-voice",
    "language": "zh",
    "reference_audio": "/home/ubuntu/genie-tts/reference.wav",
    "reference_text": "参考录音中实际说出的完整内容。"
  }
}
```

当前验证的上游提交为 `d347fd0f8683e9a362b69f59fa0a4799ddb5e828`。该版本的中文变调处理中，“嗯，很好”等输入可能因空韵母触发索引错误；使用独立环境的 Python 执行 `deploy/genie/patch_upstream.py /home/ubuntu/genie-tts/source` 应用范围有限、可重复执行的修复。脚本保留原文件并拒绝未知源码布局。部署测试可在配置好 `GENIE_DATA_DIR` 后执行 `python -m unittest discover -s deploy/genie/tests`。

安装 [anima-genie.service](deploy/systemd/anima-genie.service) 到 `/etc/systemd/system/`，执行 `sudo systemctl daemon-reload` 和 `sudo systemctl enable --now anima-genie`。本服务单实例只加载一个声音，监听 `127.0.0.1:9881`，不需要放行公网端口。默认最多使用 2 个 CPU 核、9GB 内存，推理线程数为 2，进程调度优先级低于机器人；应按服务器实测调整。健康检查为 `GET /health`，语音请求为 `POST /tts`，JSON 请求体为 `{"text":"你好","character":"default"}`，成功返回完整 WAV。

机器人配置示例：

```dotenv
TTS_ENABLED=true
TTS_BACKEND=genie
TTS_API_URL=http://127.0.0.1:9881/tts
GENIE_CHARACTER=default
TTS_DELIVERY=base64
TTS_TIMEOUT=95
```

可在 `data/tts_characters.json` 中将角色映射到服务中的声音标识，例如 `{"ling":"default","amelia":"default"}`。未映射的角色使用 `GENIE_CHARACTER`。适配服务将推理串行处理，并发忙时返回 429，机器人回退文字；模型异常或超时也会回退文字。服务推理超时会退出，由 systemd 重启，避免后台计算继续占用并与后续请求重叠。更换音色配置或权重后需重启 `anima-genie`。

上线前应在目标 CPU 上检查音频内容、发音、首条及后续生成时间、音频时长、内存峰值和繁忙回退。ONNX 转换成功不代表音质和速度已通过验收。

外部表情使用 [nekos.best](https://docs.nekos.best/getting-started/api-endpoints.html)，无需 API 密钥，但需按服务要求设置实际应用名和联系标识。外部接口只接收类别，不接收对话正文。`STICKER_PROXY` 留空时沿用 `LLM_PROXY`。

本地素材可从管理页导入，并设置情绪、角色文件 ID、适用和避免的关键词。支持 GIF/PNG/JPEG/WebP，每张不超过5MiB，最多500张。无本地匹配时再尝试外部接口；`STICKER_ENABLED=false` 仅关闭外部接口，本地素材仍可使用。

<a id="commands"></a>

## 使用命令

私聊直接发送；群聊可使用 QQ 的实际 @ 功能或叫昵称。群内命令仍需 @ 机器人。

<a id="group-chat"></a>

### 群聊低频参与

默认昵称为“小灯、玲”，可通过 `GROUP_CHAT_NICKNAMES` 调整。真实 @ 和回复机器人消息直接进入回应流程；叫昵称默认每群45秒一次。所有未 @ 参与（昵称、续聊、普通插话）合计每群每小时最多12次生成尝试，所有群合计每小时40次。

没有 @ 时也能参与：公开提问、求助先经过本地筛选，再由模型结合最近群聊上下文决定是否回复，不要求先有多个人活跃。机器人刚回复某位成员后，该成员3分钟内的续聊也会进入判断，冷却5秒。明确 @ 或引用回复其他人的消息、命令和机器人自己的消息会跳过。

普通闲聊不再限于几个固定关键词；最近5分钟至少4条消息、至少2位不同成员时，按25%概率抽样让模型决定是否加入。公开提问和普通插话合计每群冷却3分钟、每小时最多4次、滚动24小时最多12次；续聊受上面的合计小时额度约束。参数见 `.env.example`，这些是候选判断的生成尝试额度，不保证每次都有回复。

未明确 @ 或叫昵称时，模型可返回 `[不参与]`，不发送任何消息，也不把这条候选消息写入个人对话历史。适合回应时最多160字、只发一条文字，禁止生图、语音和表情包，不额外提取自动记忆；失败时保持安静。筛选不调用额外分类模型，判断和回复在一次模型调用中完成。额度在调用前预留，失败和不参与也计数，重启不会重置；底层暂态错误最多重试一次。

短期公开上下文按机器人与群号隔离：最多保留20条、每条300字，本轮使用最近12条和30分钟内内容。过期数据随新消息清理，后台另每5分钟清理一次；不导入私聊内容，原有个人和角色记忆仍分别保存。

部署时在私有 `.env.local` 设置 `GROUP_CHAT_GROUPS=群号1,群号2` 可仅对指定群开启昵称和普通参与，留空表示已加入的所有群。`GROUP_CHAT_AMBIENT_ENABLED=false` 只关闭普通参与，`GROUP_CHAT_ENABLED=false` 同时关闭昵称触发，@ 仍可使用。全部参数见 `.env.example`。

| 命令 | 功能 |
|---|---|
| `/cards` | 查看已安装角色 |
| `/switch ling` | 切换并恢复对应角色的记忆 |
| `/memory` | 查看当前角色的长期信息及阶段摘要 |
| `/remember 我喜欢海边` | 添加当前用户与角色的长期信息 |
| `/forget 1` / `/forget all` | 删除指定或全部手动长期信息条目 |
| `/events` | 查看最近10条自动记忆、ID、时间和来源 |
| `/forgetevent ID` / `/forgetevent all` | 停用指定或全部自动记忆 |
| `/reset` | 重置当前角色上下文、手动及自动记忆、摘要和图片描述 |
| `/draw 1girl, solo, smiling` | 绕过聊天模型，直接调用所选图片服务 |
| `/proactive on` / `/proactive off` | 开启或关闭私聊主动问候，默认开启 |
| `/help` | 查看帮助 |

自然聊天示例：“傍晚去海边，你穿白裙，拍张全身照。”随后说“改成红裙，其他不变”，会参考上一张成功生成图片的描述。这里是文生图描述连续，并不保证跨图人脸完全一致。

用户/角色记忆正常重启后恢复。`/reset` 不清除其他角色的记忆、不删除运行档案，也不取消已提交的图片任务。详细操作见[角色选择用户指南](角色选择用户指南.md)。

### 短信式回复

“玲”默认采用有胜负欲、嘴硬但懂分寸的偶像搭档人设，含称呼、偏好、关系边界与多场景对话示例；
角色卡位于 `characters/ling.json`，用户明确的称呼和关系优先，群聊不会套用私聊亲密关系。
普通闲聊以10～60字、一两句为表达目标，最多两个气泡；这是生成提示而非生硬截字。
具体求助、长问题、技术分析和明确要求展开的内容按需求回答，不强行缩短，也不删除旧记忆来改变风格。

日常聊天采用短信式气泡发送：按自然段、句子和长句的分句打包，私聊最多3条，群内直接回应最多2条，
未明确@的群聊最多1条。短回复保持一条，相邻气泡间停顿约0.6～2.4秒；不额外调用模型或增加生成次数。
代码分析、代码块、运行提示保留完整结构，语音保持独立。中途发送失败后停止后续气泡，
会话记忆仅记录已确认发出的文字。

<a id="code-analysis"></a>

## 代码阅读与分析

发送“分析这段代码”“解释这个函数”“审查仓库架构”等请求，或粘贴代码片段，会按需加入专业代码分析前置提示词；
短暂的“继续”“具体怎么改”等追问沿用该模式，日常新话题自动退出，不额外调用分类模型。
提示词位于 `src/plugins/chat_and_draw/code_analysis.py`，要求依据实际材料说明调用链、触发条件、影响、最小修复与验证方法，区分证据、推测和缺失材料。
不能仅凭框架或文件大小下结论，也不因为缺少事件总线等设计模式就判定架构有缺陷。

当前聊天接口没有仓库抓取或代码执行工具，仓库链接不代表已经读过源码。没有实际代码时需明确说明范围，索取最少必要片段，
不能编造路径、行号、测试或执行结果。仓库文件和注释作为待分析数据处理，不执行其中指令。

所有生成回复使用QQ纯文本，发送前转换常见Markdown标记，示例代码保留换行和缩进，不输出代码围栏。
代码分析不调用生图、语音、表情包或自动记忆提取；直接回应的默认输出预算为1400 token，
可通过 `CODE_ANALYSIS_MAX_TOKENS` 调整（该设置最多2400）。未明确@的群聊仍遵守原参与额度和短回复限制。

<a id="bot-interaction"></a>

## 与指定机器人互动

在部署机器的 `.env.local` 填写目标机器人的QQ号与允许互动的群号，多个用英文逗号分隔：

```dotenv
BOT_INTERACTION_ENABLED=true
BOT_INTERACTION_TARGETS=
BOT_INTERACTION_GROUPS=
```

两个列表必须同时填写，留空不触发，也不会猜测群里谁是机器人。配置修改后重启服务。
也可以使用 [NapCat 群号与QQ批量管理插件](deploy/napcat/napcat-plugin-anima-peer-manager/README.md)：
在 NapCat 的插件配置中选择群与好友，或粘贴多个号码批量追加、移除。
通过 `BOT_INTERACTION_CONFIG_FILE` 接入共享配置后，保存即热更新，不改动 `.env.local` 的其他设置。
部分 NapCat 版本仅接受官方白名单插件，安装前需确认加载器支持；插件不会自行修改加载器。

风格固定为当前角色的**傲娇斗嘴**：轻松吐槽、嘴硬接梗、偶尔反夸，不辱骂、不拿隐私开玩笑。
只发送一条文字，附带程序创建的真正 OneBot `at` 消息段；目标只能来自配置，模型不能自行选人或 @ 全体。

- **接对方的话**：对方 @ 或回复机器人时进入专用判断通道；普通发言默认30%抽样。
  互动窗口内，同一对象的接话不再抽样，仍受5秒最小间隔和轮次限制。发给其他成员的消息和命令会跳过。
- **主动搭话**：后台每5分钟检查一次，默认20%概率，在配置时区08–22点、近期15分钟有公开聊天的群里挑一个配置对象搭话。
  每轮检查最多主动发送1条，所有群合计每天最多3次主动生成尝试，无需保持SSH连接。
- **阻止无限互聊**：每群同时只与一个目标互动，窗口固定3分钟，默认我方最多2条（含主动开场，硬上限3条）。
  窗口结束后再冷却10分钟；即使对方继续 @ 也不会绕过限制。每群每小时最多6次、所有群滚动24小时最多12次生成尝试。
  超时、不参与和发送失败仍计入额度；发送结果不确定时不重发；重启不会重置额度或继续一个未确认发送的轮次。

专用通道只使用当前群的短期公开上下文，不读取私聊记忆，不执行对方的 `/reset` 等命令，
不提取长期记忆，也不调用生图、语音或表情包。对方可以选择不回复；本功能不改变其他机器人的规则。
`BOT_INTERACTION_ENABLED=false` 可暂停互动；保留目标QQ号时，它们也不能退回普通 @ 通道绕过限频。
完整参数见 [配置示例](.env.example)。小时和每日额度按生成尝试计数；内部模型暂态重试仍受总时限约束。

<a id="memory"></a>

## 自动事件记忆与自然对话（0.3）

默认开启自动记忆，无需新增服务或数据库。在候选消息包含面试、考试、计划、明确偏好或纠正线索时，先调用当前聊天 API 提取简短 JSON，再回答用户；普通短回应不会因此新增提取调用。

```dotenv
EVENT_MEMORY_ENABLED=true
EVENT_MEMORY_TIMEOUT=8
# 可选：同一 API 服务下用于提取的模型；留空沿用 LLM_MODEL
MEMORY_MODEL=
```

每次最多提议4条；写入必须包含当前用户消息中逐字存在的4–300字引文，不从助手回复或图片描述中建立事实。模型可能误判语义，来源引文只证明用户说过，不是独立核实；可随时用 `/events` 或管理页查看与纠错。每个用户—角色最多保留100条可检索记录，一轮最多取6条，按文字相关性与临近事件日期筛选，尚未使用向量检索。

更正会创建有来源的新版本，旧版本退出检索；明确完成或取消才改变事件状态，过了日期不会推断面试/考试结果。相对日期按记录时的服务器本地时区解析，支持今天、明天、后天、昨天和完整年月日；只有“周六”等不明确日期时保留原话，不猜日期。

典型验证：先聊“我明天下午面试，有点紧张”，用 `/events` 查看；之后说“我说错了，改成后天面试”，检查最新日期与来源。切换角色、重启后记录仍隔离保存。

自动提取增加一次可能计费的 API 调用和最多8秒默认等待（可配置1–30秒），用量计入管理页。接口失败、截断、超时或无有效证据时跳过写入，继续聊天，不自动重试；查看日志中的 `event-memory` 阶段定位问题。提取模型应能按提示输出 JSON，暂未做历史消息回填或全量自动摘要。

`EVENT_MEMORY_ENABLED=false` 关闭后续自动提取和自动回忆，已存记录仍可查看或删除，手动记忆照常使用。`/forgetevent` 停用自动记忆，不会删除最近对话、手动条目或运行档案；彻底重开当前角色请用 `/reset`。历史修订和归档仍保存在 SQLite，需按自己的数据保留策略清理。

本阶段未加入持续情绪引擎、消息聚合或事件驱动的主动发言；主动问候支持日间窗口和闲置判断。自然度需用实际对话对照验收，自动化测试验证机制和提示策略。

<a id="proactive"></a>

## 私聊主动聊天

NapCat → 扩展页面 → **AnimaBot 主动聊天** 可调整总开关、时区、可打扰时间段、闲置时间、冷却时间和每日次数。
保存后下一次后台检查（5分钟内）热生效，无需重启或编辑配置；不会立即发消息、重置额度或开启用户单独关闭的会话。
部署时一次性设置 `BOT_PROACTIVE_CONFIG_FILE`，连接插件生成的 `config.json.proactive.json` 宿主机路径，详见[插件说明](deploy/napcat/napcat-plugin-anima-peer-manager/README.md)。

私聊主动问候默认开启，`/proactive off` 关闭，`/proactive on` 重新开启。
默认北京时间（`PROACTIVE_UTC_OFFSET=8`），08–10、12–14、19–22 的窗口内随机安排；
`PROACTIVE_MODE=random` 改为08–22随机时段。至少闲置3小时才调用模型，日上限2次生成尝试，
可用 `PROACTIVE_IDLE_HOURS` 和 `PROACTIVE_DAILY_LIMIT` 调整（上限3次）。搭话只发一条文字，
不调用图片、语音或表情包。发出后未收到用户回复则暂停，重启保留额度和待回复状态。
生成期间若收到新消息、切换角色或关闭功能，本次问候取消。UTC偏移不自动处理夏令时。

内容按当前角色轮换兴趣小分享、轻轻吐槽、小借口、轻松选择及适合时接旧话题，避开最近三个主题，
不再按时段例行问候或劝睡。每条目标10～50字，最多70字；过长、重复、工具指令或主动放弃的结果静默跳过，
不额外调用模型改写。最近话题不适合打扰时允许不发，不虚构用户行踪、热点或共同经历，不要求用户回复。
默认两次生成尝试之间至少6小时（`PROACTIVE_COOLDOWN_HOURS`，最小3小时），用户回复不会清除此冷却；
全体私聊每日合计最多6次尝试（`PROACTIVE_GLOBAL_DAILY_LIMIT`，0为全局暂停，最大20）。
全局额度在SQLite事务中预占；一次后台周期最多预占一次，包括失败或放弃，避免同时向多位用户集中搭话。
冷却、主题轮换、上次主动消息及未回复状态随正常重启保留，`/proactive off` 始终生效。

<a id="admin"></a>

## 本机管理页

启动机器人后，另开项目终端：

```powershell
python tools/manage.py
```

打开输出的地址，默认 [http://127.0.0.1:8080/manage](http://127.0.0.1:8080/manage)，输入终端显示的令牌。默认令牌保存在 `data/admin-token`；可用至少32字符的 `BOT_ADMIN_TOKEN` 覆盖。不要分享令牌。

- 查看最近50次聊天运行与图片任务、模型/发送阶段和消息明细。
- 查看服务返回的实际 token 用量；不估算货币费用，也不补算服务未返回的用量。
- 编辑最近200个用户角色状态的长期信息和人工阶段摘要，查看自动事件及来源并停用错误记录。
- 预览任务图片，手动重发已有图片到原机器人、原私聊或群聊，不重新生成。
- 导入、标记、查看和删除本地表情。

页面与接口限制本机回环访问，接口还校验管理令牌和浏览器 Origin。令牌不写入浏览器持久存储。QQ接口返回成功只代表协议端接受，不代表用户已读。

<a id="data"></a>

## 目录与数据

```text
bot.py                         启动 NoneBot 和 OneBot 适配器
characters/                    角色卡
src/plugins/chat_and_draw/
  __init__.py                  NoneBot 处理器和生命周期注册
  application.py              应用状态与可替换服务边界
  routing.py / commands.py     消息触发与命令处理
  chat_flow.py / lifecycle.py  聊天编排与后台维护
  database.py                 WAL、初始化与短事务写入协调
  group_chat.py               短期群上下文与持久化限频
  sessions.py                  用户/角色状态和会话锁
  memory.py                    带证据、时间和版本的自动记忆
  conversation.py              自然对话规则与收尾提示
  llm.py                       API Key 客户端、多模态消息与动作解析
  comfyui.py / workflow_api.json  工作流字段映射及生图后端选择
  image_api.py / drawing.py    云图片接口、摄影风格与角色外貌
  jobs.py / delivery.py        后台图片任务与独立发送
  runtime.py                   运行、消息、用量和任务记录
  sticker_library.py / stickers.py  本地及外部表情
  media.py / image_history.py  语音与图片历史缓存
  incoming_images.py           入站表情/图片地址解析与动图转码
  admin.py / manage.html      本机管理 API 与页面
  tavern.py                    角色卡及世界书
  config.py                   配置和路径
ref_audio/                     自备 TTS 参考音频
tools/                        API 配置检查与管理令牌工具
tests/                        离线回归测试
```

默认 SQLite 文件为 `data/sessions.sqlite3`。`sessions` 保存会话选择与开关，`role_states` 保存角色历史/记忆，`runs`、`run_events`、`messages`、`tasks` 保存运行记录，`event_memories` 保存自动记忆及历史修订。

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

<a id="troubleshooting"></a>

## 测试与排障

```powershell
python -m unittest discover -s tests -v
```

0.4.0 版本已通过198项离线测试，覆盖角色记忆隔离、旧数据迁移、并发队列、事件去重、API Key 配置、多模态消息、用量记录、ComfyUI / 云图片 HTTP 往返、摄影风格与角色外貌选择、Chirp / Genie 语音适配、HTTP 阶段诊断和重试边界、群聊上下文、并发限频、重启额度持久化、发送失败恢复以及管理接口访问限制。测试采用临时数据库、模拟 QQ 和本机 HTTP / ASGI 服务，不会调用真实模型、发送真实 QQ 消息或使用 GPU。

| 现象 | 检查方向 |
|---|---|
| 聊天超时，未出现 ComfyUI 接受记录 | 先检查 API Key、接口地址、模型 ID、代理和 `LLM_TIMEOUT` |
| ComfyUI 拒绝任务或无图 | 检查工作流模型、节点、字段映射；先手动运行工作流 |
| 图片已生成但 QQ 未收到 | 在管理页查看发送阶段和任务图片，修复协议端后重发 |
| 群聊没有回应 | 确认真正 @ 了机器人，OneBot WebSocket 已连接 |
| 语音失败 | 检查 GPT-SoVITS、参考音频路径与 OneBot 文件可访问性 |
| 表情包无法发送 | 检查本地情绪标签或外部 User-Agent/代理配置 |

真实部署仍需依次验收文字、角色切换、重启记忆、看图、语音、生成图片和群聊 @。本次升级见[0.4 升级说明](升级说明-0.4.md)，历史说明见[0.3](升级说明-0.3.md)和[0.2](升级说明-0.2.md)。

<a id="cloud-server"></a>

## 无 GPU 的云服务器

只运行聊天、看图、记忆和情景表情包时，在部署机器的 `.env.local` 中设置：

```dotenv
DRAW_ENABLED=false
TTS_ENABLED=false
```

默认均为 `true`，已有本机部署不受影响。关闭后会限制模型工具提示、过滤相关动作，并阻止绘图命令、后台队列和直接服务请求；语音请求改为文字发送。只停掉 ComfyUI 或 TTS 服务不等于关闭功能。独立视觉理解及已有表情包仍可用。

云端与本机分别保存配置、凭据和运行数据库；不要把本机的代理地址、音频路径或凭据文件直接提交到仓库。

表情包仅发送本地素材或 API 图片，不使用 QQ 内置表情作为回退。表情包获取或发送失败时保留文字回复，其他工具继续独立执行。

<a id="systemd"></a>

## systemd 后台管理

Ubuntu 部署模板见 `deploy/systemd/anima-bot.service`。按实际部署修改用户、HOME、项目目录和虚拟环境路径，然后安装：

```bash
sudo install -m 644 deploy/systemd/anima-bot.service /etc/systemd/system/anima-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now anima-bot.service
```

服务使用 `Restart=always`，进程退出或被杀后间隔5秒重新启动，服务器开机后自动启动。`systemctl stop anima-bot` 是管理员主动停止，不会被自动拉起；真正关机的云实例需要通过云平台开机，服务文件不能为关机实例通电。SSH 隧道和浏览器访问不属于此服务。

```bash
sudo systemctl status anima-bot --no-pager
sudo systemctl restart anima-bot
sudo systemctl stop anima-bot
sudo journalctl -u anima-bot -f
```


编排模块、SQLite事务协调及旧数据迁移说明见[架构与持久化边界](ARCHITECTURE.md)。

<a id="documentation"></a>

## 文档索引

| 文档 | 内容 |
|---|---|
| [架构说明](ARCHITECTURE.md) | 模块职责、数据库协调、会话与后台任务边界 |
| [角色选择用户指南](角色选择用户指南.md) | 角色选择、切换与记忆操作 |
| [0.4 升级说明](升级说明-0.4.md) | 当前版本变更与升级验证 |
| [0.3 升级说明](升级说明-0.3.md) / [0.2 升级说明](升级说明-0.2.md) | 历史版本说明 |
| [完整配置示例](.env.example) | 模型、生图、语音、群聊与主动问候配置 |
| [systemd 服务模板](deploy/systemd/anima-bot.service) | 开机启动、退出重启与进程管理 |

[返回顶部](#top)


### NapCat 群聊与互聊管理

NapCat → 扩展页面 → **AnimaBot 群聊与互聊**，可分别设置群聊参与和指定 bot 互聊的开关、冷却、接话窗口、轮次、每小时及滚动24小时尝试额度。群聊设置控制非 @ 参与，普通成员明确 @ 的回应继续保留；已知 bot 仍走专用名单与限额通道，不能绕过互聊限制。

保存后后续消息直接生效，无需重启；后台主动互聊在下次5分钟检查采用新设置。保存不清空已用额度、轮次或冷却时间戳，生成中关闭参与会取消发送。互聊每轮我方最多3条（含主动开场）；群聊接话轮次按同一成员统计跟进窗口内的回复。0额度暂停相应自动参与，失败或不参与也计入尝试次数。名单仍在插件原生配置页管理。

首次连接共享策略与权限配置见[插件说明](deploy/napcat/napcat-plugin-anima-peer-manager/README.md)。


昵称呼叫、正常接话、随机插话现在分为三套独立的每小时/24小时额度。随机插话用完不会阻止叫昵称或正在接话；后两者也不消耗随机插话额度。关闭随机插话不会关闭接话，直接接话按成员独立冷却，回复其他成员不会中断原成员的接话窗口。明确 @ 继续绕过这些自动参与额度，已知 bot 始终使用独立互聊限制。

NapCat 页面按三类展示；v1策略读取时迁移为v2，保留既有bot设置、开关与随机插话限制。保存v2后持久化新结构，不清空已有各模式调用计数。旧的 `GROUP_CHAT_HOURLY_LIMIT` / `GROUP_CHAT_GLOBAL_HOURLY_LIMIT` / `GROUP_CHAT_DAILY_LIMIT` / `GROUP_CHAT_GLOBAL_DAILY_LIMIT` 不再作为共用总闸；新字段见[配置示例](.env.example)。额度统计生成尝试，不能保证服务限流或网络异常时仍成功回应。


### NapCat 实时额度与未回复原因

「AnimaBot 群聊与互聊」页面展示昵称、接话、随机插话、bot互聊/主动开场的小时及滚动24小时已用、剩余次数，可按群和成员查看冷却倒计时、接话/互聊轮次与最近处理结果。每5秒从本机管理接口读取，不调用模型、不预占额度，不覆盖未保存的设置。倒计时基于服务器时间，日期在浏览器本地时区显示。

限额/冷却拦截、随机未抽中、模型明确返回“不参与”、空结果、模型超时/服务限流、QQ发送结果分别记录。只保留消息ID、群/成员ID、类型和原因，不记录聊天正文或凭据；历史最多3000条、24小时，页面显示最近200条中的筛选结果。上线前的未回复不能追溯，处理中断的记录会标注状态未结束，不推测为模型拒绝。

需要一次性配置 NapCat 侧私有状态连接文件，详见[插件说明](deploy/napcat/napcat-plugin-anima-peer-manager/README.md)。Bot离线或连接配置异常时页面明确显示状态不可用，不回退到零用量。


### 私聊主动聊天实时状态

NapCat → 扩展页面 → **AnimaBot 主动聊天**，显示每私聊及全局的今日已用/剩余次数、冷却倒计时、下次最早预计搭话时间，以及未到时间、额度用尽、上次未回复等原因。状态每5秒刷新，不调用模型，不重置计数或随机计划；预计时间不是发送保证。尝试次数包含失败与模型选择不打扰，按页面配置时区的自然日统计。

<a id="personas"></a>
### 人设库与QQ分配

NapCat → 扩展页面 → **AnimaBot 人设与用户分配**，选择内置模板或新建人设，填写名称和背景/性格后，停止输入约1秒自动保存为私有预设。下次可从列表直接选择、编辑或复制。再选择人设、填写用户QQ并保存分配，后续私聊、群聊与主动聊天即采用该设定，无需重启。

管理员分配优先于用户/switch；解除后恢复分配前人设。角色历史与记忆相互隔离，切回旧角色可恢复，编辑同一预设则延续其记忆。人设切换不会更换语音模型。首次连接 `BOT_PERSONA_CONFIG_FILE` 和共享文件权限见[插件说明](deploy/napcat/napcat-plugin-anima-peer-manager/README.md)。用户自建角色和QQ分配不提交到Git。
