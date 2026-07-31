# LLM 配置指南

Book2Skill 支持三种 LLM 模式，从离线到在线渐进增强：

| 模式 | 命令 | 需要 Key | 适用场景 |
|------|------|:--------:|----------|
| Mock（默认） | `--llm mock` | ❌ | 离线验证流程、CI/CD、无网络环境 |
| OpenAI 云端 | `--llm openai` | ✅ | 调用 GPT-4o 等云端模型 |
| 本地模型 | `--llm openai --llm-base-url <url>` | ✅* | LM Studio / Ollama / vLLM 等本地部署 |

> *本地模型通常不校验 Key，但 Book2Skill 要求非空值才启用 LLM 模式。填任意字符串即可。

---

## 一、Mock 模式（默认，离线）

无需任何配置，开箱即用：

```bash
book2skill analyze book.pdf
book2skill batch ./docs/
book2skill build book.pdf --name my-skill --description "..." --use-when "..."
```

Mock 适配器使用关键词启发式检测 principle/technique/term/case，适合验证管线流程。真正的内容理解需切换到 LLM 模式。

---

## 二、OpenAI 云端模式

### 1. 安装可选依赖

```bash
pip install -e ".[llm]"
# 或单独安装
pip install openai
```

### 2. 配置 API Key（三选一）

**方式 A：环境变量（推荐）**

```bash
# Linux/macOS/Git Bash
export OPENAI_API_KEY="sk-你的密钥"

# Windows PowerShell
$env:OPENAI_API_KEY = "sk-你的密钥"

# Windows CMD
set OPENAI_API_KEY=sk-你的密钥
```

**方式 B：命令行参数**

```bash
book2skill analyze book.pdf --llm openai --llm-api-key "sk-你的密钥"
```

**方式 C：写入 shell 配置（永久生效）**

```bash
# ~/.bashrc 或 ~/.zshrc
echo 'export OPENAI_API_KEY="sk-你的密钥"' >> ~/.bashrc
source ~/.bashrc
```

### 3. 使用

```bash
# 默认模型 gpt-4o
book2skill analyze book.pdf --llm openai

# 指定模型
book2skill analyze book.pdf --llm openai --llm-model gpt-4o-mini

# 批处理
book2skill batch ./docs/ --llm openai --llm-model gpt-4o
```

---

## 三、本地部署模式（LM Studio / Ollama / vLLM）

本地模型通过 OpenAI 兼容 API 接入，只需指定 `--llm-base-url`。

### LM Studio

1. **安装 LM Studio**：https://lmstudio.ai/
2. **下载模型**：在 LM Studio 内下载所需模型（如 Llama 3.1、Qwen2 等）
3. **启动 Local Server**：在 LM Studio 的 "Local Server" 标签页，加载模型后点击 "Start Server"
4. **获取端口**：默认 `http://localhost:1234/v1`
5. **调用**：

```bash
book2skill analyze book.pdf \
  --llm openai \
  --llm-api-key local \
  --llm-base-url http://localhost:1234/v1 \
  --llm-model <LM Studio 中显示的模型名>
```

### Ollama

1. **安装 Ollama**：https://ollama.ai/
2. **拉取模型**：`ollama pull qwen2.5:7b`
3. **启动服务**：Ollama 默认监听 `http://localhost:11434/v1`
4. **调用**：

```bash
book2skill analyze book.pdf \
  --llm openai \
  --llm-api-key local \
  --llm-base-url http://localhost:11434/v1 \
  --llm-model qwen2.5:7b
```

### vLLM / 其他 OpenAI 兼容服务

```bash
book2skill analyze book.pdf \
  --llm openai \
  --llm-api-key <你的key或local> \
  --llm-base-url http://<host>:<port>/v1 \
  --llm-model <模型名>
```

---

## 四、安全注意事项

- **API Key 不会写入 git**：`.gitignore` 已排除 `.env`、`config.local.*` 等敏感文件。
- **不记录正文和密钥**：Book2Skill 的日志默认不输出文档正文、提示词、密钥和模型原始响应。
- **Key 来源优先级**：`--llm-api-key` 参数 > `OPENAI_API_KEY` 环境变量 > 降级到 Mock。
- **降级机制**：当 `openai` 包未安装、API Key 为空、或 LLM 调用失败时，自动降级到 Mock 适配器，管线不会中断。

---

## 五、验证 LLM 是否生效

```bash
# 查看 candidate_units 的 confidence 值
# Mock 模式：confidence 固定为 0.3/0.5/0.7/0.8（基于文本长度）
# LLM 模式：confidence 由模型动态评估

book2skill analyze book.pdf --llm openai --llm-api-key local --llm-base-url http://localhost:1234/v1 --json | python -m json.tool | grep confidence
```

如果 confidence 值不再是固定的 0.3/0.5/0.7/0.8，说明 LLM 已生效。
