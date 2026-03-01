from __future__ import annotations

import json
from urllib.request import Request


def build_post_request(url: str, payload: dict[str, object]) -> Request:
    body = json.dumps(payload).encode("utf-8")
    return Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
