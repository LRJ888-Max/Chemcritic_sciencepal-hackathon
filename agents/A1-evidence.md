# A1 · 取证 Agent

**平台：** SciencePal · Agent Co-Building + **Personal Knowledge Base** + **Sandbox**
**优先级：** P0
**输出 schema：** `data/records.json` 的单条记录结构（`Record.from_dict` 可直接吃）

---

## 职责

把文献变成 **entry 级**结构化记录：一条记录 = 一个表格行 + 它的脚注 + 页码定位。

**不是**"总结这篇论文讲了什么"，而是"这篇论文的哪张表第几行报了什么数、什么口径、什么条件"。

## 输入

```json
{"substrate": {"aryl_halide": "...", "boronic_acid": "...", "product": "..."},
 "reaction_type": "Suzuki-Miyaura",
 "scope": "direct | neighbor"}
```

## 输出（每条记录必须全字段存在，缺失写 null）

```json
{
  "record_id": "SM-xxx",
  "source_id": "SM-xxx",
  "citation": "作者, 期刊 年, 卷, 页",
  "doi": "...",
  "locator": "Table N entry M + footnote a, PDF p.X",
  "outcome": {"value": 40, "unit": "%", "outcome_type": "isolated",
              "label_raw": "原文措辞原文照抄", "note": "口径歧义说明"},
  "assay": {"method": "...", "internal_standard": null, "quantification_basis": "..."},
  "conditions": {"catalyst": "...", "catalyst_mol_pct": 3.0, "ligand": null, "ligand_mol_pct": null,
                 "base": "...", "base_eq": 2.0, "solvent": "...", "temp_c": 80, "time_h": 6,
                 "atmosphere": "N2", "scale_mmol": 1.0,
                 "equivalents": {"aryl_halide": 1.0, "boronic_acid": 1.1}, "concentration_m": null},
  "purification": null,
  "evidence_grade": "A | B | C",
  "verification": "verified | verified_with_caveat | candidate"
}
```

## 铁律

1. **抽不到就写 `null`，禁止补猜**。表注写 "Yield (%)" 而脚注写 "conversion" 时，`outcome_type` 必须记 `unknown`，并把两种原文措辞都抄进 `label_raw`。
2. **`locator` 必须能定位**：表号 + entry 号 + 脚注字母 + PDF 页码，缺一不可。
3. **同一篇的不同 entry 是不同记录**，但必须带同一个 `source_id`（用于"非独立来源"标记）。
4. **不得合并同一篇内的多个值**成一个范围。

## 平台绑定

- 知识库：`refs/` 的 11 篇 PDF 上传至 Personal Knowledge Base
- 沙箱：PyMuPDF / pdfplumber 抓表；抽取脚本见 `engine/`（TODO：`extract.py`）
- 检索时必须记录检索词与命中条数（可复现）

## 失败处理

| 情况 | 动作 |
|---|---|
| 无命中 | 返回空数组 + 检索词 + "建议放宽条件的维度" |
| 表格是图片 | 标记 `needs_manual_check`，不 OCR 猜数 |
| 字段疑似但无明证 | 写 `null` 并在 `note` 里记"疑似，未确认" |

## TODO（C 填）

- [ ] `engine/extract.py`：PDF → 候选表 → 结构化 JSON
- [ ] 字段抽取的 5 个 spot-check 用例（与 SM-001–006 逐字段比）
- [ ] 检索词模板（底物名 / SMILES / 反应名 / 催化剂族）
