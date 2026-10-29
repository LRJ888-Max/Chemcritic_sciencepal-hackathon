# L2 · entry 级记录（证据索引）

> **用途：** 回答「有哪些可比先例」。每条都能靠 `locator` 翻回原文。
> **铁律：** `calibration = uncalibrated` 的记录**可以展示，但不参与档内定位的差值结论**。
> **数据源：** `data/records.json`（43 条）+ `data/pool30.json`（36 条），按 record_id 去重后 **44 条**。
> **生成方式：** `python outputs/gen_l2.py`。本文件的统计数字由脚本从 JSON 计算，**不要手改**。

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
| P01 | bromobenzene | 88.0 | gc | uncalibrated | A_confirmed | Pd(PPh3)4 / Na2CO3 (2M aq.) / benzene (reflux) / 80 / 6.0h | Table I entry 8（X=Br, base 2M-Na2CO3/H2O, 6 h）+ 脚注 a/b（本地 PDF refs/miyaura1981.pdf p.515 已核） |
| P03 | 2-bromoanisole | 99.0 | gc | uncalibrated | A_confirmed | Pd(PPh3)4 / Na2CO3 / benzene (reflux) / 80 / 6.0h | Table II entry 3（o-MeOPhBr 行）+ 脚注 a/b（本地 PDF refs/miyaura1981.pdf p.517 已核） |
| P04 | 4-bromotoluene | 88.0 | gc | uncalibrated | A_confirmed | Pd(PPh3)4 / Na2CO3 / benzene (reflux) / 80 / 6.0h | Table II entry 2（p-MePhBr 行）+ 脚注 a/b（本地 PDF refs/miyaura1981.pdf p.517 已核） |
| P17 | bromobenzene | 99.0 | isolated | calibrated | A_confirmed | ADC-PdNPs（两亲树枝化共聚物包裹 Pd） / K2CO3 / EtOH/H2O = 2.5/2.5 mL / 85 / 10.0h | Table 3 entry 4（500 ppm ADC-PdNPs, K2CO3）+ 表下注（本地 PDF refs/pdf/P17_polymers-16-01080.pdf p.10 已核） |
| P21 | bromobenzene | 91.0 | isolated | calibrated | A_confirmed | D2（噻吩基 Pd(II) 膦叶立德） / K2CO3 / EtOH 2 mL / 60 / 0.17h | Table 5 entry 2（D2, EtOH/K2CO3/0.47 mol% Pd/60 °C 加粗行）+ 脚注 a/b（Europe PMC fullTextXML PMC13622566 全文已核） |
| P22 | bromobenzene | 91.0 | isolated | calibrated | A_confirmed | MIL-101(Cr)-PUT-GLUT-Pd / K2CO3 / EtOH/H2O = 1:1 (10 mL) / 80 / 1.0h | Table 5 entry 2（Ph-Br 行）+ 表注（本地 PDF refs/pdf/P22_10_1038_s41598-025-22375-7.pdf p.12 已核） |
| P28 | 4-bromotoluene | 99.0 | isolated | calibrated | A_confirmed | Pd(OAc)2 + triptycene monophosphine (1) / K3PO4 / 2-methoxyethanol 6 mL / 100 / 24.0h | Table 1 entry 2（Pd 10 mol ppm + 配体 1 20 mol ppm, 100 °C, 24 h）+ 脚注 a/b/c（PMC6640964 全文已核） |
| P29 | 4-bromotoluene | 95.0 | isolated | calibrated | A_confirmed | 生物合成 Pd NPs（淀粉稳定） / K2CO3 / H2O 20 mL / 50 / Noneh | Table 1（Recycling, Cycle 1 行）+ 脚注 a/b（PMC5619764 全文已核） |
| P30 | 4-bromotoluene | 93.0 | isolated | calibrated | A_confirmed | [Ir(pq)2(bpm)PdCl2](PF6)（3） / Cs2CO3 / DCE-EtOH = 3:1 (4 mL) / 25 / 5.0h | Table 2 entry 4（溶剂 DCE-EtOH 3:1 行）+ 正文 + Table 3 脚注（PMC9058252 全文已核） |
| P31 | 4-bromotoluene | 96.0 | isolated | calibrated | A_confirmed | GO@Tpy-Pd1/Ni10 nanosheet / K2CO3 / H2O/EtOH = 1:3 (5.0 mL) / 35 / 8.0h | Table 2 entry 6（GO@Tpy-Pd1/Ni10 行）+ 脚注 a/b（PMC9054763 全文已核） |
| P36 | 4-bromoanisole | 50 | isolated | calibrated | A_confirmed | Pd(OAc)2 + RO-PTA / Na2CO3 / H2O:CH3CN = 1:1（5 mL） / 80 / 6h | Table 3 entry 4 + 脚注 a/b；§3.2 General Procedure、§3.3 实验段（本地 PDF refs/pdf/molecules-16-06215.pdf p.6223-6226 已核） |
| P37 | bromobenzene | 85 | isolated | calibrated | A_confirmed | LaF3·Pd 纳米催化剂 / K2CO3 / H2O（3 mL） / 70 / 4h | Table 3 entry 3（1a + 2a, H2O, K2CO3, 70 °C, 4 h）+ 脚注 a/b；正文 p.21272（本地 PDF refs/pdf/d4ra00686k.pdf 已核） |
| P38 | 4-bromoacetophenone | 95 | isolated | calibrated | A_confirmed | indolyl-NNN Pd 配合物 3 / K2CO3 / toluene（2 mL） / 70 / 1h | Table 2 entry 5（催化剂 3, K2CO3/toluene, 70 °C, 1 h）+ 表注；§3 实验（本地 PDF refs/pdf/molecules-26-04426.pdf 与 JATS XML PMC8347745 双证） |
| P06 | 4-bromobenzaldehyde | 86.3 | isolated | calibrated | A_confirmed | Pd(OAc)2 / Na2CO3 / 1-propanol/H2O = 485 mL/95 mL / None / 0.75h | Procedure 1（Vol.75 p.53）：4-bromobenzaldehyde 50.0 g (0.270 mol)、PhB(OH)2 34.6 g (0.284 mol, 1.05 eq)、Pd(OAc)2 0.182 g (0.811 mmol, 0.3 mol%)、PPh3 0.638 g (0.9 mol%)、Na2CO3 0.324 mol (1.20 eq)、1-propanol 485 mL + H2O 95 mL、reflux 45 min；粗品 47.3 g (96.2%)、重结晶 42.5 g (86.3%)（本地 PDF refs/orgsyn_v75p0053.pdf 已核） |
| P07 | 4-iodoanisole | 90.3 | isolated | calibrated | A_confirmed | Pd(OAc)2 / K2CO3 / acetone/H2O = 1:1 / None / 2.0h | Procedure 1（Vol.75 p.61）：4-iodoanisole 16.8 g (71.8 mmol)、o-tolylboronic acid 10.0 g (73.6 mmol, 1.03 eq)、Pd(OAc)2 3.30 mg (0.02 mmol, 0.2 mol%)、K2CO3 25.0 g (0.180 mol, 2.5 eq)、acetone 200 mL + H2O 200 mL、reflux 2 h；12.8 g (90.3%) 蒸馏（refs/orgsyn_v75p0061.pdf 已核） |
| P18 | bromobenzene | 69.0 | nmr | uncalibrated | A_confirmed | Pd(II)/carbon composite / K2CO3 / EtOH 2 mL（球磨） / None / 3.0h | Table 4 entry 7（AC-Pd2, 180 min 行）+ 脚注 a/b（PMC7355770 全文已核；本地 PDF refs/pdf/P18_molecules-25-02951.pdf p.12） |
| P23 | bromobenzene | 100.0 | nmr | uncalibrated | A_confirmed | u-T2/Pd（茶碱微球负载 Pd） / K2CO3 / H2O / 20 / 1.0h | Table 1（H2O, 20 °C 行）+ 脚注 a/b（PMC9087375 全文已核） |
| P25 | bromobenzene | 99.6 | unknown | uncalibrated | A_confirmed | 吡啶 N 富碳纳米片负载 Pd / K2CO3 / H2O/EtOH = 1:3, 10 mL / 100 / 0.83h | Table 1 entry 5（Pd/N-CNS800, 50 min 行）+ 脚注 a（PMC11548024 全文已核） |
| P26 | bromobenzene | 95.3 | unknown | uncalibrated | A_confirmed | Schiff 碱-咪唑功能化 MOF 载 Pd 簇 / NaHCO3 / DMA 5 mL / 90 / 7.0h | Table 2 entry 4（Pd0@UIO-66-SB-Im 行）+ 表注 a（PMC8921604 全文已核） |
| P27 | bromobenzene | 77.0 | conversion | calibrated | A_confirmed | Pd-based catalyst size series / Na2CO3 / 无常规溶剂；液体添加剂 H2O:MeOH = 1:1, 0.1 mL / 25 / 1.0h | Table 3 entry 6（H2O:MeOH 1:1 行）+ 脚注 a（PMC12915526 全文已核） |
| P32 | 4-bromobenzaldehyde | 95.0 | unknown | uncalibrated | A_confirmed | 超小 Pd NPs（S/Se/N 配体） / K2CO3 / DMF:H2O = 3:2, 5 mL / 100 / 6.0h | 优化表 S. no. 4（DMF:water 3:2 + K2CO3 行）+ 脚注 a（PMC9066620 全文已核） |
| P33 | 4-bromoacetophenone | 99.0 | unknown | uncalibrated | A_confirmed | 磁载双配体 Pd（magnetic dual-ligand Pd） / K2CO3 / EtOH/H2O = 2:1 / 60 / 1.0h | Table 1 entry 3（60 min 行）+ 脚注 a（PMC11044249 全文已核） |
| P34 | 4-bromobenzophenone | 99.0 | nmr | uncalibrated | A_confirmed | S 功能化 NHC-Pd(II)（催化剂 14） / K2CO3 / H2O 1 mL / 60 / 24.0h | Table 1 entry 3（催化剂 14, 60 °C, 24 h 行）+ 脚注 a/b（PMC6268575 全文已核） |
| P39 | 4-bromoanisole | 40.0 | isolated | calibrated | A_confirmed | Pd/AM4-HDA 多孔聚合物 / 原文未给 / sc-CO2（超临界 CO2） / None / 2.0h | §3.4 Coupling Reaction（原文："Suzuki-Miyaura coupling reactions of 4-bromoanisole and phenylboronic acid, Scheme 3, were conducted using Pd-containing AM4-HDA porous polymer, catalyzed in sc-CO2 for 2 h ... The reaction conversion of 4-methoxybiphenyl in crude reaction mixtures, determined by 1H-NMR spectroscopy, was 82%, and the isolated yield was 40%."）（本地 PDF refs/pdf/NEW-materials-14-00800.pdf 已核） |

## C. 关键事实（供检索与追问使用）

- **能进入比较块的记录**：44 条中，分离收率且「口径已定 + 已核验 + calibrated」的共 **18** 条。
- **同演示底物（4-溴苯甲醚）共 16 条**：其中分离收率可用 **6** 条（SM-001b 40% / P39 40.0% / P36 50% / SM-005 95% / P35 95.0% / SM-002 100%）；口径未决 3 条（SM-004.1、SM-004.7、P12）。
- **4-溴苯甲醚的分离收率跨度**：SM-001b 40%、P39 40.0%、P36 50%、SM-005 95%、P35 95.0%、SM-002 100%；即同一底物在不同条件下可差 60 个百分点，**任何单一先例都不能当基准**。
- **P35 是跨档迁移目标，不是同档参照**：Chin. J. Catal. 2011，Pd(OAc)₂ 无配体 / 乙二醇 / 室温 / 20 min / 95% isolated；档位 borderline B/C（无配体但介质非水）。
- **SM-001 一行两个口径**：SM-001a 66%（GC）/ SM-001b 40%（isolated），同篇同行，`non_independent = true`。
- **SM-006-e1 / e7 同篇不同条目**（entry 1 的 K₃PO₄/92%、entry 7 的 K₂CO₃/95%），非独立来源。
- **SM-004 两条 `outcome_type = unknown`**：列名 `Yield (%)`、脚注 `total conversion ... based on GC`，不得当确定口径。
- **tier 不是证据等级**：`tier` 由 `engine/tier.py` 从 `catalytic_profile` 计算（A/B/C），`evidence_grade` 才是核验状态（A_confirmed / candidate / imported_unchecked）。

## D. 口径分布（用于判断有没有可比对象）

| 口径 | 条数 | 其中 calibrated |
|---|---|---|
| isolated | 18 | 18 |
| gc | 10 | 3 |
| nmr | 7 | 0 |
| conversion | 2 | 1 |
| unknown | 7 | 2 |

