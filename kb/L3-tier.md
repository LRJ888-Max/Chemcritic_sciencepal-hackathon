# L3 · 分档定义（档 A / B / C）

> **用途：** 回答「这个值属于哪个体系区间」。**"异常"只在档内定义**；跨档只谈迁移目标，不谈正常区间。
> **权威版本：** `engine/tier.py`（受控词表 → 规则 → 主判/备选）。
> **重要：分档只表示体系族不同，不表示优劣。** 档 A 不等于落后，档 B 不等于更好。

---

## 一、三档与判据

| 档 | 名称 | 判据 |
|---|---|---|
| **A** | 分子膦配体均相 Pd | 分子配合物 + **膦类配体**（含亚膦酸），无固载，有机介质 |
| **B** | 无配体 / 水相 Pd | **无外加配体**，Pd 为 Pd(II) 盐（含原位生成 Pd(0)），含水介质 |
| **C** | 特化体系 | 负载/固载、**预先制备的纳米颗粒**、非膦配体（NHC、pincer、氮/硫配体）、无溶剂 |

## 二、判档顺序（物理形态 > 配体族 > 介质）

```text
1. support = solid_support                → C   (R1)
2. medium  = solventless                  → C   (R2)
3. catalyst_form = nanoparticle（预先制备） → C   (R3)
4. catalyst_form ∈ {supported, bulk_metal} → C   (R4)
5. ligand_family = non_phosphine          → C   (R5)
6. ligand_family = none 且 medium = aqueous → B   (R6)
7. ligand_family = none 且 medium = organic  → B   (R7，2026-10-28 补漏)
7. 兜底：分子配合物 + 膦类配体              → A   (R0)
```

**为什么物理形态优先：** 活性物种是不是"分离相"，比"有没有配体"更能解释收率差异与非均相行为（可回收性、烧结、浸出）。

## 三、受控词表（抽取层只能填这些）

| 字段 | 取值 |
|---|---|
| `catalyst_form` | molecular_complex / in_situ_salt / nanoparticle / supported / bulk_metal |
| `ligand_family` | phosphine / non_phosphine / none |
| `support` | none / solid_support |
| `medium` | aqueous / organic / solventless |
| `activation_mode` | thermal / mechanochemical / microwave |

## 四、边界情形

| 情形 | 处置 |
|---|---|
| **多规则命中且结论不同**（如 SM-005：纳米颗粒 → C，无配体水相 → B） | 标 `borderline = [B, C]`、`confidence = low`，主判取物理形态（C）；输出时做**双档敏感性分析** |
| 机械化学 | 不单列为档 D；记 `activation_mode = mechanochemical`，在维度 7 标注 |
| NHC-Pd | 按 R5 归 C（**已知局限**：其操作谱系接近膦配体 Pd） |
| 同一档内条件跨度大 | 由软维度 5–7 承担，不得靠档掩盖 |

## 五、首批记录归档

| 记录 | 命中规则 | 档 |
|---|---|---|
| SM-001（Pd(PPh₃)₄/Na₂CO₃/苯） | R0 | A |
| SM-003（Pd(OAc)₂ + SPO/NaOtBu/THF） | R0 | A |
| SM-002（PdCl₂ 无配体/水相） | R6 | B |
| SM-004（Pd-bpydc-Ln MOF） | R1+R4 | C |
| SM-005（生物法 Pd NPs，边界） | R3+R6 | C（borderline B/C） |
| SM-006（SNS-Pd pincer） | R5 | C |
| P11（clinochlore-CQD@Pd 负载型） | R1 | C |
| P19（Pd(OAc)₂ + NHC 7a） | R5 | C |
| P24（PdBTC7 自组装微花） | R3（纳米颗粒） | C |

## 六、已知限制（必须对外说明）

1. **档 A 在 4-溴苯甲醚上只有 1 条同口径参照**（SM-001b 的 40%），属**单点参照**，不是统计区间。
2. **档 C 在 4-溴苯甲醚上没有任何 calibrated 同口径参照**（P12 口径未声明、P11 未校准、SM-004 口径未决、SM-006 校准未注明）。
3. NHC-Pd 归 C 会让它与膦配体 Pd 的参照池分离。
4. 档数（3）是当前数据能支撑的最大粒度；某档 ≥5 条记录时可考虑再分组。
