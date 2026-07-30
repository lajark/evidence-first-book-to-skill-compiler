# Book2Skill Skill 部署步骤

## 1. 产物

```text
skills/book2skill/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

先运行本项目验证器，再安装；不得安装未审查的第三方脚本。

## 2. Claude Code

个人级：

```bash
mkdir -p ~/.claude/skills
cp -R skills/book2skill ~/.claude/skills/
```

项目级：复制到 `.claude/skills/book2skill/`。验证目录下存在 `SKILL.md`，然后直接调用 `/book2skill` 或用自然语言触发。技能目录存在时可使用符号链接，但为跨平台可移植性，默认复制。

## 3. TRAE

复制到项目根目录 `.trae/skills/book2skill/`。不要在脚本中写死 `.claude/skills`；运行时通过 Skill 根目录环境/参数定位资源。部署后执行一次 Analyze Only 冒烟测试。

## 4. Codex / OpenAI Skills

本地全局目录：`~/.agents/skills/book2skill/`；仓库级：`.agents/skills/book2skill/`。如 ChatGPT Skills 界面可用，在 Skills 中选择 Create/Upload 上传经过验证的 Skill 包；上传扫描不能替代人工审查。

## 5. ChatGPT Project 兼容回退

若当前界面不能直接安装 Skill，把 `SKILL.md`、必要 `references/`、`assets/` 和只读脚本说明打包为执行包上传到 Project；不要上传受版权保护的书籍原文到不符合数据政策的环境。

## 6. 验收

- 能发现 Skill 名称和 description；
- 显式调用和自然语言调用均命中；
- Analyze Only 不生成最终 Skill；
- 脚本路径不依赖仓库绝对路径；
- 升级前备份旧版本，升级后保留 CHANGELOG；
- 卸载只删除 Skill 安装目录，不删除 workspace。
