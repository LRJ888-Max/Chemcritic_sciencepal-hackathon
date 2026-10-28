# L2 · entry 级记录（证据索引）

> **用途：** 回答「有哪些可比先例」。每条都能靠 `locator` 翻回原文。
> **铁律：** `calibration = uncalibrated` 的记录**可以展示，但不参与档内定位的差值结论**。
> **数据源：** `data/records.json`（8 条已核）+ `data/pool30.json`（含 P35），去重后 40 条。

## A. 已核 / 重点记录

| record_id | 底物 | 值 | 口径 | 校准 | 证据等级 | 催化剂/碱/溶剂/温度/时间 | locator |
|---|---|---|---|---|---|---|---|
| SM-001a | 4-bromoanisole | 66 | gc | uncalibrated | A_confirmed | Pd(PPh3)4 / Na2CO3 / benzene / 80 / 6h | Table II, p-MeOPhBr row, footnote |
| SM-001b | 4-bromoanisole | 40 | isolated | calibrated | A_confirmed | Pd(PPh3)4 / Na2CO3 / benzene / 80 / 6h | Table II, p-MeOPhBr row, value in parentheses |
| SM-002 | 4-bromoanisole | 100 | isolated | calibrated | A_confirmed | PdCl2 / K2CO3 / 50% EtOH/H2O / 25 / 0.5h | SI p.1 general procedure; p.2 Table 1 entry 1 + footnote |
| SM-003 | 4-bromoanisole | 95 | nmr | uncalibrated | A_confirmed | Pd(OAc)2 / NaOtBu / THF / 60 / 1h | SI p.3 Table S1 entry 2 + footnote |
| SM-004.1 | 4-bromoanisole | 35.0 | unknown | calibrated | A_confirmed | Pd-bpydc-La (1) / NaOtBu / MeOH / 30 / 8h | Table 1 entry 1 + footnotes a/c, PDF p.4 |
| SM-004.7 | 4-bromoanisole | 91.9 | unknown | calibrated | A_confirmed | Pd-bpydc-Ce (2) / NaOtBu / MeOH / 60 / 4h | Table 1 entry 7 + footnotes a/c, PDF p.4 |
| SM-005 | 4-bromoanisole | 95 | isolated | calibrated | A_confirmed | Pd NPs (papaya peel, biosynthesized) / K2CO3 / H2O / 25 / 2h | Table 1 entry 1 + table footnote; Experimental typical procedure |
| SM-006-e1 | 4-bromoanisole | 92 | gc | uncalibrated | candidate | SNS-Pd pincer 17a / K3PO4 / H2O / 140 / 4h | Table 2 entry 1 + footnote a（PMC6071702 全文已核） |
| P35 | 4-bromoanisole | 95.0 | isolated | calibrated | A_confirmed | Pd(OAc)2（无外加配体） / K3PO4·7H2O / ethylene glycol / 25 / 0.33h | Abstract（原文："...the Suzuki reaction between 4-bromoanisole and phenylboronic acid provided a 95% isolated yield in 20 min"）；正文表格/SI 未核 |
| SM-006-e7 | 4-bromoanisole | 95 | gc | uncalibrated | candidate | SNS-Pd pincer 17a / K2CO3 / H2O / 140 / 4.0h | Table 2 entry 7 + footnote a（PMC6071702 全文已核） |

## B. 其余候选记录

| record_id | 底物 | 值 | 口径 | 校准 | 证据等级 | 催化剂/碱/溶剂/温度/时间 | locator |
|---|---|---|---|---|---|---|---|
| P11 | 4-bromoanisole | 96.0 | gc | uncalibrated | A_confirmed | clinochlore-CQDs@Pd / K2CO3 / H2O/EtOH = 1:1, 2 mL / 60 / 6.0h | Table 1 entry 3 + 脚注 a（PMC13397079 全文已核，A 2026-10-26） |
| P12 | 4-bromoanisole | 99.0 | unknown | uncalibrated | A_confirmed | Pd(0)-SMTU-boehmite / K2CO3 / PEG-400 / 80 / 1.17h | Table 3 entry 7（Table 3 无表脚注、Yield 列无上标） |
| P13 | 4-bromoanisole | 100.0 | conversion | uncalibrated | A_confirmed | Pd@PP-3（微孔膦聚合物负载 Pd） / Na2CO3 / EtOH/H2O = 2 mL/3 mL / 80 / 2.0h | §2.5 General Procedure + 优化表（PMC10611190 全文已核） |
| P19a | bromobenzene | 95.0 | gc | calibrated | A_confirmed | Pd(OAc)2 / K2CO3 / toluene / 90 / 1.5h | Table 1 entry 2 + 脚注 b (GC yield)；内标依据见同文 Table 4 脚注 |
| P19b | bromobenzene | 12.0 | gc | calibrated | A_confirmed | Pd(OAc)2 / Na2CO3 / toluene / 90 / 24.0h | Table 1 entry 1 + 脚注 b (GC yield)；内标依据见同文 Table 4 脚注 |
| P20 | bromobenzene | 85.0 | isolated | calibrated | A_confirmed | Pd A (α-diimine, acenaphthene-based) / K2CO3 / DMF / 60 / 4.0h | Table 1 entry 5 + Table 2 entry 2 + Table 3 entry 1（三表脚注 b 均为 isolated） |
| P24a | bromobenzene | 98.0 | nmr | uncalibrated | A_confirmed | PdBTC 7（双茶碱微花 Pd(II)） / K2CO3 / H2O-EtOH (1:1) 5.0 mL / 20 / 2.0h | Table 1 entry 1 + 脚注 a/c（PMC9042804 全文已核） |
| P24b | bromobenzene | 8.0 | nmr | uncalibrated | A_confirmed | PdBTC 7（双茶碱微花 Pd(II)） / K2CO3 / H2O 5.0 mL / 20 / 2.0h | Table 1 entry 2 + 脚注 a/c（PMC9042804 全文已核） |
| P24c | bromobenzene | 0.0 | nmr | uncalibrated | A_confirmed | PdBTC 7（双茶碱微花 Pd(II)） / K2CO3 / THF 5.0 mL / 20 / 2.0h | Table 1 entry 6 + 脚注 a/c（PMC9042804 全文已核） |
| P16 | 4-bromoanisole | 99.0 | gc | calibrated | A_confirmed | Pd-STO(12.5) 钙钛矿（Pd 0.64 wt%，12.5 mg） / K2CO3 / i-PrOH/H2O = 1:1 / 80 / 0.5h | Table 1 entry 2 + 脚注 a; §2.2 定量方法（decaline 内标，GC-14B）（PMC6644052 全文已核） |
| P01 | bromobenzene | 88.0 | gc | uncalibrated | imported_unchecked | Pd(PPh3)4 / Na2CO3 / benzene (reflux) + EtOH 助溶 / 80 / 6.0h | Table I, entry 8 |
| P17 | bromobenzene | 99.0 | isolated | calibrated | imported_unchecked | 两亲树枝化共聚物包裹 Pd NPs / K2CO3 / EtOH/H2O = 2.5/2.5 mL / 85 / 10.0h | Table（溴苯行, 10 h） |
| P18 | bromobenzene | 69.0 | nmr | uncalibrated | imported_unchecked | Pd(II)/carbon composite / K2CO3 / EtOH 2 mL（球磨） / None / 3.0h | Table（180 min 行）+ 时间序列（30 min 49%） |
| P21 | bromobenzene | 91.0 | isolated | calibrated | imported_unchecked | D2（thienyl Pd(II) phosphonium ylide） / K2CO3 / EtOH / 60 / 0.17h | 优化表（60 °C 行） |
| P22 | bromobenzene | 91.0 | isolated | calibrated | imported_unchecked | MIL-101(Cr)-PUT-GLUT-Pd / K2CO3 / EtOH/H2O = 1:1, 10 mL / None / 1.0h | Table（回流行）+ 温度梯度段（41/55/86/91%） |
| P23 | bromobenzene | 100.0 | nmr | uncalibrated | imported_unchecked | u-T2/Pd（茶碱微球负载 Pd） / K2CO3 / H2O / 20 / 1.0h | Table（H2O, 20 °C 行） |
| P25 | bromobenzene | 99.6 | unknown | uncalibrated | imported_unchecked | 吡啶 N 富碳纳米片负载 Pd / K2CO3 / H2O/EtOH = 1:3, 10 mL / 100 / 0.83h | Table（100 °C, 50 min 行） |
| P26 | bromobenzene | 95.3 | unknown | uncalibrated | imported_unchecked | Schiff 碱-咪唑功能化 MOF 载 Pd 簇 / NaHCO3 / DMA 5 mL / 90 / 7.0h | Table（2 mmol 行） |
| P27 | bromobenzene | 77.0 | conversion | calibrated | imported_unchecked | Pd-based catalyst size series / Na2CO3 / 无常规溶剂；液体添加剂 H2O:MeOH = 1:1, 0.1 mL / 25 / 1.0h | Table（5 mol% 行，球磨 25 Hz） |
| P04 | 4-bromotoluene | 88.0 | gc | uncalibrated | imported_unchecked | Pd(PPh3)4 / Na2CO3 / benzene (reflux) / 80 / 6.0h | Table II（p-MePhBr 行） |
| P28 | 4-bromotoluene | 99.0 | isolated | calibrated | imported_unchecked | Pd + 三蝶烯单膦配体 / K3PO4 / 2-甲氧基乙醇 6 mL / 100 / 24.0h | Table（10 ppm 行） |
| P29 | 4-bromotoluene | 95.0 | isolated | calibrated | imported_unchecked | 生物合成 Pd NPs / K2CO3 / H2O 20 mL / 50 / Noneh | Table（第 1 循环）+ Experimental |
| P30 | 4-bromotoluene | 93.0 | isolated | calibrated | imported_unchecked | Pd catalyst 2.5 mol% + PPh3 5 mol% / Cs2CO3 / DCE-EtOH = 3:1 / 25 / 5.0h | Table（Cs2CO3 行）+ 碱对照（K2CO3 65%） |
| P31 | 4-bromotoluene | 96.0 | isolated | calibrated | imported_unchecked | GO@Tpy-Pd1-xNix nanosheet / - / 未注明（5.0 mL） / 35 / 8.0h | Table（35 °C 行） |
| P03 | 2-bromoanisole | 99.0 | gc | uncalibrated | imported_unchecked | Pd(PPh3)4 / Na2CO3 / benzene (reflux) / 80 / 6.0h | Table II（o-MeOPhBr 行） |
| P06 | 4-bromobenzaldehyde | 86.3 | isolated | calibrated | imported_unchecked | Pd(OAc)2 / Na2CO3 / 1-propanol/H2O = 485 mL/95 mL / None / 0.75h | Procedure 段（粗品与重结晶两个收率） |
| P07 | 4-iodoanisole | 90.3 | isolated | calibrated | imported_unchecked | Pd(OAc)2 / K2CO3 / acetone/H2O = 1:1 / None / 2.0h | Procedure 段（蒸馏收率） |
| P32 | 4-bromobenzaldehyde | 95.0 | unknown | uncalibrated | imported_unchecked | 超小 Pd NPs（S/Se/N 配体） / K2CO3 / DMF:H2O = 3:2, 5 mL / 100 / 6.0h | Table（DMF:H2O 行）+ 溶剂对照（THF 47%、甲苯 38%） |
| P33 | 4-bromoacetophenone | 99.0 | isolated | calibrated | imported_unchecked | 磁载双配体 Pd（magnetic dual-ligand Pd） / K2CO3 / EtOH/H2O = 2:1 / 60 / 1.0h | Table（entry 1, 60 min）+ 时间扫描段 |
| P34 | 4-bromobenzophenone | 99.0 | nmr | uncalibrated | imported_unchecked | S 功能化 NHC-Pd(II)（催化剂 14） / K2CO3 / H2O 1 mL / 60 / 24.0h | Table（催化剂 14 行） |

## C. 关键事实（供检索与追问使用）

- **能进入比较块的记录极少**：40 条记录里，若用户做分离收率，全库只有 **5** 条满足「口径已定 + 已核验 + calibrated」。
- **同演示底物（4-溴苯甲醚）共 13 条**：其中 4 条可用（SM-001b 40% / SM-002 100% / SM-005 95% / P35 95%），3 条口径未决（SM-004.1、SM-004.7、P12），其余为 GC/NMR 未校准或 conversion。
- **P35 是跨档迁移目标，不是同档参照**：Chin. J. Catal. 2011，Pd(OAc)₂ 无配体 / 乙二醇 / 室温 / 20 min / 95% isolated；档位 borderline B/C（无配体但介质非水）。
- **SM-001 一行两个口径**：SM-001a 66%（GC）/ SM-001b 40%（isolated），同篇同行，`non_independent = true`。
- **SM-006-e1 / e7 同篇不同条目**（entry 1 的 K₃PO₄/92%、entry 7 的 K₂CO₃/95%），非独立来源。
- **SM-004 两条 `outcome_type = unknown`**：列名 `Yield (%)`、脚注 `total conversion ... based on GC`，不得当确定口径。

## D. 口径分布（用于判断有没有可比对象）

| 口径 | 条数 | 其中 calibrated |
|---|---|---|
| isolated | 15 | 15 |
| gc | 10 | 3 |
| nmr | 7 | 0 |
| conversion | 2 | 1 |
| unknown | 6 | 2 |
