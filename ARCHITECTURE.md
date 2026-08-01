# Book2Skill 架构设计

## 1. 架构风格

采用 Ports & Adapters + 编译器管线：输入适配器把异构文档转换为统一 Raw/ExtractionMap；Schema/Skill IR 保存稳定语义；宿主 Adapter 只处理安装路径、frontmatter 扩展和调用方式。

```text
CLI / Meta Skill
      ↓
Application Use Cases
      ↓
Domain + SkillIR + Ports
      ↓
Extractors | Storage | LLM | Validators | Host Adapters
```

## 2. 目标目录

```text
book2skill/
├── src/book2skill/
│   ├── domain/
│   ├── application/
│   ├── extractors/
│   ├── storage/
│   ├── llm/
│   ├── compiler/
│   ├── validation/
│   ├── hosts/
│   └── cli.py
├── skills/book2skill/
│   ├── SKILL.md
│   ├── scripts/
│   ├── references/
│   └── assets/
├── schemas/
├── templates/
├── tests/
├── docs/
└── workspace/  # 默认 Git ignore，可由 BOOK2SKILL_DATA_HOME 指向仓库外
```

## 3. 编译阶段

1. Discover：解析文件、目录、glob；
2. Gate：合法性确认、类型、大小、加密/DRM/安全检查；
3. Ingest：复制或引用原件，生成 manifest；
4. Extract：输出 normalized Markdown + extraction map；
5. Analyze：结构、方法、术语、冲突和 review queue；
6. Normalize：生成版本化 Schema/SkillIR；
7. Compile：生成标准 Skill 与宿主 overlay；
8. Validate：结构、来源、预算、注入、版权和链接；
9. Publish：临时目录原子替换、索引、日志、快照。

## 4. 关键设计决策

- `SkillIR` 是唯一跨宿主语义源；不为 Claude/TRAE/Codex 各维护一套知识正文。
- 人工修改保存为结构化 override/patch，与生成内容分离；更新时三方合并。
- 原始文件不修改；“删除”只标记来源不可用，不擦除历史。
- SQLite 不是 P0 必需；所有状态可由版本化文件重建。
- 模型仅产生候选结构，验证器和人工审核决定是否发布。
