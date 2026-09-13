# Evidence-first Book2Skill launch note

This is the proposed first public post. It is a reviewable draft only; do not
publish it until the MIT authorization, provenance audit, CI, and release gates
listed in the repository documentation are complete.

## English version

> **Turning books into AI Skills is easy. Proving where the Skill came from is harder.**

Evidence-first Book2Skill compiles documents into deployable Agent Skills while
keeping the evidence boundary visible: immutable source hashes, stable block
locators, source-linked knowledge units, reviewable quality reports, and a
fail-closed content-integrity gate. The public example is repository-authored,
offline, and reproducible with a Mock LLM.

Start with the [three-minute demo](../examples/evidence-first-demo/README.md),
then read the [Benchmark v1 contract](BENCHMARK.md) to see what is measured and
what remains `not_measured`. The project is local-first and does not require
uploading a user's source document.

## 中文版本

> **把书籍变成 AI Skill 很容易；更难的是证明这个 Skill 来自哪里。**

Evidence-first Book2Skill 在生成可部署 Agent Skill 的同时，把证据边界保留下来：
不可变来源哈希、稳定 block locator、带来源的知识单元、可审核的质量报告，以及
失败即阻断的内容完整性闸门。公开案例使用仓库原创短文、离线 Mock LLM，并支持
确定性重跑。

请先运行[三分钟案例](../examples/evidence-first-demo/README.md)，再阅读
[Benchmark v1 合同](BENCHMARK.md)，区分已经测量的结构性指标和仍为
`not_measured` 的语义指标。项目采用本地优先方式，不要求上传用户源文档。

## Publication sequence

1. Confirm MIT authorization and complete the license/provenance/CI/release gates.
2. Publish the English post and this Chinese translation on GitHub first.
3. Adapt the same evidence and boundaries for one Chinese technical community.
4. Evaluate Hacker News or Reddit only after the first two channels are stable.

Never include source books, extracted full text, private paths, credentials, or
raw provider responses in a post or issue.
