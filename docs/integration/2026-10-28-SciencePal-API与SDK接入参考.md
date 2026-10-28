# SciencePal · Agent API 与 Python SDK 接入参考（含 ChemCrtic 实测用例）

**日期：** 2026-10-28
**来源：** 官方帮助页 `https://sciencepal.ai/help/sdk`（全文核对）+ 平台前端配置（NEXT_PUBLIC_BACKEND_URL）
**结论：** SciencePal 提供正式 HTTP API + Python SDK（v0.2.3），可以编程方式调用当前账号有权限的全部 Agent（含自建 ChemCrtic），无需源码仓库权限。

---

## 1. 关键信息速览

| 项 | 值 |
|---|---|
| API 基地址 | `https://sciencepal.ai/api` |
| 鉴权方式 | 全部请求（含流式）带 `Authorization: Bearer <API_KEY>` |
| SDK 版本 | 0.2.3（Python ≥ 3.11，Windows / macOS / Linux） |
| SDK 安装 | `python -m pip install "https://sciencepal.ai/downloads/sdk/sciencepal_sdk-0.2.3-py3-none-any.whl"` |
| 验证安装 | `python -c "import sciencepal; print(sciencepal.__version__)"`（预期 0.2.3） |
| 我们的 Agent | 名称 ChemCrtic，handle `@chemcrtic-de01f`（agent_id 以 `agents.available()` 返回为准） |
| 模型 | 系统默认 `platform::deepseek/deepseek-v4-flash`；可用列表用 `models.list()` 获取 |

## 2. 创建 API 密钥（只显示一次，务必立刻保存）

1. 登录 `sciencepal.ai` 网页；
2. 左侧栏 **用户中心 → 「连接与开发」→「API 与 Python SDK」**；
3. 输入密钥名称 + 有效天数（1–365，默认 90），点「生成 API 密钥」；
4. **完整密钥只显示一次**，立即复制保存；列表可看到期、可撤销；
5. 密钥沿用账户权限与额度，可访问本账户的 Agent、任务与沙箱文件。

> ⚠️ 不要存进网页代码、任务提示词、URL 或 Git；只放环境变量 / 本地 secrets。

## 3. HTTP API 端点（文档明确列出）

| 方法 | 端点 | 用途 |
|---|---|---|
| GET | `/api/agent-catalog?usable_only=true` | 列出当前账号可用 Agent（内置 + 自建 + 获授权共享，排除仅查看权限），返回 `agents` 数组，含 `agent_id` |
| GET | `/api/billing/available-models` | 列出可用模型，返回 `models` 数组（含 `platform::` / `user::` 前缀的 id） |
| POST | `/api/agent/initiate` | 发起一次 Agent 运行（**每次调用新建会话**），返回 `thread_id` 与 `agent_run_id` |
| GET | `/api/agent-run/{run_id}/stream` | SSE 流式读取运行事件（`data: {...}` 行，结束为 `data: [DONE]`） |
| GET | `/api/agent-run/{run_id}` | 查询运行状态（`completed / failed / stopped / asked …`） |
| POST | `/api/thread/{thread_id}/messages` | 向已有会话追加用户消息（追问/继续旧任务，需 SDK ≥0.2.1） |

## 4. Python SDK 用法

### 4.1 列出 Agent（找 ChemCrtic 的 agent_id）

```python
import asyncio, os
from sciencepal import SciencePal

async def main():
    async with SciencePal(api_key=os.environ["SCIENCEPAL_API_KEY"]) as client:
        agents = await client.agents.available()
        for agent in agents:
            print(agent.name, agent.agent_id, agent.source)
        print("可用 Agent 数量:", len(agents))

asyncio.run(main())
```

### 4.2 调用 ChemCrtic（新建会话，跑 35% 收率案例）

```python
import asyncio, os
from sciencepal import SciencePal

async def main():
    async with SciencePal(
        api_key=os.environ["SCIENCEPAL_API_KEY"],
        base_url="https://sciencepal.ai/api",
        timeout=120,
    ) as client:
        run = await client.runs.initiate(
            agent_id=os.environ["SCIENCEPAL_AGENT_ID"],
            model_name=os.environ.get("SCIENCEPAL_MODEL_ID"),  # 可省略，沿用平台默认
            prompt=("4-溴苯甲醚 1 mmol + 苯硼酸 1.2 mmol，Pd(PPh3)4 3 mol%，"
                    "Na2CO3 2 equiv，无水 THF，60 °C，12 h，N2，柱层析分离，"
                    "35% 分离收率。这个收率跟文献比怎么样？"),
        )
        print("Thread ID:", run.thread_id)
        print("Run ID:", run.agent_run_id)
        async for event in client.runs.stream(run.agent_run_id):
            print(event.model_dump_json(exclude_none=True))
        status = await client.runs.get(run.agent_run_id)
        print("Run status:", status.status)
        if status.status in {"failed", "stopped"} or status.error:
            raise RuntimeError(status.error or status.status)

asyncio.run(main())
```

> 输出是**结构化 SSE 事件**（助手输出 / 工具活动 / 状态更新），不是纯文本回复。`asked` 表示等待用户回答，不是完成。

### 4.3 追问 / 继续旧任务（同一会话，不新建）

```python
import asyncio, os
from uuid import uuid4
from sciencepal import SciencePal, AgentStartRequest

async def main():
    thread_id = os.environ["SCIENCEPAL_THREAD_ID"]
    async with SciencePal(api_key=os.environ["SCIENCEPAL_API_KEY"]) as client:
        await client.runs.add_message(thread_id, "展开", message_id=str(uuid4()))
        run = await client.runs.start(thread_id, AgentStartRequest(model_name=os.environ.get("SCIENCEPAL_MODEL_ID")))
        print("New run ID:", run.agent_run_id)
        async for event in client.runs.stream(run.agent_run_id):
            print(event.model_dump_json(exclude_none=True))

asyncio.run(main())
```

## 5. 常见错误

| 状态码 | 含义 / 处理 |
|---|---|
| 401 | 密钥缺失 / 过期 / 被撤销 |
| 403 / 404 | Agent ID 错误或账户无访问权限 |
| 422 | `Selected model is unavailable for this account`——重取模型列表用返回的 id |
| 429 | 账户额度不足，稍后重试 |
| 列表没有某模型 | 确认 SDK 与网页同一账号同一站点；网页也没有则该模型未开放 |

## 6. 落地建议（本项目）

- 用 SDK 写一个 `scripts/sciencepal_sdk_test.py`（已建，见同目录），`agents.available()` 打印 agent_id → 用 4.2 的用例跑 ChemCrtic → 流式事件存档，可作为**可复现的验收/回归脚本**，替代每次手工在网页点；
- 配合本仓库 `docs/review/2026-10-28-*` 诊断：SDK 流事件里可直接核对"证书引用、内部术语是否泄漏、token 消耗"，把验收自动化；
- Streamlit 集成示例（官方）：凭据放 `.streamlit/secrets.toml`，`st.session_state` 存 run id 防重跑。

---

## 附：核对依据

- `https://sciencepal.ai/help/sdk`（官方帮助页，2026-10-28 抓取全文）；
- 平台前端配置 chunk：`NEXT_PUBLIC_BACKEND_URL="https://sciencepal.ai/api"`、`NEXT_PUBLIC_SUPABASE_URL`/ANON KEY、`NEXT_PUBLIC_KB_URL="https://kb.sciencepal.ai"`。
