import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock


SPEC = importlib.util.spec_from_file_location("agent_board", Path(__file__).parents[1] / "scripts" / "agent_board.py")
board = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(board)


def comment(number, sender="frontend", recipient="backend", timestamp="2026-11-13T10:00:00Z"):
    metadata = json.dumps({"from": sender, "to": recipient}, separators=(",", ":"))
    return {"id": number, "body": board.MARKER + metadata + " -->\nHello",
            "user": {"login": "real-github-user"}, "html_url": f"https://github.com/example/{number}",
            "created_at": timestamp, "updated_at": timestamp}


class FakeClient:
    def __init__(self, comments, fail=False):
        self.items = comments
        self.fail = fail
        self.since = None

    def comments(self, since):
        self.since = since
        yield from self.items
        if self.fail:
            raise RuntimeError("second page failed")


class AgentBoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "inbox.sqlite3"
        self.db = board.connect(self.path)
        self.addCleanup(self.db.close)

    def test_routes_deduplicates_and_persists_acknowledgements(self):
        data = [comment(1), comment(2, recipient="all"), comment(3, recipient="robotics"),
                comment(4, sender="backend", recipient="backend")]
        client = FakeClient(data)
        self.assertEqual(board.poll_once(self.db, client, "backend"), 2)
        self.assertEqual(board.poll_once(self.db, client, "backend"), 0)
        self.assertEqual(client.since, "2026-11-13T09:59:59Z")
        messages = board.inbox(self.db, "backend")
        self.assertEqual([m["id"] for m in messages], [1, 2])
        self.assertEqual(messages[0]["author"], "real-github-user")
        self.db.execute("UPDATE inbox SET acknowledged=1 WHERE agent=? AND comment_id=?", ("backend", 1))
        self.db.commit()
        with contextlib.closing(board.connect(self.path)) as reopened:
            self.assertEqual([m["id"] for m in board.inbox(reopened, "backend")], [2])
            self.assertEqual(len(board.inbox(reopened, "backend", True)), 2)
            self.assertEqual(board.poll_once(reopened, client, "robotics"), 2)

    def test_failed_pagination_never_advances_cursor_or_writes_messages(self):
        with self.assertRaisesRegex(RuntimeError, "second page"):
            board.poll_once(self.db, FakeClient([comment(1)], fail=True), "backend")
        self.assertEqual(board.inbox(self.db, "backend"), [])
        self.assertIsNone(self.db.execute("SELECT updated_at FROM cursors").fetchone())
        self.assertEqual(board.poll_once(self.db, FakeClient([comment(1)]), "backend"), 1)

    def test_rejects_unstructured_or_malformed_messages(self):
        for text in ("ordinary comment", board.MARKER + "broken -->\nhello",
                     board.MARKER + '{"from":"x","to":42} -->\nhello',
                     board.MARKER + '{"from":"all","to":"backend"} -->\nhello'):
            invalid = comment(1)
            invalid["body"] = text
            self.assertIsNone(board.parse_message(invalid))

    def test_multiple_pages_and_time_overlap(self):
        pages = [[comment(n) for n in range(1, 101)], [comment(101)]]
        client = board.GitHub()
        with mock.patch.object(client, "request", side_effect=pages) as request:
            self.assertEqual(len(list(client.comments("2026-11-13T09:59:59Z"))), 101)
            self.assertEqual(request.call_args_list[1].kwargs["query"]["page"], 2)
        self.assertEqual(board.overlap("2026-11-13T10:00:00Z"), "2026-11-13T09:59:59Z")

    def test_send_requires_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "needs GH_TOKEN"):
            board.GitHub().send("frontend", "backend", "hello")

    def test_setup_persists_agent_id_and_environment_can_override(self):
        path = Path(self.tmp.name) / "settings" / "agent-id"
        with mock.patch.dict(os.environ, {"AGENT_BOARD_ID_FILE": str(path)}, clear=True):
            self.assertEqual(board.main(["--agent", "FrontEnd", "setup"]), 0)
            self.assertEqual(board.configured_agent(), "frontend")
            with mock.patch.dict(os.environ, {"AGENT_BOARD_ID": "robotics"}):
                self.assertEqual(board.configured_agent(), "robotics")

    def test_hook_notifies_without_injecting_untrusted_message_body(self):
        client = FakeClient([comment(1)])
        board.poll_once(self.db, client, "backend")
        script = Path(__file__).parents[1] / "scripts" / "agent_board_hook.py"
        env = dict(os.environ, AGENT_BOARD_ID="backend", AGENT_BOARD_DB=str(self.path))
        for app, event in (("codex", "UserPromptSubmit"), ("claude", "PostToolUse"),
                           ("cursor", "postToolUse")):
            result = subprocess.run([sys.executable, str(script), app], env=env,
                                    input=json.dumps({"hook_event_name": event}),
                                    text=True, capture_output=True, check=True)
            output = json.loads(result.stdout)
            notice = output["additional_context"] if app == "cursor" else output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("1 unread", notice)
            self.assertNotIn("Hello", notice)
        agent_file = Path(self.tmp.name) / "agent-id"
        agent_file.write_text("backend\n")
        env.pop("AGENT_BOARD_ID")
        env["AGENT_BOARD_ID_FILE"] = str(agent_file)
        result = subprocess.run([sys.executable, str(script), "codex"], env=env,
                                input='{"hook_event_name":"SessionStart"}',
                                text=True, capture_output=True, check=True)
        self.assertIn("1 unread", json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_http_send_poll_and_acknowledge_across_two_agent_inboxes(self):
        class Handler(BaseHTTPRequestHandler):
            comments = []

            def log_message(self, *args):
                pass

            def respond(self, value):
                data = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                entry = comment(101)
                entry["body"] = payload["body"]
                self.comments.append(entry)
                self.respond(entry)

            def do_GET(self):
                self.respond(self.comments)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.object(board, "API", f"http://127.0.0.1:{server.server_port}"):
            github = board.GitHub("test-token")
            github.send("frontend", "backend", "Ready for integration")
            self.assertEqual(board.poll_once(self.db, github, "backend"), 1)
            self.assertEqual(board.poll_once(self.db, github, "robotics"), 0)
            self.assertEqual(board.inbox(self.db, "backend")[0]["body"], "Ready for integration")
            self.assertEqual(board.main(["--db", str(self.path), "--agent", "backend", "ack", "101"]), 0)
            self.assertEqual(board.inbox(self.db, "backend"), [])


if __name__ == "__main__":
    unittest.main()
