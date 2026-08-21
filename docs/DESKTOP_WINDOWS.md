# Windows Desktop WebGUI 与安装包

本功能是 Book2Skill CLI 的可选本地桌面壳，不改变核心管线和 Core ZIP 交付方式。

## 运行

开发环境安装安全桌面依赖：

```powershell
pip install -e ".[desktop-safe]"
book2skill-desktop
```

默认目录：

```text
%LOCALAPPDATA%\Book2Skill\
├── output\bundles\
├── output\skills\
└── workspace\
```

可用 `BOOK2SKILL_HOME` 指向便携式目录。GUI 默认 Mock 离线模式；云端模型仍需显式
选择，并沿用 CLI 的 profile/环境变量优先级。

## 安全边界

- API 只绑定 `127.0.0.1` 随机端口；每次启动生成随机会话令牌。
- API 检查 Host/Origin、请求体大小和静态资源路径；SSE 只发送脱敏进度。
- 源文件路径由原生文件对话框选择，内容不会通过远程网络上传。
- 取消只在应用层安全检查点生效；已有 Raw 或缓存不会被覆盖，生成 Skill 仍使用原子发布。
- GUI 不保存明文 API Key；标准依赖不自动安装 Calibre、Tesseract 或 Docling。
- 标准桌面包也不捆绑 AGPL 的 `ebooklib` 或 PyMuPDF；EPUB 使用内置 stdlib ZIP
  fallback，具体依赖版本与许可证以安装器内的 `dependency-manifest.json` 为准。
许可证文件副本位于安装目录的 `dependency-licenses/`，清单中的
`license_artifacts` 与 `checksums.sha256` 可用于离线核对。
当前依赖清单中原有 18 个元数据缺失项已通过 PEP 639 `License-Expression` 与许可证
文件完成技术确认；这不是对外部许可证义务的法律意见。

## Windows 构建

先安装桌面与构建依赖，并准备 PyInstaller、Inno Setup 6：

```powershell
pip install -e ".[desktop-safe,build-windows]"
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -SkipInstaller
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

构建输出为 `dist/Book2Skill/` 和 `dist/installer/`。默认生成内部预览元数据；
`-ReleaseReady` 会在当前私有许可证下主动失败，避免误把未授权构建物当作公开发行版。
如果依赖清单存在 `review_required` 项，`-ReleaseReady` 同样会失败，避免遗漏上游
许可证文本审查。

在普通 Windows 用户环境可用隔离临时目录执行安装/升级/卸载验收；脚本不会使用已有的
`%LOCALAPPDATA%\Programs\Book2Skill`，也不会删除已存在目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_windows_installer.ps1 `
  -Artifact "dist\installer\Book2Skill-Setup-<version>-windows-x64.exe"
```

脚本要求安装目录是当前用户 TEMP 下的新目录，完成后默认清理；使用 `-KeepInstall` 可
保留现场供诊断。

若维护者已明确指定一个新的测试目录（例如当前本地部署目录下的 `_desktop_smoke`），
可额外传入 `-AllowExplicitRoot`；该目录必须事先不存在，脚本只会清理本次创建的目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_windows_installer.ps1 `
  -Artifact "dist\installer\Book2Skill-Setup-<version>-windows-x64.exe" `
  -InstallRoot "D:\AI\MyApp\book2skill\_desktop_smoke" `
  -AllowExplicitRoot
```

如需同时验证桌面程序启动，可追加 `-LaunchSmokeSeconds 5`；脚本会在短暂运行后关闭
由本次测试启动的进程，再执行卸载。

正式发行仍需要依赖许可证审查、干净 Windows 11 安装/升级/卸载验收，并将签名结果
写回发布 Manifest；Authenticode 签名对个人开源项目是可选增强，不是硬性前置条件。

签名脚本为 `scripts/sign_windows_release.ps1`，支持证书存储中的
`-CertificateThumbprint`，或通过环境变量 `BOOK2SKILL_SIGNING_PASSWORD` 提供密码的
`-CertificatePath`。构建时显式传入 `-Sign`；`-AllowUntrustedSignature` 只允许内部预览，
不会把不受信任证书标记为公开可信。若选择签名，正式发行应使用组织/CA 颁发的代码签名证书、
时间戳服务，并在 Manifest 中记录 `signature_status=trusted` 与
`signature_timestamp_status=present`。时间戳通过 `-TimestampUrl https://<RFC3161-endpoint>`
传入，脚本使用 SHA-256 RFC 3161 时间戳并验证签名中确实包含时间戳；不要把 PFX、密码或私钥
放入仓库、安装包或日志。个人项目在许可证允许分发时，即使没有证书也可分发未签名安装器，
Manifest 会记录 `signature_status=unsigned`，用户可能看到 SmartScreen 或未知发布者提示。

拿到组织/CA 证书后，密码只在本机环境变量中提供，示例：

```powershell
$env:BOOK2SKILL_SIGNING_PASSWORD = "<set-locally>"
pwsh -File scripts/build_windows.ps1 `
  -ReleaseReady -Sign `
  -CertificatePath "C:\secure\book2skill-code-signing.pfx" `
  -TimestampUrl "https://<RFC3161-endpoint>"
```

## 当前明确不包含

本次更新不实现 macOS 桌面构建、公证或 DMG；macOS/Linux 仍可继续使用 CLI、Wheel
和 Core ZIP。
