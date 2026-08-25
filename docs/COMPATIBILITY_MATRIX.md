# Compatibility Matrix

兼容报告严格区分结构、安装和真实运行；较弱证据不能升级为较强结论。

| Host | 结构校验 | 安装冒烟测试 | 真实任务运行 | 说明 |
|---|---|---|---|---|
| Claude | 通过 | 自动化安装/校验/卸载通过；4 个真实 Skill 安装通过 | 已验证（2026-08-25，Closure task harness） | 项目目录安装、production preflight、无 PYTHONPATH 资源复算通过；未宣称客户端对话质量 |
| Trae | 通过 | 自动化安装/校验/卸载通过；4 个真实 Skill 安装通过 | 已验证（2026-08-25，Closure task harness） | 项目目录安装、production preflight、无 PYTHONPATH 资源复算通过；未宣称客户端对话质量 |
| Codex | 通过 | 自动化安装/校验/卸载通过 | 已验证（2026-08-20） | 真实 Codex CLI 在临时项目 `.agents/skills/offline-smoke/` 安装路径中只读调用并返回 `CODEX_INSTALLED_SKILL_OK` |
| Project | 通过 | 自动化安装/校验/卸载通过；4 个真实 Skill 安装通过 | 已验证（2026-08-25，Closure task harness） | 通用项目级目录安装、production preflight 与资源复算通过 |
| ChatGPT | 通过 | 跨宿主回归安装通过；4 个真实 Skill 安装通过 | 已验证（2026-08-25，Closure task harness） | 项目目录安装、production preflight、无 PYTHONPATH 资源复算通过；未宣称客户端对话质量 |

生成物中的 `compatibility-report.json` 默认只记录
`structure_validated`。安装器测试或真实宿主执行只有在产生独立、可审计证据后，
才能将对应条目提升为 `install_smoke_passed` 或 `runtime_verified`。

矩阵中的安装结果来自 2026-08-06 全量 pytest（`1026 passed, 1 skipped`）及 2026-08-25 对四个
真实迁移 Skill 的 20 个隔离项目安装。2026-08-25 的“真实任务运行”是安装树内的 Closure task
harness：它在无 `PYTHONPATH` 环境复算产品/Closure/资源哈希，不等同于启动 Claude/Trae/ChatGPT
第三方客户端或对其模型输出作质量结论；Codex 仍另有 2026-08-20 CLI 冒烟证据。

## 校验 profile

| 层 | portable-draft | portable-release |
|---|---|---|
| Book2Skill 内部质量门 | 执行 | 执行且保持发布硬门 |
| `skills-ref==0.1.0` | 可选；缺失为 not_run/warning | 观察层，不替代内部硬门 |
| `skill-validator==1.5.6` | 可选；缺失为 not_run/warning | 配置为 blocking；缺失或失败即 fail |
| 外部链接 | 默认跳过 | 默认跳过；需要联网时显式运行 |

```powershell
book2skill compatibility path/to/skill --profile portable-draft --write
book2skill compatibility path/to/skill --profile portable-release --run-external
```
