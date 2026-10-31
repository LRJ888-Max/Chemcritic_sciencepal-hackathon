# 文献与来源索引

本文件是 ChemCritic 证据库的入口。**所有数值都可回溯到具体文献的表/entry/脚注。**

> **为什么仓库里没有 PDF：** 23 个开放获取 PDF 存于本地 `refs/pdf/`（其中 `polymers-15-04143.pdf` 与 `P13_polymers-15-04143.pdf` 重复、另有 3 个标注为可删），因篇幅与出版社许可限制未纳入仓库。下表 DOI 可直接点开核对原文。
> 引用任何收率时，必须同时给出底物、催化剂、碱、溶剂、温度、时间与**测定口径**。

---

## 一、六条已核验记录（SM-001 – SM-006）

全部为 **4-溴苯甲醚 + 苯硼酸 → 4-甲氧基联苯** 这一确切底物对。

| ID | 文献 | 报告值 | 口径 | 档 | 核验状态 |
|---|---|---|---|---|---|
| SM-001 | Miyaura, Yanagi & Suzuki, *Synth. Commun.* 1981, 11, 513 | 66% / **40%** | GLC / 分离（括号内） | A | ✅ 开放全文（HUSCAP） |
| SM-002 | Liu et al., *Org. Biomol. Chem.* 2011, 9, 1054 | **100%** | 分离 | B | ✅ RSC 官方 SI |
| SM-003 | Chang et al., *Dalton Trans.* 2015, 44, 17129 | 95% | ¹H NMR | A | ✅ RSC 官方 SI |
| SM-004 | *Molecules* 2018, 23, 2435 | 35.0 / 91.9 | GC（十六烷内标） | C | ✅ 开放全文 |
| SM-005 | Dewan et al., *ACS Omega* 2018, 3, 5327 | 95% | 分离 | C（边界 B/C） | ✅ 开放全文 |
| SM-006 | Fiebor et al., *Beilstein J. Org. Chem.* 2018, 14, 1859 | 92% | GC（校准未注明） | C | ⚠️ 候选（校准待确认） |

### 关键判定素材（来自上述记录）

| 对照 | 事实 | 闸门结论 |
|---|---|---|
| SM-004 entry 1 vs entry 7 | 同一篇，催化剂/温度/时间同时变化 | BLOCKED（维度 6+7），且不得算两条独立来源 |
| SM-001 的 66% vs 40% | 同一篇，GC 与分离两种口径 | BLOCKED（维度 3） |
| SM-006 92% vs SM-004 91.9% | 同底物、同为 GC，但内标/校准口径不同 | BLOCKED（维度 4 UNKNOWN） |

**等价类结果（仅针对上面 SM-001–SM-006 这六条）：** 8 个报告值中仅 3 个（40% / 100% / 95%）构成同一"分离收率"等价类。加入 10/28 新增记录后的分布见"一之二"。

### 一之二、10/28 新增核验（来自 `refs/pdf/` 新补 PDF）

| ID | 文献 | 报告值 | 口径 | 档 | 核验状态 |
|---|---|---|---|---|---|
| **P36** | Weeden et al., *Molecules* 2011, 16, 6215–6231 | **50%** | 分离（92 mg，称重） | A | ✅ `A_confirmed`（PDF §3.2/§3.3 + Table 3 entry 4） |
| **P37** | Panda et al., *RSC Adv.* 2024, 14, 21269–21276 | 85% | 分离 | A | ✅ `A_confirmed`（Table 3 entry 3 + 脚注 b） |
| **P38** | Lo et al., *Molecules* 2021, 26, 4426 | 95% | 分离（表内括号值；98% 为 ¹H NMR 转化率） | A | ✅ `A_confirmed`（Table 2 entry 5 + 表注） |
| **P39** | *Materials* 2021, 14, 800（Pd/AM4-HDA，sc-CO₂） | **40%** | 分离（同句另报 NMR 转化率 82%） | C | ✅ `A_confirmed`（§3.4 原文 "the isolated yield was 40%"） |
| **P40** | *Sci. Rep.* 2024, 14, 7688（Pd-DPyE@MCM-41@MNP） | 95% | 分离 | C | ✅ `A_confirmed`（Table 6 entry 9b + 脚注 a "Isolated yield"） |
| **P41** | 同上（同篇、不同底物：溴苯） | 97% | 分离 | C | ✅ `A_confirmed`（Table 6 entry 7b） |
| — | Rahman et al., *Organometallics* 2022, 41, 2281–2290 | 待核验 | 未声明 | C（暂） | ❌ Table 1 为图，正文只说 entries 7–9「high efficiency」 |

> **P36 的分量：** 与演示案例**完全同底物**（4-溴苯甲醚 + 苯硼酸）、**碱为 Na₂CO₃**、**溶剂为含水混合体系**（H₂O:MeCN = 1:1，80 ℃，6 h），分离收率 **50%**。
> 加上 SM-001b 的 40% 与 P39 的 40%，"同底物 + 碳酸盐/碱性 + 非纯有机介质"这一档已有三条落在 **40–50%**，**不是 80–95%**。
>
> **新增后同底物（4-溴苯甲醚）分离收率参照：40% / 40% / 50% / 95% / 95% / 95% / 100%**（**7 条**）—— 中位数 95%、但下沿到 40%，且**最低的三条恰好是条件最接近演示的三条**，说明该底物的收率分布极宽，单点先例不能当基准。


---

## 二、文献清单（24 个本地 PDF 文件，含扩展背景）

| # | 文献 | DOI | 与演示反应的关系 |
|---|---|---|---|
| SM-001 | Miyaura, Yanagi & Suzuki, *Synth. Commun.* 1981, 11, 513–519 | [10.1080/00397918108063618](https://doi.org/10.1080/00397918108063618) | 经典原始研究，含目标底物记录 |
| 01 | Fiebor et al., *Beilstein J. Org. Chem.* 2018, 14, 1859–1870 | [10.3762/bjoc.14.160](https://doi.org/10.3762/bjoc.14.160) | **直接底物**；表中为 GC 收率 |
| 02 | Ding et al., *Molecules* 2018, 23, 2435 | [10.3390/molecules23102435](https://doi.org/10.3390/molecules23102435) | **直接底物**；含 35.0% 与 91.9% 两个条目 |
| 03 | Esteban et al., *Polymers* 2023, 15, 4143 | [10.3390/polym15204143](https://doi.org/10.3390/polym15204143) | 多孔膦聚合物负载 Pd（背景） |
| 04 | Liu & Liu, *Polymers* 2024, 16, 1080 | [10.3390/polym16081080](https://doi.org/10.3390/polym16081080) | 纳米颗粒催化（背景） |
| 05 | Soliman et al., *Molecules* 2020, 25, 2951 | [10.3390/molecules25122951](https://doi.org/10.3390/molecules25122951) | 机械化学 Suzuki（背景） |
| 06 | Jiang et al., *Molecules* 2012, 17, 12121–12139 | [10.3390/molecules171012121](https://doi.org/10.3390/molecules171012121) | NHC-Pd 体系（背景） |
| 07 | Abroudi et al., *Sci. Rep.* 2025 | [10.1038/s41598-025-22375-7](https://doi.org/10.1038/s41598-025-22375-7) | MOF/Pd 纳米颗粒（背景） |
| 08 | Cui et al., *Nanomaterials* 2024, 14, 1690 | [10.3390/nano14211690](https://doi.org/10.3390/nano14211690) | 碳负载 Pd（背景） |
| 09 | Liu et al., *Front. Chem.* 2022, 10, 845274 | [10.3389/fchem.2022.845274](https://doi.org/10.3389/fchem.2022.845274) | MOF 负载 Pd 簇（背景） |
| 10 | Yuan & Huynh, *Molecules* 2012, 17, 2491–2517 | [10.3390/molecules17032491](https://doi.org/10.3390/molecules17032491) | S-功能化 NHC-Pd（背景） |
| 11 | Weeden et al., *Molecules* 2011, 16, 6215–6231 | [10.3390/molecules16086215](https://doi.org/10.3390/molecules16086215) | **直接底物**；Na₂CO₃/含水 MeCN，分离 50%（P36） |
| 12 | Hajighasemi et al., *Nanoscale Adv.* 2023, 5, 4925–4933 | [10.1039/d3na00403a](https://doi.org/10.1039/d3na00403a) | **直接底物**；Table 3 entry 7 >99%，但口径未声明（P12） |
| 13 | Panda et al., *RSC Adv.* 2024, 14, 21269–21276 | [10.1039/d4ra00686k](https://doi.org/10.1039/d4ra00686k) | 溴苯块：K₂CO₃/H₂O，70 ℃，分离 85%（P37） |
| 14 | Lo et al., *Molecules* 2021, 26, 4426 | [10.3390/molecules26154426](https://doi.org/10.3390/molecules26154426) | 4-溴苯乙酮块：K₂CO₃/甲苯，分离 95%（P38） |
| 15 | Rahman et al., *Organometallics* 2022, 41, 2281–2290 | [10.1021/acs.organomet.2c00262](https://doi.org/10.1021/acs.organomet.2c00262) | **直接底物**（4-溴苯甲醚/K₂CO₃/MeOH 80 ℃），数值在 Table 1 图内 → 待核验 |
| 16 | *Sci. Rep.* 2024, 14, 7688 | [10.1038/s41598-024-58310-5](https://doi.org/10.1038/s41598-024-58310-5) | 碘代芳烃为主，**不计入**芳基溴口径 |
| 17 | （重复）`polymers-15-04143.pdf` | 同 #03 | 与 `P13_polymers-15-04143.pdf` 字节相同，可删其一 |
| 18 | *Materials* 2021, 14, 800 | [10.3390/ma14040800](https://doi.org/10.3390/ma14040800) | **直接底物**；sc-CO₂，分离 40% / NMR 82%（P39） |
| 19 | Nikoshvili et al., *Nanomaterials* 2021, 12, 94 | [10.3390/nano12010094](https://doi.org/10.3390/nano12010094) | **直接底物**；只报转化率 82–94%，无单一收率 → 不入库 |
| 20 | Liang, Ning & Zhen, *Molecules* 2025, 30, 656 | [10.3390/molecules30030656](https://doi.org/10.3390/molecules30030656) | 同底物但只报 conversion（表 4 entry 5）→ 不入库 |
| 21 | *Molecules* 2024, 29, 1138 | [10.3390/molecules29102229](https://doi.org/10.3390/molecules29102229) | **不相关**（Buchwald–Hartwig）→ 可删 |
| 22 | *Nanomaterials* 2021, 11, 3345 | [10.3390/nano11123345](https://doi.org/10.3390/nano11123345) | 4-bromoanisole 仅出现在试剂清单 → 可删 |
| 23 | *Molecules* 2010, 15, 4511 | [10.3390/molecules15074511](https://doi.org/10.3390/molecules15074511) | 提取文本仅 37 KB 且无目标底物 → 可删 |

> **重要边界：** 上表中只有 SM-001、01、02、11、15 直接研究目标底物（4-溴苯甲醚）；#12 亦为同底物但口径未声明，不得当数值先例。其余为催化体系背景，**不得**当作该底物的数值先例。

---

## 三、补充材料（SI）缺口

SM-002 与 SM-003 的关键数据在 RSC 官方 SI 中；旧版直链（`www.rsc.org/suppdata/...`）已失效，本项目**未用非官方镜像替代**。核验时请从正式文章页的 Supplementary files 下载：

- SM-002：[RSC 文章页](https://pubs.rsc.org/en/content/articlelanding/2011/ob/c0ob00524j)
- SM-003：[RSC 文章页](https://pubs.rsc.org/en/content/articlelanding/2015/dt/c5dt02611c)

---

## 四、检索与核验流程

```text
候选池（34 条）→ 元数据核验（Crossref / OpenAlex / Unpaywall）
                → 开放全文获取（JATS XML / 出版社 PDF）
                → entry 级抽取（条件 + 结果 + 口径 + locator）
                → 人工复核（表/脚注/页码是否对得上）
                → 入库（SM-xxx）或退回候选池
```

**核验分级：**

| 级别 | 含义 |
|---|---|
| A | 开放全文，可定位到表格与脚注 |
| B | 官方 SI 可定位 |
| C | 仅有元数据或摘要，数值一律写 `待核验` |

> 本项目中**不存在"没读到原文却填了数字"**的条目；未确认的内容一律记 `UNKNOWN`。

---

## 五、反应类型扩展（2026-10-31）

需求：不再只收 Suzuki。引擎在 2026-10-31 已把底物比对从写死的 `aryl_halide + boronic_acid` 泛化为**任意两个非产物键**，所以 Heck（芳卤+烯烃）、Sonogashira（芳卤+炔）、Buchwald（芳卤+胺）、CuAAC（叠氮+炔）、转移氢化（酮+氢源）都能进同一个判定框架。

**当前语料按反应类型的分布（148 条）：**

| reaction_class | 条数 | 底物键示例 |
|---|---:|---|
| heck | 51 | `aryl_halide` + `alkene` / `acrylate` |
| suzuki | 46 | `aryl_halide` + `boronic_acid` |
| sonogashira | 25 | `aryl_halide` + `alkyne` |
| buchwald_hartwig | 17 | `aryl_halide` + `amine` |
| kumada | 4 | `aryl_halide` + `organometallic` |
| **click_cuaac** | 1 | `azide` + `alkyne` |
| **transfer_hydrogenation** | 1 | `ketone` + `hydride_source` |

**本轮新增（三条都是 A_confirmed）：**

| ID | 反应 | 底物 → 产物 | 值 | 口径 | 来源 |
|---|---|---|---:|---|---|
| **P42** | Sonogashira | 碘苯 + 苯乙炔 → 二苯乙炔 | 88% | 分离 | *Beilstein J. Org. Chem.* 2019, 15, 284 |
| **P43** | CuAAC | 苄基叠氮 + 苯乙炔 → 1-苄基-4-苯基三唑 | 88% | 分离 | *Beilstein J. Org. Chem.* 2025, 21, 1544 |
| **P44** | 转移氢化 | 苯乙酮 + i-PrOH → 1-苯乙醇 | 58% | 转化率 | *Molecules* 2025, 30, 910 |
| **P45** | 有氧氧化 | 1-苯乙醇 + 空气 → 苯乙酮 | 94% | 分离 | *Molecules* 2022, 27, 3727 |
| **P46** | 硝基还原 | 硝基苯 + H2 → 苯胺 | >99% | 分离 | *Commun. Chem.* 2025 |
| **P47** | 酰胺缩合 | 5-氨基吲哚 + 4-氨基-3-羟基苯甲酸 → 苯甲酰胺 | 85% | 分离 | *Metabolites* 2023, 13, 141 |

**新增的分类规则**（`engine/placement.py::_KIND_BY_KEY`）：`azide → click_cuaac`、`ketone/hydride_source → transfer_hydrogenation`、`acid → amide_coupling`（酰胺缩合规则已就位，尚待第一条记录）。

**仍然缺的类**：酰胺缩合、酯化/水解、氧化、硝基还原、保护基脱除。这些都不是架构问题——`check()` 只要求**两个及以上非产物底物键**，任何双分子反应都能入册。缺的是**可下载全文 + 明确收率**的文献：ACS/RSC 的 PDF 对脚本一律 403，只能人工下载。
