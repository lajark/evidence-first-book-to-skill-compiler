# 分发与可见性政策（Distribution Policy）

- 文档版本：v1.0.1
- 原则：**最小范围公开**。仓库推送到公开远端（GitHub）的文件只包含对外有交付价值的公开内容；开发过程文件留在本机 `.workspace/` 或由 `.gitignore` 排除；隐私与密钥类文件严禁进入任何远端历史。本文件是后续项目可继承的分发策略模板根。

> 本政策与 `AGENTS.md`、`.gitignore`、`scripts/pre_push_scan.py` 协同生效。判断文件归属时，以本文件的三类矩阵为准；`pre_push_scan.py` 是执行层守门工具，CI 与 pre-push hook 会强制运行。

## 1. 三类文件矩阵

### 1.1 公开类 PUBLIC（必须推送）

对外有交付价值、随发布包分发或支撑公开构建/测试的文件：

| 类别 | 路径/模式 |
|---|---|
| 产品源码 | `src/` |
| 契约 Schema | `schemas/` |
| 公开文档 | `docs/`（技术文档、迁移指南、兼容矩阵） |
| 输出模板 | `templates/` |
| 元 Skill | `skills/` |
| 许可证与致谢 | `LICENSES/`、`THIRD_PARTY_NOTICES.md`、`ACKNOWLEDGMENTS.md` |
| CI | `.github/` |
| 顶层文档 | `README*.md`、`CHANGELOG.md`、`LICENSE`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`EXTENSION_SDK.md`、`FORMAT_ADAPTERS.md`、`OPEN_SOURCE_REUSE_POLICY.md`、`SECURITY.md`、`SKILL_AUTHORING_STANDARD.md`、`SKILL_DEPLOYMENT.md`、`TRACEABILITY_MATRIX.md`、`RELEASE_PACKAGE_SPEC.md`、`DISTRIBUTION_POLICY.md` |
| 构建/配置 | `pyproject.toml`、`requirements-lock.txt`、`.env.example`、`llm-profiles.example.yaml`、`config.example.yaml` |
| 公开工具脚本 | `scripts/acceptance_metrics.py`、`scripts/bootstrap_self.py`、`scripts/build_release.py`、`scripts/check_provenance.py`、`scripts/install.sh`、`scripts/pre_push_scan.py`、`scripts/run_acceptance.py` |

**机制判定**：凡 wheel / 官方发布包（`book2skill-core-<version>.zip`）所依赖或引用的文件，一律归公开类。例如 `src/book2skill/llm/benchmark_slots.py`（wheel 打包、被 `quality.py` 的 `benchmark_gap` 使用）、`schemas/`、`templates/`。

### 1.2 过程类 PROCESS-INTERNAL（不推送）

开发过程、内部调研、内部质量评估与基准，无对外交付价值，且有信息显露风险。应置于 `.workspace/`（已 gitignore）或由 `.gitignore` 排除，不得推送：

| 类别 | 路径/模式 |
|---|---|
| 内部质量评估 | `Skill提炼质量评估基准/`、`benchmark_abc/`、`scripts/benchmark_abc.py`、`scripts/benchmark_analysis.py`、`scripts/benchmark_cli_startup.py`、`scripts/benchmark_evaluation.py` |
| 需求/计划 | `TODO.md`、`PRD.md`、`TASKS.md`、`IMPLEMENTATION_PLAN.md`、`ACCEPTANCE_TEST_PLAN.md` |
| 集成评审 | `docs/INTEGRATION_REVIEW.md`、`docs/M6_ACCEPTANCE_REPORT.md`、`docs/P1_ACCEPTANCE_BENCHMARK_BASELINE.md`、`docs/P2_PLATFORM_BASELINE.md` |
| 过程目录 | `.workspace/` |
| 本机/多智能体配置 | `AGENTS.md`、`CLAUDE.md`、`CLAUDE.local.*`、`trae-project-rules-mirror.md`、`GITEE_PRIVATE_REPO.md` |
| 发布产物清单 | `PACKAGE_MANIFEST.md`（发布时由构建脚本生成，见 §4） |
| 调研资料 | 根目录 `项目1_Book2Skill_参考项目与增量优化建议.md` 等 |

### 1.3 隐私类 PRIVATE（严禁推送/泄露）

密钥、凭据、真实版权材料、本机配置与运行数据，任何情况下不得进入远端历史：

| 类别 | 路径/模式 |
|---|---|
| 密钥/凭据 | `.env`、`.env.*`、`secrets/`、`*.pem`、`*.key`、`ssh-key`、`ssh-key.pub`、`config.local.*`、`llm-profiles.local.yaml` |
| 运行数据 | `workspace/`、`.workspace/` 数据、`output/`、`dist/`、`htmlcov/`、`.coverage` |
| 版权材料 | `library/raw/`、`library/books/`、真实电子书 `*.pdf`、`*.epub`、`*.mobi`、`*.azw*` |
| 规格包 | `*.zip`（原始开发规格包） |

## 2. 继承方式（供后续项目复用）

1. **复制策略**：复制本文件为 `DISTRIBUTION_POLICY.md`，按新项目实际目录调整矩阵。
2. **复制忽略规则**：从本项目 `.gitignore` 复制"过程类/隐私类"条目到新项目 `.gitignore`。
3. **复制守门脚本并接入**：复制 `scripts/pre_push_scan.py`，在 CI 增加 `policy-scan` job 运行 `python scripts/pre_push_scan.py --untracked`，并在本地接 pre-push hook。

> 不以 `AGENTS.md` 作为唯一策略入口——本仓库 `AGENTS.md` 属过程类被 gitignore，无法随公开远端被下游继承。策略以 `DISTRIBUTION_POLICY.md` + CI 守门为准。

## 3. 执行守门

- 推送前运行 `python scripts/pre_push_scan.py --untracked`，确认无 `process-internal` / `sensitive` 命中。
- CI 的 `policy-scan` job 强制同一检查，防止绕过。
- 对已跟踪的内部文件，先 `git rm --cached`（保留本地）再纳入 `.gitignore`；仅修改 `.gitignore` 不会移除已跟踪文件。
- 若密钥或真实版权材料已进入历史：轮换密钥、暂停共享，再用 `git filter-repo` 类工具清理历史并重新核验，不得只删除最新版本。

## 4. 交付物清单维护

`PACKAGE_MANIFEST.md` 是发布产物（文件 SHA-256 清单），**不入库**，由构建脚本生成。官方发布包 `book2skill-core-<version>.zip` 内的 `checksums.sha256` 已覆盖全部交付文件，作为交付完整性依据。仓库内不人工维护重复的 SHA-256 清单。