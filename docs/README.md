# 文档索引

> 评审或新成员请按 **① plan → ② rules → ③ science → ④ archive（可跳过）** 的顺序读。

---

## `plan/` — 现行方案（唯一权威）

| 文件 | 内容 | 状态 |
|---|---|---|
| [多智能体系统技术方案](plan/2026-10-25-ChemCritic-多智能体系统技术方案.md) | 六个 Agent 规格、每个模块的能力与软件清单、8 维门槛、数据结构、评测与降级路径 | ✅ 现行 |
| [角色分工与每日任务](plan/2026-10-25-ChemCritic-角色分工与每日任务.md) | 三人分工、依赖关系（谁卡谁）、10/25–10/31 每日任务与出口标准、砍单顺序 | ✅ 现行 |
| [方向定稿与 6 天执行方案](plan/2026-10-25-方向定稿与6天执行方案.md) | 三个候选定位的取舍过程、竞品对比（Elementics REACTOR 等）、评测指标设计思路 | ℹ️ 背景材料 |

---

## `science/` — 科学证据（本项目最硬的部分）

| 文件 | 内容 |
|---|---|
| [LITERATURE.md](science/LITERATURE.md) | **文献与来源索引**：6 条已核验记录、11 篇文献 DOI、关键判定素材、SI 缺口 |
| [首期决策与首批 5 条来源记录](science/2026-10-18-角色A-Suzuki首期决策与首批5条来源记录.md) | 首期反应与演示底物的决策依据；SM-001–SM-005 的字段级记录与可比性限制 |
| [Suzuki 文献核验](science/2026-10-18-角色A-Suzuki文献核验.md) | 早期核验过程：哪些结论成立、哪些被推翻（含"90% 未核实"的撤回） |
| [10/19–21 科学复核](science/2026-10-21-角色A-10-19至21任务完成与科学复核.md) | 用户自述 vs 可审核证据的分层、SM-004 结果口径决策、SM-006 候选来源 |
| [Suzuki–Miyaura 先例核验报告](science/Suzuki_Miyaura_文献核验报告.md) | 完整核验报告：证据分级方法、逐条可比性检查、全局免责边界 |

**阅读建议：** 先看 `LITERATURE.md` 的两张表，再看 `10/18 首期决策` 了解字段级口径，最后看核验报告的方法学一节。

---

## `rules/` — 判定规则（待化学签字，`DRAFT`）

| 文件 | 内容 | 交给谁 |
|---|---|---|
| [分档定义](rules/分档定义-待签字.md) | 档 A / B / C 的成员判据、判档顺序、6 条记录归档结果 | A 签字 → C 实现 `engine/placement.py` |
| [8 维门槛规则表](rules/8维门槛规则表-待签字.md) | 4 硬 + 4 软门槛的判定细则、结论优先级、T1–T18 验收用例（引擎 `--selftest` 实测 19 passed, 0 failed） | A 签字 → C 实现 `engine/gates.py` |
| [机制库 v0](rules/机制库v0-待签字.md) | 9 条机制的化学理由、判别实验、成本、序贯决策树 | A 签字 → A3/A4 调用 |

> 每份文件末尾的 D1–D6 已全部定稿（其中 D1–D5 由技术侧代签，A 可推翻）。实现与文档不一致时以测试为准。

### 配套代码骨架

| 目录 | 内容 | 状态 |
|---|---|---|
| `agents/` | A0–A5 六个 Agent 的提示词与平台配置 | 骨架，可直接粘进 SciencePal |
| `engine/` | `schemas` / `gates`（8 维门槛）/ `tier`（档位判定）/ `profiles` / `adapter`（接 C 的案例库）/ `placement` / `run_demo` / `build_bundle`（生成单文件版） | **可运行**：`--selftest` 19 passed, 0 failed；档位 8/8 一致 |
| `skills/` | `chemcritic-comparability` 可导出 Skill + `scripts/chemcritic_engine.py`（单文件、零依赖，沙箱可直接跑） | v0.2.0 可用；平台注册与验收见 [单文件引擎·平台接入三步](integration/2026-10-27-单文件引擎-平台接入三步.md) |
| `eval/` | gold set（seed）、FPR/FNR 脚本、baseline 方案 | 脚本可运行；gold set 待 B 独立标注 |
| `data/` | 8 条 entry 级记录（`records.json`） | 已核验 |

**平台接入：** 判定权威在 `engine/`，平台侧执行用 `skills/chemcritic-comparability/scripts/chemcritic_engine.py`（同源单文件版，零依赖）。步骤见 [单文件引擎·平台接入三步](integration/2026-10-27-单文件引擎-平台接入三步.md)，历史指令见 [给 C 的合并指令](integration/2026-10-25-给C-的合并指令.md)。

---

## `integration/` — 平台接入与验收

| 文件 | 内容 |
|---|---|
| [平台 Agent 搭建包](integration/2026-10-26-平台Agent搭建包.md) | 建 Agent 的 8 步、系统提示词、4 条验收问题 |
| [引擎判定接入（live-002）](integration/2026-10-27-给C-的合并指令-引擎判定接入.md) | 判定调用契约：输入 Record / 输出证书 JSON |
| [单文件引擎·平台接入三步](integration/2026-10-27-单文件引擎-平台接入三步.md) | 自举 → 固定路径调用 → 5 项验收；含沙箱不通时的降级路径 |
| [提示词补丁 v3.1 · 演示观感](integration/2026-10-27-提示词补丁v3.1-演示观感.md) | 过程自述、答案重复、L1/L2 泄漏的六条纪律与验收 |

---

## `archive/` — 已作废（仅留档）

| 文件 | 作废原因 |
|---|---|
| [ChemCritic-完善方案与黑客松计划](archive/ChemCritic-完善方案与黑客松计划.md) | 旧 Streamlit 架构与"人工检索 + 复制结果"工作流已被 10/25 技术方案取代 |
| [ChemCritic-项目分工与时间线](archive/ChemCritic-项目分工与时间线.md) | 旧时间线：缺依赖关系与出口标准，已由新分工文档取代 |
| [2026-10-16 范围与演示案例](archive/2026-10-16-角色A-范围与演示案例.md) | 部分结论（"80–95% 典型区间"、Organometallics 2006 的 90%）已被核验推翻 |
| 每日任务同步表（xlsx，未纳入仓库） | 状态自 10/22 起未更新，改用新分工文档 |
| [多反应类型检索任务包](archive/2026-10-18-ChemCritic-多反应类型文献检索任务包-DSH.md) | 面向后续扩展的调研清单，不属于首期提交范围 |

> 归档不等于删除：所有历史材料保留，便于说明"哪些是赛前已有成果"。

---

## 未纳入仓库

| 目录 | 内容 | 未纳入原因 |
|---|---|---|
| `refs/` | 11 篇开放获取 PDF、候选池（34 条）、抓取与解析脚本、原文 XML/TXT | 体积大；PDF 受出版社许可限制 |
| `outputs/` | 文献 PDF 包、预览图、历史交付表 | 可由 `docs/` 与 DOI 重建 |

文献的 DOI 与定位见 [LITERATURE.md](science/LITERATURE.md)。
