from __future__ import annotations

from typing import Any


class BizXLookupTool:
    name = "bizx_lookup"

    _fake_db = {
        "CUST-100": {"customer_id": "CUST-100", "name": "Acme Labs", "tier": "enterprise"},
        "CUST-200": {"customer_id": "CUST-200", "name": "Northwind", "tier": "pro"},
    }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        customer_id = payload.get("customer_id", "")
        record = self._fake_db.get(customer_id)
        if not record:
            return {"found": False, "customer_id": customer_id}
        return {"found": True, "record": record}
