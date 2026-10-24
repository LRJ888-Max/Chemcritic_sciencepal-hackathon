# A5 · 记录 Agent

**平台：** SciencePal · **Memory** + **Share Link**
**优先级：** P2（有余力才做；不做时在说明中标注"未实现闭环"）

---

## 职责

1. 保存每一轮的输入、判定、机制、实验计划
2. 用户回填后续实验结果后**触发 A2 重算**（证书升级/降级）
3. 生成可分享的复盘报告（`Share Link`）

## 输入 / 输出

输入：全链路输出 + 用户回填的新结果
输出：`Round[]` 时间线 + 报告

```json
{"rounds": [{"round": 1, "date": "...", "observed": {"value": 35, "outcome_type": "isolated"},
             "verdicts_summary": {"comparable": 3, "blocked": 3, "needs_evidence": 2},
             "plan": "step1 物料衡算", "outcome": null}],
 "report": {"path": "...", "share_url": "..."}}
```

## 铁律

1. **回填即重算**：不得只追加文字，必须重跑 A2 并记录新旧判定差异。
2. **保留版本**：每轮记录 `rule_version` 与模型版本，便于复现。
3. **不完整轮次单独标记**，不参与任何趋势判断。
4. 报告中的数字必须能追溯到 `Verdict`，不得在报告里新增未经判定的比较。

## 平台绑定

- Memory：跨会话记忆（若平台不支持，降级为会话内摘要 + 本地 JSON）
- Share Link：只读分享，评审可直接查看

## 失败处理

| 情况 | 动作 |
|---|---|
| Memory 不可用 | 输出 JSON 存档路径，提示用户手动保存 |
| 回填字段不全 | 标 `incomplete_round`，不更新趋势 |

## TODO（C 填）

- [ ] 本地 JSON 存档格式（`data/sessions/<exp_id>.json`）
- [ ] 报告模板（复盘卡 + 时间线 + 局限声明）
- [ ] 分享链接的权限检查（免登录可读）
