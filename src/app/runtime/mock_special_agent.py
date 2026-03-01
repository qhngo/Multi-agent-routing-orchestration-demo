from __future__ import annotations

import re
from typing import Any

from src.app.config.settings import AppSettings
from src.app.runtime.agent_tools import (
    InternalToolOrchestrator,
    IterativeToolPlanner,
    ToolDecision,
    standard_execute_tool,
    standard_list_available_tools,
    standard_list_tools_payload,
    tool,
)
from src.app.runtime.interface import AgentRuntimeInterface, RuntimeAgentSpec
from src.app.tools.bizx_lookup import BizXLookupTool


class MockSpecialAgent(AgentRuntimeInterface):
    def __init__(self) -> None:
        self._bizx_lookup = BizXLookupTool()
        self._orchestrator = InternalToolOrchestrator(self)
        self._planner = IterativeToolPlanner(self._orchestrator, max_steps=4)
        self.description = (
            "Mock special agent with internal tool orchestration for structured BizX lookup tasks "
            "(e.g., CUST-100)."
        )

    def run(self, message: str, session_id: str) -> tuple[str, list[str]]:
        trace = ["mock_agent:start", f"mock_agent:session:{session_id}"]
        query = self._extract_current_user_message(message)
        state, planner_trace = self._planner.run(
            initial_state={"query": query, "done": False, "tool_name": None, "tool_payload": {}, "tool_result": {}},
            build_plan=self._build_plan,
            apply_result=self._apply_result,
            should_stop=self._should_stop,
        )
        trace.extend(planner_trace)
        tool_name = state.get("tool_name")
        if not isinstance(tool_name, str):
            tool_name = ""
        payload = state.get("tool_payload", {})
        if not isinstance(payload, dict):
            payload = {}
        result = state.get("tool_result", {})
        if not isinstance(result, dict):
            result = {}
        if not tool_name:
            trace.append("mock_agent:plan:none")
            trace.append("mock_agent:end")
            return self._usage_text(), trace
        trace.append("mock_agent:end")
        return self._format_result(tool_name=tool_name, payload=payload, result=result), trace

    def list_available_tools(self) -> list[tuple[str, str]]:
        return standard_list_available_tools(self._orchestrator)

    def execute_tool(self, tool_name: str, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        _ = session_id
        return standard_execute_tool(self._orchestrator, tool_name=tool_name, payload=payload)

    @staticmethod
    def _extract_current_user_message(message: str) -> str:
        marker = "Current user message:\n"
        if marker not in message:
            return (message or "").strip()
        after_marker = message.split(marker, 1)[1]
        if "\n\nLast selected agent" in after_marker:
            return after_marker.split("\n\nLast selected agent", 1)[0].strip()
        return after_marker.strip()

    def _build_plan(self, state: dict[str, Any], tools: list[Any]) -> list[ToolDecision]:
        available = {getattr(tool, "name", "") for tool in tools}
        query = str(state.get("query", ""))
        lowered = (query or "").lower()
        if "list_tools" in available and any(token in lowered for token in ("help", "capabilities", "what can you do", "tools")):
            return [ToolDecision(tool_name="list_tools", payload={}, note="user_requested_tool_catalog")]

        customer_id = self._extract_customer_id(query)
        if customer_id and "lookup_customer" in available:
            return [ToolDecision(
                tool_name="lookup_customer",
                payload={"customer_id": customer_id},
                note="customer_id_detected",
            )]

        if "lookup_customer" in available and any(token in lowered for token in ("customer", "lookup", "bizx", "cust-")):
            return [ToolDecision(
                tool_name="lookup_customer",
                payload={"customer_id": ""},
                note="lookup_requested_without_customer_id",
            )]

        return []

    @staticmethod
    def _apply_result(state: dict[str, Any], decision: ToolDecision, result: dict[str, Any]) -> None:
        state["tool_name"] = decision.tool_name
        state["tool_payload"] = decision.payload
        state["tool_result"] = result
        state["done"] = True

    @staticmethod
    def _should_stop(state: dict[str, Any]) -> bool:
        return bool(state.get("done", False))

    @staticmethod
    def _extract_customer_id(text: str) -> str | None:
        match = re.search(r"\bCUST-\d+\b", text or "", flags=re.IGNORECASE)
        return match.group(0).upper() if match else None

    @tool(name="lookup_customer", description="Lookup BizX customer by customer_id.")
    def _tool_lookup_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        customer_id = str(payload.get("customer_id", "")).strip().upper()
        if not customer_id:
            return {
                "ok": False,
                "error": "Missing customer_id. Provide a value like CUST-100.",
            }
        tool_result = self._bizx_lookup.run({"customer_id": customer_id})
        return {"ok": True, "tool_result": tool_result}

    @tool(name="list_tools", description="List tools available in this agent.")
    def _tool_list_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        _ = payload
        return standard_list_tools_payload(self._orchestrator)

    def _format_result(self, tool_name: str, payload: dict[str, Any], result: dict[str, Any]) -> str:
        if tool_name == "list_tools":
            tools = result.get("tools", [])
            if isinstance(tools, list) and tools:
                lines = ["Available tools:"]
                for item in tools:
                    name = str(item.get("name", "")).strip()
                    description = str(item.get("description", "")).strip()
                    lines.append(f"- {name}: {description}")
                return "\n".join(lines)
            return self._usage_text()

        if tool_name == "lookup_customer":
            if not result.get("ok", False):
                return str(result.get("error", "Lookup failed."))

            tool_result = result.get("tool_result")
            if not isinstance(tool_result, dict):
                return "Lookup returned an unexpected response."

            if not tool_result.get("found", False):
                customer_id = str(payload.get("customer_id", "")).strip()
                return f"No customer record found for '{customer_id}'."

            record = tool_result.get("record")
            if not isinstance(record, dict):
                return "Customer lookup returned malformed record data."

            return (
                "Customer found:\n"
                f"- customer_id: {record.get('customer_id', '')}\n"
                f"- name: {record.get('name', '')}\n"
                f"- tier: {record.get('tier', '')}"
            )

        return "Tool executed, but no formatter is defined for the result."

    @staticmethod
    def _usage_text() -> str:
        return (
            "I can run structured BizX lookups.\n"
            "Try: 'Find customer CUST-100' or 'List your tools'."
        )


def build_agent_specs(settings: AppSettings) -> list[RuntimeAgentSpec]:
    _ = settings
    runtime = MockSpecialAgent()
    return [
        RuntimeAgentSpec(
            agent_id="special:mock",
            runtime=runtime,
            runtime_type="special",
            keywords=("customer", "cust-", "bizx", "lookup", "tool", "tools"),
            is_fallback=False,
        )
    ]
