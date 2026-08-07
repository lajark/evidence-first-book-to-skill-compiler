# 分发与可见性政策（DISTRIBUTION_POLICY.md 模板）

> 复制本模板到新项目根目录，按实际目录调整三类文件矩阵。同步执行"三步配方"：
> ① 复制本文件；② 从源项目 `.gitignore` 复制过程类/隐私类条目；③ 复制 `scripts/pre_push_scan.py` 并接入 CI / pre-push hook。

## 1. 三类文件矩阵

### 1.1 公开类 PUBLIC（必须推送）

（列出对外有交付价值的源码、契约、文档、模板、许可证、构建配置与公开工具脚本。）

### 1.2 过程类 PROCESS-INTERNAL（不推送）

（列出内部质量评估、需求/计划、集成评审、过程目录 `.workspace/`、本机/多智能体配置、发布产物清单、调研资料。应置于 `.workspace/` 或由 `.gitignore` 排除。）

### 1.3 隐私类 PRIVATE（严禁推送）

（列出密钥/凭据、运行数据、版权材料与原始规格包。任何情况下不得进入远端历史。）

## 2. 继承与守门

- 推送前运行 `python scripts/pre_push_scan.py --untracked`。
- CI 增加 `policy-scan` job：`python scripts/pre_push_scan.py --untracked`。
- 已跟踪的内部文件：`git rm --cached`（保留本地）后加入 `.gitignore`。
- 密钥/版权材料若已进历史：轮换并清理历史，不得只删最新版本。