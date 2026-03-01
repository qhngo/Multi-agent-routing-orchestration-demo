from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

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
from src.app.runtime.http_client import build_post_request
from src.app.runtime.interface import AgentRuntimeInterface
from src.app.runtime.interface import RuntimeAgentSpec
from src.app.runtime.response_parsing import (
    extract_processing_time_s,
    extract_total_tokens,
    find_first_string,
)


class GenericAnswerAgent(AgentRuntimeInterface):
    def __init__(self, local_api_url: str, timeout_seconds: float = 20.0) -> None:
        self._local_api_url = local_api_url
        self._timeout_seconds = timeout_seconds
        self._orchestrator = InternalToolOrchestrator(self)
        self._planner = IterativeToolPlanner(self._orchestrator, max_steps=3)
        self.description = (
            "General-purpose local agent for queries that cannot be handled by specialized agents, via Local API "
            f"(URL={self._local_api_url})."
        )

    def run(self, message: str, session_id: str) -> tuple[str, list[str]]:
        query_text = self._extract_current_user_message(message)
        trace = [
            "generic_agent:start",
            f"generic_agent:session:{session_id}",
        ]
        state, planner_trace = self._planner.run(
            initial_state={
                "query": query_text,
                "done": False,
                "selected_tool": None,
                "tool_result": {},
            },
            build_plan=self._build_plan,
            apply_result=self._apply_result,
            should_stop=self._should_stop,
        )
        trace.extend(planner_trace)
        selected_tool = state.get("selected_tool")
        if not isinstance(selected_tool, str):
            selected_tool = ""
        tool_result = state.get("tool_result", {})
        if not isinstance(tool_result, dict):
            tool_result = {}
        answer = self._format_tool_result(tool_name=selected_tool, result=tool_result)
        trace.append("generic_agent:end")
        return answer, trace

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
        query = str(state.get("query", "")).strip()
        if "list_tools" in available and self._is_explicit_tool_request(query):
            return [ToolDecision(tool_name="list_tools", payload={}, note="user_requested_tool_catalog")]
        if "answer_api" in available:
            return [ToolDecision(tool_name="answer_api", payload={"prompt": query}, note="default_answer_path")]
        return []

    @staticmethod
    def _is_explicit_tool_request(query: str) -> bool:
        text = (query or "").strip().lower()
        if not text:
            return False

        patterns = (
            r"\b(list|show|display)\s+(all\s+)?tools?\b",
            r"\bavailable\s+tools?\b",
            r"\bwhat\s+tools?\s+(do\s+you\s+have|are\s+available)\b",
            r"^\s*help\s*$",
            r"\bwhat\s+can\s+you\s+do\b",
            r"\bcapabilities\b",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _should_stop(state: dict[str, Any]) -> bool:
        return bool(state.get("done", False))

    @staticmethod
    def _apply_result(state: dict[str, Any], decision: ToolDecision, result: dict[str, Any]) -> None:
        state["selected_tool"] = decision.tool_name
        state["tool_result"] = result
        state["done"] = True

    @tool(name="answer_api", description="Send prompt to configured local answer API.")
    def _tool_answer_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt", "")).strip()
        payload = {"question_json": {"received": prompt}}
        request = build_post_request(self._local_api_url, payload)

        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                response_text = response.read().decode("utf-8")
        except URLError as error:
            return {
                "ok": False,
                "error": (
                    "I could not reach the configured answer service. "
                    f"URL: {self._local_api_url}. Error: {error.reason}"
                ),
            }
        except Exception as error:  # pragma: no cover
            return {
                "ok": False,
                "error": (
                    "I could not complete the request with the configured answer service. "
                    f"URL: {self._local_api_url}. Error: {error}"
                ),
            }

        answer, processing_time_s, total_tokens = self._extract_response(response_text)
        if answer:
            return {
                "ok": True,
                "answer": answer,
                "processing_time_s": processing_time_s,
                "total_tokens": total_tokens,
            }

        return {
            "ok": False,
            "error": (
                "The answer service returned an unexpected response format. "
                f"Raw response: {response_text}"
            ),
        }

    @tool(name="list_tools", description="List tools available in this agent.")
    def _tool_list_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        _ = payload
        return standard_list_tools_payload(self._orchestrator)

    def _format_tool_result(self, tool_name: str, result: dict[str, Any]) -> str:
        if tool_name == "list_tools":
            tools = result.get("tools")
            if isinstance(tools, list) and tools:
                lines = ["Available tools:"]
                for tool_item in tools:
                    if not isinstance(tool_item, dict):
                        continue
                    lines.append(f"- {tool_item.get('name', '')}: {tool_item.get('description', '')}")
                return "\n".join(lines)
            return "No tools are registered."

        if tool_name == "answer_api":
            if not result.get("ok", False):
                return str(result.get("error", "Answer API call failed."))
            answer = str(result.get("answer", "")).strip()
            processing_time_s = result.get("processing_time_s")
            total_tokens = result.get("total_tokens")
            return self._format_answer_with_metrics(
                answer=answer,
                processing_time_s=processing_time_s if isinstance(processing_time_s, (int, float)) else None,
                total_tokens=total_tokens if isinstance(total_tokens, int) else None,
            )

        return "Tool executed but no formatter is defined."

    def _extract_response(self, response_text: str) -> tuple[str | None, float | None, int | None]:
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            raw = response_text.strip() or None
            return raw, None, None

        candidate = find_first_string(parsed)
        processing_time_s = extract_processing_time_s(parsed)
        total_tokens = extract_total_tokens(parsed)
        return (candidate.strip() if candidate else None), processing_time_s, total_tokens

    @staticmethod
    def _format_answer_with_metrics(
        answer: str,
        processing_time_s: float | None,
        total_tokens: int | None,
    ) -> str:
        metrics: list[str] = []
        if processing_time_s is not None:
            metrics.append(f"processing_time_s={processing_time_s:.3f}")
        if total_tokens is not None:
            metrics.append(f"total_tokens={total_tokens}")
        if not metrics:
            return answer
        return f"{answer}\n\n[{', '.join(metrics)}]"


def build_agent_specs(settings: AppSettings) -> list[RuntimeAgentSpec]:
    runtime = GenericAnswerAgent(
        local_api_url=settings.local_api_url,
        timeout_seconds=settings.local_api_timeout_seconds,
    )
    return [
        RuntimeAgentSpec(
            agent_id="generic",
            runtime=runtime,
            runtime_type="direct",
            keywords=(),
            is_fallback=True,
        )
    ]
