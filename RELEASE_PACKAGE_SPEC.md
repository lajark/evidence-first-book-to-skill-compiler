# Book2Skill Core 最终发布包规范

## 1. 文件名

`book2skill-core-<semver>.zip`

同时输出同名 `.sha256` 文件。ZIP 是项目2和项目3开发、安装、兼容测试所使用的唯一正式上游交付物；不得要求下游读取本项目源码仓库。

## 2. 包结构

```text
book2skill-core-<version>/
├── README_INSTALL.md
├── VERSION
├── release-manifest.json
├── checksums.sha256
├── install.py
├── input/
│   └── README.md
├── output/
│   ├── README.md
│   ├── bundles/.gitkeep
│   ├── skills/.gitkeep
│   └── workspace/.gitkeep
├── dist/
│   └── book2skill-<version>-py3-none-any.whl
├── skills/
│   └── book2skill-skill.zip
├── contracts/
│   ├── extension-manifest.schema.json
│   ├── release-manifest.schema.json
│   ├── source-manifest.schema.json
│   ├── extraction-map-entry.schema.json
│   ├── analysis-bundle.schema.json
│   ├── skill-ir.schema.json
│   └── compatibility.md
├── sdk/
│   ├── EXTENSION_SDK.md
│   └── api-surface.json
├── examples/
│   ├── sample-extension/
│   └── normalized-export/
├── LICENSES/
├── THIRD_PARTY_NOTICES.md
├── ACKNOWLEDGMENTS.md
└── CHANGELOG.md
```

## 3. 安装结果

安装脚本在用户确认后：

1. 创建或使用独立 Python 环境并安装 Wheel；
2. 初始化 `BOOK2SKILL_HOME`；
3. 创建 `input/`、`output/bundles/`、`output/skills/` 与
   `output/workspace/`；
4. 安装 Book2Skill 元 Skill；
5. 初始化扩展注册表；
6. 执行 `book2skill doctor`；
7. 输出安装位置、版本、输入目录、输出目录、回滚和卸载说明。

默认运行数据根为 `BOOK2SKILL_HOME/output/workspace`；用户可以通过
`--data-home` 覆盖。安装根默认为 `~/.book2skill`，也可通过
`BOOK2SKILL_HOME` 覆盖。

## 4. Release Manifest

至少记录：产品、版本、构建时间、Python 兼容范围、Wheel/Skill/Contract 路径和 SHA-256、公开 SDK 版本、支持的 extension schema、第三方许可证、构建来源 Commit（真实存在时）和验收报告摘要。

## 5. 发布门

- 在干净环境完成安装、基础转换、扩展 fixture、升级和卸载测试；
- `checksums.sha256` 覆盖除自身外的全部交付文件；
- contracts 与 Wheel 内公开模型一致；
- ZIP 不含电子书、工作区数据、密钥、缓存、构建临时物或未授权源码；仅允许
  `input/`、`output/` 的空目录占位/说明文件；
- 未执行的宿主真实测试必须在 release manifest 中如实标明。
