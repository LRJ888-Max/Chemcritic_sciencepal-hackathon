# 第三方代码与数据使用声明

**项目：** ChemCritic（SciencePal AI4Science Hackathon 2026 · Track 4）
**日期：** 2026-10-27

---

## 一、本项目原创代码

**核心判定引擎零第三方依赖**，仅使用 Python 标准库（`json` / `os` / `re` / `sys` / `dataclasses` / `typing`）。

```
engine/schemas.py    数据模型
engine/gates.py      8 维门槛判定（确定性，无 LLM 参与）
engine/tier.py       档位判定（受控词表 → 规则 → 边界敏感性）
engine/placement.py  等价类划分与档内定位
engine/extract.py    候选抽取器（只产候选，不产结论）
engine/run_demo.py   演示与 19 项回归测试（T1–T18）
eval/metrics.py      FPR / FNR / BDA / CAL
```

克隆后 `python engine/run_demo.py` 可直接运行，**无需安装任何包**。

---

## 二、计划引入的第三方库（仅抽取层）

| 项目 | 许可 | 用途 | 本次提交是否已包含 |
|---|---|---|---|
| pdfplumber | MIT | PDF 表格抽取（轻量） | 否（未并入） |
| Docling（IBM） | MIT | 表格结构识别（首选） | 否（未并入） |
| GROBID | Apache-2.0 | 学术 PDF → XML（备用） | 否（未并入） |
| RxnScribe | MIT | 反应图识别（仅评估，未采用） | 否 |

**义务：** 保留版权与许可声明；在材料中说明用途与是否修改；不将第三方代码声称为原创。**不引入 GPL/AGPL 类许可。**

---

## 三、文献数据的使用边界

本项目引用的是**已发表文献中的数值与条件**，不复制全文文本，不随仓库分发 PDF。

| 类型 | 可否入库 | 说明 |
|---|---|---|
| CC BY / 开放获取 | ✅ | 可引用数值与表号；注明出处 |
| CC BY-NC-ND | ⚠️ | 可引用事实性数据，**不得改编或再分发原文** |
| 订阅 / 付费墙 | ❌ | **不上传全文**，只登记 DOI 与官方入口 |

- `refs/`、`outputs/` 已在 `.gitignore` 中；**PDF 不随代码分发**。
- 知识库中每条记录均带 `locator`（表号 / entry / 脚注）与 `source_id`，可逐条回查。
- 缺失的支撑材料（如 SM-002 的 SI）显式标注"未存档，仅提供官方入口"，**不以镜像替代**。

---

## 四、赛前 / 赛期成果区分

| 阶段 | 内容 |
|---|---|
| **赛前** | Suzuki–Miyaura 文献的初步收集与化学直觉 |
| **赛期内（2026-10-18 起）** | 8 维门槛规则、档位定义、机制库、证据链字段、判定引擎、评测指标、知识库、平台 Agent 配置、全部文献核验记录 |

规则与数据的每一次决策都有落盘文档（`docs/rules/`、`docs/science/`），带日期与决策人。

---

## 五、核心环节使用 SciencePal 的说明

本项目**不以 SciencePal 做写作或排版**，核心环节的实质使用为：

| 环节 | SciencePal 能力 |
|---|---|
| 证据检索 | Personal Knowledge Base（40 条 entry 级记录 + 11 篇开放获取文献，返回带 locator） |
| 判定执行 | Agent 按 8 维门槛规则执行判定（规则由 `engine/gates.py` 确定，模型不得覆盖） |
| 流程编排 | Agent Co-Building（系统提示词 + 子 agent 并行排查机制）+ Share Link 供评审访问 |
| 抽取（预研） | API + 沙箱：指定 PDF → 抽取脚本 → 下载 JSON（3 篇 51 条候选，仅产候选不产结论） |

**抽取工具只负责"把 PDF 变成表格"，不承担判定，不承担检索。**

---

## 六、致谢

文献数值来自各篇原文，引用信息见 `docs/science/LITERATURE.md` 与 `kb/L2-records.md`。
SciencePal 平台提供知识库、智能体编排与沙箱能力。
