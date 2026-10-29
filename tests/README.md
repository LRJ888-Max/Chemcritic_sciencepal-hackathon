# tests/ — 可复现验证

两个脚本，解决两件不同的事。**不要混用**：引擎能判的绝不用眼睛判，眼睛只判引擎判不了的。

## 1. 引擎层（确定性，可断言）

```bash
# 测沙箱实际在跑的那一版（从 GitHub main 拉，消除本地副本落后的问题）
python tests/engine_contract.py --fetch

# 测本地改过的版本
python tests/engine_contract.py

# 改之前存基线，改之后对比
python tests/engine_contract.py --fetch --snapshot /tmp/before.json
# …改代码…
python tests/engine_contract.py --fetch --snapshot /tmp/after.json --diff /tmp/before.json
```

输出 `5 passed, 4 failed` 这种格式，退出码 0/1，可以接进任何流程。

用例在 `cases.json`，每条带 `rule` 字段说明依据。**允许改断言，但必须同时改 rule**——否则断言就变成了「让测试通过」而不是「让行为正确」。

`ref_set` 用精确集合而非包含：多一条和少一条都算失败。多出来的往往就是底物污染。

`patch: null` 的用例（C8）不走判定，改跑 `--list-records` 校验登记表完整性。

## 2. 措辞层（模型生成，用特征判定）

```bash
# 把平台输出整段粘进 txt，再跑
python tests/wording_lint.py draft.txt --case R1 --ref 40 50
python tests/wording_lint.py draft.txt --case R2 --ref 40 50 93 99
python tests/wording_lint.py draft.txt --case R4
```

`--case` 决定「用户到底给了哪些值」。用户没给的数字出现在输出里，直接报编造。
`--ref` 是引擎本次返回的参照值，出现在输出里属于合法，不计编造。**不传就会把 40/50 误报成编造。**

检查项分五类：

| 前缀 | 含义 |
|---|---|
| `W1–W20` | 硬性违规（编造碱/载量/当量、无水位、区间、思维链、内部术语、预期效果、判成败、0 差值…） |
| `X1` | 编造数值（数字 + 单位，用户没给、引擎也没给） |
| `X2` | 假溯源（试剂名标了「用户给出」但用户从没提） |
| `S` | 结构（v8 五节骨架 + 停止线） |
| `M` | 必含项 |

`W13`（尾部状态说明）只扫最后 5 行——因为 v8 规定的**开头句本身就是**「已完成对您…的判定」，全文匹配会误报每一条合规输出。

## 3. patches/ — 引擎补丁存档

```
tests/patches/placement_2026-10-30.patch   底物过滤 + ensure_keys 及其接线（完整版，已应用）
tests/patches/gates_2026-10-29.patch       缺失不写成差异（已应用）
tests/patches/placement_2026-10-29.patch   （已被 10-30 完整版取代，仅留档，勿再应用）
```

**2026-10-30 已应用到 engine/ 并 rebuild**：`engine_contract` **9/9**，`--selftest` **28/28**。
实测锚点：C1 池 [40,50] delta −5；C6 池 [93,99] delta 6.0；C3 NO_COMPARABLE 且键齐全值为 null。

## 当前基线（2026-10-29，GitHub main，引擎 1.1.0 / 提示词 v8）

> ⚠️ 此基线是修复**前**的远端快照，仅供对照；本地 engine/ 现为修复后状态，待 push。

```
C1 主场景 4-溴苯甲醚    FAIL   ref [40,50,93,99]，应为 [40,50]（93/99 是 4-溴甲苯）
C2 残缺输入            pass
C3 库外底物 3-溴噻吩    FAIL   status=PLACED / delta=0，应为 NO_COMPARABLE / null
C4 跨口径 溴苯 GC       pass
C5 口径推定 label_raw空 pass
C6 4-溴甲苯            FAIL   ref 与 C1 完全相同，底物过滤未生效
C7 口径与原话矛盾       pass   （原话写 GC 却标 isolated → UNDEFINED）
C8 登记表完整性         pass   （32 条 / 5 底物 / locator_zh 齐全）
C9 字段稳定性          FAIL   UNDEFINED 时 placement 只有 3 个键
```

**5 passed / 4 failed。四个失败同一条链**：`placement.py:124` 的 `same_class` 只按口径过滤，不看底物对。

## 纪律

- 改完必须 **C1–C9 全 pass**，且 `--diff` 里除 `ref_values` / `reference_count` / `delta_vs_min` 外**不许有意外变化**。
- 只让 C1 通过、C6 不通过，等于没修（两者互为镜像）。
- 改完源码记得 `build_bundle.py` + push：**改 `engine/` 不等于改了沙箱**。判断方法是跑 `--fetch`，结果没变就是没打包。
- 这些用例只覆盖 4 个底物 × 2 种口径。通过不等于全库正确，只等于「已测过的行为没退化」。
