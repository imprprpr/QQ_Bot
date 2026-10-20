# TTS 参考音频

将你有权使用的参考音频放在此目录，或填写 GPT-SoVITS 服务可访问的其他路径。
音频不随仓库分发，也不上传到 Git。

在本地 `.env` 配置 `TTS_REF_AUDIO`、`TTS_PROMPT_TEXT` 和 `TTS_PROMPT_LANG`。
参考音频路径由 GPT-SoVITS 服务读取；跨机器部署时应使用该服务所在机器的有效路径。
