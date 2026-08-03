# LLM 配置指南

Book2Skill 支持三种 LLM 模式，从离线到在线渐进增强。LLM 接入走 **OpenAI-compatible** 通用协议，不绑定特定厂商——支持 OpenAI / Azure / **阿里云百炼（DashScope）** / Ollama / vLLM / LM Studio 等任何兼容端点。`--llm openai` 与 `--llm compatible` 等价（后者为通用别名）。

| 模式 | 命令 | 需要 Key | 适用场景 |
|------|------|:--------:|----------|
| Mock（默认） | `--llm mock` | ❌ | 离线验证流程、CI/CD、无网络环境 |
| 云端兼容端点 | `--llm compatible` | ✅ | OpenAI / 阿里云百炼 / Azure 等云端模型 |
| 本地模型 | `--llm compatible --llm-base-url <url>` | ✅* | LM Studio / Ollama / vLLM 等本地部署 |

> *本地模型通常不校验 Key，但 Book2Skill 要求非空值才启用 LLM 模式。填任意字符串即可。

---

## 〇、一次性配置：`.env` 文件（推荐）

不想每次命令都重复传 `--llm` / `--llm-base-url` / `--llm-model`？在项目根目录放一个 `.env` 文件，配置一次后所有 `analyze` / `batch` 命令自动生效。API Key 只能通过环境变量或 `.env` 提供，不接受命令行参数，以免泄漏到 shell 历史或进程列表。

```bash
cp .env.example .env    # 从模板复制（.env 已被 .gitignore 排除，不会提交）
# 编辑 .env，按需取消注释并填值
```

`.env` 支持的变量（通用 `LLM_*` 为规范名，优先级高于旧版 `OPENAI_*`，两者均生效）：

| 变量 | 作用 | 云端示例 | 本地示例 |
|------|------|---------|---------|
| `BOOK2SKILL_LLM` | 默认适配器，免去每次 `--llm`；取 `mock`/`openai`/`compatible` | `compatible` | `compatible` |
| `BOOK2SKILL_LOCALE` | 人类可读 CLI/进度输出语言；`zh-CN`（默认）或 `en` | `en` | `zh-CN` |
| `LLM_API_KEY` | API Key | `sk-你的密钥` | `local` |
| `LLM_BASE_URL` | OpenAI 兼容端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `http://localhost:11434/v1` |
| `LLM_MODEL` | 模型名 | `qwen-plus` | `qwen2.5:7b` |

> 旧版 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` 仍生效，优先级低于 `LLM_*`。

一个本地部署的 `.env` 示例：

```ini
BOOK2SKILL_LLM=compatible
LLM_API_KEY=local
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
```

配置好后直接运行，无需任何 `--llm*` 参数：

```bash
book2skill analyze book.pdf --json > bundle.json
book2skill batch ./docs/ --json
```

> **查找规则**：Book2Skill 从当前目录向上逐级查找 `.env`，因此在子目录运行命令也能找到项目根的 `.env`。
> **优先级**：非敏感命令行参数（`--llm` / `--llm-model` / `--llm-base-url`）> 系统环境变量（`LLM_*` 优先于 `OPENAI_*`）> `.env` 文件 > 默认 Mock。API Key 只能从环境变量或 `.env` 读取；命令行参数可临时覆盖非敏感配置（如 `--llm mock` 跑离线）。
> **不污染环境**：`.env` 由内置解析器读取，不会写入进程环境变量或日志；密钥不进 git、不进日志。

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

## 二、云端兼容端点（OpenAI / 阿里云百炼 / Azure 等）

### 1. 安装可选依赖

```bash
pip install -e ".[llm]"
# 或单独安装
pip install openai
```

> `openai` 包是通用 OpenAI 兼容客户端，并非绑定 OpenAI 厂商——任何兼容端点（百炼/Azure/Ollama/vLLM）都通过它接入。

### 2. 配置 API Key（三选一）

> 首选「`.env` 一次性配置」（见上文第〇节）。下面三种方式适用于临时或单次场景。

**方式 A：环境变量**

```bash
# Linux/macOS/Git Bash
export LLM_API_KEY="sk-你的密钥"

# Windows PowerShell
$env:LLM_API_KEY = "sk-你的密钥"

# Windows CMD
set LLM_API_KEY=sk-你的密钥
```

**方式 B：写入 shell 配置（永久生效）**

```bash
# ~/.bashrc 或 ~/.zshrc
echo 'export LLM_API_KEY="sk-你的密钥"' >> ~/.bashrc
source ~/.bashrc
```

### 3. 使用

```bash
# OpenAI 云端，默认模型 gpt-4o
book2skill analyze book.pdf --llm compatible

# 指定模型
book2skill analyze book.pdf --llm compatible --llm-model gpt-4o-mini

# 阿里云百炼
book2skill analyze book.pdf \
  --llm compatible \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model qwen-plus

# 批处理
book2skill batch ./docs/ --llm compatible --llm-model gpt-4o
```

> `--llm openai` 与 `--llm compatible` 完全等价，`openai` 保留向后兼容。

### 阿里云百炼（DashScope）

百炼提供 OpenAI 兼容端点，直接用 `compatible` 适配器接入：

1. **获取 API-KEY**：登录 [百炼控制台](https://bailian.console.aliyun.com/) → 模型广场/API-KEY 管理 → 创建。
2. **选择模型**：百炼支持 `qwen-plus`、`qwen-max`、`qwen-turbo` 等（见控制台模型列表）。
3. **配置**（`.env` 一次性，推荐）：

```ini
BOOK2SKILL_LLM=compatible
LLM_API_KEY=sk-你的百炼key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

4. **验证**：

```bash
book2skill analyze book.pdf --json | python -m json.tool | grep confidence
# confidence 不再固定为 0.3/0.5/0.7/0.8 即说明百炼已生效
```

---

## 三、本地部署模式（LM Studio / Ollama / vLLM）

本地模型通过 OpenAI 兼容 API 接入，只需指定 `--llm-base-url`。

> 推荐把 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY=local` 写入 `.env`，避免每次命令重复传参。

### LM Studio

1. **安装 LM Studio**：https://lmstudio.ai/
2. **下载模型**：在 LM Studio 内下载所需模型（如 Llama 3.1、Qwen2 等）
3. **启动 Local Server**：在 LM Studio 的 "Local Server" 标签页，加载模型后点击 "Start Server"
4. **获取端口**：默认 `http://localhost:1234/v1`
5. **调用**：

```bash
book2skill analyze book.pdf \
  --llm compatible \
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
  --llm compatible \
  --llm-base-url http://localhost:11434/v1 \
  --llm-model qwen2.5:7b
```

### vLLM / 其他 OpenAI 兼容服务

```bash
book2skill analyze book.pdf \
  --llm compatible \
  --llm-base-url http://<host>:<port>/v1 \
  --llm-model <模型名>
```

---

## 四、安全注意事项

- **API Key 不会写入 git**：`.gitignore` 已排除 `.env`、`config.local.*` 等敏感文件，仅保留 `.env.example` 模板。
- **不污染进程环境**：`.env` 由内置解析器读取后用于本次解析，不写入 `os.environ`，避免泄漏到子进程或日志。
- **不记录正文和密钥**：Book2Skill 的日志默认不输出文档正文、提示词、密钥和模型原始响应。
- **Key 来源优先级**：`LLM_API_KEY` 系统环境变量 > `OPENAI_API_KEY` 系统环境变量 > `.env` 文件；Key 从不进入命令行参数。
- **降级机制**：当 `openai` 包未安装、API Key 为空、或 LLM 调用失败时，默认 fail-closed；只有显式传入 `--allow-llm-fallback` 才会回退 Mock。

---

## 五、验证 LLM 是否生效

```bash
# 查看 candidate_units 的 confidence 值
# Mock 模式：confidence 固定为 0.3/0.5/0.7/0.8（基于文本长度）
# LLM 模式：confidence 由模型动态评估

LLM_API_KEY=local book2skill analyze book.pdf --llm compatible --llm-base-url http://localhost:1234/v1 --json | python -m json.tool | grep confidence
```

如果 confidence 值不再是固定的 0.3/0.5/0.7/0.8，说明 LLM 已生效。
