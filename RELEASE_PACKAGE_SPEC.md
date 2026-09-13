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

## 6. Windows Desktop Preview / Installer

桌面安装包是 Core ZIP 之外的独立交付物，不要求下游读取源码仓库：

```text
dist/installer/
├── Book2Skill-Setup-<version>-windows-x64.exe
├── checksums.sha256
├── dependency-manifest.json
├── dependency-licenses/
│   └── <package>/...license files...
└── release-manifest.json
```

桌面 Manifest 至少记录 `platform=windows`、`architecture=x64`、构建 Commit、
工作区是否有未提交改动（`source_dirty`）、安装器 SHA-256、签名状态、许可证状态和
`release_ready`。即使项目代码采用 MIT，Windows 安装器也只能在依赖许可证清单存在、
依赖审查无 `review_required`、可信 Authenticode 签名存在且显式传入
`release_ready=true` 时进入公开发布；否则必须保持 `release_ready=false`，只能作为
内部预览。未签名时明确记录 `signature_status=unsigned`；可信 Authenticode、内部自签名
和不受信任链分别记录为 `trusted`、`self_signed_untrusted`、`signed_untrusted`。
使用时间戳时，`signature_timestamp_status` 必须为 `present`；没有请求时间戳时记录
`not_applicable` 或 `not_requested`。签名缺失应提示 SmartScreen/信任链风险，但不得
伪报为已签名。
