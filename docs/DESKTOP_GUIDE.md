# Book2Skill Windows WebGUI 使用指南

本指南面向日常使用者。Book2Skill 的桌面 WebGUI 是本机应用壳，默认只绑定
`127.0.0.1`，源文件和生成结果默认留在本机；它不要求安装 Codex、TRAE、Claude
或 ChatGPT，也不会替用户安装这些宿主。

## 1. 安装与启动

1. 运行 `Book2Skill-Setup-<version>-windows-x64.exe`。
2. 安装完成后双击安装目录中的 `Book2Skill.exe`。
3. 首次使用建议保持 `Mock（离线）` 模式，确认本地流程正常后再配置云端模型。

标准安装目录只包含桌面程序、冻结运行时、依赖许可证和卸载程序；Python、pip 和
宿主 Agent 不是桌面程序的启动前置条件。Windows WebView2 运行时由系统提供；若
系统缺失，按 Windows 的 WebView2 安装提示补齐即可。

## 2. 界面分区

- **功能**：选择文档、确认使用权、运行 Analyze/Build、查看进度和结果，并预览
  目标宿主的安装动作。
- **配置**：设置 Mock/OpenAI-compatible 模式、模型名、Base URL、profile 文件和
  路由策略。配置只保存在本机浏览器配置和用户数据目录，不会写入生成 Skill。
- **帮助**：查看安装、配置、功能步骤、输出目录和宿主边界说明。

## 3. 配置文件位置

### 桌面安装版

默认用户数据根目录为：

```text
%LOCALAPPDATA%\Book2Skill\
```

桌面版使用以下文件：

```text
%LOCALAPPDATA%\Book2Skill\.env
%LOCALAPPDATA%\Book2Skill\llm-profiles.local.yaml
```

如果设置了 `BOOK2SKILL_HOME`，则将该目录替代 `%LOCALAPPDATA%\Book2Skill`。
桌面版会显式读取数据根目录中的 `.env`，不依赖程序安装目录的当前工作目录。

`.env` 示例（仅在本机保存，勿提交或分享）：

```ini
BOOK2SKILL_LLM=compatible
LLM_API_KEY=your-local-secret
LLM_BASE_URL=https://example.invalid/v1
LLM_MODEL=your-model
```

API Key 只应来自系统环境变量或 `.env`，不要填入 Skill、日志、发布清单或命令行。

### Core/CLI 实例

Core/CLI 仍按“当前工作目录向上查找”的规则读取 `.env`。例如：

```text
D:\AI\MyApp\book2skill\.env
```

从该目录启动 `venv\Scripts\book2skill.exe` 时即可生效。Core/CLI 的 `.env` 与桌面版
数据根目录的 `.env` 可以相同，但应通过安全的本地复制维护，不要把它们提交到 Git。

## 4. 日常使用步骤

1. 打开“功能”页，选择 `Analyze`（先生成可审核 AnalysisBundle）或 `Build`（直接
   生成可部署 Skill）。
2. 点击“选择文件”，选择本人合法持有且有权处理的 PDF、EPUB、MOBI/AZW、TXT、
   Markdown、DOCX、HTML 或 RTF 文件；也可以手动填写路径。
3. 填写“合法性 / 使用权确认”。未确认时任务会被拒绝。
4. Build 模式下填写 Skill 名称、描述和使用边界。
5. 在“配置”页选择模型。默认 Mock 离线模式不上传原文；云端模型必须显式选择并
   自行确认数据边界、网络和服务条款。
6. 返回“功能”页点击“开始”，查看进度和事件日志。取消只在安全检查点生效。
7. 任务完成后查看 Bundle/Skill 路径；需要部署时选择目标宿主并点击“预览安装动作”。
   预览不会写入宿主目录。

## 5. Skill 与宿主适配边界

Book2Skill 生成的是宿主无关的 Agent Skill。Codex、TRAE、Claude、ChatGPT 和普通项目
目录只是不同的安装目标：

- Book2Skill 核心管线不导入、启动或安装这些宿主。
- 生成 Skill 的 frontmatter、链接、来源追踪和安全约束保持一致。
- 只有用户明确执行安装动作时，才根据目标宿主选择对应目录和备份策略。
- 如果目标宿主不存在，仍可生成、检查、复制和分享 Skill；不影响 Core/CLI 使用。

## 6. 输出目录

桌面版默认写入：

```text
%LOCALAPPDATA%\Book2Skill\output\bundles
%LOCALAPPDATA%\Book2Skill\output\skills
%LOCALAPPDATA%\Book2Skill\workspace
```

Core/CLI 实例则使用其 `BOOK2SKILL_HOME` 或部署目录下的 `input/`、`output/` 和
`workspace/`。输入文件不会被覆盖；更新会创建新版本并保留日志。

## 7. 常见问题

- **窗口打开但页面空白**：升级到包含静态资源路径修复的安装包；确认安装目录中有
  `_internal\book2skill\desktop\static\index.html`。
- **没有看到文件选择器**：确认是通过 `Book2Skill.exe` 启动；在普通浏览器中打开本地
  URL 时只能手动填写路径，无法使用原生文件对话框。
- **云端模型不可用**：检查“配置”页的模式、Base URL、profile 和数据根目录中的
  `.env`；不要把密钥放到 GUI 文本字段或命令行。
- **缺少可选格式工具**：Calibre、Tesseract、Docling 不是标准桌面包的不可替代依赖，
  缺失时会走可用的降级路径并报告诊断。
