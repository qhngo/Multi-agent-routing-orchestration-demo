from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from src.app.repositories.interfaces import ConversationRepository, ConversationStateRepository
from src.app.runtime.agent_registry import AgentRegistry
from src.app.runtime.agent_router import AgentRouter
from src.app.runtime.http_client import build_post_request
from src.app.runtime.interface import AgentRuntimeInterface
from src.app.runtime.response_parsing import find_first_string, parse_json_dict_from_text


class RoutedRuntime(AgentRuntimeInterface):
    def __init__(
        self,
        registry: AgentRegistry,
        router: AgentRouter,
        conversation_repo: ConversationRepository,
        conversation_state_repo: ConversationStateRepository,
        logger: logging.Logger,
        local_api_url: str,
        local_api_timeout_seconds: float,
        max_orchestration_steps: int = 3,
    ) -> None:
        self._registry = registry
        self._router = router
        self._conversation_repo = conversation_repo
        self._conversation_state_repo = conversation_state_repo
        self._logger = logger
        self._local_api_url = local_api_url
        self._local_api_timeout_seconds = local_api_timeout_seconds
        self._max_orchestration_steps = max(1, max_orchestration_steps)
        self.description = "High-level runtime that routes messages to the best available agent."

    def run(self, message: str, session_id: str) -> tuple[str, list[str]]:
        self._logger.info(
            "Routed runtime received request. session_id='%s' message_length=%s",
            session_id,
            len(message or ""),
        )
        history = self._conversation_repo.get_history(session_id)
        context_history = self._history_without_current_message(history=history, current_message=message)
        last_selected_agent = self._conversation_state_repo.get_last_selected_agent(session_id)
        selected_agent, route_trace = self._router.route_with_context(
            message=message,
            history=context_history,
            last_selected_agent=last_selected_agent,
        )
        trace = ["router_runtime:start", *route_trace]
        trace.append(f"router_runtime:history_messages:{len(context_history)}")
        trace.append(f"router_runtime:last_selected:{last_selected_agent or 'none'}")
        self._logger.info(
            "Routed runtime executing selected agent. session_id='%s' agent_id='%s'",
            session_id,
            selected_agent.agent_id,
        )
        answer, orchestration_trace = self._run_multi_step_plan(
            session_id=session_id,
            original_user_message=message,
            primary_agent_id=selected_agent.agent_id,
            history=context_history,
            last_selected_agent=last_selected_agent,
        )
        trace.extend(orchestration_trace)
        trace.append("router_runtime:end")
        return answer, trace

    def _run_multi_step_plan(
        self,
        session_id: str,
        original_user_message: str,
        primary_agent_id: str,
        history: list[dict[str, str]],
        last_selected_agent: str | None,
    ) -> tuple[str, list[str]]:
        trace: list[str] = []
        fallback_agent_id = self._registry.fallback_agent_id
        plan = self._build_execution_plan(
            primary_agent_id=primary_agent_id,
            fallback_agent_id=fallback_agent_id,
            original_user_message=original_user_message,
            history=history,
            last_selected_agent=last_selected_agent,
            trace=trace,
        )
        trace.append(self._encode_orchestration_event("plan", step=1, plan=plan))
        self._logger.info("Current execution plan list (initial): %s", plan)

        last_answer = ""
        last_agent_id = primary_agent_id
        executed_steps = 0

        while plan and executed_steps < self._max_orchestration_steps:
            step_spec = plan.pop(0)
            executed_steps += 1
            step_agent_id = str(step_spec.get("agent_id", fallback_agent_id))
            step_action = str(step_spec.get("action", "run_agent")).strip().lower()
            step_purpose = str(step_spec.get("purpose", "handle")).strip().lower()
            if step_action == "run_agent" and step_purpose == "synthesize":
                step_message = self._compose_synthesis_message(
                    original_user_message=original_user_message,
                    prior_answer=last_answer,
                    prior_agent_id=last_agent_id,
                )
            else:
                step_message = self._compose_agent_message(
                    message=original_user_message,
                    history=history,
                    last_selected_agent=last_selected_agent,
                    prior_step_output=last_answer,
                )

            trace.append(
                f"router_runtime:plan_step:{executed_steps}:agent:{step_agent_id}:action:{step_action}:purpose:{step_purpose}"
            )
            previous_answer = last_answer
            if step_action == "call_tool":
                tool_name = str(step_spec.get("tool_name", "")).strip()
                payload = step_spec.get("tool_payload", {})
                if not isinstance(payload, dict):
                    payload = {}
                answer, agent_trace, executed_agent_id, step_succeeded = self._execute_tool_step(
                    session_id=session_id,
                    agent_id=step_agent_id,
                    tool_name=tool_name,
                    payload=payload,
                    trace=trace,
                )
            else:
                answer, agent_trace, executed_agent_id, step_succeeded = self._execute_agent_with_fallback(
                    session_id=session_id,
                    preferred_agent_id=step_agent_id,
                    message=step_message,
                    trace=trace,
                )

            if step_purpose == "synthesize" and not step_succeeded and previous_answer:
                trace.append("router_runtime:synthesize_step_failed:using_previous_answer")
                answer = previous_answer
                step_succeeded = True

            last_answer = answer
            last_agent_id = executed_agent_id
            trace.extend(agent_trace)
            trace.append(
                self._encode_orchestration_event(
                    "step_output",
                    step=executed_steps,
                    plan=[],
                    metadata={
                        "agent_id": executed_agent_id,
                        "action": step_action,
                        "purpose": step_purpose,
                        "answer_preview": answer[:240],
                    },
                )
            )

            if not plan:
                break

        return last_answer, trace

    def _build_execution_plan(
        self,
        primary_agent_id: str,
        fallback_agent_id: str,
        original_user_message: str,
        history: list[dict[str, str]],
        last_selected_agent: str | None,
        trace: list[str],
    ) -> list[dict[str, Any]]:
        default_plan = self._default_plan(
            primary_agent_id=primary_agent_id,
            fallback_agent_id=fallback_agent_id,
        )
        prompt = self._build_execution_plan_prompt(
            original_user_message=original_user_message,
            history=history,
            last_selected_agent=last_selected_agent,
            default_plan=default_plan,
        )
        parsed = self._run_high_level_query(prompt=prompt, trace=trace, phase="plan")
        candidate_plan = self._extract_plan_from_response(parsed)
        self._logger.info(
            "LLM returned execution plan (raw extracted). plan=%s",
            json.dumps(candidate_plan, ensure_ascii=True),
        )
        normalized = self._normalize_plan(candidate_plan, fallback_agent_id=fallback_agent_id)
        self._logger.info(
            "Execution plan after normalization. plan=%s",
            json.dumps(normalized, ensure_ascii=True),
        )
        return normalized if normalized else default_plan

    def _default_plan(self, primary_agent_id: str, fallback_agent_id: str) -> list[dict[str, Any]]:
        if primary_agent_id == fallback_agent_id:
            return [
                {"agent_id": fallback_agent_id, "action": "run_agent", "purpose": "handle"},
            ]
        return [
            {"agent_id": primary_agent_id, "action": "run_agent", "purpose": "handle"},
            {"agent_id": fallback_agent_id, "action": "run_agent", "purpose": "synthesize"},
        ]

    @staticmethod
    def _compose_synthesis_message(
        original_user_message: str,
        prior_answer: str,
        prior_agent_id: str,
    ) -> str:
        return "\n".join(
            [
                "You are refining a prior agent output into a concise final response for the user.",
                "",
                f"Original user request:\n{original_user_message}",
                "",
                f"Prior agent ({prior_agent_id}) output:\n{prior_answer}",
                "",
                "Task: produce the final answer for the user. Keep useful details, remove noise.",
            ]
        )

    def _execute_agent_with_fallback(
        self,
        session_id: str,
        preferred_agent_id: str,
        message: str,
        trace: list[str],
    ) -> tuple[str, list[str], str, bool]:
        preferred_agent = self._registry.get_agent(preferred_agent_id)
        try:
            answer, agent_trace = preferred_agent.runtime.run(message=message, session_id=session_id)
            self._conversation_state_repo.set_last_selected_agent(session_id, preferred_agent.agent_id)
            if self._looks_like_error_answer(answer):
                return answer, agent_trace, preferred_agent.agent_id, False
            return answer, agent_trace, preferred_agent.agent_id, True
        except Exception as error:
            trace.append(f"router_runtime:error:{preferred_agent.agent_id}:{type(error).__name__}")
            self._logger.exception(
                "Selected agent failed. session_id='%s' agent_id='%s'",
                session_id,
                preferred_agent.agent_id,
            )

        fallback_agent = self._registry.get_agent(self._registry.fallback_agent_id)
        if fallback_agent.agent_id == preferred_agent.agent_id:
            trace.append("router_runtime:fallback_unavailable")
            return "The selected agent failed and no fallback agent is available.", [], preferred_agent.agent_id, False

        trace.append(f"router_runtime:fallback_execute:{fallback_agent.agent_id}")
        try:
            answer, fallback_trace = fallback_agent.runtime.run(message=message, session_id=session_id)
            self._conversation_state_repo.set_last_selected_agent(session_id, fallback_agent.agent_id)
            if self._looks_like_error_answer(answer):
                return answer, fallback_trace, fallback_agent.agent_id, False
            return answer, fallback_trace, fallback_agent.agent_id, True
        except Exception as error:
            self._logger.exception(
                "Fallback agent failed. session_id='%s' agent_id='%s'",
                session_id,
                fallback_agent.agent_id,
            )
            trace.append(f"router_runtime:fallback_error:{type(error).__name__}")
            return "Both primary and fallback agents failed to process the request.", [], fallback_agent.agent_id, False

    def _execute_tool_step(
        self,
        session_id: str,
        agent_id: str,
        tool_name: str,
        payload: dict[str, Any],
        trace: list[str],
    ) -> tuple[str, list[str], str, bool]:
        if not tool_name:
            trace.append("router_runtime:tool_step:missing_tool_name")
            return "Tool step is missing tool_name.", [], agent_id, False

        try:
            agent = self._registry.get_agent(agent_id)
        except KeyError:
            trace.append(f"router_runtime:tool_step:unknown_agent:{agent_id}")
            return f"Tool step references unknown agent '{agent_id}'.", [], agent_id, False

        execute_tool = getattr(agent.runtime, "execute_tool", None)
        if not callable(execute_tool):
            trace.append(f"router_runtime:tool_step:unsupported:{agent_id}:{tool_name}")
            return (
                f"Agent '{agent_id}' does not support direct tool execution for '{tool_name}'.",
                [],
                agent_id,
                False,
            )

        trace.append(f"router_runtime:tool_step:execute:{agent_id}:{tool_name}")
        try:
            raw_result = execute_tool(tool_name=tool_name, payload=payload, session_id=session_id)
        except Exception as error:  # pragma: no cover
            self._logger.exception(
                "Tool step execution failed. session_id='%s' agent_id='%s' tool='%s'",
                session_id,
                agent_id,
                tool_name,
            )
            trace.append(f"router_runtime:tool_step:error:{agent_id}:{tool_name}:{type(error).__name__}")
            return f"Tool '{tool_name}' on agent '{agent_id}' failed.", [], agent_id, False

        if not isinstance(raw_result, dict):
            trace.append(f"router_runtime:tool_step:invalid_result:{agent_id}:{tool_name}")
            return (
                f"Tool '{tool_name}' on agent '{agent_id}' returned invalid result.",
                [],
                agent_id,
                False,
            )

        ok = bool(raw_result.get("ok", True))
        if ok:
            self._conversation_state_repo.set_last_selected_agent(session_id, agent_id)

        formatted = self._format_tool_result_for_context(
            agent_id=agent_id,
            tool_name=tool_name,
            payload=payload,
            result=raw_result,
        )
        return formatted, [f"router_runtime:tool_step:ok:{agent_id}:{tool_name}:{str(ok).lower()}"], agent_id, ok

    @staticmethod
    def _looks_like_error_answer(answer: str) -> bool:
        text = (answer or "").lower()
        error_signals = (
            "could not complete the request with the configured answer service",
            "could not reach the configured answer service",
            "timed out",
            "failed to process the request",
        )
        return any(signal in text for signal in error_signals)

    def _run_high_level_query(self, prompt: str, trace: list[str], phase: str) -> dict[str, Any] | None:
        payload = {"question_json": {"received": prompt}}
        request = build_post_request(self._local_api_url, payload)
        trace.append(f"router_runtime:high_level_query:{phase}:request")
        try:
            with urlopen(request, timeout=self._local_api_timeout_seconds) as response:
                response_text = response.read().decode("utf-8")
        except URLError as error:
            trace.append(f"router_runtime:high_level_query:{phase}:error:URLError:{error.reason}")
            return None
        except Exception as error:  # pragma: no cover
            trace.append(f"router_runtime:high_level_query:{phase}:error:{type(error).__name__}")
            return None

        trace.append(f"router_runtime:high_level_query:{phase}:response")
        parsed = parse_json_dict_from_text(response_text)
        if parsed is not None:
            return parsed
        wrapped = find_first_string(response_text)
        if wrapped:
            return parse_json_dict_from_text(wrapped)
        return None

    def _build_execution_plan_prompt(
        self,
        original_user_message: str,
        history: list[dict[str, str]],
        last_selected_agent: str | None,
        default_plan: list[dict[str, Any]],
    ) -> str:
        agent_lines = self._build_agent_catalog_lines()
        history_lines = [f"- {entry.get('creator', 'unknown')}: {entry.get('message', '')}" for entry in history]
        return "\n".join(
            [
                "Generate a response plan for a multi-agent orchestrator.",
                "Return STRICT JSON only, with this shape:",
                '{"plan":[{"agent_id":"...","action":"run_agent|call_tool","purpose":"handle|synthesize","tool_name":"","tool_payload":{}}]}',
                f"Max steps: {self._max_orchestration_steps}",
                f"Last selected agent: {last_selected_agent or 'N/A'}",
                "Available agents and tools (with descriptions):",
                *agent_lines,
                "Rules:",
                "- Use action='run_agent' for normal agent execution steps.",
                "- Use action='call_tool' only when a specific tool should be invoked directly.",
                "- For action='call_tool', provide valid tool_name and optional tool_payload.",
                "- Return only agents and tools from the catalog above.",
                "Conversation history:",
                *(history_lines or ["- N/A"]),
                f"User request: {original_user_message}",
                f"Default plan if unsure: {json.dumps(default_plan)}",
            ]
        )

    @staticmethod
    def _extract_plan_from_response(parsed: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(parsed, dict):
            return []
        for key in ("plan", "steps", "execution_plan"):
            value = parsed.get(key)
            if isinstance(value, list):
                extracted: list[dict[str, Any]] = []
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    agent_id = str(item.get("agent_id") or item.get("agent") or "").strip()
                    action = str(item.get("action") or item.get("type") or "run_agent").strip().lower()
                    purpose = str(item.get("purpose") or "handle").strip().lower()
                    tool_name = str(item.get("tool_name") or item.get("tool") or "").strip()
                    tool_payload = item.get("tool_payload") or item.get("payload") or {}
                    if not isinstance(tool_payload, dict):
                        tool_payload = {}
                    if not agent_id:
                        continue
                    extracted.append(
                        {
                            "agent_id": agent_id,
                            "action": action,
                            "purpose": purpose,
                            "tool_name": tool_name,
                            "tool_payload": tool_payload,
                        }
                    )
                return extracted
        return []

    def _normalize_plan(self, plan: list[dict[str, Any]], fallback_agent_id: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen = 0
        for step in plan:
            if seen >= self._max_orchestration_steps:
                break
            agent_id = str(step.get("agent_id", "")).strip()
            action = str(step.get("action", "run_agent")).strip().lower()
            purpose = str(step.get("purpose", "handle")).strip().lower()
            tool_name = str(step.get("tool_name", "")).strip()
            tool_payload = step.get("tool_payload", {})
            if not isinstance(tool_payload, dict):
                tool_payload = {}

            if not agent_id or not self._registry.has_agent(agent_id):
                continue
            if action not in {"run_agent", "call_tool"}:
                action = "run_agent"
            if purpose not in {"handle", "synthesize"}:
                purpose = "handle"

            if action == "call_tool":
                if not tool_name:
                    continue
                if not self._tool_exists_on_agent(agent_id=agent_id, tool_name=tool_name):
                    continue

            normalized.append(
                {
                    "agent_id": agent_id,
                    "action": action,
                    "purpose": purpose,
                    "tool_name": tool_name,
                    "tool_payload": tool_payload,
                }
            )
            seen += 1

        if not normalized:
            return []
        if normalized[-1].get("purpose") != "synthesize":
            if len(normalized) < self._max_orchestration_steps:
                normalized.append(
                    {
                        "agent_id": fallback_agent_id,
                        "action": "run_agent",
                        "purpose": "synthesize",
                        "tool_name": "",
                        "tool_payload": {},
                    }
                )
            else:
                normalized[-1] = {
                    "agent_id": fallback_agent_id,
                    "action": "run_agent",
                    "purpose": "synthesize",
                    "tool_name": "",
                    "tool_payload": {},
                }
        return normalized[: self._max_orchestration_steps]

    @staticmethod
    def _encode_orchestration_event(
        event_name: str,
        step: int,
        plan: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "event": event_name,
            "step": step,
            "plan": plan,
        }
        if metadata:
            payload["metadata"] = metadata
        return "router_planner:event:" + json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _history_without_current_message(
        history: list[dict[str, str]],
        current_message: str,
    ) -> list[dict[str, str]]:
        # ChatService stores the current user message before routing/runtime execution.
        # Drop that single trailing message so "Conversation history" stays prior-only.
        if not history:
            return history
        last = history[-1]
        last_message = str(last.get("message", ""))
        last_creator = str(last.get("creator", ""))
        if last_creator.lower() != "agent" and last_message == (current_message or ""):
            return history[:-1]
        return history

    @staticmethod
    def _compose_agent_message(
        message: str,
        history: list[dict[str, str]],
        last_selected_agent: str | None,
        prior_step_output: str = "",
    ) -> str:
        history_lines = [
            f"- {entry.get('creator', 'unknown')}: {entry.get('message', '')}"
            for entry in history
        ]
        return "\n".join(
            [
                f"Current user message:\n{message}",
                "",
                f"Last selected agent (if available): {last_selected_agent or 'N/A'}",
                "",
                f"Prior step output (if any):\n{prior_step_output or 'N/A'}",
                "",
                "Conversation history (chronological):",
                *(history_lines or ["- N/A"]),
            ]
        )

    def _build_agent_catalog_lines(self) -> list[str]:
        lines: list[str] = []
        for agent in self._registry.list_agents():
            lines.append(f"- agent_id: {agent.agent_id} | description: {agent.description}")
            tools = self._collect_available_tools(agent.agent_id)
            if not tools:
                lines.append("  - tools: none")
                continue
            lines.append("  - tools:")
            for name, description in tools:
                if description:
                    lines.append(f"    - name: {name} | description: {description}")
                else:
                    lines.append(f"    - name: {name}")
        return lines

    def _collect_available_tools(self, agent_id: str) -> list[tuple[str, str]]:
        try:
            agent = self._registry.get_agent(agent_id)
        except KeyError:
            return []

        runtime = agent.runtime
        collected: list[tuple[str, str]] = []

        list_available_tools = getattr(runtime, "list_available_tools", None)
        if callable(list_available_tools):
            try:
                raw_tools = list_available_tools()
                for item in raw_tools:
                    if not isinstance(item, tuple) or len(item) != 2:
                        continue
                    name = str(item[0]).strip()
                    description = str(item[1]).strip()
                    if name:
                        collected.append((name, description))
            except Exception:
                collected = []

        if not collected:
            orchestrator = getattr(runtime, "_orchestrator", None)
            list_tools = getattr(orchestrator, "list_tools", None)
            if callable(list_tools):
                try:
                    for spec in list_tools():
                        name = str(getattr(spec, "name", "")).strip()
                        description = str(getattr(spec, "description", "")).strip()
                        if name:
                            collected.append((name, description))
                except Exception:
                    collected = []

        unique: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in collected:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique

    def _tool_exists_on_agent(self, agent_id: str, tool_name: str) -> bool:
        normalized = (tool_name or "").strip().lower()
        if not normalized:
            return False
        for name, _description in self._collect_available_tools(agent_id):
            if name.lower() == normalized:
                return True
        return False

    def _format_tool_result_for_context(
        self,
        agent_id: str,
        tool_name: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        return "\n".join(
            [
                f"Tool execution result:",
                f"- agent_id: {agent_id}",
                f"- tool_name: {tool_name}",
                f"- payload: {json.dumps(self._to_json_safe(payload), ensure_ascii=True)}",
                f"- result: {json.dumps(self._to_json_safe(result), ensure_ascii=True)}",
            ]
        )

    @staticmethod
    def _to_json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): RoutedRuntime._to_json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [RoutedRuntime._to_json_safe(item) for item in value]
        if isinstance(value, set):
            return sorted(RoutedRuntime._to_json_safe(item) for item in value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
