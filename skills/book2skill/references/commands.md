# Book2Skill CLI 命令参考

本文件是元 Skill 的命令合同详细参考。所有命令通过本机 `book2skill` CLI 调用，默认离线、无遥测。CLI 入口为 `src/book2skill/cli.py`，由 `pyproject.toml` 注册为 `book2skill` 脚本。

通用规则：
- 失败返回稳定错误码 + 输入 ID + 阶段 + 恢复建议，不输出文档正文；
- 所有命令支持 `--json` 机器可读输出，便于 Agent 解析后续动作；
- 所有命令默认离线运行，唯一联网导入是 `urllib.parse.urlparse`（仅 URL 解析，不发起请求）；
- 路径参数支持单文件、多文件、目录、glob 模式。

## analyze — Analyze Only 模式（FR-03-1）

```bash
book2skill analyze <sources...> [--json] [--data-home <dir>] [--rights-note <note>] [--collection-id <id>]
```

输入：
- `<sources...>`：一个或多个源文件 / 目录 / glob；
- `--data-home`：Raw 存储根目录（默认内存态，不落盘）；
- `--rights-note`：合法使用权确认备注，写入每个 manifest；
- `--collection-id`：显式 collection id（默认自动派生）；
- `--json`：stdout 输出 AnalysisBundle JSON。

输出：
- 可读模式：`collection_id` / `sources` / `structure entries` / `candidate units` / `review queue` / `conflicts` / `suggested skills` 摘要；
- `--json`：完整 AnalysisBundle，含 `structure` / `candidate_units` / `review_queue` / `conflicts` / `suggested_skills`；
- **不生成最终 Skill**，不写 SKILL.md。

失败码：
- `GATE_FILE_NOT_FOUND` / `GATE_EMPTY_FILE` / `GATE_DAMAGED_FILE` / `GATE_ENCRYPTED_FILE` / `GATE_UNSUPPORTED_FORMAT` / `GATE_COMPRESSION_BOMB` / `GATE_FILE_TOO_LARGE`；
- 单文件失败在 `batch` 模式下不阻塞其他文件。

## batch — 批处理 Analyze Only（FR-02）

```bash
book2skill batch <sources...> [--json] [--data-home <dir>] [--rights-note <note>]
```

逐文件调用 AnalyzeUseCase，单文件失败不影响其他文件，输出失败清单（`FailureRecord`：path/code/message/recovery）。退出码：全失败为 1，否则 0。

## build — Full Build / Build from Analysis（FR-03-2 / FR-03-3）

```bash
book2skill build <sources...> --name <slug> --description <desc> --use-when <trigger> [--use-when ...] [--no-use-when <boundary>] [--data-home <dir>] [--rights-note <note>] [--output-dir <dir>] [--json]
book2skill build --from-analysis <bundle.json> --name <slug> --description <desc> --use-when <trigger> [...]
```

输入：
- `<sources...>` 与 `--from-analysis` 互斥；
- `--name`：Skill slug，必须匹配 `^[a-z0-9]+(?:-[a-z0-9]+)*$`；
- `--description`：至少 10 字符，写明做什么、何时用、何时不用；
- `--use-when`（可重复）：触发场景；
- `--no-use-when`（可重复）：不适用场景；
- `--data-home`：启用时 `provenance.yml` 填真实 `content_sha256` / `title` / `format` / `ingested_at`；
- `--output-dir`：默认 `workspace/skills/<name>/`；
- `--rights-note`：Full Build 必填，Build from Analysis 可选（无新 Raw 写入）。

输出：标准目录 `SKILL.md` + `references/<kind>.md` + `assets/` + `provenance.yml` + `quality-report.md` + `skill.meta.json`。

失败码：
- `BUILD_INPUT_INVALID`：参数互斥冲突或缺失；
- `BUILD_IR_FAILED`：无可用 KnowledgeUnit（全部被 rejected/superseded）；
- `BUILD_BUDGET_EXCEEDED`：SKILL.md 超过硬上限 5000 tokens；
- 警告（不阻塞）：候选单元被过滤、provenance 字段回退 unknown。

## update — Update / Fold-in（FR-03-4 / FR-10）

```bash
book2skill update <skill-dir> <new-sources...> --data-home <dir> [--collection-id <id>] [--name <slug>] [--description <desc>] [--use-when <trigger>] [--no-use-when <boundary>] [--confirm] [--rights-note <note>] [--json]
book2skill update <skill-dir> --rollback --data-home <dir>
```

行为：
- 默认 dry-run：复用 `DiffEngine.diff` + `merge_with_overrides`，输出 added / modified / removed / conflicts + merge 摘要；
- `--confirm`：原子发布——staging 编译 → 快照旧树 → `os.replace` 替换 → 追加 `publish-log.jsonl` → 更新 `wiki/index.md`；
- `--rollback`：从 `<data-home>/snapshots/<name>/<ts>/` 恢复上一版本，当前版本转存为新快照；
- 无变更返回 `no_changes`，不产快照、不写日志（幂等）；
- `--collection-id` / `--name` / `--description` / `--use-when` 缺省时从 `skill.meta.json` 读取。

失败码：
- `PUBLISH_FAILED`：原子替换失败；
- `PUBLISH_ROLLBACK_FAILED`：回滚失败（需人工介入快照目录）；
- `STORAGE_NOT_FOUND`：skill 目录或 collection 不存在。

## diff — 差异引擎（FR-03-4 / FR-06）

```bash
book2skill diff <old> <new> [--data-home <dir>] [--merge] [--json]
```

输入：`<old>` / `<new>` 各为 collection_id 或 AnalysisBundle JSON 路径。`--merge` 需 `--data-home`，从 collection 加载 override 做三方合并（ours 优先，同字段双修标 unresolvable）。

输出：added / removed / modified（含 changed_fields）/ conflicts / unchanged + 可选 merge 摘要。

## validate — 质量与安全校验（FR-07 / FR-08）

```bash
book2skill validate <skill-dir> [--write] [--max-quote-words 25] [--json]
```

校验项：
- `frontmatter`：name slug 模式 + description 至少 10 字符；
- `source-coverage`：来源覆盖、孤立引用、重复 ID、缺失 reference 链接；
- `copyright`：长引文检测（25 词软上限 warn / 40 词硬上限 fail，中文按 1.5 字符/词折算）；
- `injection`：注入文本（中英文短语）、隐藏字符（ZWSP/BOM/RTL/C0-C1）、可疑 URL（非 https / 内网 IP / data: / javascript:）、路径穿越（`../` 与绝对路径）；
- `budget`：SKILL.md token 预算（2500–5000 目标，5000 硬上限）。

退出码：fail=1，pass / pass_with_warnings=0。`--write` 仅写 `quality-report.md`/`.json`，不触碰 SKILL.md / references / provenance.yml。

## install / uninstall — 多宿主部署（FR-04 / FR-09）

```bash
book2skill install <skill-dir> --host <claude|trae|codex|project> [--project-level] [--project-root <dir>] [--backup-root <dir>] [--target-dir <sub>] [--dry-run] [--no-backup] [--json]
book2skill uninstall <skill-name> --host <claude|trae|codex|project> [--project-level] [--project-root <dir>] [--target-dir <sub>] [--dry-run] [--json]
```

安装路径（详见 `references/deployment.md`）：
- `claude`：个人级 `~/.claude/skills/` 或项目级 `.claude/skills/`；
- `trae`：项目级 `.trae/skills/`（忽略 `--project-level`）；
- `codex`：个人级 `~/.agents/skills/` 或项目级 `.agents/skills/`，额外重写 `agents.md` overlay；
- `project`：通用项目级，默认 `skills/` 子目录（可由 `--target-dir` 覆盖，拒绝绝对路径）。

行为：原子复制（staging → `os.replace`），旧版本备份到 `~/.book2skill/backups/<host>/<skill>/<ts>/`（除非 `--no-backup`）。Skill 名称从 SKILL.md frontmatter `name` 字段读取，保证目录名与 slug 一致。卸载幂等：不存在的 Skill 返回成功 no-op，不删 workspace/raw/backups。

失败码：`INSTALL_FAILED` / `INSTALL_SKILL_DIR_INVALID` / `UNINSTALL_FAILED`。

## version / hello

```bash
book2skill version
book2skill hello
```

`version` 输出版本号；`hello` 是 M0 占位命令，用于验证安装可发现。
