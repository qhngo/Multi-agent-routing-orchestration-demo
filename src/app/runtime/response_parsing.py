from __future__ import annotations

import json


PREFERRED_TEXT_KEYS: tuple[str, ...] = (
    "answer",
    "response",
    "final_answer",
    "text",
    "result",
    "output",
    "message",
)


def find_first_string(value: object, preferred_keys: tuple[str, ...] = PREFERRED_TEXT_KEYS) -> str | None:
    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        for key in preferred_keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        for item in value.values():
            found = find_first_string(item, preferred_keys)
            if found and found.strip():
                return found

    if isinstance(value, list):
        for item in value:
            found = find_first_string(item, preferred_keys)
            if found and found.strip():
                return found

    return None


def extract_processing_time_s(value: object) -> float | None:
    if isinstance(value, dict):
        item = value.get("processing_time_s")
        if isinstance(item, (int, float)):
            return float(item)
        for nested in value.values():
            found = extract_processing_time_s(nested)
            if found is not None:
                return found

    if isinstance(value, list):
        for nested in value:
            found = extract_processing_time_s(nested)
            if found is not None:
                return found

    return None


def extract_total_tokens(value: object) -> int | None:
    if isinstance(value, dict):
        item = value.get("total_tokens")
        if isinstance(item, int):
            return item
        if isinstance(item, float):
            return int(item)

        prompt_tokens = value.get("prompt_tokens")
        completion_tokens = value.get("completion_tokens")
        if isinstance(prompt_tokens, (int, float)) and isinstance(completion_tokens, (int, float)):
            return int(prompt_tokens) + int(completion_tokens)

        for nested in value.values():
            found = extract_total_tokens(nested)
            if found is not None:
                return found

    if isinstance(value, list):
        for nested in value:
            found = extract_total_tokens(nested)
            if found is not None:
                return found

    return None


def parse_json_dict_from_text(text: str) -> dict[str, object] | None:
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
