# Compatibility Matrix

兼容报告严格区分结构、安装和真实运行；较弱证据不能升级为较强结论。

| Host | 结构校验 | 安装冒烟测试 | 真实任务运行 | 说明 |
|---|---|---|---|---|
| Claude | 通过 | 自动化安装/校验/卸载通过 | 未执行 | 不声明 runtime verified |
| Trae | 通过 | 自动化安装/校验/卸载通过 | 未执行 | 不声明 runtime verified |
| Codex | 通过 | 自动化安装/校验/卸载通过 | 已验证（2026-08-20） | 真实 Codex CLI 在临时项目 `.agents/skills/offline-smoke/` 安装路径中只读调用并返回 `CODEX_INSTALLED_SKILL_OK` |
| Project | 通过 | 自动化安装/校验/卸载通过 | 未执行 | 通用项目级安装 |
| ChatGPT | 通过 | 跨宿主回归安装通过 | 未执行 | 仅结构与打包边界 |

生成物中的 `compatibility-report.json` 默认只记录
`structure_validated`。安装器测试或真实宿主执行只有在产生独立、可审计证据后，
才能将对应条目提升为 `install_smoke_passed` 或 `runtime_verified`。

矩阵中的安装结果来自 2026-08-06 全量 pytest（`1026 passed, 1 skipped`）；Codex 的真实运行证据来自
2026-08-20 的已授权 Codex CLI `0.147.0` 临时项目冒烟；
生成物报告仍保守停留在 `structure_validated`，因为安装结果属于仓库级适配器
证据，不等同于该具体 Skill 已实际安装。

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
