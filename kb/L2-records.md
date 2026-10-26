# L2 · entry 级记录（证据索引）

> **用途：** 回答「有哪些可比先例」。每条都能靠 `locator` 翻回原文。
> **铁律：** `calibration = uncalibrated` 的记录**可以展示，但不参与档内定位的差值结论**。
> **数据源：** 机器可读版本在 `data/records.json`（已核 8 条）与 `data/pool15.json`（候选 16 条）。本文件是给人看的镜像。

## A. 已核记录（`data/records.json`，8 条）

| record_id | 文献 | 值 | 口径 | 校准 | 证据等级 | 条件（催化剂/碱/溶剂/温/时） | locator |
|---|---|---|---|---|---|---|---|
| SM-001a | Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11, 513 | 66 | gc | uncalibrated | A_confirmed | Pd(PPh3)4 / Na2CO3 / benzene / 80 °C / 6 h | Table II, p-MeOPhBr row, footnote |
| SM-001b | Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11, 513 | 40 | isolated | calibrated | A_confirmed | Pd(PPh3)4 / Na2CO3 / benzene / 80 °C / 6 h | Table II, p-MeOPhBr row, value in parentheses |
| SM-002 | Liu et al., Org. Biomol. Chem. 2011, 9, 1054 | 100 | isolated | calibrated | A_confirmed | PdCl2 / K2CO3 / 50% EtOH/H2O / 25 °C / 0.5 h | SI p.1 general procedure; p.2 Table 1 entry 1 + footnote |
| SM-003 | Chang et al., Dalton Trans. 2015, 44, 17129 | 95 | nmr | uncalibrated | A_confirmed | Pd(OAc)2 / NaOtBu / THF / 60 °C / 1 h | SI p.3 Table S1 entry 2 + footnote |
| SM-004.1 | Molecules 2018, 23, 2435 | 35.0 | unknown | calibrated | A_confirmed | Pd-bpydc-La (1) / NaOtBu / MeOH / 30 °C / 8 h | Table 1 entry 1 + footnotes a/c, PDF p.4 |
| SM-004.7 | Molecules 2018, 23, 2435 | 91.9 | unknown | calibrated | A_confirmed | Pd-bpydc-Ce (2) / NaOtBu / MeOH / 60 °C / 4 h | Table 1 entry 7 + footnotes a/c, PDF p.4 |
| SM-005 | Dewan et al., ACS Omega 2018, 3, 5327 | 95 | isolated | calibrated | A_confirmed | Pd NPs (papaya peel, biosynthesized) / K2CO3 / H2O / 25 °C / 2 h | Table 1 entry 1 + table footnote; Experimental typical procedure |
| SM-006-e1 | Fiebor et al., Beilstein J. Org. Chem. 2018, 14, 1859 | 92 | gc | uncalibrated | candidate | SNS-Pd pincer 17a / K3PO4 / H2O / 140 °C / 4 h | Table 2 entry 1 + footnote a（PMC6071702 全文已核） |

## B. 候选记录（`data/pool15.json`，16 条，均已回原文核对）

| record_id | 文献 | 值 | 口径 | 校准 | 证据等级 | 条件（催化剂/碱/溶剂/温/时） | locator |
|---|---|---|---|---|---|---|---|
| P11 | Gholinejad, Jafarpour & Sansano, RSC Adv. 2026, 16(38), 42714-42723 | 96.0 | gc | uncalibrated | A_confirmed | clinochlore-CQDs@Pd / K2CO3 / H2O/EtOH = 1:1 / 60 °C / 6.0 h | Table 1 entry 3（4-溴苯甲醚优化表）+ Table 2 脚注 a（通用条件） |
| P12 | Hajighasemi, Nahipour, Ghorbani-Choghamarani & Taherinia, Nanoscale Adv. 2023, 5(18), 4925-4933 | 99.0 | unknown | uncalibrated | imported_unchecked | Pd(0)-SMTU-boehmite / K2CO3 / PEG-400 / 80 °C / 1.17 h | Table 3（4-溴苯甲醚行，70 min）+ Experimental |
| P13 | Esteban et al., Polymers 2023, 15(20), 4143 | 100.0 | conversion | uncalibrated | imported_unchecked | Pd@PP-3（微孔膦聚合物负载 Pd） / Na2CO3 / EtOH/H2O = 2 mL/3 mL / 80 °C / 2.0 h | 优化表（4-溴苯甲醚行）+ 10 mmol 放大实验段 |
| P19a | Jiang et al., Molecules 2012, 17, 12121-12139（C2 对称 Pd-NHC） | 95.0 | gc | uncalibrated | imported_unchecked | Pd(OAc)2 / K2CO3 / toluene / 90 °C / 1.5 h | Table（K2CO3 行，entry 待 A 补） |
| P19b | Jiang et al., Molecules 2012, 17, 12121-12139（C2 对称 Pd-NHC） | 12.0 | gc | uncalibrated | imported_unchecked | Pd(OAc)2 / Na2CO3 / toluene / 90 °C / 24.0 h | Table（Na2CO3 行，24 h） |
| P24a | Kaikake, Jou, Shitara & Jin, RSC Adv. 2021, 11(56), 35311-35320（PdBTC 7） | 98.0 | nmr | uncalibrated | imported_unchecked | PdBTC 7（双茶碱微花 Pd(II)） / K2CO3 / H2O/EtOH = 1:1 / 20 °C / 2.0 h | Table（H2O/EtOH = 1:1 行） |
| P24b | Kaikake, Jou, Shitara & Jin, RSC Adv. 2021, 11(56), 35311-35320（PdBTC 7） | 8.0 | nmr | uncalibrated | imported_unchecked | PdBTC 7（双茶碱微花 Pd(II)） / K2CO3 / H2O / 20 °C / 2.0 h | Table（H2O 行） |
| P23 | Kaikake, Takada, Soma & Jin, RSC Adv. 2018, 8(60), 34505-34513（茶碱微球负载 Pd, u-T2/Pd） | 100.0 | nmr | uncalibrated | imported_unchecked | u-T2/Pd（茶碱微球负载 Pd） / K2CO3 / H2O / 20 °C / 1.0 h | Table（H2O, 20 °C 行） |
| P21 | Sepehrfar et al., RSC Adv. 2026, 16(45), 58585-58605（噻吩基 Pd(II) 膦叶立德 D2） | 91.0 | isolated | calibrated | imported_unchecked | D2（thienyl Pd(II) phosphonium ylide） / K2CO3 / EtOH / 60 °C / 0.17 h | 优化表（60 °C 行） |
| P20 | Talukder et al., ACS Omega 2020, 5(37), 24018-24032（alpha-二亚胺 Ni/Pd 配合物） | 85.0 | isolated | calibrated | A_confirmed | Pd A (α-diimine, acenaphthene-based) / K2CO3 / DMF / 60 °C / 4.0 h | Table 1 entry 5 + Table 2 entry 2 + Table 3 entry 1（三表脚注 b 均为 isolated） |
| P27 | Feketshane, Vatsha & Bingwa, RSC Adv. 2026, 16(11), 9692-9708（机械化学 Pd 尺寸效应） | 77.0 | conversion | calibrated | imported_unchecked | Pd-based catalyst size series / Na2CO3 / 无常规溶剂；液体添加剂 H2O:MeOH = 1:1, 0.1 mL / 25 °C / 1.0 h | Table（5 mol% 行，球磨 25 Hz） |
| P03 | Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11(7), 513-519（表 II） | 99.0 | gc | uncalibrated | imported_unchecked | Pd(PPh3)4 / Na2CO3 / benzene (reflux) / 80 °C / 6.0 h | Table II（o-MeOPhBr 行） |
| P06 | Huff, Koenig, Mitchell & Staszak, Org. Synth. 1998, 75, 53 (Coll. Vol. 10, 102) | 86.3 | isolated | calibrated | imported_unchecked | Pd(OAc)2 / Na2CO3 / 1-propanol/H2O = 485 mL/95 mL / None °C / 0.75 h | Procedure 段（粗品与重结晶两个收率） |
| P07 | Goodson, Wallow & Novak, Org. Synth. 1998, 75, 61 (Coll. Vol. 10, 501), ligandless Pd | 90.3 | isolated | calibrated | imported_unchecked | Pd(OAc)2 / K2CO3 / acetone/H2O = 1:1 / None °C / 2.0 h | Procedure 段（蒸馏收率） |
| P33 | ACS Omega 2024, 9, 17817（磁载双配体 Pd） | 99.0 | isolated | calibrated | imported_unchecked | 磁载双配体 Pd（magnetic dual-ligand Pd） / K2CO3 / EtOH/H2O = 2:1 / 60 °C / 1.0 h | Table（entry 1, 60 min）+ 时间扫描段 |
| SM-006-e7 | Fiebor et al., Beilstein J. Org. Chem. 2018, 14, 1859 | 95 | gc | uncalibrated | candidate | SNS-Pd pincer 17a / K2CO3 / H2O / 140 °C / 4.0 h | Table 2 entry 7 + footnote a（PMC6071702 全文已核） |

## C. 关键事实（供检索与追问使用）

- **同演示底物（4-溴苯甲醚）的记录**：SM-001a/001b（66% GC / 40% 分离）、SM-004.1/004.7（口径未决）、SM-005（95% 分离）、P11（96% GC 未校准）、P12（口径未声明）、SM-006-e1/e7（GC 未校准）。
- **能进入档内定位的（calibrated + 同口径）只有 3 条**：SM-001b（40%）、SM-002（100%）、SM-005（95%），全部是分离收率。
- **SM-004 的两条 `outcome_type = unknown`**：表列名写 `Yield (%)`、脚注写 `total conversion ... based on GC`，不得当作确定口径。
- **SM-006-e1 与 SM-006-e7 是同一篇的两个条目**（entry 1 的 K₃PO₄/92%、entry 7 的 K₂CO₃/95%），非独立来源。
- **P12 的 >99% 口径未声明**（Table 3 无脚注）→ `unknown`；**P13 的 100% 是 GC-MS 转化率**。
- **P11 提供同底物的温度与载量梯度**：温度 25→20%、50→60%、60→96%；载量 0.035→15%、0.07→50%、0.1→96%。

## D. 口径分布（用于判断有没有可比对象）

| 口径 | 条数 | 其中 calibrated |
|---|---|---|
| isolated | 8 | 8 |
| gc | 7 | 0 |
| nmr | 4 | 0 |
| conversion | 2 | 1 |
| unknown | 3 | 2 |
