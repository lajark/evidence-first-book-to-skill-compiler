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

如未来继续选择性移植上游文件，必须在本文件和 `docs/PROVENANCE.yml` 中补充：
- 精确 Commit SHA
- 被复制文件的原始路径与本地路径
- 保留的版权头
- 修改说明
- 许可证文本副本（已保存至 `LICENSES/`）
