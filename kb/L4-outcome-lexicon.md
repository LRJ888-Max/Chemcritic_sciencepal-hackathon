# L4 · 口径词表（outcome lexicon）

> **用途：** 把原文的自由表述映射到受控口径。抽取层用它判断 `outcome_type`，规则层用它验证"标记是否被原文支撑"。
> **同时存在于代码：** `engine/gates.py::_LABEL_HINTS`。两处不一致就是 bug。

---

## 一、正向词表（原文出现这些 → 判为该口径）

| 口径 | 原文可能写法 |
|---|---|
| **`isolated`** | isolated yield / isolated / isolated product / 分离收率 / 分离产率 / 过柱后称重 / 称重 / weighed / after purification |
| **`gc`** | GC yield / GLC yield / GC / GLC / by GC / 气相 / 气相色谱 / GC-MS（定量）/ area normalization（GC） |
| **`nmr`** | ¹H NMR yield / 1H-NMR yield / by ¹H NMR / NMR analysis / 核磁 / ¹H NMR 积分 / NMR 定量 |
| **`conversion`** | conversion / 转化率 / 转化 |

## 二、判定规则

1. **同一格出现两个口径词** → `outcome_type = unknown`，并把两种原文都抄进 `label_raw`。
   例：`"66% GLC/GC；40% isolated"` → 必须**拆成两条记录**，不能合并成一个值。
2. **表列名与脚注冲突** → `unknown`。
   例：列名 `Yield (%)`，脚注 `total conversion ... based on GC` → 记 `unknown`（SM-004 就是这种）。
3. **只写 `Yield (%)` 且无脚注说明** → `unknown`（P12 的 Table 3 就是这种）。
4. **表头写 `Yield (%)` 但单元格无 % 号** → 数值仍可收，但必须标 `extraction_note: 数值无 % 号，依据表头判定`。
5. **`Conversion [%]` 列** → 一律 `conversion`，不得当收率（P24 的 Table 2 就是这种）。

## 三、易混对（必须分清）

| 易混 | 区分方法 |
|---|---|
| `-OCH₃`（甲氧基）vs `-COCH₃`（乙酰基） | 看有无 **C=O 双键**。甲氧基给电子、乙酰基吸电子；5 分钟跑完的多半是乙酰基 |
| `Yield` vs `Conversion` | 同一篇里两者常并列，**不得混用**；转化率不含副产物与纯化损失 |
| `isolated yield` vs `purity` | 分离收率 ≠ 纯度；"100% yield" 不是 "100% purity" |
| 无内标的 NMR "yield" vs 校准过的 NMR yield | 若实验部分只写"萃取后测 NMR 估算"，实际接近"产物/原料积分比"→ 更接近转化率 |

## 四、`label_raw` 的写法要求

- **逐字照抄**，不翻译、不概括、不加括号解释。
- 必须能定位到**单元格或脚注**，例：`Yield (%) 列 = 92；表脚注 a: "aGC yield."`
- 若为转述，必须在 `label_raw_provenance` 标 `transcribed`（`verbatim` / `transcribed` / `inferred`）。
