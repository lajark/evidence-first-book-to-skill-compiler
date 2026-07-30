# 格式适配器与降级策略

| 格式 | P0 首选 | 可选增强 | 失败/降级 |
|---|---|---|---|
| PDF | `pdftotext`/PyMuPDF 类文本提取 | Docling/结构化解析、OCR | 加密/损坏明确报错；无文本提示 OCR，不自动上传 |
| EPUB | ebooklib/ZIP + spine | 元数据和脚注增强 | 损坏 spine 输出部分成功报告 |
| MOBI/AZW | Calibre CLI Adapter | 其他合法解析器 | 未安装时给离线安装步骤；疑似 DRM 停止 |
| DOCX | python-docx | 表格/脚注/批注增强 | 不支持对象列入缺失清单 |
| TXT | 编码探测 + 纯文本 | 章节启发式 | 编码不明时要求选择，不静默替换 |
| Markdown | 原样结构归一 | 链接与代码块增强 | 防路径穿越，不抓取远程链接 |

每个 Adapter 实现 `probe/extract/capabilities/diagnostics`，不得把最终 Skill 编译逻辑放入格式层。
