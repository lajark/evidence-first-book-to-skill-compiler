## Runtime execution scaffolding
The following fields are operator-provided runtime inputs, not facts extracted
from the source. Ask for them or state an explicit assumption before acting.

### Start-of-run clarification
- Current task/activity（当前任务/活动）:
- Available time（可用时间）:
- Urgent constraints or dependencies（紧急约束/依赖）:
- Estimate（估算）:
- Current activity/state（当前活动/状态）:
- First concrete action（第一步具体行动）:

### Estimate feedback record
Record one row for each completed attempt, then use the result to adjust the
next estimate or split the activity when the deviation is material.

| Date（日期） | Activity（活动） | Estimate（估算） | Actual（实际） | Deviation/cause（偏差/原因） | Interruptions（中断） | Next adjustment（后续调整） |
|---|---|---:|---:|---|---|---|

### State transition record
Use only the states supported by the method. Treat an emergency as a priority
exception: assess urgency and safety first, stop and record the incomplete work,
switch to the emergency response, and never splice unrelated session fragments.

| Current state（当前状态） | Trigger/condition（触发/条件） | Next state（下一状态） | Action and record（动作与记录） |
|---|---|---|---|
