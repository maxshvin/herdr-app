import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "defaults/mods/team-desk/runtime")
)

import team
import telemetry


class DeskTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.terms = {}
        self.calls = []
        for override in (
            patch.object(team, "ROOT", self.root),
            patch.object(team, "APP_HOME", self.root / "app"),
            patch.object(team, "SHARED", self.root / "board.json"),
            patch.object(team, "command", self.command),
            patch.dict(os.environ, {"HC_TERMINAL_ID": ""}),
        ):
            override.start()
            self.addCleanup(override.stop)
        team.save(
            team.SHARED,
            {"agent-board.tickets": {"EX-1": {"t": "One example", "y": "started"}}},
        )

    def command(self, argv, timeout=30):
        self.calls.append(argv)
        _, action, *args = argv
        if action == "ls":
            terms = list(self.terms.values())
            if "--label" in args:
                key, value = args[args.index("--label") + 1].split("=", 1)
                terms = [t for t in terms if t["labels"].get(key) == value]
            return json.dumps({"terminals": terms})
        if action == "run":
            labels = dict(
                args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label"
            )
            agent = args[args.index("--") + 1]
            addr = "t" + str(len(self.terms) + 1)
            term = {
                "address": addr,
                "state": "live",
                "agent": agent,
                "status": {"app": agent, "state": "idle"},
                "labels": labels,
                "parent": args[args.index("--parent") + 1]
                if "--parent" in args
                else None,
                "turn": 1,
                "status_ms": 15,
            }
            self.terms[addr] = term
            return json.dumps(term)
        if action == "get":
            return json.dumps({"terminal": self.terms[args[0]]})
        if action == "read":
            return "Choose a plan\n> First\n  Second"
        if action in ("send", "interrupt", "keys"):
            return json.dumps({"outcome": "stopped"})
        self.fail("Unexpected external action: " + action)

    def test_delegate_review_and_reconnect(self):
        master = team.launch("coordinator", "Help manage the team")["id"]
        team.launch("coordinator", "What is blocked?")
        self.assertEqual(len(self.terms), 1)
        first = team.launch("worker", "Work on this", "EX-1")["id"]
        self.assertEqual(
            team.launch("worker", "Please work on this", "EX-1"),
            {"id": first, "existing": True},
        )
        self.assertEqual(self.terms[first]["parent"], master)
        self.assertIn(
            "--no-daemon", next(c for c in self.calls if c[1] == "run" and "codex" in c)
        )
        with patch.dict(os.environ, {"HC_TERMINAL_ID": first}):
            team.publish(
                {
                    "problem": "Retries stop too early",
                    "example": "Case one",
                    "solution": "Retry once",
                    "steps": [
                        {
                            "owner": "Reviewer",
                            "name": "Review the patch",
                            "state": "next",
                        }
                    ],
                }
            )
            cases = self.root / "cases.json"
            cases.write_text('[{"case":"One","before":"lost","after":"retried"}]')
            artifact = team.artifact({"path": str(cases), "title": "Compare"})
            self.assertEqual(
                team.preview(artifact)["row"], json.loads(cases.read_text())[0]
            )
            team.publish({"review": "One case verified"})
        worker = next(t for t in team.status({})["agents"] if t["id"] == first)
        self.assertEqual(worker["report"]["artifacts"][0]["title"], "Compare")
        self.assertIn("steps", worker["report"])
        team.message(first, "Keep the change in case One")
        self.assertEqual(team.dispatch("chat", {"id": first})[-1]["who"], "You")
        self.assertEqual(team.dispatch("stop", {"id": first})["outcome"], "stopped")
        del self.terms[first]
        self.assertTrue(
            any(
                t["id"] == first and t["state"] == "finished"
                for t in team.status({})["agents"]
            )
        )

    def test_bare_ticket_uses_the_same_worker_from_every_entry_point(self):
        link = "https://linear.app/example/issue/EX-1/fix-retries?from=board#details"
        first = team.dispatch("master", {"text": " ex-1 "})
        self.assertTrue(first["delegated"])
        for action, payload in [
            ("master", {"text": link}),
            ("start", {"ticket": link}),
            ("start", {"text": "EX-1"}),
        ]:
            result = team.dispatch(action, payload)
            self.assertTrue(result["existing"])
            self.assertEqual(result["id"], first["id"])
        runs = [c for c in self.calls if c[1] == "run"]
        self.assertEqual(len(runs), 1)
        self.assertIn("independent review", runs[0][-1])
        self.assertEqual(self.terms[first["id"]]["labels"]["linear.issue"], "EX-1")

    def test_native_evidence_pagination(self):
        with patch.dict(os.environ, {"HC_TERMINAL_ID": "t1"}):
            diff = self.root / "change.diff"
            diff.write_text("\n".join(f"+{i}" for i in range(61)))
            ref = team.artifact({"title": "Patch", "path": str(diff)})
            last = team.preview({**ref, "index": 5})
            self.assertEqual((last["index"], last["lines"]), (1, ["+60"]))
            html = self.root / "result.html"
            html.write_text("<button>Try it</button>")
            ref = team.artifact({"title": "Preview", "path": str(html)})
            with patch.object(team, "command") as open_file:
                self.assertEqual(team.preview({**ref, "open": True})["type"], "html")
                wrapper = Path(open_file.call_args.args[0][1]).read_text()
            self.assertIn('sandbox="allow-scripts"', wrapper)
            self.assertIn("&lt;button&gt;", wrapper)
            self.assertNotIn("allow-same-origin", wrapper)

    @unittest.skipUnless(shutil.which("herdr-app"), "requires Herdr's native renderer")
    def test_workspace_views_render(self):
        mod = self.root / "render"
        mod.mkdir()
        shutil.copyfile(team.HERE.parent / "main.js", mod / "desk.js")
        shutil.copyfile(team.HERE.parent / "mod.json", mod / "mod.json")
        agent = {
            "id": "t1",
            "title": "Review retry behavior",
            "provider": "codex",
            "state": "idle",
            "tokens": 30000,
            "ticket": "EX-1",
            "role": "worker",
            "report": {
                "problem": "A retry failed",
                "example": "Case A",
                "solution": "Retry once",
                "steps": [
                    {"name": "Review patch", "state": "working", "owner": "Reviewer"}
                ],
                "artifacts": [{"title": "Review", "key": "sample.json"}],
                "review": "Checked",
            },
        }
        previews = [
            {
                "type": "case",
                "index": 0,
                "count": 2,
                "row": {"case": "A", "before": "Once", "after": "Twice"},
            },
            {"type": "diff", "index": 0, "count": 1, "lines": ["+retry"]},
            {"type": "image", "path": "frame/welcome/herdr.png"},
            {"type": "html"},
        ]
        scenarios = [
            ("overview", None),
            ("master", None),
            ("usage", None),
            ("tickets", None),
        ]
        scenarios += [("task", sample) for sample in previews]
        for page, preview in scenarios:
            with self.subTest(page=page, preview=preview):
                source = """import Desk from './desk.js';
export default class Scenario extends Desk {
  init(props,cx){super.init(props,cx);
    this.page=PAGE;this.data={agents:[AGENT],watch:25000};this.id="t1";this.loaded=true;
    this.chat=[{who:"Agent",text:"Reviewed"}];this.tickets={"EX-1":"Retry behavior"};
    this.limits={codex:[["5 hours",75]]};
    this.evidence={item:{title:"Review",key:"sample.json"},data:PREVIEW};}
}"""
                (mod / "main.js").write_text(
                    source.replace("PAGE", json.dumps(page))
                    .replace("AGENT", json.dumps(agent))
                    .replace("PREVIEW", json.dumps(preview))
                )
                result = subprocess.run(
                    ["herdr-app", "check", str(mod)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("mod exec'd", result.stderr)

    def test_ticket_in_a_question_or_reply_stays_in_master_chat(self):
        first = team.dispatch("master", {"text": "Explain EX-1 only."})
        reply = team.dispatch("master", {"text": "EX-1", "reply": True})
        self.assertEqual(reply["id"], first["id"])
        self.assertEqual(self.terms[first["id"]]["labels"]["team.role"], "coordinator")
        self.assertEqual(len(self.terms), 1)

    def test_dialog_rejects_old_selection(self):
        a = team.launch("worker", "Do the work")["id"]
        self.terms[a]["status"]["state"] = "blocked"
        revision = team.dialog({"id": a})["revision"]
        self.terms[a]["turn"] += 1
        with self.assertRaisesRegex(ValueError, "changed"):
            team.dialog_key({"id": a, "key": "enter", "revision": revision})
        self.assertFalse(any(c[1] == "keys" for c in self.calls))

    def test_session_usage_handles_streaming_duplicates(self):
        f = self.root / "session.jsonl"
        line = lambda n: (
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"id": "one", "usage": {"output_tokens": n}},
                }
            )
            + "\n"
        )
        f.write_text(line(10) + line(25))
        cache = {}
        self.assertEqual(telemetry.count(f, "claude", cache), 25)
        with f.open("a") as stream:
            stream.write(line(35)[:-1])
        self.assertEqual(telemetry.count(f, "claude", cache), 25)
        with f.open("a") as stream:
            stream.write("\n")
        self.assertEqual(telemetry.count(f, "claude", cache), 35)
        cf = self.root / "codex.jsonl"

        def codex(n):
            return (
                json.dumps(
                    {
                        "payload": {
                            "type": "token_count",
                            "info": {"total_token_usage": {"output_tokens": n}},
                        }
                    }
                )
                + "\n"
            )

        cf.write_text(codex(100) + codex(150))
        self.assertEqual(telemetry.count(cf, "codex", {}), 150)


if __name__ == "__main__":
    unittest.main()
