# Book2Skill 四种模式工作流与决策树

本文件扩展 SKILL.md 的 Workflow 节，说明四种模式的输入输出合同、模式间衔接、人工审核点与回滚路径。所有模式默认离线，云 LLM 需显式 opt-in。

## 模式总览

| 模式 | 输入 | 输出 | 是否生成 Skill | 典型用途 |
|---|---|---|---|---|
| Analyze Only | 源文件 | AnalysisBundle | 否 | 先验证输入与候选可信 |
| Full Build | 源文件 | 标准 Skill 目录 | 是 | 一次性从书到 Skill |
| Build from Analysis | AnalysisBundle.json | 标准 Skill 目录 | 是 | 跳过重复抽取，人工审核后再编译 |
| Update / Fold-in | 已发布 Skill + 新源 | 差异计划 / 原子发布 / 快照 | 是（增量） | 加新资料、保留人工修改 |

## 数据分层铁律

```
workspace/
├── raw/<source_id>/<version>/    # 不可变：原件 + manifest + extracted.md + extraction-map.jsonl
├── schema/<collection_id>/       # 机器契约：collection.json + units.jsonl + 各 kind .jsonl
└── wiki/<skill-slug>/            # 人类视图：SKILL.md + references/ + provenance.yml + quality-report.md
```

- Raw 不可变：相同哈希去重，同名异哈希创建新版本，更新创建新版本与日志，不原地擦除；
- Schema 是机器契约：KnowledgeUnit 含稳定 ID / kind / content / source_refs / review_status / 版本与替代关系；
- Wiki 是可生成的人类视图：任何结论不得脱离来源进入 Wiki；
- 页码不能确定时标记待确认，禁止编造。

## 决策树

### 用户首次提交一本书
1. 询问合法使用权（`--rights-note`）；
2. 跑 `book2skill analyze <sources> --json --data-home <dir> --rights-note <note>`；
3. 检查 `review_queue` 与 `conflicts`，与用户确认低置信项；
4. 人工修订 AnalysisBundle.json（可调整 candidate_units 的 content / kind / source_refs，标记 rejected）；
5. 跑 `book2skill build --from-analysis <bundle.json> --name ... --description ... --use-when ...`；
6. 跑 `book2skill validate <skill-dir> --write`，确认 pass 或 pass_with_warnings；
7. 跑 `book2skill install <skill-dir> --host <h>` 部署。

### 用户要给已发布 Skill 加新资料
1. 确认 `<skill-dir>` 与 `<data-home>` 一致（同一 collection）；
2. 跑 `book2skill update <skill-dir> <new-sources> --data-home <dir> --rights-note <note>`（dry-run）；
3. 检查 added / modified / removed / conflicts；
4. 若有冲突，与用户确认是否记录为 override 或保留双观点并列；
5. `--confirm` 原子发布；
6. 若发布后发现问题：`--rollback` 恢复上一版本。

### 用户要比较两份资料
- 两份源文件 → 各跑 `analyze` 生成 AnalysisBundle，再 `diff <old.json> <new.json>`；
- 两版 collection → `diff <old-id> <new-id> --data-home <dir>`，要保留人工修改加 `--merge`。

## 人工审核点

- Analyze Only 输出的 `review_queue`：低置信项、需人工确认的候选；
- Analyze Only 输出的 `conflicts`：两来源观点冲突，并列记录不自动裁决；
- Update dry-run 输出的 `conflicts`：同字段双修，标 unresolvable，需人工选值写入 override；
- validate 输出的 `pass_with_warnings`：可发布但建议复审（如长引文接近上限、references 链接缺失）。

## override 与三方合并

- 人工修改保存为结构化 `overrides.jsonl`（append-only），不依赖注释字符串判断；
- `diff --merge` 加载 override 做 ours 优先的三方合并；
- 同字段双修标 `unresolvable`，不自动裁决；
- removed 单元的 override 进 `preserved_overrides`，modified 单元 bump 版本，unchanged 跳过避免重复版本记录。

## 原子发布与回滚

- 发布：staging 编译 → 快照旧树到 `<data-home>/snapshots/<name>/<ts>/` → `os.replace` 替换 → 追加 `publish-log.jsonl` → 更新 `wiki/index.md`；
- 快照目录与 skill 同卷（`data_home` 下），避免跨设备 rename；
- 失败不破坏已发布版本：编译失败不触碰已发布；快照后失败自动回滚；
- 回滚：从快照恢复，当前版本转存为新快照，append 日志；
- `wiki/index.md` 是 `publish-log.jsonl` 的派生视图（latest per skill），避免双写分歧。

## 离线与降级

- 默认离线：LLM 为规则驱动 Mock（关键词启发式检测 principle/technique/term/case/anti_pattern）；
- 云 LLM 显式 opt-in：配置 `cloud_llm.enabled: true` + `base_url` + `model`，并提示数据边界；
- Calibre 缺失：MOBI/AZW 输出 `EXTRACT_UNSUPPORTED` + 离线安装说明，不阻塞其他格式；
- 加密 PDF / DRM MOBI：输出 `GATE_ENCRYPTED_FILE`，不绕过；
- 可选依赖缺失：清晰降级 + 安装建议，不作为单点依赖。
