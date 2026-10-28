# -*- coding: utf-8 -*-
"""
SciencePal SDK 测试脚本（ChemCritic 项目用）
用法：
  1) 先按 docs/integration/2026-10-28-SciencePal-API与SDK接入参考.md 创建 API 密钥
  2) 设置环境变量：
       $env:SCIENCEPAL_API_KEY="your-key"     (PowerShell)
       export SCIENCEPAL_API_KEY="your-key"   (bash/zsh)
  3) python scripts/sciencepal_sdk_test.py [--list-only]
说明：--list-only 只打印可用 Agent 列表（找 ChemCrtic 的 agent_id）；
      否则运行 ChemCritic 的 35% 收率标准用例并输出结构化事件。
"""

import asyncio
import os
import sys

from sciencepal import SciencePal

TEST_PROMPT = (
    "4-溴苯甲醚 1 mmol + 苯硼酸 1.2 mmol，Pd(PPh3)4 3 mol%，Na2CO3 2 equiv，"
    "无水 THF，60 °C，12 h，N2，柱层析分离，35% 分离收率。这个收率跟文献比怎么样？"
)


async def list_agents(client: SciencePal) -> list:
    agents = await client.agents.available()
    print("可用 Agent 数量:", len(agents))
    for a in agents:
        print(f"  {a.name} | {a.agent_id} | {a.source}")
    return agents


async def main() -> None:
    api_key = os.environ.get("SCIENCEPAL_API_KEY")
    if not api_key:
        raise SystemExit("缺少环境变量 SCIENCEPAL_API_KEY")

    list_only = "--list-only" in sys.argv
    async with SciencePal(
        api_key=api_key,
        base_url="https://sciencepal.ai/api",
        timeout=120,
    ) as client:
        agents = await list_agents(client)
        if list_only:
            return
        agent_id = os.environ.get("SCIENCEPAL_AGENT_ID")
        if not agent_id:
            # 自动匹配 ChemCritic（名称可能拼写为 ChemCrtic）
            cand = [a for a in agents if "chem" in a.name.lower()]
            if len(cand) != 1:
                raise SystemExit(
                    "未找到唯一 ChemCritic agent，请用 --list-only 确认后设置 SCIENCEPAL_AGENT_ID"
                )
            agent_id = cand[0].agent_id
            print("自动选择:", cand[0].name, agent_id)

        run = await client.runs.initiate(
            agent_id=agent_id,
            model_name=os.environ.get("SCIENCEPAL_MODEL_ID"),
            prompt=TEST_PROMPT,
        )
        print("Thread ID:", run.thread_id)
        print("Run ID:", run.agent_run_id)
        if not run.agent_run_id:
            raise RuntimeError("未创建 Agent run")

        async for event in client.runs.stream(run.agent_run_id):
            print(event.model_dump_json(exclude_none=True))

        status = await client.runs.get(run.agent_run_id)
        print("Run status:", status.status)
        if status.status in {"failed", "stopped"} or status.error:
            raise RuntimeError(status.error or status.status)


if __name__ == "__main__":
    asyncio.run(main())
