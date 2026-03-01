from __future__ import annotations

import logging
import re
from urllib.error import URLError
from urllib.request import urlopen

from src.app.runtime.agent_registry import AgentRegistry, RegisteredAgent
from src.app.runtime.http_client import build_post_request
from src.app.runtime.response_parsing import parse_json_dict_from_text


class AgentRouter:
    def __init__(
        self,
        registry: AgentRegistry,
        local_api_url: str,
        local_api_timeout_seconds: float,
        logger: logging.Logger | None = None,
        minimum_score: int = 1,
    ) -> None:
        self._registry = registry
        self._local_api_url = local_api_url
        self._local_api_timeout_seconds = local_api_timeout_seconds
        self._logger = logger or logging.getLogger("src.app.core")
        self._minimum_score = minimum_score
        self._logger.info(
            "Agent router initialized. selector_url='%s' timeout_seconds=%s minimum_score=%s",
            self._local_api_url,
            self._local_api_timeout_seconds,
            self._minimum_score,
        )

    def route_with_context(
        self,
        message: str,
        history: list[dict[str, str]],
        last_selected_agent: str | None,
    ) -> tuple[RegisteredAgent, list[str]]:
        trace = ["router:route:start"]
        self._logger.info(
            "Agent routing started. message_length=%s candidate_agents=%s history_messages=%s last_selected_agent='%s'",
            len(message or ""),
            len(self._registry.list_non_fallback_agents()),
            len(history),
            last_selected_agent or "",
        )

        planner_agent = self._generate_execution_plan(
            message=message,
            trace=trace,
            history=history,
            last_selected_agent=last_selected_agent,
        )
        if planner_agent:
            trace.append(f"router:selected:{planner_agent.agent_id}")
            trace.append("router:route:end")
            self._logger.info("Agent selected by LLM planner. agent_id='%s'", planner_agent.agent_id)
            return planner_agent, trace

        trace.append("router:planner:miss")

        if (
            last_selected_agent
            and last_selected_agent != self._registry.fallback_agent_id
            and self._registry.has_agent(last_selected_agent)
        ):
            trace.append(f"router:last_selected_skip:{last_selected_agent}:reason:requires_selector_confirmation")
            self._logger.info(
                "Skipping implicit last_selected_agent reuse. agent_id='%s' reason='requires_selector_confirmation'",
                last_selected_agent,
            )

        normalized_message = (message or "").lower()
        tokens = set(re.findall(r"[a-z0-9\-]+", normalized_message))

        best_agent: RegisteredAgent | None = None
        best_score = -1

        for agent in self._registry.list_non_fallback_agents():
            score = self._score(agent, normalized_message, tokens)
            trace.append(f"router:candidate:{agent.agent_id}:score:{score}")
            if score > best_score:
                best_score = score
                best_agent = agent

        if best_agent and best_score >= self._minimum_score:
            trace.append(f"router:selected:{best_agent.agent_id}")
            trace.append("router:route:end")
            self._logger.info(
                "Agent selected by heuristic router. agent_id='%s' score=%s",
                best_agent.agent_id,
                best_score,
            )
            return best_agent, trace

        fallback_agent = self._registry.get_agent(self._registry.fallback_agent_id)
        trace.append(f"router:fallback:{fallback_agent.agent_id}")
        trace.append("router:route:end")
        self._logger.info("Agent routing fell back to fallback agent. agent_id='%s'", fallback_agent.agent_id)
        return fallback_agent, trace

    def _generate_execution_plan(
        self,
        message: str,
        trace: list[str],
        history: list[dict[str, str]],
        last_selected_agent: str | None,
    ) -> RegisteredAgent | None:
        selectable_agents = self._registry.list_non_fallback_agents()
        if not selectable_agents:
            trace.append("router:planner:skip:no_candidates")
            self._logger.info("LLM planner skipped. reason='no_candidates'")
            return None

        prompt = self._build_execution_plan_prompt(
            message=message,
            agents=selectable_agents,
            history=history,
            last_selected_agent=last_selected_agent,
        )
        payload = {"question_json": {"received": prompt}}
        request = build_post_request(self._local_api_url, payload)
        trace.append("router:planner:request")
        self._logger.info("LLM planner prompt:\n%s", prompt)
        self._logger.debug(
            "LLM planner request dispatched. planner_url='%s' candidates=%s",
            self._local_api_url,
            [agent.agent_id for agent in selectable_agents],
        )

        try:
            with urlopen(request, timeout=self._local_api_timeout_seconds) as response:
                response_text = response.read().decode("utf-8")
        except URLError as error:
            trace.append(f"router:planner:error:URLError:{error.reason}")
            self._logger.warning("LLM planner request failed. error='%s'", error.reason)
            return None
        except Exception as error:  # pragma: no cover
            trace.append(f"router:planner:error:{type(error).__name__}")
            self._logger.warning(
                "LLM planner request failed. error_type='%s'",
                type(error).__name__,
            )
            return None

        trace.append("router:planner:response")
        selected_agent_id = self._extract_first_agent_id_from_plan(response_text, selectable_agents)
        self._logger.debug(
            "LLM planner response parsed. selected_agent_id='%s'",
            selected_agent_id,
        )
        if not selected_agent_id:
            return None

        if selected_agent_id == self._registry.fallback_agent_id:
            trace.append(f"router:planner:fallback:{selected_agent_id}")
            self._logger.info("LLM planner chose fallback agent.")
            return None

        try:
            return self._registry.get_agent(selected_agent_id)
        except KeyError:
            trace.append(f"router:planner:unknown:{selected_agent_id}")
            self._logger.warning(
                "LLM planner returned unknown agent id. selected_agent_id='%s'",
                selected_agent_id,
            )
            return None

    def _build_execution_plan_prompt(
        self,
        message: str,
        agents: list[RegisteredAgent],
        history: list[dict[str, str]],
        last_selected_agent: str | None,
    ) -> str:
        fallback_agent = self._registry.get_agent(self._registry.fallback_agent_id)
        all_agents = [*agents]
        if all(agent.agent_id != fallback_agent.agent_id for agent in all_agents):
            all_agents.append(fallback_agent)
        agent_lines: list[str] = []
        for agent in all_agents:
            agent_lines.append(f"- agent_id: {agent.agent_id} | description: {agent.description}")
            tool_lines = self._build_tool_lines(agent)
            if tool_lines:
                agent_lines.extend(tool_lines)
            else:
                agent_lines.append("  - available_tools: none")
        history_lines = [
            f"- {entry.get('creator', 'unknown')}: {entry.get('message', '')}"
            for entry in history
        ]
        return "\n".join(
            [
                "You are generating an execution plan for a multi-step orchestration pipeline.",
                "Return STRICT JSON only using this shape:",
                '{"plan":[{"agent_id":"...","action":"run_agent|call_tool","purpose":"handle|synthesize","tool_name":"","tool_payload":{}}]}',
                f"If no specialized agent is clearly appropriate, use `{self._registry.fallback_agent_id}` as the first step agent_id.",
                "Do not output explanations, markdown, or extra text outside JSON.",
                "Use last selected agent and conversation history for continuity when relevant.",
                "Prefer specialized agents/tools when the query strongly matches their scope.",
                "",
                f"- last selected agent (if available): {last_selected_agent or 'N/A'}",
                f"- here is the user query/message: {message}",
                "- here is all past conversation history (chronological):",
                *(history_lines or ["- N/A"]),
                "- here is a list of agents and their available tools:",
                *agent_lines,
            ]
        )

    @staticmethod
    def _collect_available_tools(agent: RegisteredAgent) -> list[tuple[str, str]]:
        runtime = agent.runtime
        tool_pairs: list[tuple[str, str]] = []

        list_available_tools = getattr(runtime, "list_available_tools", None)
        if callable(list_available_tools):
            try:
                for item in list_available_tools():
                    if not isinstance(item, tuple) or len(item) != 2:
                        continue
                    name = str(item[0]).strip()
                    description = str(item[1]).strip()
                    if name:
                        tool_pairs.append((name, description))
            except Exception:
                # Tool metadata is optional for routing prompts.
                pass

        orchestrator = getattr(runtime, "_orchestrator", None)
        list_tools = getattr(orchestrator, "list_tools", None)
        if callable(list_tools) and not tool_pairs:
            try:
                for spec in list_tools():
                    name = str(getattr(spec, "name", "")).strip()
                    description = str(getattr(spec, "description", "")).strip()
                    if name:
                        tool_pairs.append((name, description))
            except Exception:
                # Tool metadata is optional for routing prompts.
                pass

        if not tool_pairs:
            for attribute_name in dir(runtime):
                try:
                    attribute = getattr(runtime, attribute_name)
                except Exception:
                    continue
                spec = getattr(attribute, "__agent_tool_spec__", None)
                if spec is None:
                    continue
                name = str(getattr(spec, "name", "")).strip()
                description = str(getattr(spec, "description", "")).strip()
                if name:
                    tool_pairs.append((name, description))

        unique_pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for pair in tool_pairs:
            if pair in seen:
                continue
            seen.add(pair)
            unique_pairs.append(pair)

        return unique_pairs

    @staticmethod
    def _build_tool_lines(agent: RegisteredAgent) -> list[str]:
        tools = AgentRouter._collect_available_tools(agent)
        if not tools:
            return []
        lines = ["  - available_tools:"]
        for name, description in tools:
            if description:
                lines.append(f"    - name: {name} | description: {description}")
            else:
                lines.append(f"    - name: {name}")
        return lines

    def _extract_first_agent_id_from_plan(self, response_text: str, agents: list[RegisteredAgent]) -> str | None:
        candidate_ids = [agent.agent_id for agent in agents] + [self._registry.fallback_agent_id]
        raw_response = (response_text or "").strip()
        lowered_response = raw_response.lower()
        if lowered_response in {candidate.lower() for candidate in candidate_ids}:
            for candidate in candidate_ids:
                if lowered_response == candidate.lower():
                    return candidate

        parsed = parse_json_dict_from_text(response_text)

        if isinstance(parsed, dict):
            for key in ("plan", "steps", "execution_plan"):
                plan = parsed.get(key)
                if not isinstance(plan, list):
                    continue
                for item in plan:
                    if not isinstance(item, dict):
                        continue
                    value = item.get("agent_id") or item.get("agent")
                    if isinstance(value, str):
                        matched = self._match_agent_id(value.strip(), candidate_ids)
                        if matched:
                            return matched
            for key in ("agent_id", "selected_agent_id", "agent", "selection"):
                value = parsed.get(key)
                if isinstance(value, str):
                    matched = self._match_agent_id(value.strip(), candidate_ids)
                    if matched:
                        return matched

        # Fallback text extraction: accept a single, unambiguous agent id mention only.
        matches = [
            candidate
            for candidate in candidate_ids
            if re.search(rf"(^|[^a-z0-9:_-]){re.escape(candidate.lower())}($|[^a-z0-9:_-])", lowered_response)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _match_agent_id(value: str, candidates: list[str]) -> str | None:
        lowered = (value or "").strip().lower()
        for candidate in candidates:
            if lowered == candidate.lower():
                return candidate
        return None

    @staticmethod
    def _score(agent: RegisteredAgent, normalized_message: str, tokens: set[str]) -> int:
        score = 0
        for keyword in agent.keywords:
            lowered = keyword.lower()
            if lowered in tokens:
                score += 2
                continue
            if lowered in normalized_message:
                score += 1
        return score
