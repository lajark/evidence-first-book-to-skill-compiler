# 顶层配置契约

`config.example.yaml` 是可校验的顶层配置模板。它描述语言、数据根目录、资源限制、扩展注册表和允许的宿主；密钥仍只能来自环境变量或系统凭据，不写入 YAML。

校验配置（完全离线，不会启动 LLM 或网络请求）：

```bash
book2skill config validate config.example.yaml
book2skill config validate config.example.yaml --json
```

契约使用 `schema_version: 1`，未知字段、未知宿主、重复版本和不完整的云端 LLM 配置会被拒绝。相对路径按配置文件所在目录解析，便于从其他工作目录调用校验命令。

处理命令可显式加载配置：

```bash
book2skill --config config.example.yaml analyze input/notes.txt --llm mock
```

`--config` 只在主动提供时生效：CLI 参数优先，其次是环境变量和 `.env`，最后才使用 YAML 中的 locale、默认 `data_home` 和启用的云端 LLM 参数。默认配置保持离线；云端 LLM 必须同时声明 `network: enabled`，凭据仍来自环境变量。
