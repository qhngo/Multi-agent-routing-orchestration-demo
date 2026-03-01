from __future__ import annotations

import logging
import re

from src.app.repositories.interfaces import ConversationRepository, UserSessionRepository
from src.app.runtime.interface import AgentRuntimeInterface


class ChatService:
    def __init__(
        self,
        logger: logging.Logger,
        user_session_repo: UserSessionRepository,
        conversation_repo: ConversationRepository,
        runtime: AgentRuntimeInterface,
        last_interaction_threshold_days: int,
    ) -> None:
        self._logger = logger
        self._user_session_repo = user_session_repo
        self._conversation_repo = conversation_repo
        self._runtime = runtime
        self._last_interaction_threshold_days = last_interaction_threshold_days

    def resolve_user_session_and_history(
        self, username: str
    ) -> tuple[str, list[dict[str, object]]]:
        session_id, is_new = self._user_session_repo.get_or_create_active_session(
            username=username,
            threshold_days=self._last_interaction_threshold_days,
        )
        if is_new:
            self._logger.info(
                "Conversation session assigned. username='%s' session_id='%s' status='new'",
                username,
                session_id,
            )
            return session_id, []
        self._logger.info(
            "Conversation session assigned. username='%s' session_id='%s' status='existing'",
            username,
            session_id,
        )
        history = self._conversation_repo.get_history(session_id)
        return session_id, history

    def create_new_conversation(self, username: str) -> str:
        session_id = self._user_session_repo.create_new_session(username)
        self._logger.info(
            "Conversation session created. username='%s' session_id='%s' action='create_new'",
            username,
            session_id,
        )
        return session_id

    def clear_conversation(self, username: str) -> str:
        session_id, is_new = self._user_session_repo.get_or_create_active_session(
            username=username,
            threshold_days=self._last_interaction_threshold_days,
        )
        deleted_count = self._conversation_repo.clear_history(session_id=session_id)
        self._user_session_repo.touch_session(session_id)
        self._logger.info(
            (
                "Conversation cleared. username='%s' session_id='%s' "
                "status='%s' deleted_messages=%s action='clear_history'"
            ),
            username,
            session_id,
            "new" if is_new else "existing",
            deleted_count,
        )
        return session_id

    def process_for_user(
        self, username: str, message: str
    ) -> tuple[str, str, list[str]]:
        session_id, is_new = self._user_session_repo.get_or_create_active_session(
            username=username,
            threshold_days=self._last_interaction_threshold_days,
        )
        self._logger.info(
            "Conversation session used. username='%s' session_id='%s' status='%s'",
            username,
            session_id,
            "new" if is_new else "existing",
        )
        self._conversation_repo.add_message(
            session_id=session_id,
            creator=username,
            message=message,
        )
        answer, trace = self._run_runtime(session_id=session_id, message=message)
        clean_answer, processing_time_s, total_tokens = self._extract_answer_metrics(answer)
        if processing_time_s is None or total_tokens is None:
            trace_processing_time_s, trace_total_tokens = self._extract_trace_metrics(trace)
            if processing_time_s is None:
                processing_time_s = trace_processing_time_s
            if total_tokens is None:
                total_tokens = trace_total_tokens
        handling_agent = self._extract_handling_agent(trace)
        self._conversation_repo.add_message(
            session_id=session_id,
            creator="agent",
            message=clean_answer,
            processing_time_s=processing_time_s,
            total_tokens=total_tokens,
            handling_agent=handling_agent,
        )
        self._user_session_repo.touch_session(session_id)
        return session_id, answer, trace

    def process_ephemeral(self, session_id: str, message: str) -> tuple[str, list[str]]:
        return self._run_runtime(session_id=session_id, message=message)

    def _run_runtime(self, session_id: str, message: str) -> tuple[str, list[str]]:
        self._logger.info(
            "Chat request received. session_id='%s' message_length=%s",
            session_id,
            len(message or ""),
        )
        answer, trace = self._runtime.run(message=message, session_id=session_id)
        self._logger.info(
            "Chat response generated. session_id='%s' trace_steps=%s",
            session_id,
            len(trace),
        )
        return answer, trace

    @staticmethod
    def _extract_answer_metrics(answer: str) -> tuple[str, float | None, int | None]:
        text = str(answer or "")
        match = re.search(
            r"\n\n\[(?P<metrics>[^]]*processing_time_s=[^]]*total_tokens=[^]]*)\]\s*$",
            text,
            flags=re.DOTALL,
        )
        if not match:
            return text, None, None

        metrics_text = match.group("metrics")
        metrics_map: dict[str, str] = {}
        for part in metrics_text.split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            metrics_map[key.strip()] = value.strip()

        processing_time_s: float | None = None
        total_tokens: int | None = None

        processing_raw = metrics_map.get("processing_time_s")
        if processing_raw:
            try:
                processing_time_s = float(processing_raw)
            except ValueError:
                processing_time_s = None

        tokens_raw = metrics_map.get("total_tokens")
        if tokens_raw:
            try:
                total_tokens = int(float(tokens_raw))
            except ValueError:
                total_tokens = None

        return text[: match.start()].rstrip(), processing_time_s, total_tokens

    @staticmethod
    def _extract_handling_agent(trace: list[str]) -> str | None:
        selected_prefix = "router:selected:"
        fallback_prefix = "router:fallback:"
        for entry in trace:
            if entry.startswith(selected_prefix):
                return entry[len(selected_prefix):].strip() or None
        for entry in trace:
            if entry.startswith(fallback_prefix):
                return entry[len(fallback_prefix):].strip() or None
        return None

    @staticmethod
    def _extract_trace_metrics(trace: list[str]) -> tuple[float | None, int | None]:
        processing_time_s: float | None = None
        total_tokens: int | None = None
        processing_prefix = "arxiv_agent:metrics:processing_time_s:"
        token_prefix = "arxiv_agent:metrics:total_tokens:"

        for entry in trace:
            if isinstance(entry, str) and entry.startswith(processing_prefix):
                raw = entry[len(processing_prefix):].strip()
                try:
                    processing_time_s = float(raw)
                except ValueError:
                    processing_time_s = None
            if isinstance(entry, str) and entry.startswith(token_prefix):
                raw = entry[len(token_prefix):].strip()
                try:
                    total_tokens = int(float(raw))
                except ValueError:
                    total_tokens = None
        return processing_time_s, total_tokens
