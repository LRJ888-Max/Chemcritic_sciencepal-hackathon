"""ChemCritic 规则引擎（可独立运行，也可作为子包被 `app/chemcritic/` 导入）。

模块职责：
- `schemas`   数据结构（无业务判断）
- `gates`     8 维门槛判定（确定性，LLM 无权覆盖）
- `tier`      档位判定（受控词表 → 规则 → 主判/备选）
- `profiles`  案例库记录的档位字段补丁表
- `adapter`   把 C 的案例库 JSON 转成本引擎的 Record
- `placement` 等价类、档内定位、双档敏感性
- `run_demo`  三屏演示 + 规则表验收矩阵（CLI）

两种用法：
    python engine/run_demo.py                 # 独立运行
    from engine.gates import judge            # 作为子包导入
"""

__all__ = ["schemas", "gates", "tier", "profiles", "adapter", "placement"]
