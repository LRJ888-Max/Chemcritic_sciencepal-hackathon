# A3 · 机制 Agent

**平台：** SciencePal · **Subagent**（≤10 并发）+ **Knowledge Base** + **Memory**
**优先级：** P1（10/28 未串通 P0 则砍）
**知识源：** `docs/rules/机制库v0-待签字.md`

---

## 职责

当 A2 判定"用户值低于本档最低值"时，并行推演候选机制，每个机制必须**可在一次实验内被支持或排除**。

**一个机制 = 一个子 Agent**：父级派 N 个子任务（N = 候选机制数），每个子任务独立检索该机制的文献支持与反证，返回结构化结果。父级负责合并、去重、排序。

## 输入

```json
{"placement": {"tier": "A", "observed": 35, "reference_min": 40},
 "conditions": {"catalyst": "Pd(PPh3)4", "solvent": "THF", "temp_c": 60, "time_h": 12},
 "missing_fields": ["base", "catalyst_mol_pct", "scale_mmol"]}
```

## 输出

```json
{"mechanisms": [{"id": "M5", "name": "后处理/纯化损失",
                 "rationale": "...",
                 "supporting_refs": [{"record_id": "SM-001b", "note": "同篇 66% GLC vs 40% 分离"}],
                 "counter_refs": [],
                 "discriminating_test": {"action": "物料衡算", "metric": "转化率 − 分离收率",
                                         "cost_days": 0.5, "support_if": ">15 个百分点",
                                         "exclude_if": "<5 个百分点"},
                 "confidence": "supported | speculative",
                 "prior_rank": 1}]}
```

## 铁律

1. **每个机制必须绑定至少一个判别实验**，无法区分的机制不入库。
2. **无文献支撑 → `confidence=speculative`，不得进前 2 位**，且前端必须显示"无文献支撑"。
3. **只给候选，不给诊断**。禁止输出"原因就是 X"。
4. **不得生成通用建议清单**（升温、换溶剂之类），除非它对应某个具体机制的判别实验。
5. 同一篇文献的多条 entry 不得当作多条独立证据。

## 平台绑定

- Subagent：父级 → N 个子 Agent（general/explore 角色）
- 知识库：每个子 Agent 检索自己的机制关键词
- Memory：复用历史判读（同一体系此前排查过什么）

## 失败处理

| 情况 | 动作 |
|---|---|
| 子 Agent 超时 | 返回已完成的机制，缺的标 `incomplete` |
| 全部无证据 | 输出"当前证据不足以列出候选"，不排优先级 |
| 机制过少（≤1） | 不进入 A4 决策树，只输出一条验证实验 |

## TODO（C 填）

- [ ] 子 Agent 的 prompt 模板（每机制一个）
- [ ] 合并去重规则（同一机制不同表述）
- [ ] `prior_rank` 由"单位成本排除机制数"计算，而非由模型排序
