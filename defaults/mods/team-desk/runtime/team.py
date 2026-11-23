#!/usr/bin/python3
"""Local control surface for Team Desk and its agents. Commands take JSON on stdin."""

import concurrent.futures
import contextlib
import fcntl
import html
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__:
    from . import telemetry
else:
    import telemetry

HERE = Path(__file__).resolve().parent
APP_HOME = Path.home() / ".config/herdr-desk-dev"
ROOT = APP_HOME / "tasks-state"
HC = str(APP_HOME / "bin/hc")
HERDR = str(APP_HOME / "bin/herdr-app")
SHARED = APP_HOME / "shared.json"
PROVIDERS = {"codex": ["codex", "--no-daemon"], "claude": ["claude"]}
TICKET_REF = re.compile(
    r"(?:(https://linear\.app/[\w-]+)/issue/)?([a-z][a-z0-9]*-\d+)([/#?]\S*)?",
    re.IGNORECASE | re.ASCII,
)
os.umask(0o077)


def in_dev_profile():
    return (
        os.environ.get("HERDR_APP_HOME") == str(APP_HOME)
        and os.environ.get("HC_SESSION") == "desk-dev"
        and os.environ.get("HC_SOCKET") == str(APP_HOME / "run/dev.sock")
    )


def read(path, fallback):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return fallback


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        try:
            f.write(json.dumps(value, ensure_ascii=False).encode())
            f.flush()
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


@contextlib.contextmanager
def lock(name):
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / (name + ".lock")).open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def settings():
    cfg = {**read(HERE / "settings.json", {}), **read(APP_HOME / "team-desk.json", {})}
    if any(cfg.get(key) not in PROVIDERS for key in ("coordinator", "worker")):
        raise ValueError("Choose claude or codex in settings.json.")
    return cfg


def worker_command():
    return shlex.join([sys.executable, "-B", str(HERE / "team.py")])


def command(argv, timeout=30):
    p = subprocess.run(
        argv, text=True, capture_output=True, timeout=timeout, check=False
    )
    if p.returncode:
        raise ValueError((p.stderr or p.stdout or "Command failed").strip()[:500])
    return p.stdout


def hc(*args):
    return json.loads(command([HC, *args, "--json"]) or "{}")


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"t[a-z0-9]+", value):
        raise ValueError("Select a local Herdr agent.")
    return value


def folder(a):
    return ROOT / "tasks" / address(a)


def clean(s, limit=4000):
    if not isinstance(s, str) or not s.strip() or len(s) > limit:
        raise ValueError("A text field is empty or too long.")
    return s.strip()


def note(a, who, text, event=None):
    with lock(a):
        p = folder(a) / "chat.json"
        log = read(p, [])
        if event and any(m.get("event") == event for m in log[-12:]):
            return
        log.append({"who": who, "text": text, "time": time.time(), "event": event})
        save(p, log[-150:])


def get(a):
    r = hc("get", address(a))
    return r.get("terminal", r)


def live(a):
    t = get(a)
    if t.get("state") != "live" or not (t.get("status") or {}).get("app"):
        raise ValueError("The agent is no longer running. Refresh the team list.")
    return t


def list_agents():
    rows = hc("ls").get("terminals", [])
    ids = [
        r["address"]
        for r in rows
        if re.fullmatch(r"t[a-z0-9]+", r["address"])
        and (r.get("agent") or (folder(r["address"]) / "meta.json").is_file())
    ]

    def inspect(a):
        try:
            return get(a)
        except ValueError:
            return None  # A helper may have exited since the list was read.

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return [t for t in pool.map(inspect, ids) if t and t.get("state") == "live"]


def tickets():
    rows = read(SHARED, {}).get("agent-board.tickets", {})
    return {
        k: v["t"]
        for k, v in rows.items()
        if v.get("t") and v.get("y") not in ("completed", "canceled")
    }


def status(_):
    cfg = settings()
    ts = list_agents()
    board = tickets()
    agents = []
    paths = {}
    with lock("usage"):
        cachepath = ROOT / "token-counts.json"
        cache = read(cachepath, {})
        before = {k: (v["offset"], v.get("modified")) for k, v in cache.items()}
        for t in ts:
            a, l, s = t["address"], t.get("labels", {}), t.get("status") or {}
            provider = s.get("app") or read(folder(a) / "meta.json", {}).get(
                "provider", ""
            )
            session = l.get("desk.session")
            log = telemetry.locate(session, provider, paths)
            try:
                tokens = telemetry.count(log, provider, cache)
            except OSError:
                tokens = None
            ticket = l.get("linear.issue", "")
            role = l.get("team.role", "")
            kind = "" if s.get("state") == "working" else l.get("desk.kind", "")
            if kind == "blocked" and s.get("state") != "blocked":
                kind = ""
            agents.append(
                {
                    "id": a,
                    "title": l.get("team.title")
                    or board.get(ticket)
                    or l.get("desk.topic")
                    or t.get("title")
                    or a,
                    "ticket": ticket,
                    "role": role,
                    "parent": l.get("desk.parent") or t.get("parent"),
                    "provider": provider,
                    "state": s.get("state") or "starting",
                    "kind": kind,
                    "ask": l.get("desk.ask") or l.get("desk.card", ""),
                    "detail": l.get("desk.did", ""),
                    "tokens": tokens,
                    "prs": l.get("desk.prs", ""),
                    "report": read(folder(a) / "report.json", {}),
                }
            )
        if any(before.get(k) != (v["offset"], v["modified"]) for k, v in cache.items()):
            save(cachepath, cache)
    live_ids = {t["id"] for t in agents}
    for path in sorted(
        (ROOT / "tasks").glob("*/meta.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        meta = read(path, {})
        if meta["id"] not in live_ids:
            agents.append(
                dict(
                    meta,
                    state="finished",
                    kind="",
                    tokens=None,
                    report=read(path.parent / "report.json", {}),
                )
            )
    return {
        "agents": agents,
        "tickets": board,
        "watch": cfg["output_token_watch"],
        "sections": cfg.get("task_sections", ["brief", "plan", "evidence", "review"]),
        "accent": cfg["accent"],
        "worker": cfg["worker"],
        "focus": read(ROOT / "focus.json", None),
    }


def message(a, text, display=None):
    t = live(a)
    command([HC, "send", address(a), "--", clean(text, 24000)], timeout=75)
    with lock(a):
        meta = folder(a) / "meta.json"
        if not meta.is_file():
            labels = t.get("labels", {})
            save(
                meta,
                {
                    "id": a,
                    "title": labels.get("desk.topic") or t.get("title") or a,
                    "ticket": labels.get("linear.issue", ""),
                    "provider": t["status"]["app"],
                    "role": labels.get("team.role", ""),
                    "parent": t.get("parent"),
                },
            )
    note(
        a, "Coordinator" if os.environ.get("HC_TERMINAL_ID") else "You", display or text
    )
    return {"id": a}


def ticket_id(text):
    match = TICKET_REF.fullmatch(text.strip())
    if not match:
        return ""
    workspace, identifier, suffix = match.groups()
    if not workspace and suffix:
        return ""
    if workspace:
        board = read(SHARED, {}).get("agent-board.org")
        if board and workspace.lower() != board.rstrip("/").lower():
            return ""
    return identifier.upper()


def launch(role, text="", ticket="", provider=None):
    cfg = settings()
    provider = provider or cfg["coordinator" if role == "coordinator" else "worker"]
    if provider not in PROVIDERS:
        raise ValueError("Unknown agent provider.")
    if ticket:
        reference = ticket
        ticket = ticket_id(reference)
        if not ticket:
            raise ValueError(
                "Use a ticket ID or issue link from your Linear workspace."
            )
        text = text.strip() or reference
    elif role == "worker":
        ticket = ticket_id(text)
    text = clean(text, 12000)
    title = (
        "Master chat"
        if role == "coordinator"
        else tickets().get(ticket) or text.splitlines()[0][:100]
    )
    digest = hashlib.sha256((ticket or text).encode()).hexdigest()[:16]
    key = "master" if role == "coordinator" else digest
    with lock("launch-" + key):
        dup = hc("ls", "--label", "team.key=" + key).get("terminals", [])
        if role != "coordinator" and ticket and not dup:
            dup = [
                t
                for t in list_agents()
                if ticket
                in t.get("labels", {}).get("linear.issue", "").upper().split(",")
            ]
        if dup:
            a = dup[0]["address"]
            if role == "coordinator":
                return message(a, text)
            return {"id": a, "existing": True}
        workspace = str(Path(cfg["workspace"]).expanduser())
        prompt = (
            (HERE / (role + ".txt")).read_text().replace("{desk}", worker_command())
        )
        if ticket:
            prompt += "\nLinear ticket: " + ticket + " · " + title
        prompt += "\n\nUser request:\n" + text
        args = [
            HC,
            "run",
            "--bg",
            "--json",
            "--cwd",
            workspace,
            "--name",
            "desk-" + key,
        ]
        parent = os.environ.get("HC_TERMINAL_ID") if role == "worker" else None
        if role == "worker" and not parent:
            leads = hc("ls", "--label", "team.key=master").get("terminals", [])
            parent = leads[0]["address"] if leads else None
        args += (
            ["--parent", address(parent), "--outlive-parent"]
            if parent
            else ["--no-parent"]
        )
        for k, v in {
            "team.key": key,
            "team.role": role,
            "team.title": title,
            "linear.issue": ticket,
        }.items():
            if v:
                args += ["--label", k + "=" + v]
        raw = json.loads(
            command([*args, "--", *PROVIDERS[provider], "--", prompt], timeout=80)
        )
        t = raw.get("terminal", raw)
        a = address(t["address"])
        save(
            folder(a) / "meta.json",
            {
                "id": a,
                "title": title,
                "ticket": ticket,
                "provider": provider,
                "role": role,
                "parent": parent,
            },
        )
        note(a, "You", text)
        return {"id": a}


def publish(data):
    a = address(os.environ.get("HC_TERMINAL_ID"))
    valid = {
        "problem",
        "example",
        "solution",
        "question",
        "decision",
        "review",
        "steps",
    }
    if not data or set(data) - valid:
        raise ValueError("Report fields: " + ", ".join(sorted(valid)))
    for k, v in data.items():
        if k == "steps":
            if not isinstance(v, list) or len(v) > 30:
                raise ValueError("Steps must be a list of at most 30 items.")
            for item in v:
                clean(item["name"], 180)
                clean(item["owner"], 120)
                if item["state"] not in ("next", "working", "done", "blocked"):
                    raise ValueError(
                        "Step state must be next, working, done or blocked."
                    )
        elif v != "":
            clean(v)
    with lock(a):
        p = folder(a) / "report.json"
        r = read(p, {})
        r.update(data)
        save(p, r)
    return {"id": a}


def artifact(data):
    a = address(os.environ.get("HC_TERMINAL_ID"))
    path = Path(clean(data["path"])).expanduser()
    kind = path.suffix.lower()
    if kind not in (".html", ".png", ".diff", ".patch", ".json"):
        raise ValueError("Use HTML, PNG, a git patch, or a comparison JSON file.")
    title = clean(data["title"], 160)
    with path.open("rb") as f:
        content = f.read(4_000_001)
    if len(content) > 4_000_000:
        raise ValueError("Artifact exceeds 4 MB.")
    if kind == ".json":
        rows = json.loads(content)
        if not isinstance(rows, list) or not rows or len(rows) > 2000:
            raise ValueError("Use a list of up to 2000 cases.")
        for row in rows:
            for k in ("case", "before", "after"):
                clean(row[k], 16000)
            if row.get("source"):
                clean(row["source"], 16000)
    elif kind == ".png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid PNG.")
    else:
        content.decode("utf-8")
    key = hashlib.sha256(content).hexdigest() + kind
    with lock(a):
        dest = folder(a) / "artifacts" / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        p = folder(a) / "report.json"
        r = read(p, {})
        items = [v for v in r.get("artifacts", []) if v["key"] != key]
        r["artifacts"] = [{"key": key, "title": title}, *items]
        save(p, r)
    return {"id": a, "key": key}


def preview(p):
    a = address(p["id"])
    key = p["key"]
    if not re.fullmatch(r"[0-9a-f]{64}\.(html|png|patch|diff|json)", key):
        raise ValueError("Unknown artifact.")
    path = folder(a) / "artifacts" / key
    if not path.is_file():
        raise ValueError("This evidence file is unavailable.")
    index = max(0, int(p.get("index", 0)))
    if key.endswith(".json"):
        rows = json.loads(path.read_bytes())
        index = min(index, len(rows) - 1)
        return {"type": "case", "index": index, "count": len(rows), "row": rows[index]}
    if key.endswith(".png"):
        # GPUI resolves image paths relative to the app home.
        dest = APP_HOME / "data/team-desk-images" / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            with lock("image-" + key):
                if not dest.is_file():
                    with tempfile.NamedTemporaryFile(
                        dir=dest.parent, delete=False
                    ) as f:
                        f.write(path.read_bytes())
                    os.replace(f.name, dest)
        return {"type": "image", "path": str(dest.relative_to(APP_HOME))}
    if key.endswith(".html"):
        if p.get("open"):
            offline = """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'">"""
            frame = html.escape(offline + path.read_text(), quote=True)
            wrapper = path.with_suffix(".preview.html")
            wrapper.write_text(
                """<!doctype html><meta charset=utf-8><title>Team Desk preview</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src about:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'">
<style>html,body,iframe{margin:0;width:100%;height:100%;border:0;display:block}</style>
<iframe sandbox="allow-scripts" title="Interactive evidence" srcdoc="""
                + '"'
                + frame
                + '"></iframe>'
            )
            command(["open", str(wrapper)])
        return {"type": "html"}
    lines = path.read_text().splitlines()
    pages = max(1, (len(lines) + 59) // 60)
    index = min(index, pages - 1)
    return {
        "type": "diff",
        "index": index,
        "count": pages,
        "lines": [line[:2000] for line in lines[index * 60 : index * 60 + 60]],
    }


def limits(_):
    helper = APP_HOME / "bin/claude-usage"
    if not helper.is_file():
        return {"claude": [], "codex": []}
    usage = json.loads(command([str(helper), "--json"], timeout=65))
    return {
        k: [
            (label, row.get("percent"))
            for label, field in (
                ("5 hours", "five_hour"),
                ("Week", "seven_day" if k == "claude" else "weekly"),
            )
            for row in [(usage.get(k) or {}).get(field)]
            if isinstance(row, dict) and isinstance(row.get("percent"), (int, float))
        ]
        for k in ("claude", "codex")
    }


def peek(p):
    a = address(p["id"])
    command([HERDR, "do", "terminal.peek", json.dumps({"terminal": a})])
    return {"id": a}


def dialog(p):
    a = address(p["id"])
    t = live(a)
    if t["status"]["state"] != "blocked":
        raise ValueError("This dialog has already closed.")
    screen = command([HC, "read", a]).rstrip()
    identity = [a, t["status_ms"], t["turn"], screen]
    return {
        "screen": screen,
        "revision": hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
    }


def dialog_key(p):
    a, key = address(p["id"]), p["key"]
    if key not in ("up", "down", "enter", "esc", "space", "tab"):
        raise ValueError("Unsupported dialog key.")
    if dialog(p)["revision"] != p["revision"]:
        raise ValueError("The dialog changed. Read it again before answering.")
    return hc("keys", a, key)


def dispatch(action, p):
    if action == "status":
        return status(p)
    if action == "roster":
        rows = status({})["agents"]
        return [
            {
                k: t.get(k)
                for k in ("id", "title", "ticket", "state", "parent", "tokens", "ask")
            }
            for t in rows
            if t["state"] != "finished"
        ]
    if action == "report":
        return read(folder(p["id"]) / "report.json", {})
    if action == "tickets":
        return tickets()
    if action == "master":
        text = p["text"]
        if not p.get("reply") and ticket_id(text):
            return {**launch("worker", text), "delegated": True}
        return launch("coordinator", text)
    if action == "start":
        return launch(
            "worker", p.get("text", ""), p.get("ticket", ""), p.get("provider")
        )
    if action == "send":
        return message(address(p["id"]), p["text"])
    if action == "brief":
        a = address(p["id"])
        guide = (HERE / "worker.txt").read_text().split("Early in the task,", 1)[1]
        prompt = (
            "Summarize this existing task for Team Desk using its actual current state. For this update only, "
            + guide.replace("{desk}", worker_command())
        )
        return message(
            a, prompt, "Show the problem, one example, and the proposed fix."
        )
    if action == "stop":
        a = address(p["id"])
        live(a)
        return hc("interrupt", a)
    if action == "peek":
        return peek(p)
    if action == "dialog":
        return dialog(p)
    if action == "dialogKey":
        return dialog_key(p)
    if action == "openPR":
        url = p["url"]
        if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", url):
            raise ValueError("Unknown pull request URL.")
        command(["open", url])
        return {}
    if action == "chat":
        return read(folder(p["id"]) / "chat.json", [])
    if action == "publish":
        return publish(p)
    if action == "artifact":
        return artifact(p)
    if action == "preview":
        return preview(p)
    if action == "limits":
        return limits(p)
    raise ValueError("Unknown Team Desk command.")


if __name__ == "__main__":
    try:
        if not in_dev_profile():
            raise ValueError("Open Team Desk Dev to use its isolated agent core.")
        if sys.argv[1] == "board":
            ticket = sys.argv[2]
            with lock("board"):
                if len(sys.argv) > 3:
                    result = {"id": live(sys.argv[3])["address"]}
                else:
                    result = launch("worker", ticket=ticket)
                save(ROOT / "focus.json", {"id": result["id"], "time": time.time()})
                command(
                    [HERDR, "do", "layout.switch", json.dumps({"name": "team-desk"})]
                )
        elif sys.argv[1] == "ui":
            for key in ("HC_TERMINAL_ID", "HC_SCOPE", "HC_TERMINAL_SOCKET"):
                os.environ.pop(key, None)
            action = sys.argv[2]
            result = dispatch(action, json.loads(sys.argv[3]))
            if action == "status":
                result.pop("tickets", None)
        else:
            result = dispatch(
                sys.argv[1], json.load(sys.stdin) if not sys.stdin.isatty() else {}
            )
        print(json.dumps(result))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        subprocess.TimeoutExpired,
    ) as exc:
        sys.exit(str(exc))
