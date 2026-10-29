<a id="top"></a>

# AnimaBot · QQ 角色聊天机器人

<p>
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/License-MIT-green?style=flat-square"></a>
  <a href="https://github.com/imprprpr/QQ_Bot/actions/workflows/test.yml"><img alt="tests" src="https://github.com/imprprpr/QQ_Bot/actions/workflows/test.yml/badge.svg"></a>
  <a href="https://www.python.org/"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white"></a>
  <img alt="OpenAI-compatible API" src="https://img.shields.io/badge/模型-任意_OpenAI_兼容接口-412991?style=flat-square">
</p>

让 QQ 小号变成一个有性格、记得你、会在群里自然接话的角色。基于 NoneBot2 + OneBot V11（NapCat），当前版本 **0.5.0**。

- **随便换角色**：直接使用 SillyTavern 角色卡，每个用户和每个角色的记忆分开保存，切回去还记得。
- **随便换模型**：DeepSeek、Kimi、智谱、通义、硅基流动、Gemini、OpenRouter，或者本机的 Ollama，像切换线路一样一键切换。
- **会聊天，不刷屏**：被 @ 一定回复，叫名字会回应，平时低频插话；私聊偶尔主动找你。
- **可选扩展**：看图、生图（ComfyUI 或云 API）、语音（GPT-SoVITS / Genie / 云 TTS）、情景表情包。

<a id="quick-start"></a>

## 5 分钟上手

你需要准备两样东西：

1. **一个 QQ 小号**，用 [NapCat](https://napneko.github.io/guide/start-install) 登录（它负责收发 QQ 消息）。
2. **一个聊天模型的 API Key**。不知道选哪家就用 DeepSeek：国内直连、便宜、中文好。

| 提供商 | 适合 | 获取 Key |
|---|---|---|
| DeepSeek ★ | 大多数人；国内直连、便宜 | [platform.deepseek.com](https://platform.deepseek.com/api_keys) |
| 硅基流动 ★ | 想一个 Key 试多家模型 | [cloud.siliconflow.cn](https://cloud.siliconflow.cn/account/ak) |
| Kimi / 智谱 / 通义 / 豆包 | 已经在用这些平台 | 配置向导里会给出链接 |
| Gemini / OpenAI / OpenRouter | 有海外网络 | 配置向导里会给出链接 |
| Ollama / LM Studio | 免费、离线，在自己电脑上跑模型 | 不需要 Key |

### Windows

1. 下载本项目：点页面上方绿色的 **Code → Download ZIP**，解压到一个**不含中文和空格**的文件夹（例如 `D:\AnimaBot`）。
2. 双击 **`一键启动机器人.bat`**。
   - 没装 Python 会自动打开下载页，安装时勾选 **Add python.exe to PATH**，装好后再双击一次。
   - 第一次会自动安装依赖（1～3 分钟），然后打开**配置向导**：选提供商 → 粘贴 API Key → 自动列出模型并推荐一个 → 发一条测试消息 → 选角色。
3. 按向导最后的提示，在 NapCat 里添加一个连接（见下方[连接 NapCat](#napcat)），然后在 QQ 里给机器人发 `/help`。

以后每次使用都只要双击 `一键启动机器人.bat`。

### Linux / macOS

```bash
git clone https://github.com/imprprpr/QQ_Bot.git AnimaBot
cd AnimaBot
./start.sh          # 自动建环境、装依赖，第一次会进入配置向导
```

想开机自启：先运行一次 `./start.sh`，再执行 `sudo deploy/systemd/install.sh`。

### Docker（含 NapCat）

```bash
git clone https://github.com/imprprpr/QQ_Bot.git AnimaBot && cd AnimaBot
cp .env.example .env
docker compose run --rm animabot python tools/setup_wizard.py --docker
docker compose up -d
```

然后打开 `http://127.0.0.1:6099/webui`（登录令牌用 `docker compose logs napcat` 查看），扫码登录 QQ，再按下面的步骤添加连接，URL 中的 `127.0.0.1` 换成 `animabot`。

<a id="napcat"></a>

## 连接 NapCat

NapCat 登录 QQ 后，在它的网页后台：

1. **网络配置 → 新建 → WebSocket 客户端**（也叫“反向 WebSocket”）。
2. **URL**：`ws://127.0.0.1:8080/onebot/v11/ws`（端口按向导里设置的填写）。
3. **Token**：填配置向导最后显示的那串字符（也可以在 `.env` 的 `ONEBOT_ACCESS_TOKEN` 里找到）。
4. 启用并保存。机器人窗口出现 `connected` 字样就说明连上了。

## 切换模型

三种方式，选顺手的：

- **本机管理页（推荐）**：机器人运行时打开 `http://127.0.0.1:8080/manage`，令牌用 `python tools/manage.py` 查看（Windows 用 `.venv\Scripts\python.exe tools\manage.py`）。在「模型提供商」里添加几个提供商，以后点「启用」即可切换，还能一键测试每个线路的延迟。不用重启。
- **重新运行配置向导**：`python tools/setup_wizard.py`，会修改 `.env` 并备份旧文件。
- **NapCat 扩展页**：安装仓库自带的 [NapCat 插件](deploy/napcat/napcat-plugin-anima-peer-manager/README.md) 后，在 NapCat 里管理。

海外服务（Gemini、OpenAI、OpenRouter）在国内通常需要代理：在 `.env` 里设置 `LLM_PROXY=http://127.0.0.1:7890`（换成你的代理端口）。

## 常用命令

私聊直接发；群里需要先 @ 机器人。

| 命令 | 作用 |
|---|---|
| `/help` | 查看帮助 |
| `/cards`、`/switch 名字` | 查看角色、切换角色（各角色记忆分开保存） |
| `/remember 内容`、`/memory`、`/forget 序号` | 让角色记住、查看、删除长期信息 |
| `/reset` | 清空当前对话记忆 |
| `/proactive on` / `off` | 开关私聊主动聊天 |
| `/draw 画面描述` | 直接生图（需开启生图） |

## 添加自己的角色

把 SillyTavern 角色卡（`.json` 或带角色数据的 `.png`）放进 `characters` 文件夹，在 QQ 里发 `/cards` 就能看到。
想让生图时外貌稳定，可以在角色卡 `data` 里加一项 `"draw_tags": "1boy, short black hair, glasses"` 这样的英文标签。
详见 [角色选择用户指南](角色选择用户指南.md)。

## 进阶功能

这些都需要额外的服务，准备好后在 `.env` 里打开对应开关。详细步骤见 [进阶文档](docs/advanced.md)：

- [看图](docs/advanced.md#model-api)、[生图（ComfyUI / 云 API）](docs/advanced.md#image-generation)、[语音与表情包](docs/advanced.md#voice-stickers)
- [群聊参与节奏](docs/advanced.md#group-chat)、[私聊主动聊天](docs/advanced.md#proactive)、[和其他机器人斗嘴](docs/advanced.md#bot-interaction)
- [人设库与按 QQ 分配](docs/advanced.md#personas)、[自动记忆](docs/advanced.md#memory)、[本机管理页](docs/advanced.md#admin)
- [全部配置项](docs/config-reference.env)、[架构说明](ARCHITECTURE.md)

## 常见问题

**机器人不回消息？** 先看机器人窗口有没有 `connected`。没有：检查 NapCat 里的 URL、端口和 Token 是否与向导显示的一致。有但不回：群里要真正 @ 它；看窗口里的报错。

**提示 API Key 无效 / 余额不足？** 重新运行配置向导或在管理页「模型提供商」里点「测试」，它会用中文说明是密钥、余额、地址还是网络的问题。

**连接超时？** 海外服务在国内需要代理，见上方 `LLM_PROXY`。本机模型要先启动 Ollama / LM Studio。

**依赖安装很慢或失败？** 启动脚本失败时会自动改用清华镜像重试；仍失败请检查网络后再双击一次。

**端口 8080 被占用？** 重新运行配置向导换一个端口，并同步修改 NapCat 里的 URL。

**怎么更新？** 用 Git 的执行 `git pull`；下载 ZIP 的用户下载新版覆盖（保留 `.env`、`data` 文件夹和自己的角色卡），然后照常启动，依赖会自动更新。

**从 0.4 升级要注意什么？** 见 [0.5 升级说明](升级说明-0.5.md)。

## 开发与测试

```bash
python -m unittest discover -s tests                          # Python（离线，不调用真实模型和 QQ）
cd deploy/napcat/napcat-plugin-anima-peer-manager && npm test  # 管理页面与 NapCat 插件
```

每次推送都会在 GitHub Actions 中于 Windows 和 Linux 上运行全部测试并构建 Docker 镜像。修改管理页面的公共样式后运行 `npm run build`。

## 许可证

[MIT](LICENSE)。角色卡、音色和参考音频请确认你有权使用。
