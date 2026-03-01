from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.error import URLError

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
from src.app.runtime.interface import AgentRuntimeInterface, RuntimeAgentSpec
from src.app.runtime.response_parsing import (
    extract_processing_time_s,
    extract_total_tokens,
    find_first_string,
    parse_json_dict_from_text,
)


MAX_KEYWORDS = 8


class ArxivSpecialAgent(AgentRuntimeInterface):
    def __init__(
        self,
        local_api_url: str,
        local_api_timeout_seconds: float,
        max_results_per_keyword: int = 1,
        bert_model_name: str = "bert-base-uncased",
    ) -> None:
        self._logger = logging.getLogger("src.app.core")
        self._local_api_url = local_api_url
        self._local_api_timeout_seconds = local_api_timeout_seconds
        self._max_results_per_keyword = max(1, max_results_per_keyword)
        self._bert_model_name = bert_model_name
        self._bert_tokenizer = None
        self._bert_model = None
        self._orchestrator = InternalToolOrchestrator(self)
        self._planner = IterativeToolPlanner(self._orchestrator, max_steps=6)
        self.description = (
            "Special agent for querying arXiv research papers by user-provided keywords. "
            "It uses LLM-assisted keyword extraction from free-form text and searches arXiv per keyword."
        )

    def run(self, message: str, session_id: str) -> tuple[str, list[str]]:
        self._logger.info(
            "arXiv agent run started. session_id='%s' message_length=%s",
            session_id,
            len(message or ""),
        )
        trace = [
            "arxiv_agent:start",
            f"arxiv_agent:session:{session_id}",
        ]
        query_text = self._extract_current_user_message(message)
        self._logger.debug("arXiv agent extracted user query text. session_id='%s' query='%s'", session_id, query_text)
        top_n = self._extract_top_n(query_text)
        trace.append(f"arxiv_agent:top_n:{top_n}")
        self._logger.info("arXiv agent top_n resolved. session_id='%s' top_n=%s", session_id, top_n)
        state: dict[str, Any] = {
            "query_text": query_text,
            "top_n": top_n,
            "llm_processing_time_s_total": 0.0,
            "llm_total_tokens": 0,
            "selected_tool": None,
            "tool_result": {},
            "done": False,
        }
        state, planner_trace = self._planner.run(
            initial_state=state,
            build_plan=self._build_plan,
            apply_result=self._apply_result,
            should_stop=self._should_stop,
        )
        trace.extend(planner_trace)
        tool_trace = state.get("tool_trace", [])
        if isinstance(tool_trace, list):
            trace.extend(str(item) for item in tool_trace)
        selected_tool = state.get("selected_tool")
        if not isinstance(selected_tool, str):
            selected_tool = ""
        tool_result = state.get("tool_result", {})
        if not isinstance(tool_result, dict):
            tool_result = {}
        if selected_tool == "list_tools":
            trace.append("arxiv_agent:end")
            return self._format_tool_result(tool_name=selected_tool, result=tool_result), trace

        keywords = state.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        llm_processing_time_s_total = float(state.get("llm_processing_time_s_total", 0.0))
        llm_total_tokens = int(state.get("llm_total_tokens", 0))
        combined_entries = state.get("combined_entries", [])
        if not isinstance(combined_entries, list):
            combined_entries = []
        top_entries = state.get("top_entries", [])
        if not isinstance(top_entries, list):
            top_entries = []

        if not keywords:
            trace.append("arxiv_agent:no_keywords")
            trace.append(f"arxiv_agent:metrics:processing_time_s:{llm_processing_time_s_total:.3f}")
            trace.append(f"arxiv_agent:metrics:total_tokens:{llm_total_tokens}")
            trace.append("arxiv_agent:end")
            self._logger.info("arXiv agent completed with no keywords. session_id='%s'", session_id)
            return "Please provide one or more keywords (space/comma/semicolon separated) for arXiv search.", trace

        if not combined_entries:
            trace.append("arxiv_agent:no_results")
            trace.append(f"arxiv_agent:metrics:processing_time_s:{llm_processing_time_s_total:.3f}")
            trace.append(f"arxiv_agent:metrics:total_tokens:{llm_total_tokens}")
            trace.append("arxiv_agent:end")
            answer = f"No arXiv papers found for extracted keywords: {', '.join(keywords)}"
            return answer, trace

        trace.append("arxiv_agent:end")
        trace.append(f"arxiv_agent:metrics:processing_time_s:{llm_processing_time_s_total:.3f}")
        trace.append(f"arxiv_agent:metrics:total_tokens:{llm_total_tokens}")
        self._logger.info(
            "arXiv agent run completed. session_id='%s' searched_keywords=%s returned_top=%s",
            session_id,
            len(keywords),
            len(top_entries),
        )
        answer = self._format_ranked_results(top_entries=top_entries, keywords=keywords, top_n=top_n)
        return answer, trace

    def list_available_tools(self) -> list[tuple[str, str]]:
        return standard_list_available_tools(self._orchestrator)

    def execute_tool(self, tool_name: str, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        _ = session_id
        return standard_execute_tool(self._orchestrator, tool_name=tool_name, payload=payload)

    def _build_plan(self, state: dict[str, Any], tools: list[Any]) -> list[ToolDecision]:
        available = {getattr(tool, "name", "") for tool in tools}
        plan: list[ToolDecision] = []
        keywords = state.get("keywords")
        combined_entries = state.get("combined_entries")
        top_entries = state.get("top_entries")
        query_text = str(state.get("query_text", "")).strip()

        if "list_tools" in available and self._is_explicit_tool_request(query_text):
            plan.append(
                ToolDecision(
                    tool_name="list_tools",
                    payload={},
                    note="user_requested_tool_catalog",
                )
            )
            return plan

        if "extract_keywords" in available and keywords is None:
            plan.append(
                ToolDecision(
                    tool_name="extract_keywords",
                    payload={"query_text": state.get("query_text", "")},
                    note="extract_keywords_first",
                )
            )
        if (
            "search_arxiv" in available
            and isinstance(keywords, list)
            and keywords
            and combined_entries is None
        ):
            plan.append(
                ToolDecision(
                    tool_name="search_arxiv",
                    payload={"keywords": keywords},
                    note="search_for_keywords",
                )
            )
        if (
            "rerank_results" in available
            and isinstance(combined_entries, list)
            and combined_entries
            and top_entries is None
        ):
            plan.append(
                ToolDecision(
                    tool_name="rerank_results",
                    payload={
                        "entries": combined_entries,
                        "query_text": state.get("query_text", ""),
                        "keywords": keywords if isinstance(keywords, list) else [],
                        "top_n": state.get("top_n", 5),
                        "llm_processing_time_s_total": state.get("llm_processing_time_s_total", 0.0),
                    },
                    note="rerank_and_trim",
                )
            )
        return plan

    @staticmethod
    def _should_stop(state: dict[str, Any]) -> bool:
        return bool(state.get("done", False))

    @staticmethod
    def _apply_result(state: dict[str, Any], decision: ToolDecision, result: dict[str, Any]) -> None:
        _ = decision
        state["selected_tool"] = decision.tool_name
        state["tool_result"] = result
        state_update = result.get("state_update", {})
        if isinstance(state_update, dict):
            state.update(state_update)
        tool_trace = result.get("trace", [])
        if isinstance(tool_trace, list):
            existing = state.get("tool_trace", [])
            if not isinstance(existing, list):
                existing = []
            existing.extend(str(item) for item in tool_trace)
            state["tool_trace"] = existing
        if "top_entries" in state or not state.get("keywords"):
            state["done"] = True

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
    def _extract_current_user_message(message: str) -> str:
        marker = "Current user message:\n"
        if marker not in message:
            return message.strip()
        after_marker = message.split(marker, 1)[1]
        if "\n\nLast selected agent" in after_marker:
            return after_marker.split("\n\nLast selected agent", 1)[0].strip()
        return after_marker.strip()

    @tool(name="extract_keywords", description="Extract arXiv-relevant keywords from query text via local LLM.")
    def _tool_extract_keywords(self, payload: dict[str, Any]) -> dict[str, Any]:
        query_text = str(payload.get("query_text", "")).strip()
        local_trace: list[str] = []
        keywords, llm_processing_time_s, llm_tokens = self._extract_keywords_with_llm(query_text, local_trace)
        processing_time = llm_processing_time_s or 0.0
        total_tokens = llm_tokens or 0
        local_trace.append(f"arxiv_agent:keywords:{len(keywords)}")
        self._logger.info(
            "arXiv agent resolved keywords. keyword_count=%s keywords=%s",
            len(keywords),
            keywords,
        )
        return {
            "state_update": {
                "keywords": keywords,
                "llm_processing_time_s_total": processing_time,
                "llm_total_tokens": total_tokens,
            },
            "trace": local_trace,
        }

    @tool(name="search_arxiv", description="Search arXiv for each extracted keyword and deduplicate by paper id.")
    def _tool_search_arxiv(self, payload: dict[str, Any]) -> dict[str, Any]:
        keywords = payload.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []

        combined_by_id: dict[str, dict[str, object]] = {}
        local_trace: list[str] = []
        for item in keywords:
            keyword = str(item).strip()
            if not keyword:
                continue
            local_trace.append(f"arxiv_agent:search:{keyword}")
            self._logger.info("arXiv agent searching keyword. keyword='%s'", keyword)
            entries = self._search_single_keyword(keyword)
            for entry in entries:
                paper_id = entry["id"]
                if paper_id not in combined_by_id:
                    combined_by_id[paper_id] = {
                        "title": entry["title"],
                        "id": paper_id,
                        "published": entry["published"],
                        "summary": entry.get("summary", ""),
                        "matched_keywords": {keyword},
                    }
                else:
                    matched = combined_by_id[paper_id].get("matched_keywords")
                    if isinstance(matched, set):
                        matched.add(keyword)

        combined_entries = list(combined_by_id.values())
        local_trace.append(f"arxiv_agent:combined_entries:{len(combined_entries)}")
        self._logger.info(
            "arXiv agent combined and deduplicated entries. entry_count=%s",
            len(combined_entries),
        )
        return {"state_update": {"combined_entries": combined_entries}, "trace": local_trace}

    @tool(name="rerank_results", description="Rerank deduplicated arXiv entries and keep top_n.")
    def _tool_rerank_results(self, payload: dict[str, Any]) -> dict[str, Any]:
        entries = payload.get("entries", [])
        query_text = str(payload.get("query_text", ""))
        keywords = payload.get("keywords", [])
        top_n = payload.get("top_n", 5)
        if not isinstance(entries, list):
            entries = []
        if not isinstance(keywords, list):
            keywords = []
        if not isinstance(top_n, int):
            top_n = 5

        rerank_started = time.perf_counter()
        ranked_entries = self._rerank_entries(entries=entries, query_text=query_text, keywords=keywords)
        rerank_time_s = time.perf_counter() - rerank_started
        return {
            "state_update": {
                "top_entries": ranked_entries[: max(1, top_n)],
                "llm_processing_time_s_total": float(payload.get("llm_processing_time_s_total", 0.0)) + rerank_time_s,
            },
            "trace": [],
        }

    @tool(name="list_tools", description="List tools available in this agent.")
    def _tool_list_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        _ = payload
        return standard_list_tools_payload(self._orchestrator)

    def _extract_keywords_with_llm(self, query: str, trace: list[str]) -> tuple[list[str], float | None, int | None]:
        prompt = (
            "Task: extract research-paper search keywords.\n"
            "Output format (strictly JSON only):\n"
            '{"keywords":["k1","k2","k3"]}\n'
            "Rules:\n"
            "1. Keep only useful research terms.\n"
            "2. Remove instruction words: search, for, paper, papers, keyword, keywords, find, show.\n"
            "3. Prefer short phrases (1-3 words).\n"
            "4. Return 2-6 keywords, max 8.\n"
            "5. No explanation text.\n\n"
            f"Query:\n{query}"
        )
        payload = {"question_json": {"received": prompt}}
        request = build_post_request(self._local_api_url, payload)
        trace.append("arxiv_agent:keyword_extraction:request")
        self._logger.debug("arXiv agent keyword extraction request sent. selector_url='%s'", self._local_api_url)
        try:
            with urllib.request.urlopen(request, timeout=self._local_api_timeout_seconds) as response:
                response_text = response.read().decode("utf-8")
        except URLError as error:
            trace.append(f"arxiv_agent:keyword_extraction:error:URLError:{error.reason}")
            self._logger.warning(
                "arXiv agent keyword extraction request failed. error='%s'",
                error.reason,
            )
            return [], None, None
        except Exception as error:  # pragma: no cover
            trace.append(f"arxiv_agent:keyword_extraction:error:{type(error).__name__}")
            self._logger.warning(
                "arXiv agent keyword extraction request failed. error_type='%s'",
                type(error).__name__,
            )
            return [], None, None

        trace.append("arxiv_agent:keyword_extraction:response")
        self._logger.debug("arXiv agent keyword extraction response received.")
        return self._parse_keywords_response(response_text)

    def _parse_keywords_response(self, response_text: str) -> tuple[list[str], float | None, int | None]:
        processing_time_s: float | None = None
        total_tokens: int | None = None
        parsed: object
        try:
            parsed = json.loads(response_text)
            processing_time_s = extract_processing_time_s(parsed)
            total_tokens = extract_total_tokens(parsed)
        except json.JSONDecodeError:
            parsed = parse_json_dict_from_text(response_text)
            if parsed is None:
                return self._parse_keywords_non_json(response_text), processing_time_s, total_tokens

        keywords = self._find_keywords_list(parsed)
        if not keywords:
            # Some providers wrap content under fields like "answer"/"response".
            # Unwrap that text and retry non-JSON keyword parsing.
            wrapped_text = find_first_string(parsed)
            if wrapped_text:
                return self._parse_keywords_non_json(wrapped_text), processing_time_s, total_tokens
            return [], processing_time_s, total_tokens

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in keywords:
            if not isinstance(item, str):
                continue
            parts = [p.strip() for p in re.split(r"[,;]", item) if p.strip()]
            cleaned_parts = self._clean_keyword_candidates(parts)
            for token in cleaned_parts:
                lowered = token.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                cleaned.append(token)
                if len(cleaned) >= MAX_KEYWORDS:
                    break
            if len(cleaned) >= MAX_KEYWORDS:
                break
        return cleaned, processing_time_s, total_tokens

    def _parse_keywords_non_json(self, response_text: str) -> list[str]:
        text = (response_text or "").strip()
        if not text:
            return []

        # Handle embedded list forms like: keywords:["AI","Machine learning"]
        embedded_list = re.search(r"(?i)\bkeywords?\b\s*[:=]\s*\[([^\]]+)\]", text)
        if embedded_list:
            candidates = [part.strip() for part in embedded_list.group(1).split(",") if part.strip()]
            return self._clean_keyword_candidates(candidates)

        # Handle common non-JSON forms:
        # - "keywords: AI, Machine learning"
        # - bullet/number lists
        normalized = re.sub(r"(?i)^keywords?\s*[:\-]\s*", "", text)
        lines = [line.strip(" -*\t\r") for line in normalized.splitlines() if line.strip()]
        if len(lines) > 1:
            candidates = lines
        else:
            candidates = [part.strip() for part in re.split(r"[,;]", normalized) if part.strip()]
        return self._clean_keyword_candidates(candidates)

    def _clean_keyword_candidates(self, candidates: list[str]) -> list[str]:
        if not candidates:
            return []

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            token = (item or "").strip()
            token = token.replace('"', "").replace("'", "").replace("[", "").replace("]", "")
            token = re.sub(r"(?i)^keywords?\s*[:=\-]*\s*", "", token).strip()
            token = re.sub(r"^[^\w]+|[^\w]+$", "", token).strip()
            if not token:
                continue
            lowered = token.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(token)
            if len(cleaned) >= MAX_KEYWORDS:
                break
        return cleaned

    def _find_keywords_list(self, value: object) -> list[object] | None:
        if isinstance(value, dict):
            direct = value.get("keywords")
            if isinstance(direct, list):
                return direct
            for nested in value.values():
                found = self._find_keywords_list(nested)
                if found is not None:
                    return found
        if isinstance(value, list):
            for nested in value:
                found = self._find_keywords_list(nested)
                if found is not None:
                    return found
        return None

    def _search_single_keyword(self, keyword: str) -> list[dict[str, str]]:
        url = (
            "http://export.arxiv.org/api/query?"
            + urllib.parse.urlencode(
                {
                    "search_query": f"all:{keyword}",
                    "start": 0,
                    "max_results": self._max_results_per_keyword,
                }
            )
        )
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                raw_xml = response.read().decode("utf-8")
        except Exception as error:
            self._logger.warning(
                "arXiv agent keyword search failed. keyword='%s' error_type='%s'",
                keyword,
                type(error).__name__,
            )
            return []

        entries = self._parse_entries(raw_xml)
        self._logger.debug(
            "arXiv agent keyword search parsed entries. keyword='%s' entry_count=%s",
            keyword,
            len(entries),
        )
        return entries

    @staticmethod
    def _extract_top_n(query: str, default_n: int = 5) -> int:
        text = (query or "").lower()
        for pattern in (
            r"\btop\s+(\d{1,2})\b",
            r"\bfirst\s+(\d{1,2})\b",
            r"\blimit\s+(\d{1,2})\b",
            r"\bn\s*=\s*(\d{1,2})\b",
        ):
            match = re.search(pattern, text)
            if match:
                return max(1, min(20, int(match.group(1))))
        return default_n

    def _rerank_entries(
        self,
        entries: list[dict[str, object]],
        query_text: str,
        keywords: list[str],
    ) -> list[dict[str, object]]:
        bert_ranked = self._rerank_with_bert(entries=entries, query_text=query_text, keywords=keywords)
        if bert_ranked is not None:
            return bert_ranked
        return self._rerank_with_lexical(entries=entries, query_text=query_text, keywords=keywords)

    def _rerank_with_bert(
        self,
        entries: list[dict[str, object]],
        query_text: str,
        keywords: list[str],
    ) -> list[dict[str, object]] | None:
        model_objects = self._load_bert_model()
        if model_objects is None:
            return None

        tokenizer, model, torch = model_objects
        query_embedding = self._encode_text(query_text, tokenizer, model, torch)
        if query_embedding is None:
            return None

        ranked: list[dict[str, object]] = []
        for entry in entries:
            title = str(entry.get("title", ""))
            summary = str(entry.get("summary", ""))
            entry_text = f"{title}. {summary}".strip()
            entry_embedding = self._encode_text(entry_text, tokenizer, model, torch)
            if entry_embedding is None:
                return None

            similarity = torch.nn.functional.cosine_similarity(
                query_embedding,
                entry_embedding,
                dim=0,
            ).item()
            matched_keywords = entry.get("matched_keywords")
            matched_count = len(matched_keywords) if isinstance(matched_keywords, set) else 0
            keyword_bonus = float(matched_count) * 0.03
            score = similarity + keyword_bonus

            enriched = dict(entry)
            enriched["score"] = score
            ranked.append(enriched)

        ranked.sort(
            key=lambda item: (
                float(item.get("score", 0.0)),
                str(item.get("published", "")),
            ),
            reverse=True,
        )
        self._logger.info(
            "arXiv agent reranked entries using BERT. model='%s' entry_count=%s",
            self._bert_model_name,
            len(ranked),
        )
        return ranked

    def _rerank_with_lexical(
        self,
        entries: list[dict[str, object]],
        query_text: str,
        keywords: list[str],
    ) -> list[dict[str, object]]:
        query_terms = self._terms(query_text)
        ranked: list[dict[str, object]] = []
        for entry in entries:
            title = str(entry.get("title", ""))
            summary = str(entry.get("summary", ""))
            text = f"{title} {summary}".lower()
            title_lower = title.lower()
            matched_keywords = entry.get("matched_keywords")
            matched_count = len(matched_keywords) if isinstance(matched_keywords, set) else 0

            score = matched_count * 6
            for keyword in keywords:
                kw = keyword.lower().strip()
                if not kw:
                    continue
                if kw in title_lower:
                    score += 5
                elif kw in text:
                    score += 3
            for term in query_terms:
                if len(term) < 2:
                    continue
                if term in text:
                    score += 1

            enriched = dict(entry)
            enriched["score"] = score
            ranked.append(enriched)

        ranked.sort(
            key=lambda item: (
                int(item.get("score", 0)),
                str(item.get("published", "")),
            ),
            reverse=True,
        )
        return ranked

    def _load_bert_model(self) -> tuple[object, object, object] | None:
        if self._bert_tokenizer is not None and self._bert_model is not None:
            try:
                import torch  # type: ignore

                return self._bert_tokenizer, self._bert_model, torch
            except Exception:
                return None

        try:
            import torch  # type: ignore
            from transformers import AutoModel, AutoTokenizer  # type: ignore
        except Exception as error:
            self._logger.warning(
                "arXiv agent BERT reranking unavailable. Missing deps for transformers/torch. error_type='%s'",
                type(error).__name__,
            )
            return None

        project_root = Path(__file__).resolve().parents[3]
        local_model_dir = project_root / "models" / self._bert_model_name

        try:
            if local_model_dir.exists() and any(local_model_dir.iterdir()):
                self._logger.info(
                    "arXiv agent loading BERT model from local directory. model='%s' path='%s'",
                    self._bert_model_name,
                    local_model_dir,
                )
                self._bert_tokenizer = AutoTokenizer.from_pretrained(str(local_model_dir), local_files_only=True)
                self._bert_model = AutoModel.from_pretrained(str(local_model_dir), local_files_only=True)
            else:
                self._logger.info(
                    "arXiv agent local BERT model not found. Downloading model='%s' and caching to '%s'",
                    self._bert_model_name,
                    local_model_dir,
                )
                local_model_dir.mkdir(parents=True, exist_ok=True)
                downloaded_tokenizer = AutoTokenizer.from_pretrained(self._bert_model_name)
                downloaded_model = AutoModel.from_pretrained(self._bert_model_name)
                downloaded_tokenizer.save_pretrained(str(local_model_dir))
                downloaded_model.save_pretrained(str(local_model_dir))
                self._bert_tokenizer = downloaded_tokenizer
                self._bert_model = downloaded_model

            self._bert_model.eval()
        except Exception as error:
            self._logger.warning(
                "arXiv agent BERT model load failed. model='%s' path='%s' error_type='%s'",
                self._bert_model_name,
                local_model_dir,
                type(error).__name__,
            )
            self._bert_tokenizer = None
            self._bert_model = None
            return None

        return self._bert_tokenizer, self._bert_model, torch

    @staticmethod
    def _encode_text(text: str, tokenizer: object, model: object, torch: object) -> object | None:
        if not text.strip():
            return None
        try:
            encoded = tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=256,
            )
            with torch.no_grad():
                outputs = model(**encoded)
            hidden = outputs.last_hidden_state[0]  # [tokens, hidden]
            attention_mask = encoded["attention_mask"][0].unsqueeze(-1)  # [tokens, 1]
            masked_hidden = hidden * attention_mask
            denom = attention_mask.sum().clamp(min=1)
            pooled = masked_hidden.sum(dim=0) / denom
            return pooled
        except Exception:
            return None

    @staticmethod
    def _terms(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

    @staticmethod
    def _format_ranked_results(
        top_entries: list[dict[str, object]],
        keywords: list[str],
        top_n: int,
    ) -> str:
        lines = [
            f"arXiv combined results (top {top_n}):",
            f"Extracted keywords: {', '.join(keywords)}",
            "",
        ]
        for index, entry in enumerate(top_entries, start=1):
            title = str(entry.get("title", "(untitled)"))
            paper_id = str(entry.get("id", "(no link)"))
            published = str(entry.get("published", "(unknown)"))
            raw_score = entry.get("score", 0)
            score = f"{float(raw_score):.4f}" if isinstance(raw_score, float) else str(raw_score)
            matched = entry.get("matched_keywords")
            matched_keywords = sorted(matched) if isinstance(matched, set) else []
            lines.append(f"{index}. {title}")
            lines.append(f"   URL: {paper_id}")
            lines.append(f"   Published: {published}")
            lines.append(f"   Relevance score: {score}")
            if matched_keywords:
                lines.append(f"   Matched keywords: {', '.join(matched_keywords)}")
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_entries(raw_xml: str) -> list[dict[str, str]]:
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            return []

        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        entries: list[dict[str, str]] = []
        for entry in root.findall("atom:entry", namespace):
            title = (entry.findtext("atom:title", default="", namespaces=namespace) or "").strip()
            paper_id = (entry.findtext("atom:id", default="", namespaces=namespace) or "").strip()
            published = (entry.findtext("atom:published", default="", namespaces=namespace) or "").strip()
            summary = (entry.findtext("atom:summary", default="", namespaces=namespace) or "").strip()
            if not title and not paper_id:
                continue
            entries.append(
                {
                    "title": title or "(untitled)",
                    "id": paper_id or "(no link)",
                    "published": published or "(unknown)",
                    "summary": summary,
                }
            )
        return entries

    @staticmethod
    def _format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
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
        return "Tool executed but no formatter is defined."


def build_agent_specs(settings: AppSettings) -> list[RuntimeAgentSpec]:
    runtime = ArxivSpecialAgent(
        local_api_url=settings.local_api_url,
        local_api_timeout_seconds=settings.local_api_timeout_seconds,
        max_results_per_keyword=20,
    )
    return [
        RuntimeAgentSpec(
            agent_id="special:arxiv",
            runtime=runtime,
            runtime_type="special",
            keywords=(
                "arxiv",
                "paper",
                "papers",
                "research",
                "keyword",
                "keywords",
                "publication",
                "publications",
            ),
            is_fallback=False,
        )
    ]
