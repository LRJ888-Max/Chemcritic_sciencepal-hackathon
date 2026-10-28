"""用户判定输入的来源审计（不含化学判断）。

目标：让 user.json 的每个外部事实都能追溯来源，禁止把历史对话或账号记忆
拼装成"用户申报过"的实验条件。

`inputs_source` 是字段路径到来源的映射，例如：
    {"outcome.outcome_type": "user_stated", "conditions.base": "unknown"}

来源只允许：
- user_stated：用户在当前对话中明确提供；
- unknown：用户未提供或明确表示无法提供，对应字段必须为 null / 缺省；
- memory：历史对话或账号记忆。该来源不允许进入判定，审计直接拒绝。
"""

from __future__ import annotations

from typing import Any, Dict

ALLOWED_INPUT_SOURCES = {"user_stated", "memory", "unknown"}
_MISSING = object()


def _value_at_path(payload: Dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def audit_input_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """返回可序列化审计结果；rejected 的输入不得进入判定。"""
    declared = payload.get("inputs_source")
    if declared is None:
        return {"status": "not_declared", "errors": []}
    if not isinstance(declared, dict):
        return {"status": "rejected", "errors": ["inputs_source 必须是字段路径到来源的对象"]}

    errors = []
    for path, source in declared.items():
        if not isinstance(path, str) or not path.strip():
            errors.append("inputs_source 含空字段路径")
            continue
        if source not in ALLOWED_INPUT_SOURCES:
            errors.append(f"{path}: 未知来源 {source!r}")
            continue
        value = _value_at_path(payload, path)
        if value is _MISSING:
            errors.append(f"{path}: 字段路径不存在")
            continue
        if source == "memory":
            errors.append(f"{path}: 历史记忆不得作为判定输入；请改为 null 并标 unknown")
        elif source == "unknown" and value is not None:
            errors.append(f"{path}: 标为 unknown 时字段值必须为 null")

    return {"status": "rejected" if errors else "pass", "errors": errors}
