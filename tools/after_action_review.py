#!/usr/bin/env python3
"""After-Action Success Review (C22-J). US Army AAR + Appreciative Inquiry 4D."""
import json, logging, os, tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
STORE_FILENAME = "after_action_reviews.json"
MAX_RECORDS = 200
MAX_FIELD_LEN = 1000
MAX_TAG_LEN = 50
MAX_TAGS = 10
REQUIRED_FIELDS = ("intent", "why_success")
CONTENT_FIELDS = ("intent", "actual", "why_success", "replicable", "context_dependent", "transferable")

def load_store(memory_dir):
    path = os.path.join(memory_dir, STORE_FILENAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load AAR store: %s", e)
        return []

def _save_store(memory_dir, records):
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, STORE_FILENAME)
    fd, tmp = tempfile.mkstemp(dir=memory_dir, prefix=".aar_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def _truncate(text, max_len=MAX_FIELD_LEN):
    return text[:max_len]

def create_aar(memory_dir, intent, actual, why_success, replicable, context_dependent, transferable, tags=None):
    fields = dict(intent=intent, actual=actual, why_success=why_success, replicable=replicable, context_dependent=context_dependent, transferable=transferable)
    for name in REQUIRED_FIELDS:
        if not fields[name] or not fields[name].strip():
            raise ValueError(f"{name} must not be empty")
    truncated = {k: _truncate(v) for k, v in fields.items()}
    tags = [_truncate(str(t), MAX_TAG_LEN) for t in (tags or [])[:MAX_TAGS]]
    records = load_store(memory_dir)
    new_id = max((r.get("id", 0) for r in records), default=0) + 1
    record = {"id": new_id, **truncated, "tags": tags, "recorded_at": datetime.now(timezone.utc).isoformat()}
    records.append(record)
    if len(records) > MAX_RECORDS:
        records = records[-MAX_RECORDS:]
    _save_store(memory_dir, records)
    return record

def search_aars(memory_dir, query="", tags=None, limit=5):
    records = load_store(memory_dir)
    q = query.lower().strip() if query else ""
    results = []
    for rec in records:
        if tags and not set(rec.get("tags", [])).intersection(set(tags)):
            continue
        if q and q not in " ".join(str(rec.get(f, "")) for f in CONTENT_FIELDS).lower():
            continue
        results.append(rec)
    results.sort(key=lambda r: r.get("id", 0), reverse=True)
    return results[:limit]

def get_aar_report(memory_dir, limit=5):
    records = load_store(memory_dir)
    if not records:
        return "No After-Action Reviews recorded yet."
    records.sort(key=lambda r: r.get("id", 0), reverse=True)
    records = records[:limit]
    lines = [f"=== After-Action Reviews (latest {len(records)}) ==="]
    for rec in records:
        lines.append("")
        lines.append(f"--- AAR #{rec.get('id', '?')} [{rec.get('recorded_at', '')}] ---")
        if rec.get("tags"): lines.append(f"  Tags: {', '.join(rec['tags'])}")
        for f in CONTENT_FIELDS:
            lines.append(f"  {f}: {rec.get(f, '')}")
    return "\n".join(lines)
