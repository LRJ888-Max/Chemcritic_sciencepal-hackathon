---
name: chemcritic-comparability
description: 判定两条实验数值（文献或本实验）能否放在同一个比较里。用于文献先例比对、实验复盘、投稿前自查引用是否可比。输出可比性证书：可并列 / 禁止同框 / 需补证，并给出阻断维度与解锁条件。
version: 0.2.0
---

# 可比性判定 Skill

> 核心命题：**没有通过硬门槛的数字，不许同框。**
> 判定由 `scripts/chemcritic_engine.py` 执行；模型只负责把证书翻译成人话，**不得口算、不得改写结论**。

## 何时使用

- 用户问"文献里这个收率能跟我比吗""我的结果算不算正常"
- 需要把多篇文献的数值汇总成一个区间时（**默认禁止**，除非全部通过门槛）
- 投稿 / 组会前自查："我引用的这几个数是不是同一个口径"

## 不适用

- 收率预测、条件推荐、反应路线设计
- 任何需要"猜"的场合：抽不到的字段一律 `null`，不补猜

## 执行（先跑脚本，再说话）

```bash
# 1. 自检：确认沙箱里的引擎是好的（必须看到 19 passed, 0 failed）
python scripts/chemcritic_engine.py --selftest

# 2. 判定：把你整理好的 Record JSON 交给引擎
python scripts/chemcritic_engine.py --json --input user.json

# 3. 降级：只需要一张几百字节的证书摘要（贴回会话用）
python scripts/chemcritic_engine.py --brief --input user.json

# 4. 换参照集（默认内嵌 8 条已核验 L2 记录）
python scripts/chemcritic_engine.py --json --input user.json --records refs.json
```

- 零第三方依赖，Python 3.8+；单文件自包含（参照记录内嵌，不依赖工作目录）。
- 退出码：`0` 成功 / `1` 自检失败 / `2` 输入不合法。
- 产物由 `engine/build_bundle.py` 从 `engine/*.py` + `data/records.json` 生成，**不要直接改生成文件**。

## 输入：Record（原文没写的字段一律 null）

```json
{
  "record_id": "USER-001",
  "outcome": {"value": 35, "outcome_type": "isolated", "label_raw": "分离收率（柱层析，称重）"},
  "substrate": {"aryl_halide": "4-bromoanisole", "boronic_acid": "phenylboronic acid",
                "product": "4-methoxybiphenyl"},
  "conditions": {"catalyst": "Pd(PPh3)4", "catalyst_mol_pct": 3.0, "base": "Na2CO3", "base_eq": 2.0,
                 "solvent": "THF", "temp_c": 60, "time_h": 12, "atmosphere": "N2",
                 "scale_mmol": 1.0, "equivalents": {"aryl_halide": 1.0, "boronic_acid": 1.2}},
  "assay": {"method": "isolated (weighed)", "internal_standard": null,
            "quantification_basis": "isolated product"},
  "catalytic_profile": {"catalyst_form": "molecular_complex", "ligand_family": "phosphine",
                        "support": "none", "medium": "organic", "activation_mode": "thermal"}
}
```

- `outcome.outcome_type` 只能是 `isolated` / `gc` / `nmr` / `conversion`；不确定就写 `unknown`，引擎会判 `NEEDS_EVIDENCE`。
- `catalytic_profile` 决定档位（A/B/C）；缺了引擎按记录号查补丁表，再不行就不分档（只做同口径比较）。
- `label_raw` 是原文措辞，口径判定的唯一依据；不要用"看起来像"去推断。

## 输出：可比性证书

```json
{"subject": "USER-001",
 "coverage": {"total_values": 8, "largest_class": 3, "coverage": 0.375, "unresolved": 2},
 "verdicts": [
   {"subject_id": "USER-001", "reference_id": "SM-001b", "verdict": "COMPARABLE",
    "blocking_dimension": null, "unlock_condition": null,
    "calibration": "calibrated", "ref_calibration": "calibrated",
    "non_independent": false,
    "gates": {"1": {"dimension": "substrate", "status": "PASS", "detail": "底物对一致"}},
    "soft_flags": [{"dimension": "base_solvent_temp_time_atmosphere", "level": "L2",
                    "detail": "温度相差 20 °C"}]}
 ],
 "placement": {"status": "PLACED", "tier": "A", "reference_values": [40],
               "delta_vs_min": -5, "delta_vs_max": -5, "uncalibrated_refs": []}}
```

- `verdict` 三值 `COMPARABLE` / `BLOCKED` / `NEEDS_EVIDENCE` **只来自脚本**。
- `calibration` 为 `uncalibrated` 的参照只可展示，**不进档内定位的差值结论**。
- `non_independent=true` 表示同篇不同条目，不得当两条独立来源。

## 8 维门槛（脚本内的判定顺序）

| # | 维度 | 类型 | 通过条件 |
|---|---|---|---|
| 1 | 底物对一致 | 硬 | 芳基卤 + 硼酸结构一致（离去基不同即不同底物） |
| 2 | 产物一致 | 硬 | 产物结构一致 |
| 3 | outcome_type | 硬 | 同类且均非 unknown |
| 4 | assay / 校准 | 硬 | 口径标记被原文措辞支撑、证据等级达标、定量方法可支撑 |
| 5 | 规模 / 当量 / 浓度 | 软 | — |
| 6 | 催化体系 | 软 | — |
| 7 | 碱 / 溶剂 / 温度 / 时间 / 气氛 | 软 | — |
| 8 | 后处理与纯化 | 软 | — |

**结论优先级：** 硬门槛 FAIL → `BLOCKED`；否则硬门槛 UNKNOWN → `NEEDS_EVIDENCE`；否则 `COMPARABLE`。

## 三条禁令

1. **禁止换算**：GC 转化率不得折成分离收率；NMR 定量不得当作收率。
2. **禁止区间拼接**：不同口径的数值不得合成一个范围（这是最常见的假精确）。
3. **禁止跨档定义"异常"**：跨档只谈迁移目标。

## 给平台 Agent 的硬纪律

1. **先说来源**：每个 `verdict` 必须能指认脚本输出。没跑到脚本 → 只能回答"本次未执行判定"，不得给结论。
2. **不得补文献**：模型记忆里的文献、数值、DOI 一律不得作为先例；先例只能来自参照记录集。
3. **未校准不外推**：`uncalibrated` 的参照只展示，不参与"差 X 个百分点"的结论。
4. **输出长度**：【结论】/【为什么】/【下一步】三段，默认 ≤20 行；用户说"展开"才给证书明细。

## 实现与验收

- 引擎：`scripts/chemcritic_engine.py`（单文件，纯 stdlib；源头是仓库 `engine/`）
- 验收矩阵：`--selftest` → `最终：19 passed, 0 failed`（T1–T18，对应 `docs/rules/8维门槛规则表-待签字.md` §4）
- 内置参照：`data/records.json` 的 8 条已核验 L2 记录（SM-001…SM-006）
- 口径与规则版本：`gates v0.4 · tier-0.1`
