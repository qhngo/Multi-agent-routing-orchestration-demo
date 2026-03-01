from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str
    method_name: str


def tool(name: str, description: str) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    def decorator(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        setattr(
            func,
            "__agent_tool_spec__",
            AgentToolSpec(name=name, description=description, method_name=func.__name__),
        )
        return func

    return decorator


class InternalToolOrchestrator:
    def __init__(self, owner: object) -> None:
        self._tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._tool_specs: dict[str, AgentToolSpec] = {}
        self._discover_tools(owner)

    def list_tools(self) -> list[AgentToolSpec]:
        return [self._tool_specs[name] for name in sorted(self._tool_specs.keys())]

    def execute(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        tool_func = self._tools.get(tool_name)
        if not tool_func:
            return {"ok": False, "error": f"Unknown tool '{tool_name}'"}
        try:
            return tool_func(payload)
        except Exception as error:  # pragma: no cover
            return {"ok": False, "error": f"Tool '{tool_name}' failed: {type(error).__name__}"}

    def _discover_tools(self, owner: object) -> None:
        for attribute_name in dir(owner):
            attribute = getattr(owner, attribute_name)
            if not callable(attribute):
                continue
            spec = getattr(attribute, "__agent_tool_spec__", None)
            if not isinstance(spec, AgentToolSpec):
                continue
            if spec.name in self._tools:
                raise ValueError(f"Duplicate internal tool name detected: '{spec.name}'")
            self._tools[spec.name] = attribute
            self._tool_specs[spec.name] = spec


def standard_list_available_tools(orchestrator: InternalToolOrchestrator) -> list[tuple[str, str]]:
    return [
        (spec.name, spec.description)
        for spec in orchestrator.list_tools()
    ]


def standard_execute_tool(
    orchestrator: InternalToolOrchestrator,
    tool_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = orchestrator.execute(tool_name=tool_name, payload=payload)
    if not isinstance(result, dict):
        return {"ok": False, "error": "Tool returned a non-dict result."}
    if "ok" not in result:
        result["ok"] = True
    return result


def standard_list_tools_payload(orchestrator: InternalToolOrchestrator) -> dict[str, Any]:
    return {
        "ok": True,
        "tools": [
            {"name": spec.name, "description": spec.description}
            for spec in orchestrator.list_tools()
        ],
    }


@dataclass(frozen=True)
class ToolDecision:
    tool_name: str | None
    payload: dict[str, Any]
    note: str = ""


class IterativeToolPlanner:
    def __init__(self, orchestrator: InternalToolOrchestrator, max_steps: int = 6) -> None:
        self._orchestrator = orchestrator
        self._max_steps = max(1, max_steps)

    def run(
        self,
        initial_state: dict[str, Any],
        build_plan: Callable[[dict[str, Any], list[AgentToolSpec]], list[ToolDecision]],
        apply_result: Callable[[dict[str, Any], ToolDecision, dict[str, Any]], None],
        should_stop: Callable[[dict[str, Any]], bool],
    ) -> tuple[dict[str, Any], list[str]]:
        tools = self._orchestrator.list_tools()
        state = dict(initial_state)
        trace: list[str] = ["tool_planner:engine:simple"]
        plan_shown = False

        for step in range(1, self._max_steps + 1):
            if should_stop(state):
                trace.append(f"tool_planner:stop:step:{step - 1}")
                break

            decisions = build_plan(state, tools)
            if decisions and not plan_shown:
                trace.append(self._plan_event(step, decisions))
                plan_shown = True

            if not decisions:
                trace.append("tool_planner:decision:none")
                break

            decision = decisions[0]
            if decision.note:
                trace.append(f"tool_planner:decision_note:{decision.note}")
            if not decision.tool_name:
                trace.append("tool_planner:decision:none")
                break

            trace.append(f"tool_planner:step:{step}:tool:{decision.tool_name}")
            result = self._orchestrator.execute(decision.tool_name, decision.payload)
            trace.append(self._step_output_event(step, decision, result))
            apply_result(state, decision, result)
        else:
            trace.append(f"tool_planner:stop:max_steps:{self._max_steps}")

        return state, trace

    @staticmethod
    def _plan_event(step_number: int, decisions: list[ToolDecision]) -> str:
        payload = {
            "event": "plan",
            "step": step_number,
            "plan": [
                {
                    "tool_name": decision.tool_name,
                    "payload": IterativeToolPlanner._to_json_safe(decision.payload),
                    "note": decision.note,
                }
                for decision in decisions
            ],
        }
        return "tool_planner:event:" + json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _step_output_event(step_number: int, decision: ToolDecision, result: dict[str, Any]) -> str:
        payload = {
            "event": "step_output",
            "step": step_number,
            "tool_name": decision.tool_name,
            "result": IterativeToolPlanner._summarize_result(result),
        }
        return "tool_planner:event:" + json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"type": str(type(result).__name__)}
        summary: dict[str, Any] = {"keys": sorted(result.keys())}
        if "ok" in result:
            summary["ok"] = result.get("ok")
        if "error" in result:
            summary["error"] = result.get("error")
        state_update = result.get("state_update")
        if isinstance(state_update, dict):
            summary["state_update_keys"] = sorted(state_update.keys())
        if "tools" in result and isinstance(result.get("tools"), list):
            summary["tool_count"] = len(result.get("tools", []))
        if "answer" in result:
            summary["answer_preview"] = str(result.get("answer", ""))[:120]
        return summary

    @staticmethod
    def _to_json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): IterativeToolPlanner._to_json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [IterativeToolPlanner._to_json_safe(item) for item in value]
        if isinstance(value, set):
            return sorted(IterativeToolPlanner._to_json_safe(item) for item in value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
