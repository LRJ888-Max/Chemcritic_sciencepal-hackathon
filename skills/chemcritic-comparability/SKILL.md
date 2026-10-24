---
name: chemcritic-comparability
description: 判定两条实验数值（文献或本实验）能否放在同一个比较里。用于文献先例比对、实验复盘、投稿前自查引用是否可比。输出可比性证书：可并列 / 禁止同框 / 需补证，并给出阻断维度与解锁条件。
version: 0.1.0
---

# 可比性判定 Skill

> 核心命题：**没有通过硬门槛的数字，不许同框。**

## 何时使用

- 用户问"文献里这个收率能跟我比吗""我的结果算不算正常"
- 需要把多篇文献的数值汇总成一个区间时（**默认禁止**，除非全部通过门槛）
- 投稿 / 组会前自查："我引用的这几个数是不是同一个口径"

## 不适用

- 收率预测、条件推荐、反应路线设计
- 任何需要"猜"的场合：抽不到的字段一律 `null`，不补猜

## 判定流程（确定性，非模型判断）

```text
1. 分堆：按 outcome_type（isolated / gc / nmr / conversion）分组，unknown 单独一组
2. 过闸：对每个候选做 8 维门槛判定
3. 出证：COMPARABLE / BLOCKED(dim, unlock) / NEEDS_EVIDENCE(dim, unlock)
4. 定位：仅在等价类内比较；跨档只输出迁移目标，不输出"正常区间"
5. 覆盖：最大等价类 / 报告值总数（分母含 unknown）
```

## 8 维门槛

| # | 维度 | 类型 | 通过条件 |
|---|---|---|---|
| 1 | 底物对一致 | 硬 | 芳基卤 + 硼酸结构一致（离去基不同即不同底物） |
| 2 | 产物一致 | 硬 | 产物结构一致 |
| 3 | outcome_type | 硬 | 同类且均非 unknown |
| 4 | assay / 校准 | 硬 | isolated：明确分离称重；gc：内标 + 定量基准；nmr：内标 + 采样方式 |
| 5 | 规模 / 当量 / 浓度 | 软 | — |
| 6 | 催化体系 | 软 | — |
| 7 | 碱 / 溶剂 / 温度 / 时间 / 气氛 | 软 | — |
| 8 | 后处理与纯化 | 软 | — |

**结论优先级：** 硬门槛 FAIL → `BLOCKED`；否则硬门槛 UNKNOWN → `NEEDS_EVIDENCE`；否则 `COMPARABLE`。

## 输出契约

```json
{"reference_id": "SM-004.1",
 "verdict": "NEEDS_EVIDENCE",
 "blocking_dimension": "outcome_type",
 "unlock_condition": "请确认该数值的口径（分离 / GC / NMR / 转化率）",
 "soft_flags": [{"dimension": "catalyst_system", "level": "L2", "detail": "催化剂不同：..."}]}
```

## 三条禁令

1. **禁止换算**：GC 转化率不得折成分离收率；NMR 定量不得当作收率。
2. **禁止区间拼接**：不同口径的数值不得合成一个范围（这是最常见的假精确）。
3. **禁止跨档定义"异常"**：跨档只谈迁移目标。

## 实现

规则引擎：`engine/gates.py`（确定性 Python，LLM 不得覆盖）
档位与分布：`engine/placement.py`
验收用例：`python engine/run_demo.py` → 12 passed

规则版本与字段口径见 `docs/rules/8维门槛规则表-待签字.md`。
