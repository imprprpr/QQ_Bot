# AnimaBot 群号与QQ批量管理

NapCat 原生配置面板插件，管理 **AnimaBot 与其他机器人的互动名单**。
可读取当前QQ好友与群列表、多选、批量追加、批量移除；支持换行、英文/中文逗号或空格分隔。
非好友的机器人可在“批量追加QQ号”中输入。取消勾选可以删除，批量移除优先于追加。
保存后追加/移除输入框会清空，重新打开配置查看最终列表。

插件不发送消息、不加好友、不加群、不踢人，不读取 AnimaBot API 密钥或 WebUI token。
所有配置操作使用 NapCat 自带的登录鉴权；没有新增免鉴权 HTTP 接口。
号码和配置文件不随源码分发。NapCat 插件的加载开关控制管理界面；
暂停互动请使用本插件配置里的“启用指定机器人互动”开关。原有 AnimaBot 冷却、轮次、额度持久化机制继续生效。

## 安装条件

需要支持第三方插件的 NapCat 版本，提供 `plugin_init`、`plugin_get_config`、
`plugin_set_config` 和 `plugin_config_ui` 接口。
若加载器报告 `not in official plugin whitelist`，复制文件无法启用本插件。
本项目不自动修改 NapCat 加载器或冒用官方插件名称；需由部署者决定安装版本与放行策略。

## 安装与连接

1. 将整个 `napcat-plugin-anima-peer-manager` 文件夹放入 NapCat `plugins` 目录。
2. 插件保存文件为 NapCat `config/plugins/napcat-plugin-anima-peer-manager/config.json`。
   容器部署时，AnimaBot 必须能通过宿主机挂载路径读取同一文件。
3. 首次接入前，在 AnimaBot 项目目录执行下列命令，将已有 `.env.local` 名单转换成初始配置：

   ```bash
   python tools/seed_peer_config.py --output /宿主机/NapCat/config/plugins/napcat-plugin-anima-peer-manager/config.json
   ```

   脚本拒绝覆盖已存在的配置，不修改 `.env.local`，不打印号码或凭据。
4. 在 AnimaBot `.env.local` 设置 `BOT_INTERACTION_CONFIG_FILE` 为上面的宿主机绝对路径，
   并保留 `BOT_INTERACTION_ENABLED=true` 总开关，重启 AnimaBot 一次。
5. 打开 NapCat WebUI → 插件管理 → **AnimaBot 群号与QQ批量管理** → 配置。
   选择群和QQ号，或批量粘贴后保存。此后名单和插件开关热更新，不必重启机器人。

任一名单为空时不会触发互动。读取异常时关闭互动；删除或禁用的已知机器人继续被隔离，
不能退回不受专用轮次限制的普通 @ 通道。保留 `.env.local` 旧名单用于异常时识别旧机器人；
配置共享文件启用后，旧名单不会覆盖插件选出的有效互动名单。

## 验证

```bash
node --test deploy/napcat/napcat-plugin-anima-peer-manager/test.mjs
python -m unittest discover -s tests -p test_peer_config.py
```

参考：[NapCat 官方插件配置文档](https://napneko.github.io/develop/plugin/start-webui)。

## 模型提供商页面（1.1.0）

NapCat 的“扩展页面”中打开“AnimaBot 模型提供商”。支持 OpenAI Chat Completions 兼容接口；预设可自动填写地址，
填写密钥后拉取 `/models`，选择或手动输入模型 ID，再点击“保存并启用”。支持多个配置及恢复部署默认。
列表可能包含非聊天模型，列出模型不保证账号有聊天权限。Anthropic 原生协议、订阅登录等不属于兼容接口。

接口使用 NapCat WebUI 登录鉴权，密钥不通过读取接口回显。数据保存到 `ctx.configPath + '.models.json'`，
权限为0640，不在静态资源目录。Bot 配置 `BOT_MODEL_CONFIG_FILE` 为此文件的宿主机路径后，每次请求读取，
新请求即时生效，正在进行的请求保留原配置；配置损坏时拒绝调用，不自动切换到另一家付费接口。
共享目录应只允许 NapCat 与 Bot 的运行用户访问；两者用户不同时，为目录设置专用共享组与 setgid，
确保 Bot 能读取0640文件。不要公开此文件或加入 Git。恢复部署默认仅取消页面覆盖，不修改原环境配置。

提供商说明：[NapCat 页面和鉴权路由](https://napneko.github.io/develop/plugin/start-webui)、
[DeepSeek](https://api-docs.deepseek.com/)、[硅基流动](https://docs.siliconflow.cn/docs/userguide/quickstart)、
[OpenRouter](https://openrouter.ai/docs/api/api-reference/models/get-models)、[Gemini兼容接口](https://ai.google.dev/gemini-api/docs/openai)。


## 主动聊天页面（1.2.0）

NapCat → 扩展页面 → AnimaBot 主动聊天，可控制私聊主动聊天的总开关、UTC偏移、1至8个时间段、闲置时间（1至24小时）、冷却（3至48小时）、每私聊每日次数（0至3）和全部私聊每日次数（0至20）。0次表示暂停，失败与主动选择不打扰也计入尝试次数。时间段不重叠，跨午夜拆开填写，结束允许24:00。

页面保存到 `ctx.configPath + '.proactive.json'`。初次部署将现有 PROACTIVE_* 环境配置写入该文件，并为 Bot 设置 `BOT_PROACTIVE_CONFIG_FILE` 为相应宿主机路径，重启一次。共享目录权限要求同模型提供商页面。未接入共享文件的独立部署仍使用原环境变量；已接入但文件损坏或丢失时暂停主动聊天。

后续保存无需重启，下一次5分钟后台检查采用新设置并重新安排时间，不会立即发送消息。每个用户的独立开关、未回复状态、冷却与已用额度继续保留。生成途中配置发生变化会取消该条发送，仍保留已用生成额度。鉴权使用 NapCat 登录，不新增免鉴权写接口。

```bash
node --test deploy/napcat/napcat-plugin-anima-peer-manager/*.test.mjs
python -m unittest discover -s tests -p test_proactive.py
```


## 群聊与互聊页面（1.4.0）

扩展页面 → AnimaBot 群聊与互聊，分别管理非 @ 群聊参与与指定 bot 互聊：开关、自主插话开关、冷却、接话窗口、轮次、概率、每小时和滚动24小时尝试次数。bot 每轮硬上限3条（含开场），互聊的群和QQ仍使用原生名单页，名单页的互动开关也需开启。普通成员的明确 @ 不受自动参与开关和额度限制。

首次安装时先从旧配置生成共享文件：

```bash
python tools/seed_participation_config.py --output /宿主机/NapCat/config/plugins/napcat-plugin-anima-peer-manager/config.json.participation.json
```

将 `BOT_PARTICIPATION_CONFIG_FILE` 指向这个宿主机路径，并重启 Bot 一次。共享文件及目录权限同模型提供商页面（文件0640、共享组、目录setgid），不要放入静态目录。脚本拒绝覆盖已有文件。未配置共享文件时沿用环境变量，接入后读取失败时暂停非 @ 参与和互聊，已知 bot 隔离名单继续保留。

之后保存立即影响后续群消息和轮次判断，无需重启；后台主动互聊在下次检查采用。降低轮次会取消尚未发送且已超出新上限的回复。冷却用原有时间戳与新间隔计算，互聊窗口长度仅影响新一轮，不借保存重置已用额度和轮次。


### 三类群聊额度（v2策略）

昵称呼叫、正常接话和随机插话分别设置每群/全群的每小时及滚动24小时额度，不再经过共用24次总闸。昵称和接话的冷却按成员计算；同一成员的接话轮次按窗口统计，每次实际回复刷新窗口，回复其他成员不挤掉原成员。随机插话的开关不再控制正常接话。

旧v1文件经过严格校验后读取为v2；旧共用每小时/每日限制仅迁移到随机插话，不会继续限制直接聊天。昵称和接话使用新的独立预算。原计数表按模式查询，旧尝试计数仍保留；新增按成员的轮次/冷却表不迁移不可归属的旧公共冷却。页面保存v2文件，无需人工改JSON；bot互聊名单和限额不受影响。
