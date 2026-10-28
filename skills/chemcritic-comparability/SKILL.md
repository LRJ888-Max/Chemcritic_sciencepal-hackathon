---
name: chemcritic-comparability
description: 判定两条实验数值（文献或本实验）能否放在同一个比较里。用于文献先例比对、实验复盘、投稿前自查引用是否可比。输出可比性证书：可并列 / 禁止同框 / 需补证，并给出阻断维度与解锁条件。
version: 0.3.0
---

# 可比性判定 Skill

> 核心命题：**没有通过硬门槛的数字，不许同框。**
> 判定由 `/workspace/chemcritic/chemcritic_engine.py` 执行；模型只负责把证书翻译成人话，**不得口算、不得改写结论**。

## 何时使用

- 用户问"文献里这个收率能跟我比吗""我的结果算不算正常"
- 需要把多篇文献的数值汇总成一个区间时（**默认禁止**，除非全部通过门槛）
- 投稿 / 组会前自查："我引用的这几个数是不是同一个口径"

## 不适用

- 收率预测、条件推荐、反应路线设计
- 任何需要"猜"的场合：抽不到的字段一律 `null`，不补猜

## 执行（先跑脚本，再说话）

固定路径（自举后一定存在）：`/workspace/chemcritic/chemcritic_engine.py`

```bash
# 0. 只有在提示"文件不存在"时才自举（每个新对话一次）
cd /workspace && rm -rf .cc_src && git clone --depth 1 https://github.com/LRJ888-Max/Chemcritic_sciencepal-hackathon.git .cc_src && bash .cc_src/bootstrap.sh

# 1. 自检：确认沙箱里的引擎是好的（必须看到 23 passed, 0 failed）
python /workspace/chemcritic/chemcritic_engine.py --selftest

# 2. 判定：把你整理好的 Record JSON 交给引擎
python /workspace/chemcritic/chemcritic_engine.py --json --input user.json

# 3. 降级：只留一张证书摘要（贴回会话用；已带 citation / locator / locator_zh）
python /workspace/chemcritic/chemcritic_engine.py --brief --input user.json

# 4. 知识库盘点：无需实测值，一次列出全部参照（禁止 grep/sed 翻引擎源码）
python /workspace/chemcritic/chemcritic_engine.py --list-records

# 5. 换参照集（默认使用内嵌已核验记录）
python /workspace/chemcritic/chemcritic_engine.py --json --input user.json --records refs.json
```

- 零第三方依赖，Python 3.8+；单文件自包含（参照记录内嵌，不依赖工作目录）。
- `--json` 的每条证书都带 `reference.citation / doi / locator / tier`：**引用照抄证书，不要另找、不要凭记忆补**。
- 退出码：`0` 成功 / `1` 自检失败 / `2` 输入不合法。
- 产物由 `engine/build_bundle.py` 从 `engine/*.py` + `data/records.json` 生成，**不要直接改生成文件**。

## 输入：Record（原文没写的字段一律 null）

```json
{
  "record_id": "USER-001",
  "inputs_source": {"outcome.outcome_type": "user_stated", "conditions.base": "user_stated"},
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
- `inputs_source`（可选但强烈建议）：字段路径 → 来源。只允许 `user_stated`（用户本次明确提供）
  或 `unknown`（未提供，对应字段必须 null）。**`memory` 会被引擎直接拒绝（退出码 2）**——
  历史对话或账号记忆里的条件不得拼进 user.json。不声明时证书标 `not_declared`。
- `catalytic_profile` 决定档位（A/B/C）；缺了引擎按记录号查补丁表，再不行就不分档（只做同口径比较）。
- `label_raw` 是原文措辞，口径判定的唯一依据；不要用"看起来像"去推断。
  **用户侧同样受检**：写了 `outcome_type` 但 `label_raw` 里没有"分离/GC/核磁/转化"字样
  （例如只写"收了35%"），引擎会判 `NEEDS_EVIDENCE`，且档内定位返回 `UNDEFINED`。

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
- 证书顶层含 `inputs_source` 与 `input_audit`（pass / not_declared / rejected）；证书的
  `verdicts[].reference` 现在带 `locator_zh`（中文定位）与 `label_raw`（原文口径措辞）——
  需要"复述原文两处措辞"时直接引用它，不要翻源码或记录文件。
- 知识库盘点用 `--list-records`（一次输出 citation / locator / locator_zh / 数值 / 口径 / 档位）。

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
3. **引用照抄证书**：`reference.citation / locator` 是引擎给的，原文照抄；`locator_zh` 存在时定位用中文版。证书里没有的引用，一个字都不许加。
4. **未校准不外推**：`uncalibrated` 的参照只展示，不参与"差 X 个百分点"的结论。
5. **不露内部术语**：`L1` / `L2` / `D1–D8` / `record_id` / `soft_flags` 只进内部证书，用户文案改成化学说法（"溶剂同属非含水体系"）。
6. **输出长度**：【结论】/【为什么】/【下一步】三段，默认 ≤20 行；用户说"展开"才给证书明细。
7. **工具轮次零自然语言**：带工具调用的消息不写任何过程自述；最终答案只在最后一条消息出现一次。
8. **不读引擎源码**：引用、口径、档位一律取证书或 `--list-records`；禁止 grep/sed/cat 翻引擎文件。

## 实现与验收

- 引擎：`/workspace/chemcritic/chemcritic_engine.py`（单文件，纯 stdlib；源头是仓库 `engine/`）
- 自举：仓库根 `bootstrap.sh`，一条命令装到固定路径并自检，成功打印 `ENGINE_READY`
- 验收矩阵：`--selftest` → `最终：23 passed, 0 failed`（T1–T22；T20–T22 覆盖输入来源审计、
  参照元数据完整性与模糊口径双重拦截）
- 内置参照：`data/records.json` 的 19 条已核验记录（SM-001…SM-006、P11…P35，均带 locator_zh）
- 证书字段：`verdict` / `blocking_dimension` / `unlock_condition` / `calibration` /
  `reference{citation,doi,locator,locator_zh,value,outcome_type,label_raw,source_id,tier}` / `soft_flags`；
  顶层另含 `inputs_source` / `input_audit`
- 口径与规则版本：`gates v0.5 · tier-0.1`；引擎 `chemcritic-engine/1.1.0`
