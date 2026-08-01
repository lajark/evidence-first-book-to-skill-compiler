# Book2Skill Core 与 Skill 部署步骤

## 1. 最终交付物

正式交付为 `book2skill-core-<version>.zip`，结构见 `RELEASE_PACKAGE_SPEC.md`。其中包含 Core Wheel、Book2Skill 元 Skill、公开 contracts、Extension SDK 和安装器。

## 2. 安装 Core

1. 校验 ZIP 同名 `.sha256`；
2. 解压到临时目录，不在压缩包内直接运行；
3. 阅读 `README_INSTALL.md`、Manifest、许可证和第三方致谢；
4. 在用户确认后运行发布包内 `install.py`；
5. 执行 `book2skill doctor` 和 Analyze Only 冒烟测试；
6. 记录 Core 版本、SDK 版本、`BOOK2SKILL_HOME` 和 Skill 安装位置。

目标命令在真实实现后固定；规格阶段不得声称已经可运行。

## 3. 宿主 Skill 安装

安装器根据用户选择将 `book2skill` 元 Skill 部署到：

- Claude Code：个人级 `~/.claude/skills/book2skill/`，项目级 `.claude/skills/book2skill/`；
- TRAE：项目级 `.trae/skills/book2skill/`；
- Codex：全局 `~/.agents/skills/book2skill/`，项目级 `.agents/skills/book2skill/`；
- ChatGPT：上传经过验证的 Skill 包；若界面不支持，使用 Project 执行包回退。

脚本不得写死某个宿主路径，必须由 Host Adapter 处理。

## 4. 安装下游扩展

用户按以下顺序安装：

```text
Book2Skill Core
→ DD Methods Extension
→ DD Workbench Extension
```

统一目标命令：

```text
book2skill extensions inspect <extension.zip>
book2skill extensions install <extension.zip>
book2skill extensions doctor <extension-id>
book2skill extensions list
book2skill extensions rollback <extension-id>
book2skill extensions uninstall <extension-id>
```

安装器校验 Manifest、哈希、Core 兼容范围和其他扩展依赖。若 DD Workbench 已依赖 DD Methods，不允许先卸载 DD Methods。

## 5. 验收

- Core 在无扩展时基础功能完整；
- 元 Skill 能被宿主发现并正确触发；
- 扩展注册、依赖解析、升级和回滚通过；
- 扩展脚本不依赖源码仓库绝对路径；
- 卸载程序默认不删除用户 workspace 和扩展领域数据；
- 未审查第三方脚本不得安装。
