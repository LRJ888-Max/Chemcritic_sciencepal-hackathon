# A2 · 定位 Agent

**平台：** SciencePal · **Sandbox**（确定性 Python）+ **Skill**
**优先级：** P0（产品的核心，也是最不可替代的一步）
**实现：** `engine/gates.py` + `engine/placement.py`

---

## 职责

1. 对每条文献记录与用户实验做 **8 维门槛判定** → 输出证书
2. 按 `outcome_type` 做**等价类划分**
3. 在等价类内按**档**定位用户值 → 输出"落在哪一档、档内区间、差距"
4. 生成**取证清单**（`unlock_condition`）

## 输入 / 输出

输入：`USER` 记录 + `data/records.json`
输出：`Verdict[]` + `PlacementResult` + `Coverage`

```json
{"verdicts": [{"reference_id": "SM-004.1", "verdict": "NEEDS_EVIDENCE",
               "blocking_dimension": "outcome_type",
               "unlock_condition": "请确认该数值的口径（分离 / GC / NMR / 转化率）"}],
 "classes": [{"outcome_type": "isolated", "values": [40, 100, 95]}],
 "coverage": {"largest_class": 3, "total_values": 8, "coverage": 0.375},
 "placement": {"status": "PLACED", "tier": "A", "reference_values": [40],
               "delta_vs_min": -5.0}}
```

## 铁律

1. **判定必须由沙箱代码执行，LLM 只负责解释输出**。禁止"让模型自己判断一下"。
2. **硬门槛任一 FAIL → BLOCKED；任一 UNKNOWN → NEEDS_EVIDENCE**（FAIL 优先）。
3. **unknown 口径纪律（硬，2026-10-27）**：`outcome_type=unknown`（口径未决，如 SM-004.1/4.7）时，证书结论只能是 `NEEDS_EVIDENCE` + 阻断维度 + 解锁条件；**禁止**把 unknown 表述为确定口径（GC/NMR/conversion 等），**禁止**误判为 `BLOCKED`。
4. **软门槛只产出 L1/L2 标注**，不得改变结论。
5. **跨档不输出"正常区间"**，只输出迁移目标。
6. 覆盖率分母**包含**口径未决的值（它们是报告值，只是进不了等价类）。

## 平台绑定

- 沙箱执行：`python engine/run_demo.py --json`
- Skill：`skills/chemcritic-comparability/SKILL.md`（8 维规则可导出复用）
- 输出需带 `rule_version`，便于复现

## 失败处理

| 情况 | 动作 |
|---|---|
| 用户 `outcome_type` 未定 | 返回 `UNDEFINED` + 追问口径，不进入比较 |
| 同类口径下无记录 | 返回 `NO_COMPARABLE` + 建议补充哪类口径 |
| 可比块为空 | 仍输出分堆与阻断清单（空块本身是结论） |

## 验收

`python engine/run_demo.py` → **12 passed, 0 failed**（对应规则表 §4 的 T1–T12）。

## TODO（C 填）

- [ ] 把 `records.json` 换成知识库检索结果（当前是静态文件）
- [ ] 加 `rule_version` 与运行时间戳，写入输出
- [ ] 底物比对换成 rdkit canonical SMILES
