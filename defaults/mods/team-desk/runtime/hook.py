"""Capture Claude Code and Codex turn-end messages for Team Desk sessions."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import team


def final_text(event):
    text = event.get("last_assistant_message")
    if isinstance(text, str) and text.strip():
        return text.strip()
    transcript = event.get("transcript_path")
    if not transcript:
        return ""
    with Path(transcript).open("rb") as stream:
        stream.seek(max(0, stream.seek(0, 2) - 2_000_000))
        lines = stream.read().splitlines()
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("isSidechain"):
            continue
        if item.get("type") == "response_item":
            item = item.get("payload") or {}
        message = item.get("message") or item
        if item.get("type") == "user" or message.get("role") == "user":
            break
        if message.get("role") != "assistant":
            continue
        text = "\n".join(
            part.get("text", "")
            for part in message.get("content", [])
            if isinstance(part, dict) and part.get("type") in ("text", "output_text")
        )
        if text.strip():
            return text.strip()
    return ""


def capture(event, address):
    if not team.in_dev_profile() or not address or event.get("agent_id"):
        return
    terminal = team.get(address)
    if not terminal.get("labels", {}).get("team.role"):
        return
    session = event.get("session_id") or ""
    if re.fullmatch(r"[a-fA-F0-9-]{36}", session):
        team.hc("label", address, "desk.session=" + session)
    if event.get("hook_event_name") == "Stop":
        text = final_text(event)
        if text:
            stamp = f"{session}:{terminal.get('turn')}:{terminal.get('status_ms')}"
            team.note(address, "Agent", text, event=stamp)


if __name__ == "__main__":
    try:
        capture(json.load(sys.stdin), os.environ.get("HC_TERMINAL_ID"))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        pass
