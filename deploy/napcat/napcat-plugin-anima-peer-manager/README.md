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
