"""Browser history export parser for CITDS contextual evidence.

Supports JSON and CSV exports. Parsed visits become BrowserHistory EvidenceUnits
and are always contextual: they can support or refine an action but cannot create
an obligation without primary evidence.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

import evidence_service


def _pick(record: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    lower = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        if key in record:
            return record[key]
        if key.lower() in lower:
            return lower[key.lower()]
    return default


def parse_json_history(raw: bytes) -> List[Dict[str, Any]]:
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict):
        if "items" in data:
            data = data["items"]
        elif "history" in data:
            data = data["history"]
        elif "Browser History" in data:
            data = data["Browser History"]
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON history export must be a list or object containing items/history.")
    return [normalize_history_record(item) for item in data if isinstance(item, dict)]


def parse_csv_history(raw: bytes) -> List[Dict[str, Any]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [normalize_history_record(row) for row in reader]


def normalize_history_record(record: Dict[str, Any]) -> Dict[str, Any]:
    url = _pick(record, "url", "URL", "href", "link", default="")
    title = _pick(record, "title", "Title", "name", default=url)
    visited_at = _pick(record, "visited_at", "visit_time", "lastVisitTime", "time", "timestamp", "date", default=None)
    visit_count = _pick(record, "visit_count", "visitCount", "count", default=None)
    relation_key = _pick(record, "relation_key", "relation", "topic", default=url)
    return {
        "url": str(url or ""),
        "title": str(title or url or "Browser history item"),
        "visited_at": str(visited_at) if visited_at else None,
        "visit_count": int(visit_count) if str(visit_count or "").isdigit() else None,
        "relation_key": str(relation_key or url or "browser-history"),
        "metadata": {"raw": record},
    }


def parse_history_export(filename: str, raw: bytes) -> List[Dict[str, Any]]:
    lower = filename.lower()
    if lower.endswith(".json"):
        return parse_json_history(raw)
    if lower.endswith(".csv"):
        return parse_csv_history(raw)
    raise ValueError("Unsupported browser history export format. Upload .json or .csv.")


def import_history_export(db, user_id: str, filename: str, raw: bytes) -> Dict[str, Any]:
    records = parse_history_export(filename, raw)
    units = []
    for item in records:
        url = item.get("url")
        if not url:
            continue
        title = item.get("title") or url
        content = (
            f"Browser history: visited {title}. URL: {url}. "
            "This is contextual support only and cannot create an obligation alone."
        )
        units.append({
            "source_type": "BrowserHistory",
            "title": title,
            "content": content,
            "source_timestamp": item.get("visited_at"),
            "thread_id": None,
            "relation_key": item.get("relation_key") or url,
            "metadata": {
                "connector": "browser_history_export",
                "url": url,
                "visit_count": item.get("visit_count"),
                **(item.get("metadata") or {}),
            },
        })
    created = evidence_service.import_evidence_units(db, user_id, units)
    return {"parsed": len(records), "imported": len(created), "ids": [unit.id for unit in created]}
