# Layered Quality Gates

1. 内部质量门：frontmatter、来源、版权、注入、预算、高风险断言、运行脚手架和
   证据边界。来源/安全/版权硬门不被外部得分覆盖。
2. 官方参考层：锁定的 `skills-ref==0.1.0`，只用于规范一致性观察。
3. 第三方补充层：锁定的 `skill-validator==1.5.6`，补充结构、链接、Token 和内容
   质量检查；默认跳过网络链接。
4. 宿主证据层：分别记录结构、安装冒烟和真实运行等级。
5. 生成物内容完整性层：按 `source → normalized → Skill → package/installed copy` 相邻阶段，
   逐项复核稳定 unit/source ID、数量、章节内容、source refs、来源哈希和必要文件；最终载体
   必须重新打开检查 `content-integrity.json` 与 `compilation-artifact.json`。

外部进程始终使用参数数组、`shell=False`、超时、版本核对和路径脱敏。工具缺失、
版本不符、超时、非零退出码都保留独立状态和证据码；草稿 profile 不因可选工具
缺失中断，发布 profile 对 blocking 工具执行严格失败。

内容层是发布硬门：删除、截断、替换、重排、重复、额外单元、来源台账不一致、文件损坏或
未执行阶段均不得计为通过。`scripts/check_generated_skill_integrity.py` 在提供权威
SourceManifest 时执行完整来源复核；未提供时只能标记 internal-only。
