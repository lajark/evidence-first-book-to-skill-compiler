# Acknowledgments

本项目在设计与实现过程中参考或选择性移植了以下开源项目。实际移植项必须与 `docs/PROVENANCE.yml` 一致。

## Upstream projects

### virgiliojr94/book-to-skill
- Repository: https://github.com/virgiliojr94/book-to-skill
- License observed at specification stage: MIT
- Exact commit used: `92b248fa5e7039d770d56630444310e36ff014e0`
- Reuse type: `selective_port`
- Files/ideas reused:
  - `book_to_skill/sanitize.py` — invisible Unicode codepoint removal
  - `book_to_skill/parsers/text.py` — BOM-aware plain-text reader
  - `book_to_skill/parsers/html.py` — HTML → plain text (stdlib + optional BeautifulSoup)
  - `book_to_skill/parsers/epub.py` — EPUB extraction (ebooklib + stdlib zip fallback)
  - `book_to_skill/exceptions.py` — `ExtractionError` for non-fatal extraction failures
  - `book_to_skill/parsers/docx.py` — DOCX extraction (python-docx + stdlib zip fallback) and XML safety validation
  - `book_to_skill/parsers/pdf.py` — PDF backends (pdftotext / pypdf / pdfminer / docling)
  - `book_to_skill/parsers/calibre.py` — MOBI/AZW extraction via Calibre `ebook-convert`
- Local modifications:
  - 添加 provenance 版权头
  - `parsers/text.py` 模块重命名后置于 `extractors/_vendor/book_to_skill/text.py`
  - 各 vendored parser 的包内导入改为本包内相对引用（`text` / `html` / `exceptions`）
  - `parsers/docx.py` 移除 `extract_docx` 编排器与 `print` 调试日志，后端选择由 `DocxExtractor` 包装器接管
  - `parsers/calibre.py` 将上游 `OUTPUT_DIR` 依赖替换为按调用临时文件并新增清理
  - 通过 `extractors/*_extractor.py` 包装器接入 Book2Skill 的 `SourceManifest` / `ExtractionMapEntry` 体系
  - `pdf_extractor.py` 额外新增本地 `extract_with_pymupdf` 后端（非上游移植）

### apple-ouyang/book-to-skill
- Repository: https://github.com/apple-ouyang/book-to-skill
- License observed at specification stage: MIT
- Exact commit used: `a24960ac89a3baa96a87cdf5ebaecf16c5d2eab1`
- Reuse type: `design_reference`
- Files/ideas reused: None at M0; candidate areas documented in `docs/PROVENANCE.yml`
- Local modifications: N/A

感谢上述项目作者和贡献者。第三方组件仍受其原许可证约束。
