#!/usr/bin/env python3
"""A small GitHub Issues mailbox for agents on different machines."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "Vrun1506/Cache-Flow"
ISSUE = 1
API = "https://api.github.com"
MARKER = "<!-- cache-flow-agent-board:v1 "
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
DEFAULT_DB = Path.home() / ".cache" / "cache-flow-agent-board" / "inbox.sqlite3"
DEFAULT_ID_FILE = Path.home() / ".config" / "cache-flow-agent-board" / "agent-id"


def agent_id(value):
    if not value or not ID_RE.fullmatch(value) or value.lower() == "all":
        raise ValueError("agent ID must be 1–64 letters, digits, '.', '_' or '-' (not 'all')")
    return value.lower()


def configured_agent():
    value = os.environ.get("AGENT_BOARD_ID")
    if value:
        return value
    path = Path(os.environ.get("AGENT_BOARD_ID_FILE", str(DEFAULT_ID_FILE))).expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def github_token():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"],
                                text=True, capture_output=True, timeout=5, check=True)
        return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


class GitHub:
    def __init__(self, token=None):
        self.token = token

    def request(self, path, *, query=None, payload=None):
        url = API + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "cache-flow-agent-board",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if payload is not None:
            if not self.token:
                raise RuntimeError("Sending needs GH_TOKEN, GITHUB_TOKEN, or gh auth login.")
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub returned HTTP {exc.code}; check access and rate limits.") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"GitHub request failed: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}") from None

    def comments(self, since):
        page = 1
        while True:
            query = {"per_page": 100, "page": page}
            if since:
                query["since"] = since
            result = self.request(f"/repos/{REPO}/issues/{ISSUE}/comments", query=query)
            if not isinstance(result, list):
                raise RuntimeError("Unexpected GitHub comments response")
            yield from result
            if len(result) < 100:
                return
            page += 1

    def send(self, sender, recipient, message):
        metadata = {"from": sender, "to": recipient}
        body = MARKER + json.dumps(metadata, separators=(",", ":")) + " -->\n" + message
        return self.request(f"/repos/{REPO}/issues/{ISSUE}/comments", payload={"body": body})


def parse_message(comment):
    body = comment.get("body") or ""
    first, separator, message = body.partition("\n")
    if not separator or not first.startswith(MARKER) or not first.endswith(" -->"):
        return None
    try:
        meta = json.loads(first[len(MARKER):-4])
        sender = agent_id(meta["from"])
        recipient = meta["to"].lower()
        if recipient != "all":
            recipient = agent_id(recipient)
        if not message.strip() or len(message) > 12000:
            return None
        return {"id": int(comment["id"]), "from": sender, "to": recipient,
                "author": comment["user"]["login"], "url": comment["html_url"],
                "created_at": comment["created_at"], "body": message}
    except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
        return None


def connect(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cursors (agent TEXT PRIMARY KEY, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox (
            agent TEXT NOT NULL, comment_id INTEGER NOT NULL, message TEXT NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (agent, comment_id)
        );
    """)
    return db


def overlap(timestamp):
    if not timestamp:
        return None
    value = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")) - dt.timedelta(seconds=1)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def poll_once(db, client, agent):
    row = db.execute("SELECT updated_at FROM cursors WHERE agent = ?", (agent,)).fetchone()
    watermark = row[0] if row else ""
    # Fetch every page before saving either messages or the cursor: a failed page
    # must not make messages on that page disappear on the next attempt.
    comments = list(client.comments(overlap(watermark)))
    messages = []
    newest = watermark
    for comment in comments:
        updated = comment.get("updated_at", "")
        if updated > newest:
            newest = updated
        parsed = parse_message(comment)
        if parsed and parsed["to"] in (agent, "all") and parsed["from"] != agent:
            messages.append(parsed)
    added = 0
    with db:
        for message in messages:
            result = db.execute("INSERT OR IGNORE INTO inbox (agent, comment_id, message) VALUES (?, ?, ?)",
                                (agent, message["id"], json.dumps(message, ensure_ascii=False)))
            added += result.rowcount
        if newest:
            db.execute("INSERT INTO cursors (agent, updated_at) VALUES (?, ?) "
                       "ON CONFLICT(agent) DO UPDATE SET updated_at = excluded.updated_at", (agent, newest))
    return added


def inbox(db, agent, include_acknowledged=False):
    sql = "SELECT message FROM inbox WHERE agent = ?"
    if not include_acknowledged:
        sql += " AND acknowledged = 0"
    sql += " ORDER BY comment_id"
    return [json.loads(row[0]) for row in db.execute(sql, (agent,))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=configured_agent(), help="unique agent ID; overrides local setup")
    parser.add_argument("--db", default=os.environ.get("AGENT_BOARD_DB", str(DEFAULT_DB)))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="save this laptop's agent ID")
    poll = commands.add_parser("poll", help="fetch messages once or continuously")
    poll.add_argument("--watch", action="store_true")
    poll.add_argument("--interval", type=int, default=120, help="seconds between polls (minimum 15)")
    show = commands.add_parser("inbox", help="list unread messages as JSON")
    show.add_argument("--all", action="store_true", help="include acknowledged messages")
    ack = commands.add_parser("ack", help="acknowledge a message by comment ID")
    ack.add_argument("id", type=int)
    send = commands.add_parser("send", help="post a message to the public board")
    send.add_argument("--to", required=True, help="agent ID, or all")
    send.add_argument("--message", required=True, help="public message text")
    args = parser.parse_args(argv)
    try:
        agent = agent_id(args.agent)
        if args.command == "setup":
            path = Path(os.environ.get("AGENT_BOARD_ID_FILE", str(DEFAULT_ID_FILE))).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(agent + "\n", encoding="utf-8")
            print(f"Agent ID saved to {path}")
            return 0
        if args.command == "send":
            recipient = "all" if args.to.lower() == "all" else agent_id(args.to)
            if not args.message.strip() or len(args.message) > 12000:
                raise ValueError("message must contain 1–12000 characters")
            result = GitHub(github_token()).send(agent, recipient, args.message)
            print(json.dumps({"id": result["id"], "url": result["html_url"]}))
            return 0
        with connect(args.db) as db:
            if args.command == "inbox":
                print(json.dumps(inbox(db, agent, args.all), ensure_ascii=False, indent=2))
            elif args.command == "ack":
                with db:
                    result = db.execute("UPDATE inbox SET acknowledged = 1 WHERE agent = ? AND comment_id = ?",
                                        (agent, args.id))
                if not result.rowcount:
                    raise ValueError("message not found in this agent's inbox")
                print(f"Acknowledged {args.id}")
            else:
                if args.interval < 15:
                    raise ValueError("poll interval must be at least 15 seconds")
                client = GitHub(github_token())
                while True:
                    try:
                        count = poll_once(db, client, agent)
                        print(json.dumps({"new": count, "unread": len(inbox(db, agent))}), flush=True)
                    except RuntimeError as exc:
                        if not args.watch:
                            raise
                        print(f"Polling failed; retrying: {exc}", file=sys.stderr, flush=True)
                    if not args.watch:
                        break
                    time.sleep(args.interval)
        return 0
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-board: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
