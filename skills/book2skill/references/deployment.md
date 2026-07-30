# Book2Skill 多宿主部署

本文件扩展 SKILL.md 的部署部分，覆盖 Claude Code / TRAE / Codex / ChatGPT Project 回退、安装前校验、升级备份、卸载与冒烟测试。安装动作推荐用 `book2skill install` 命令，避免手写脚本路径。

## 部署前校验

任何 Skill 在安装前必须先通过本项目验证器：

```bash
book2skill validate <skill-dir> --write
```

- 退出码 0 = pass / pass_with_warnings（可安装）；
- 退出码 1 = fail（不得安装，先修复）；
- `--write` 生成 `quality-report.md`/`.json` 供审查；
- 不得安装未审查的第三方脚本。

## Claude Code

个人级（全局可用）：

```bash
book2skill install <skill-dir> --host claude
# 安装到 ~/.claude/skills/<skill-name>/
```

项目级（仅当前项目）：

```bash
book2skill install <skill-dir> --host claude --project-level
# 安装到 <project-root>/.claude/skills/<skill-name>/
```

部署后：直接调用 `/<skill-name>` 显式触发，或用自然语言触发（如"用 book2skill 分析这本书"）。技能目录存在时可使用符号链接，但为跨平台可移植性，默认复制。

## TRAE

```bash
book2skill install <skill-dir> --host trae
# 安装到 <project-root>/.trae/skills/<skill-name>/（项目级，忽略 --project-level）
```

注意：TRAE 仅为项目级部署；不要在脚本中写死 `.claude/skills`，运行时通过 Skill 根目录环境/参数定位资源。部署后执行一次 Analyze Only 冒烟测试（见下文"冒烟测试"）。

## Codex / OpenAI Skills

个人级：

```bash
book2skill install <skill-dir> --host codex
# 安装到 ~/.agents/skills/<skill-name>/，并重写 ~/.agents/agents.md overlay
```

项目级：

```bash
book2skill install <skill-dir> --host codex --project-level
# 安装到 <project-root>/.agents/skills/<skill-name>/，并重写 .agents/agents.md overlay
```

Codex overlay 每次重写 `agents.md`，避免上一版本残留陈旧索引。如 ChatGPT Skills 界面可用，在 Skills 中选择 Create/Upload 上传经过验证的 Skill 包；上传扫描不能替代人工审查。

## 通用项目级

```bash
book2skill install <skill-dir> --host project [--target-dir <sub>]
# 安装到 <project-root>/<sub>/<skill-name>/（默认 <sub>=skills）
```

`--target-dir` 拒绝绝对路径（POSIX `/` 前缀 + Windows 驱动器号），通过 `resolve_within` 终检路径穿越。

## ChatGPT Project 兼容回退

若当前界面不能直接安装 Skill：
1. 把 `SKILL.md`、必要 `references/`、`assets/` 和只读脚本说明打包为执行包；
2. 上传到 ChatGPT Project；
3. 不要上传受版权保护的书籍原文到不符合数据政策的环境；
4. 元 Skill `book2skill` 本身无版权原文，可安全上传。

## 升级与备份

- 安装前自动备份旧版本到 `~/.book2skill/backups/<host>/<skill>/<ts>/`（除非 `--no-backup`）；
- 备份目录默认放 `~/.book2skill/backups/` 而非 workspace 下，避免 workspace 清理时丢失回滚入口；
- 安装原子性：新树先复制到 `<target>/.<name>.staging-<ts>/` 暂存目录，再 `os.replace` 替换；overlay 失败时自动恢复备份（best-effort，不抛二错）；
- 升级后保留 CHANGELOG（若存在）。

## 卸载

```bash
book2skill uninstall <skill-name> --host <claude|trae|codex|project> [--project-level] [--project-root <dir>]
```

- 仅删 Skill 安装目录，不删 workspace / raw / schema / backups；
- 幂等：不存在的 Skill 返回成功 no-op，不抛异常；
- 与 `update --rollback` 的 `STORAGE_NOT_FOUND` 行为不同，因为安装目录可能已被人工删除。

## 冒烟测试

部署后执行一次 Analyze Only 冒烟测试，验证 CLI 可发现、Analyze Only 可运行：

```bash
python <skill-dir>/scripts/smoke_test.py
```

脚本会：
1. 优先查找 `.venv/Scripts/book2skill.exe` 或 `.venv/bin/book2skill`，回退到 PATH 中的 `book2skill`；
2. 在临时目录创建一个最小 TXT 样本；
3. 运行 `book2skill analyze <sample> --json`；
4. 解析 JSON，断言 `collection_id` 存在且 `candidate_units` 为列表；
5. 输出 PASS / FAIL 并返回对应退出码。

## 跨平台注意事项

- 路径处理：使用 `Path.home()` 与 `Path.cwd()` 而非手工 `~` 展开；
- `os.replace` 目录原子替换依赖"目标不存在"不变量，Windows 11 实测通过；
- 跨平台仅 Windows 11 实测，macOS/Linux 兼容但未自动化验证。
