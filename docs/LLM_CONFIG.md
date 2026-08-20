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
| `BOOK2SKILL_LLM_MAX_CONCURRENT_REQUESTS` | 同时在途请求上限；云端默认 `2` | `2` | `1`–`2` |
| `BOOK2SKILL_LLM_REQUESTS_PER_MINUTE` | 请求启动速率；云端默认 `15` | `15` | `30` |
| `BOOK2SKILL_LLM_TOKENS_PER_MINUTE` | 可选的输入 Token 滚动窗口上限；留空不启用 | 留空 | 按供应商配额 |
| `BOOK2SKILL_LLM_REQUEST_TIMEOUT_SECONDS` | 单请求超时秒数 | `180` | `60` |
| `BOOK2SKILL_LLM_STREAMING` | 单 provider 是否请求流式响应；`true`/`false` | `true` | `true` |

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
book2skill analyze input/book.pdf --json
book2skill batch ./docs/ --json
```

> **查找规则**：Book2Skill 从当前目录向上逐级查找 `.env`，因此在子目录运行命令也能找到项目根的 `.env`。
> **优先级**：非敏感命令行参数（`--llm` / `--llm-model` / `--llm-base-url`）> 系统环境变量（`LLM_*` 优先于 `OPENAI_*`）> `.env` 文件 > 默认 Mock。API Key 只能从环境变量或 `.env` 读取；命令行参数可临时覆盖非敏感配置（如 `--llm mock` 跑离线）。
> **不污染环境**：`.env` 由内置解析器读取，不会写入进程环境变量或日志；密钥不进 git、不进日志。

### 长书的受控并行

云端模式默认最多 2 个在途请求、每分钟最多启动 15 个请求；这是为普通
账户和兼容端点准备的保守起点，而非对供应商配额的保证。遇到 `429` 或超时
时，应先降为 `1` 并发并按供应商的 `Retry-After`/控制台配额调整，切勿为缩短
单次任务而盲目提高并发。

若供应商同时提供 TPM（Tokens Per Minute）配额，可设置
`BOOK2SKILL_LLM_TOKENS_PER_MINUTE`。运行时按请求输入内容估算 Token，在
60 秒滚动窗口中预留额度；单个请求超过窗口上限会立即 fail-closed，不会
通过重试或 Mock 回退绕过配额。多 provider profile 可在各 profile 中使用
`tokens_per_minute` 覆盖该限制。

对于 EPUB，Book2Skill 保留 spine 章节边界，以约 1,200 Token 的限长证据卡 →
章节综合 → 全书综合处理。综合阶段只传递前一层的结构化卡片，不会重复上传整章原文；
每个 Map/Reduce 请求均按内容寻址缓存，重跑会复用已完成的结果。

### 可选流式响应（OPT-P2-05）

设置 `BOOK2SKILL_LLM_STREAMING=true`，或在 profile 中为单个通道设置
`streaming: true`。流式响应会在审计中记录首 Token 延迟、输出 Token 数和
Token/s；只有完整拼接并通过结构化 JSON 校验后，请求才算完成。若兼容端点明确
拒绝 `stream=true`（常见为 400/404/405/422），该请求自动重试一次非流式模式；
其他网络错误或不完整 JSON 仍 fail-closed，不会以部分输出伪造完成。

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

---

## 六、多 provider profile（P1）

当你有多个云模型 API Key，希望按角色/成本/数据策略分别使用时，用 profile 集合代替单 provider 配置。这是 `balanced`/`quality` 多通道路由（OPT-P1-09/10）的前置契约；当前阶段（P1-08）可先用 `--llm-profile` 选定单通道运行。

```bash
cp llm-profiles.example.yaml llm-profiles.local.yaml   # 复制模板（已 .gitignore）
# 编辑 llm-profiles.local.yaml，按需填 profile
book2skill analyze book.pdf \
  --llm-profiles llm-profiles.local.yaml \
  --llm-profile fast-channel

# `balanced` 策略：把 Map 块并发放到多个可用通道，Reduce/Synthesis 用强模型
book2skill analyze book.pdf \
  --llm-profiles llm-profiles.local.yaml \
  --llm-strategy balanced
```

### YAML 结构

```yaml
schema_version: 1
default_profile: strong-model      # 综合阶段默认强模型；必须等于某 profile_id
profiles:
  - profile_id: fast-channel        # 小写字母/数字/连字符，全局唯一
    provider: openai                # openai = 任意 OpenAI 兼容端点
    model: deepseek-v4-flash-0731
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: LLM_API_KEY        # 环境变量名，不是密钥本身
    roles: [map]                    # 该通道允许承担的角色
    max_concurrent_requests: 2
    requests_per_minute: 15
    request_timeout_seconds: 180
    streaming: false
    cost_per_million_input_tokens: 0.14
    data_send_policy: original_text_allowed
    allowed_material_categories: [licensed_book, public_doc]
```

### 角色与数据策略

- **角色**：`map`、`section_reduce`、`book_reduce`、`synthesis`、`skill`、`critic`、`arbiter`。路由器只在 `roles` 列表内的通道上分配对应工作。
- **`data_send_policy`**：
  - `original_text_allowed`：可接收整段原文（默认）。
  - `evidence_cards_only`：只接收结构化证据卡，不接收原文（强模型综合推荐）。
  - `no_send`：禁止向该通道发送任何内容（仅作占位/审计）。
- **`allowed_material_categories`**：省略=不限；否则源类别（`licensed_book`/`internal_doc`/`public_doc`/`user_provided`）不在列表内时 fail-closed，路由器不会把该资料交给此通道。

### 安全契约

- **API Key 只写环境变量名**：`api_key_env` 是变量名（如 `LLM_API_KEY`），密钥从进程环境变量运行时读取；若进程环境缺省，`.env` 文件提供兜底（shell 环境变量优先于 `.env`，与单 provider 优先级一致）。密钥从不写入 YAML、manifest、cache 或日志。多个 profile 可共用同一 `api_key_env`（指向同一把 key），也可各指一个不同变量名（多把 key）。
- **禁止密钥字段**：profile 模型为 `extra="forbid"`，YAML 中出现 `api_key: sk-...`、`secret`、`token` 等字段会被立即拒绝。
- **凭据缺失 fail-closed**：选定真实 provider 的 profile 但对应环境变量未设置时，`LLMUnavailableError`，不静默回退 Mock（除非显式 `--allow-llm-fallback`）。
- **审计脱敏**：`AnalysisRunManifest` 记录 `profile_id` 与 `data_send_policy`，不记录 key、端点 URL 全文、prompt 或正文。

### 与现有 `.env` 单 provider 的关系

- 不使用 `--llm-profiles` 时，CLI 完全走原有 `--llm`/`.env` 单 provider 路径，向后兼容。
- `.env` 单 provider 等价于一个 `profile_id: default` 的单 profile 集合；如需多通道，迁移到 YAML profile 集合即可。
- 缓存键已纳入 `profile_id` 与路由策略版本：不同 profile/策略对同一输入不会误复用缓存结果。

### 动态通道激活（预留插槽 + 按可用密钥调整）

`ProviderProfileSet.active(env_file=None)` 返回只含「密钥可解析」profile 的新集合（mock 恒激活；shell 环境变量优先于 `.env`）。模板可预留 N 个通道插槽，实际参与路由的通道数 = 有密钥的插槽数，后续新增 key 到环境/.env 即可自动增通道，无需改 YAML。**默认 fail-closed**：默认通道密钥缺失时报错；`benchmark_abc.py --skip-missing` 显式开启动态跳过。

### `balanced` 路由策略（OPT-P1-09）

- 每个 Map 块只路由到一个 `roles` 含 `map` 且 `accepts_material` 通过的通道；选择按角色资格、EWMA 延迟、当前负载与估算成本加权，熔断通道权重极大。Reduce/Synthesis/Skill 优先落到配置的强模型（`default_profile`）。
- 全局并发受硬上限约束（默认 `min(各通道并发之和, 8)`），防止并行通道无限花费；另有 `BOOK2SKILL_LLM_MAX_TOTAL_CALLS` / `BOOK2SKILL_LLM_MAX_TOTAL_COST` 硬预算，触及即停止新调用。
- 结果按输入顺序返回，与完成顺序无关；通道失败自动重入队到其他 eligible 通道一次，无通道则 fail-closed。
- 每次选择记录脱敏 `RoutingDecision`（profile_id、role、strategy_version、候选池），manifest 汇总各通道调用并记录 `profile_id`；不记录 key、prompt 或正文。
- 进度/P2 阶段：`balanced` 模式暂不提供逐通道 ETA（`--llm-strategy single` 有）；JSON 契约与 `--json` stdout 纯净不变。

### `quality` 选择性多模型复核（OPT-P1-10）

- 默认关闭：不传 `--quality` 时零额外 LLM 调用，`quality_review` 为 `null`。
- 本地质量门（无 LLM）标记低置信、冲突、覆盖不足、异常候选；仅这些候选进入复核。`--quality-critic-profile` / `--quality-arbiter-profile` 指定 Critic/Arbiter 通道（默认取首个 `critic` 角色与 `default_profile`）。
- Critic 匿名：只看到 `EvidenceCard`（脱敏模型 id，无 prompt/端点/第一轮模型身份）；Arbiter 基于来源证据裁决，不用多数票；每个 `ReviewPatch` 的 `source_refs` 必须是候选原有来源的子集（来源回放），未知引用被拒。
- `--quality-mode maximum` 实验模式默认关闭：必须显式 `--quality-authorization` 与 `--quality-budget-calls`/`--quality-budget-cost` 硬预算，否则拒绝启动；触及预算即停止新调用并报告 `budget_exceeded`，不静默丢弃。
- 仅 Analyze 单命令启用；Build/Update/Batch 不跑质量复核（人工审核在编译前）。
- `QualityGate.detect` 可带 `benchmark_slots`（benchmark_abc/slots/*.yaml）触发 `benchmark_gap` 标记：默认按 kind + 关键词 + 来源覆盖检测；槽位声明 `structure_keywords` 时，同时纳入章节标题/结构预览证据，本地无 LLM 调用。

---

## 七、A/B/C 基准验证（OPT-P1-11）

三步基准比较 `single`、`balanced`、`quality` 在《孙子兵法》《番茄工作法图解》《微习惯》上的质量—速度—费用，不预设线性加速或固定质量提升。`balanced`/`quality` 需要 ≥2 个不同通道的 provider profile。

### 阶段一：离线 Mock（无网络、无成本）

```bash
python scripts/benchmark_abc.py --mock --output-dir workspace/benchmarks/abc-offline/
```

输出 `single.md` / `balanced.md` / `quality.md` / `summary.md`（脱敏，无 key/端点/prompt/正文）。`benchmark_score` 默认保持 `null`，不会由槽位覆盖启发式自动推断。人工或独立评测师按 `Skill提炼质量评估基准/` 输出带 `book_id`、`strategy`、`benchmark_id`、`evaluator`、`evaluation_date`、`confidence`、`final_score` 的 JSON 后，可用 `--evaluations <file-or-dir>` 显式回填匹配的报告记录；不匹配或重复记录会 fail-closed。

### 阶段二：真实多云（需多 provider profile + 数据发送授权）

```bash
cp llm-profiles.example.yaml llm-profiles.local.yaml   # 模板预留 5 个通道插槽
python scripts/benchmark_abc.py --profiles llm-profiles.local.yaml \
  --skip-missing \
  --output-dir workspace/benchmarks/abc-<timestamp>/
```

- 模板 `llm-profiles.example.yaml` 预留 5 个完整通道插槽（`channel-1`..`channel-5`，model/base_url/api_key_env/roles 齐全）。`--skip-missing` 时只激活 `api_key_env` 有密钥的插槽，实际通道数 = 有密钥数；默认不加该参数为 fail-closed（缺密钥报错）。
- 默认按 `--strategy all` × `--book all` 跑 3×3=9 次；可用 `--strategy single` / `--book pomodoro` 缩小范围。
- 记录：wall time、调用/缓存/重试/失败/回退计数、输入 Token 估算、按 profile 的估算费用、来源覆盖、基准槽位覆盖与缺口、profile_ids。
- 连续策略共享同一 `data_home` 缓存，后跑策略会命中内容寻址缓存（`cache_hit_count` 反映真实复用）。
- 扫描件（如《孙子兵法》无 OCR）按 `GATE_DAMAGED_FILE` fail-closed，记录 `source_error` 不崩溃；安装 Tesseract 并启用 OCR 后可重跑。
- 按真实数据决定默认并发、角色分配与质量模式阈值（ADR-001），并回填 `recommended_defaults` 到文档。
