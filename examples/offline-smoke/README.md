# 离线最小示例

该示例只使用仓库内的短文本和 Mock LLM，不联网、不上传原文。命令从仓库根目录执行：

```bash
book2skill analyze examples/offline-smoke/input.txt \
  --llm mock \
  --data-home .workspace/tmp/offline-smoke/workspace \
  --bundle-dir .workspace/tmp/offline-smoke/bundles \
  --json
```

输出的 JSON bundle 位于指定的 `bundle-dir`，过程数据位于指定的临时 `data-home`；两者都不应作为产品运行数据提交。
