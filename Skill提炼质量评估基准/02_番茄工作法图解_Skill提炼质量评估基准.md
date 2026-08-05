---
benchmark_id: "book2skill-pomodoro-illustrated-zh-v1"
benchmark_version: "1.0"
book_title: "番茄工作法图解"
original_title: "Pomodoro Technique Illustrated"
author: "Staffan Nöteberg"
benchmark_level: "L2-流程型Skill验证"
evaluation_focus:
  - "流程状态与步骤提炼"
  - "中断和异常处理"
  - "记录、估算、复盘与适应"
total_score: 100
recommended_pass_score: 70
language: "zh-CN"
---

# 《番茄工作法图解》Skill 提炼质量评估打分表

## 1. 测评目标

本基准用于评估程序能否从一本“流程、工具、规则、例外、复盘”并存的书中，提炼出真正可运行的流程型 Skill。

高质量候选 Skill 应完成：

```text
方法说明
→ 流程状态机
→ 表单与记录
→ 中断处理
→ 估算与反馈
→ 个体/团队适配
```

仅生成“25分钟工作、休息5分钟”的计时说明，不能视为通过。

## 2. 评测对象与版本容差

- 评测对象：由《番茄工作法图解》生成的完整 Skill 包。
- 候选 Skill 可使用纸笔、表格、命令行或软件化表达，但核心流程必须一致。
- 时间长度允许作为默认值或可调参数；不得因个体化调整而破坏专注区间的完整性和记录逻辑。
- 中文版术语可能不同，例如“活动清单/活动库存”“今日待办”“记录表”；语义等价即可。
- 团队协作属于扩展能力，不应取代个人流程主线。


## 3. 通用评分方法

### 3.1 单项评分锚点

每个评分项先给出 `rating`，再换算为得分：

| rating | 含义 | 得分比例 |
|---:|---|---:|
| 0 | 缺失、不可用，或与原书明显冲突 | 0% |
| 1 | 仅提及概念，存在严重缺口或无法执行 | 25% |
| 2 | 基本正确，但覆盖、结构或执行性不足 | 50% |
| 3 | 正确且可用，仅有少量非关键缺口 | 75% |
| 4 | 准确、完整、结构清晰，可直接稳定调用 | 100% |

计算方式：

```text
item_score = item_max_score × rating ÷ 4
total_score = Σ item_score - penalties
```

允许保留 0.25 分；最终总分四舍五入至 1 位小数。

### 3.2 证据规则

- 只对候选 Skill 中**可定位、可验证**的内容计分。
- 每个评分项必须记录证据位置，例如：`SKILL.md > 使用流程 > 第2步`。
- 同一证据可以支持多个相互独立的评分项，但不得对同一能力重复计分。
- 仅存在于原始电子书、程序日志或评测者常识中，而未进入候选 Skill 的内容，不计分。
- 术语、译名、章节名允许存在版本差异；语义等价即可。
- 候选 Skill 新增的通用知识必须明确标为“扩展建议”或“非原书内容”，否则按混淆来源处理。

### 3.3 等级与通过条件

| 总分 | 等级 | 结论 |
|---:|---|---|
| 90–100 | S | 高质量，可作为正式 Skill 使用 |
| 80–89.9 | A | 良好，少量修订后可用 |
| 70–79.9 | B | 基本通过，仍需补齐关键缺口 |
| 60–69.9 | C | 未通过，提炼质量不稳定 |
| <60 | D | 未通过，不具备可靠使用价值 |

除总分外，还必须同时满足本文件规定的“专项通过条件”。

### 3.4 封顶规则

出现下列情况时，即使加总分更高，也执行封顶：

| 问题 | 总分上限 |
|---|---:|
| 文件无法解析、入口缺失，AI 无法知道如何调用 | 20 |
| 产物实质上只是读书摘要，没有可执行的 Skill 行为 | 45 |
| 核心方法被系统性误解或反向表达 | 55 |
| 大量编造原书不存在的原则，且未标注为扩展 | 60 |
| 依赖大段复制原书才能工作，缺乏转化与抽象 | 65 |


## 4. 专项评分表

### A. 核心流程与状态完整性（30分）

| item_id | max_score | 满分要求 |
|---|---:|---|
| A1 | 8 | 给出完整日循环：收集活动 → 当日选择与承诺 → 启动专注区间 → 休息 → 完成标记 → 记录/处理 → 复盘。 |
| A2 | 6 | 强调一次只做一项活动，专注区间不是随意切换任务的容器。 |
| A3 | 5 | 清楚区分短休息、较长恢复和工作状态；休息用于脱离任务而非继续工作。 |
| A4 | 5 | 能处理活动完成、未完成、提前完成和跨多个番茄的状态变化。 |
| A5 | 6 | 将流程表达为可判断的状态、事件、转移和动作，而不是松散建议集合。 |

### B. 工具、表单与数据模型（15分）

| item_id | max_score | 满分要求 |
|---|---:|---|
| B1 | 4 | 包含活动清单/库存，用于收集候选工作。 |
| B2 | 4 | 包含今日待办或等价结构，用于当日选择、排序和承诺。 |
| B3 | 4 | 包含记录表或等价日志，可记录完成番茄、估算、实际和中断。 |
| B4 | 3 | 定义必要字段、标记或数据状态，AI 能创建和更新这些记录。 |

### C. 中断与异常处理（20分）

| item_id | max_score | 满分要求 |
|---|---:|---|
| C1 | 6 | 内部中断采用“觉察/接受 → 记录 → 返回当前任务”的闭环。 |
| C2 | 6 | 外部中断采用保护当前番茄、协商、记录并安排稍后处理的策略。 |
| C3 | 4 | 表达番茄的原子性：真正被打断时不能把碎片冒充完整番茄；给出取消、重新开始或记录方式。 |
| C4 | 2 | 对真实紧急事件保留例外，不以维护计时器为由忽略安全或重大责任。 |
| C5 | 2 | 能处理“提前完成”“等待系统响应”“临时想到小事”等边缘情况。 |

### D. 估算、反馈与适应（15分）

| item_id | max_score | 满分要求 |
|---|---:|---|
| D1 | 5 | 使用番茄作为抽象工作量单位，对活动进行估算和拆分。 |
| D2 | 4 | 比较估算与实际，记录误差并用于后续改进。 |
| D3 | 3 | 包含日终或周期复盘，能识别中断、过载、低估和节奏问题。 |
| D4 | 3 | 支持基于数据的小步调整；明确“适应”不是随意删除核心规则。 |

### E. 交互式教练与场景表现（10分）

| item_id | max_score | 满分要求 |
|---|---:|---|
| E1 | 6 | 完成第6节六个场景测试；每个场景 1 分。 |
| E2 | 2 | 在启动前询问任务、可用时间、限制和当前清单，不凭空安排整天。 |
| E3 | 2 | 输出下一步行动清晰，可直接开始、暂停、记录、复盘或调整。 |

### F. AI 可读性与工程质量（10分）

| item_id | max_score | 满分要求 |
|---|---:|---|
| F1 | 3 | 入口文件明确触发条件、输入、状态、步骤、输出及异常路径。 |
| F2 | 2 | 流程、规则、模板和背景知识分开存放或清晰分区。 |
| F3 | 2 | 时间、任务、估算、中断等参数命名一致，状态不会互相冲突。 |
| F4 | 2 | 提供至少一个可复用记录模板或数据结构。 |
| F5 | 1 | Markdown 规范、无乱码、无明显重复，内部链接有效。 |

## 5. 最低流程模型

候选 Skill 不必使用以下字段名，但必须具备等价能力。

```yaml
states:
  - inbox
  - planned_today
  - focusing
  - short_break
  - long_break
  - interrupted
  - completed
  - reviewed

core_entities:
  activity:
    fields: [title, priority, estimate_pomodoros, actual_pomodoros, status]
  pomodoro_session:
    fields: [activity_id, start_time, planned_length, outcome, interruption_count]
  interruption:
    fields: [type, note, urgent, follow_up_time]
  daily_record:
    fields: [date, completed_pomodoros, estimates, actuals, observations]
```

专项通过条件：

```yaml
must_meet:
  total_score_gte: 70
  dimension_A_score_gte: 18
  dimension_C_score_gte: 12
  scenario_pass_count_gte: 5
  has_executable_daily_cycle: true
```

## 6. 场景测试集

### CASE-PO-01｜开始一天

```yaml
input:
  activities:
    - "写项目周报"
    - "回复普通邮件"
    - "修复线上故障"
  available_time: "3小时"
expected_behavior:
  - "先澄清线上故障是否真实紧急"
  - "从活动清单形成今日计划"
  - "为任务排序并估算番茄数"
  - "指定当前唯一活动并启动第一个专注区间"
```

### CASE-PO-02｜内部中断

```yaml
input:
  current_state: "focusing"
  distraction: "突然想起要订机票"
expected_behavior:
  - "快速记录该事项"
  - "不立即搜索机票"
  - "返回当前活动"
```

### CASE-PO-03｜外部中断

```yaml
input:
  current_state: "focusing"
  interruption: "同事询问一个可以稍后回答的问题"
expected_behavior:
  - "礼貌说明当前不可用"
  - "约定稍后回复时间"
  - "记录事项并继续当前番茄"
```

### CASE-PO-04｜不可避免的紧急中断

```yaml
input:
  current_state: "focusing"
  interruption: "生产系统出现严重故障，需要立即响应"
expected_behavior:
  - "中止当前番茄并记录为未完成/被打断"
  - "切换到紧急任务"
  - "不得把前后碎片合并计为一个完整番茄"
```

### CASE-PO-05｜任务提前完成

```yaml
input:
  current_state: "focusing"
  remaining_time: "7分钟"
  activity_status: "已完成"
expected_behavior:
  - "不随意开始无关大型任务"
  - "可用于检查、整理、复核或等待区间结束"
  - "保持对当前活动上下文的完整性"
```

### CASE-PO-06｜估算连续失准

```yaml
input:
  observation: "连续三天实际番茄数约为估算的两倍"
expected_behavior:
  - "分析活动拆分、任务类型和中断记录"
  - "更新后续估算基准"
  - "提出小步调整，而非宣告方法无效"
```

## 7. 典型扣分项

| penalty_id | 问题 | 扣分 |
|---|---|---:|
| P01 | 把番茄工作法简化为单一计时器 | -10 |
| P02 | 没有活动清单、今日计划或记录机制 | 每缺一类 -3 |
| P03 | 中断后暂停计时，回来继续并仍算完整番茄，且无说明 | -4 |
| P04 | 鼓励休息时继续处理同一工作 | -3 |
| P05 | 只记录完成数量，不进行估算误差或复盘 | -3 |
| P06 | 任意修改时长、流程和规则，却不保留实验记录 | -2 至 -6 |
| P07 | 把所有外部中断都粗暴拒绝，不识别紧急情况 | -3 |


## 8. AI 评测输出格式

评测 AI 必须按以下顺序输出，禁止只给总分。

### 8.1 人类可读结果

```markdown
# Skill 提炼质量评测结果

- benchmark_id:
- candidate_skill:
- evaluator:
- evaluation_date:
- raw_score:
- penalty:
- capped_score:
- final_score:
- grade:
- pass: true | false
- confidence: high | medium | low

## 分项评分
| item_id | max_score | rating_0_to_4 | awarded_score | evidence | rationale |
|---|---:|---:|---:|---|---|

## 场景测试
| case_id | pass | evidence_or_observed_behavior | issue |
|---|---|---|---|

## 关键缺失
1. ...

## 事实错误或来源混淆
1. ...

## 优先修订建议
1. ...
2. ...
3. ...
```

### 8.2 机器可读结果

```json
{
  "benchmark_id": "",
  "benchmark_version": "1.0",
  "candidate_skill": "",
  "raw_score": 0,
  "penalty": 0,
  "cap_applied": null,
  "final_score": 0,
  "grade": "S|A|B|C|D",
  "pass": false,
  "confidence": "high|medium|low",
  "dimension_scores": {},
  "critical_missing": [],
  "factual_errors": [],
  "scenario_results": [],
  "revision_priorities": []
}
```
