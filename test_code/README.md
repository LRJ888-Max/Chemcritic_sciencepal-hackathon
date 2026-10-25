# ChemCritic 本地最小工程

## 队友下载后快速启动

安装Python 3.11（建议与已测试版本一致），下载仓库ZIP并解压，或克隆仓库。在包含本README的文件夹打开终端执行：

```powershell
python -m app.chemcritic.server
```

浏览器打开 http://127.0.0.1:8765/ ，保持终端开启。Windows若`python`不可用但Python启动器可用，可改为`py -3.11 -m app.chemcritic.server`。无需安装第三方Python包、Node.js、SciencePal客户端或配置API密钥。端口冲突可加`--port 8766`并访问对应端口。

本分享包不含个人历史数据库、临时文件或完整文献证据包。首次运行会自动创建自己的`outputs/chemcritic.sqlite3`。默认案例是模拟数据，不代表真实实验；现有团队库为首批5篇论文、7条结果记录，未包含尚未交接的30篇文献。

在同一目录验证：

```powershell
python -m unittest discover -s tests -q
```

新版SciencePal示例可在网页选择`cases/sciencepal/SM004_reverified.json`导入。完整证据包未随仓库分发，文档中的证据归档路径与原作者本机路径可能无法访问，不影响应用及测试运行。历史文档不是另一个待执行的任务清单。

GitHub上传时应上传本文件夹内的内容，使`README.md`、`app/`、`cases/`处于仓库根目录。不是将整个上传包再嵌套在仓库的一个同名文件夹里。建议先使用私有仓库与队友共享。

首期范围：已完成的 Suzuki–Miyaura 单次实验复盘。
当前实现纯 Python 输入校验、用户目标比较、证据来源分层、未知字段追问，以及跨结果口径比较拦截。Python 3.11，无第三方依赖。

## v0.2 多先例更新

在页面点“载入团队先例库”，会加载7条结果记录（5篇DOI论文），评估时按完整底物对匹配。结果页显示逐字段“相同/不同/未知”、来源定位和原报告口径。SM-004的yield/total conversion解释分歧显式保留，不自动定论。

命令行同样可用：

```powershell
python -m app.chemcritic cases/C2_simulated_42_goal60.json --library cases/library/suzuki_handoff_v1.json
python -m tools.run_validation
```

后一命令生成8个固定案例、各3次运行的本地软件验收报告，位于`outputs/validation/report.md`。具体已实现内容与未决事项见`docs/integration_status.md`。

结果页新增“优先核查行动”，最多3项：按证据缺口生成原因、需核对的原始材料、补齐后的判断范围及限制。引用仅来自本次先例；无先例时不补造引用。行动由确定性规则生成，不是LLM输出，也不提供调整反应条件的处方。旧版历史快照不自动补写行动，重新评估后才使用`core-0.2.1-partial`规则。

## 打开浏览器工作台

在项目目录的PowerShell终端运行：

```powershell
python -m app.chemcritic.server
```

保持终端开启，在浏览器打开`http://127.0.0.1:8765`。停止服务按Ctrl+C；端口占用时运行`python -m app.chemcritic.server --port 8766`，并打开8766对应地址。

页面会载入C2模拟案例与SM-004原始JSON：

1. 修改实验条件、结果或用户目标；未知字段留空。
2. 可上传或粘贴SciencePal原始JSON，也可清空先例。此版本仅适配SM-004 Table 1 entry 7格式。
3. 点击“开始复盘”，查看中文目标比较、证据来源、推断边界和缺失追问。
4. 点击“保存本次复盘”，再从历史列表打开。修改后需重新评估，再保存为新记录；旧记录不被覆盖。
5. “导出JSON”下载当前输入、SciencePal原始文本和规则结果。输入修改后旧结果会标为过期，保存及导出暂时禁用。

历史数据库位于`outputs/chemcritic.sqlite3`，重启仍保留，已加入Git忽略。历史快照保存输入、原始先例文本、规则结果和输入摘要；不是人工化学审核或不可篡改账本。页面只在本机访问，当前没有公网链接、账号系统或实时SciencePal连接。

实验编号是用户自定的固定标签，不随收率变化。历史列表标题按每条记录实际保存的结果与目标生成，例如`65% 分离收率 · 目标≥60% 分离收率`，下方单独显示实验编号。此显示方式同样适用于旧记录，不修改原始数据。

## 运行

在本目录打开终端：

```powershell
python -m app.chemcritic cases/C2_simulated_42_goal60.json --precedents cases/SM004_precedent.json
python -m unittest discover -s tests -v
```

第一条命令在终端输出 JSON；不会改写案例。CLI 验证失败时向标准错误输出字段路径并返回退出码 2。

## 使用SciencePal提取结果

之前粘贴的完整SM-004 JSON已保存为`cases/sciencepal/SM004_raw.json`。在项目目录执行：

```powershell
python -m app.chemcritic cases/C2_simulated_42_goal60.json --sciencepal cases/sciencepal/SM004_raw.json
```

该命令先将SciencePal原始JSON转为先例记录，再调用原规则。`retrieval.mode`应为`sciencepal_file_import`，`live_retrieval_performed`仍为false。`--sciencepal`与`--precedents`只能选一个。

导入保留整个`raw_record`、原结果定义、逐字段来源文本和原文件SHA-256。不执行导入文本中的命令，不下载URL，不把`tools_used/access_status`升级为已核验事实。规则输出包含导入模式、SHA-256、口径及核验标记；完整原文仍在原始文件中。

当前适配器仅支持本次`entry_7_extraction`格式及Table 1 entry 7。其他导出布局需新增适配，不能仅更改entry编号复用。未提供的条件保留null/unknown；无法映射的结果类型保留unknown。无法识别的单位或错误数据会报字段路径，不静默换算。

SM-004原始metric同时包含yield与conversion，当前根据明确的开头`GC yield`映射为gc，同时保留原句并提示A核验。该映射本身不是原文真实性或化学口径的独立确认。

## 第一个案例

`cases/C2_simulated_42_goal60.json` 来自团队已测试的模拟输入：42%分离收率、60%分离收率目标、碱未知、用户报告NMR匹配。

预期：`goal_comparison.state=below_target`，`gap_pp=18.0`，`base.state=unknown`，`product_evidence.state=user_report_only`。SM-004为GC口径，返回`assay_mismatch`，禁止文献数值对比。

差距`gap_pp`定义为目标减实测；正值为未达，负值表示超过目标。相同收率类型只允许按用户明确目标做算术比较，不代表已核验定量方法或实验结果。

## 数据约定

- 百分比存数字42，不存0.42或字符串“42%”。温度为摄氏度、时间为小时、载量为mol%、规模为mmol。
- 未知数字用`null`。`base_status`为`present/absent/unknown`，字段缺失按unknown处理；未知不等于未加入。
- `simulated`必须显式填写布尔值，并保留在结果中。
- `user_goal`可以为null；设目标时需同时声明结果类型。不从文献推导用户目标。
- 表征按条目记录`method/supports_target/provenance`。`user_report`不升级为系统确认；`human_review`须附`review_reference`，这里只保存审核记录，不验证其真实性。
- `source_id`代表文章，`record_id`代表实验条目。同文可以有多条实验记录和不同结果口径。
- SM-004示例来自团队交接及SciencePal提取报告，当前工程未独立核验原文，不包含实时检索。

## 未冻结的规则

按B v0.2，文献先例不触发S2，MVP文献数值比较关闭。已实现无歧义的S3证据缺失/矛盾与结果完全缺失分支；证据不足时阻断数值结论。用户报告是否满足S1证据门槛、未知结果口径下的正式状态、无检索命中对状态的影响仍待确认，其余分支保留`status=null`。

所有文献数值比较关闭；同口径返回`mvp_numeric_disabled`，跨口径返回`assay_mismatch`，未知口径返回`assay_unknown`。禁止因同口径就自动放行。

已实现命令行、SciencePal文件适配、团队案例库精确底物匹配、逐字段差异、本地网页与SQLite历史保存。仅对少量明确名称别名做对齐，没有化学结构标准化、来源自动发现、排序打分、LLM解释或公网部署。工具调用自报文本不视为已核验执行日志。网页中的中文解释由固定模板生成。
