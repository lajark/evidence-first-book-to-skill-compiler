# Book2Skill Extension SDK 规范

- 版本：v1 draft（随 Core 首个稳定发布冻结）
- 目标：让用户自有项目在不复制基础代码的情况下扩展 Book2Skill。

## 1. 公开命名空间

下游只允许依赖 `book2skill.sdk` 和发布包中的 `contracts/`。`book2skill._internal`、未导出的模块和源码目录均为私有实现。

公开能力至少包括：

- `SourceService`：发现、MIME 检测、合法性门、SHA-256、稳定 ID 与版本；
- `ExtractionService` / extractor ports：调用已安装格式适配器并产生 ExtractionMap；
- `StorageService`：Raw 不可变写入、Schema 版本化、Wiki 渲染目标和原子发布；
- `SkillCompiler`：SkillIR、模板、引用、质量门和宿主 overlay；
- `ValidatorRegistry`：Schema、来源、版权、注入、路径和链接校验；
- `ExtensionContext`：配置、日志、数据根、临时目录、权限和已安装依赖；
- `ExtensionRegistry`：安装记录、版本选择、依赖图、快照和健康检查。

## 2. 扩展 Manifest

每个扩展发布包根目录必须包含 `extension-manifest.json`，并通过 `schemas/extension-manifest.schema.json` 校验。关键字段：

```json
{
  "schema_version": 1,
  "extension_id": "dd-methods",
  "version": "1.0.0",
  "requires": {
    "book2skill": ">=1.0.0,<2.0.0",
    "extensions": []
  },
  "entry_points": ["ddmethods.extension:activate"],
  "contributes": {
    "commands": ["dd-methods"],
    "skills": ["dd-orchestrator"],
    "validators": [],
    "pipelines": ["due-diligence-methods"]
  },
  "permissions": ["read_normalized_sources", "write_extension_data"],
  "checksums_file": "checksums.sha256"
}
```

## 3. 生命周期

1. `inspect`：只读解析 Manifest、哈希、许可证和兼容范围；
2. `install`：验证依赖、安装 Wheel/资产到版本目录、运行受控迁移、注册入口点；
3. `activate`：创建 ExtensionContext，只暴露声明权限；
4. `doctor`：验证入口、数据契约、Skill 和依赖；
5. `upgrade`：并列安装新版本、迁移、冒烟、切换 active，失败回滚；
6. `rollback`：切回前一已验证版本；
7. `uninstall`：检查下游依赖，默认保留领域数据与快照。

## 4. 禁止事项

- 复制或 vendoring Core 的通用解析、哈希、Raw、SkillIR、宿主安装代码；
- 导入 Core 私有模块或依赖源码相对路径；
- 修改 Core 安装目录、注册表历史或其他扩展数据；
- 绕过 Manifest、哈希、权限、兼容检查或迁移流程；
- 在扩展安装阶段自动联网或拉取源码。

## 5. 兼容与测试

Core 提供最小 `sample-extension` fixture。每个公开 SDK 变更必须运行：当前主版本、上一个受支持次版本、安装/升级/回滚/卸载、依赖缺失、版本冲突和断网测试。破坏性修改只能进入 Core 主版本升级，并提供迁移指南。
