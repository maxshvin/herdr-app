"""Read provider-reported output tokens from the session's local transcript."""

import json
import re
from pathlib import Path


def locate(session, provider, paths):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", session or ""):
        return None
    if session in paths:
        return paths[session]
    home = Path.home()
    if provider == "claude":
        root, pattern = home / ".claude/projects", "*/*.jsonl"
    elif provider == "codex":
        root, pattern = home / ".codex/sessions", "*/*/*/*.jsonl"
    else:
        return None
    marker = ("indexed", provider)
    if marker not in paths:
        for path in root.glob(pattern):
            paths[path.stem[-36:]] = path
        paths[marker] = True
    return paths.get(session)


def count(path, provider, cache):
    if not path or not path.is_file():
        return None
    stat = path.stat()
    key = str(path)
    row = cache.get(key, {})
    if row.get("inode") != stat.st_ino or row.get("offset", 0) > stat.st_size:
        row = {}
    if row.get("offset") == stat.st_size and row.get("modified") == stat.st_mtime_ns:
        return row.get("total")
    offset = row.get("offset", 0)
    total = row.get("total")
    messages = row.get("messages", {})
    with path.open("rb") as stream:
        stream.seek(offset)
        for line in stream:
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            if b'"output_tokens"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if provider == "codex":
                p = d.get("payload") or {}
                if p.get("type") == "token_count":
                    n = ((p.get("info") or {}).get("total_token_usage") or {}).get(
                        "output_tokens"
                    )
                    if isinstance(n, int):
                        total = n
            elif d.get("type") == "assistant" and not d.get("isSidechain"):
                msg = d.get("message") or {}
                mid, n = msg.get("id"), (msg.get("usage") or {}).get("output_tokens")
                if mid and isinstance(n, int):
                    prev = messages.get(mid, 0)
                    total = (total or 0) + max(0, n - prev)
                    messages[mid] = max(prev, n)
    cache[key] = {
        "inode": stat.st_ino,
        "modified": stat.st_mtime_ns,
        "offset": offset,
        "total": total,
        "messages": messages,
    }
    return total
