# Third-Party Notices

本项目当前选择性移植了以下上游项目的部分文件，并将其作为 design reference。

## 1. `virgiliojr94/book-to-skill`

- Repository: https://github.com/virgiliojr94/book-to-skill
- License: MIT
- Exact commit: `92b248fa5e7039d770d56630444310e36ff014e0`
- Reuse type: `selective_port`

### Ported files

| 上游原始路径 | 本地路径 | 修改说明 |
|---|---|---|
| `book_to_skill/sanitize.py` | `src/book2skill/extractors/_vendor/book_to_skill/sanitize.py` | 添加 provenance 版权头；无功能改动 |
| `book_to_skill/parsers/text.py` | `src/book2skill/extractors/_vendor/book_to_skill/text.py` | 添加 provenance 版权头；模块重命名；无功能改动 |
| `book_to_skill/parsers/html.py` | `src/book2skill/extractors/_vendor/book_to_skill/html.py` | 添加 provenance 版权头；改用包内 vendored `text` 模块；移除未使用的 `html` 导入；无功能改动 |
| `book_to_skill/parsers/epub.py` | `src/book2skill/extractors/_vendor/book_to_skill/epub.py` | 添加 provenance 版权头；改用包内 vendored `html` 模块；无功能改动 |
| `book_to_skill/exceptions.py` | `src/book2skill/extractors/_vendor/book_to_skill/exceptions.py` | 添加 provenance 版权头；无功能改动 |
| `book_to_skill/parsers/docx.py` | `src/book2skill/extractors/_vendor/book_to_skill/docx.py` | 添加 provenance 版权头；改用包内 vendored `exceptions` 模块；移除 `extract_docx` 编排器与 `print` 调试日志（后端选择由 DocxExtractor 包装器接管）；其余抽取/校验逻辑无改动 |
| `book_to_skill/parsers/pdf.py` | `src/book2skill/extractors/_vendor/book_to_skill/pdf.py` | 添加 provenance 版权头；无功能改动 |
| `book_to_skill/parsers/calibre.py` | `src/book2skill/extractors/_vendor/book_to_skill/calibre.py` | 添加 provenance 版权头；将上游 `book_to_skill.config.OUTPUT_DIR` 依赖替换为按调用临时文件；新增临时目录清理；`ebook-convert` 调用逻辑无改动 |

此外，`src/book2skill/extractors/pdf_extractor.py` 包含一个本地新增的 `extract_with_pymupdf` 后端（非上游移植），用于在 PyMuPDF 可用时提供更可靠的纯 Python 抽取。

### License

完整许可证文本见 `LICENSES/MIT-upstream-virgilio-book-to-skill.txt`。

## 2. `apple-ouyang/book-to-skill`

- Repository: https://github.com/apple-ouyang/book-to-skill
- License: MIT
- Exact commit: `a24960ac89a3baa96a87cdf5ebaecf16c5d2eab1`
- Reuse type: `design_reference`
- Files ported: 无

---

## 3. Windows desktop dependency profile

Windows 内部预览安装器使用 `desktop-safe+build-windows` 依赖闭包。具体版本、
许可证字段、许可证文件路径和未声明项以安装器内的
`dependency-manifest.json` 为准；构建脚本从当前虚拟环境的发行包元数据离线生成，
不联网解析许可证。清单中的 `license_artifacts` 指向安装器内
`dependency-licenses/<package>/` 下随包携带的许可证证据副本，便于离线复核；
`checksums.sha256` 覆盖这些证据文件。此前 18 个 `review_required` 项已依据
PEP 639 `License-Expression` 元数据与随包许可证文件完成技术确认；没有该字段的
`clr-loader` 依据其随包 MIT `LICENSE` 文件确认。该确认是分发材料核对，不替代
法律意见；`setuptools` 的 vendored 组件许可证和 NOTICE 文件仍完整保留。

| 组件 | 用途 | 许可证/分发备注 |
|---|---|---|
| pywebview | 本地 WebGUI 壳 | BSD-3-Clause |
| pythonnet / clr-loader | Windows WebView 后端依赖 | MIT |
| bottle / proxy-tools | pywebview 本地 HTTP 支持 | MIT |
| pypdf / pdfminer.six | PDF 标准后端 | BSD-3-Clause / MIT |
| beautifulsoup4 / python-docx / lxml | HTML、DOCX 适配器 | MIT / MIT / BSD-3-Clause |
| openai | OpenAI-compatible Adapter | Apache-2.0 |
| PyInstaller | 冻结构建工具 | GPL-2.0-or-later with special exception；需随构建物保留其声明 |
| pyinstaller-hooks-contrib / pefile / pywin32-ctypes / altgraph | 冻结构建依赖 | 以 `dependency-manifest.json` 的逐包记录为准 |

`ebooklib`（AGPL）和 `PyMuPDF`（AGPL 或商业许可）不属于标准 Windows 桌面包，
并在 PyInstaller spec 中显式排除。EPUB 默认使用项目已有的 stdlib ZIP fallback；
如需启用高风险可选后端，必须单独完成许可证评估，不得将其混入对外标准包。

---

如未来继续选择性移植上游文件，必须在本文件和 `docs/PROVENANCE.yml` 中补充：
- 精确 Commit SHA
- 被复制文件的原始路径与本地路径
- 保留的版权头
- 修改说明
- 许可证文本副本（已保存至 `LICENSES/`）
