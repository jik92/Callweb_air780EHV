"""Persist SMS history and request IDs before any external send."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.recordings = path.parent / "calls"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, direction TEXT NOT NULL, phone TEXT NOT NULL,
            content TEXT NOT NULL, timestamp REAL NOT NULL, state TEXT NOT NULL,
            error TEXT, sim_id TEXT NOT NULL DEFAULT '')""")
        self.conn.commit()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS calls (
            id TEXT PRIMARY KEY, phone TEXT NOT NULL, started_at REAL NOT NULL,
            connected_at REAL, ended_at REAL, state TEXT NOT NULL,
            reason TEXT, sample_rate INTEGER, recording_error TEXT)""")
        self.conn.execute(
            "UPDATE calls SET state='unknown',reason='service_restarted',ended_at=? "
            "WHERE state IN ('dialing','connected','streaming','hanging_up')",
            (time.time(),),
        )
        self.conn.commit()
        self.mark_unknown("service_restarted")

    def call_get(self, call_id):
        row = self.conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        return dict(row) if row else None

    def call_prepare(self, call_id, phone):
        self.conn.execute(
            "INSERT INTO calls (id,phone,started_at,state) VALUES (?,?,?,'dialing')",
            (call_id, phone, time.time()),
        )
        self.conn.commit()

    def call_update(self, state):
        self.conn.execute(
            "UPDATE calls SET state=?,reason=?,connected_at=COALESCE(connected_at,?),"
            "ended_at=?,sample_rate=?,recording_error=? WHERE id=?",
            (
                state["state"],
                state.get("reason"),
                state.get("connected_at"),
                state.get("ended_at"),
                state.get("sample_rate"),
                state.get("recording_error"),
                state.get("call_id"),
            ),
        )
        self.conn.commit()

    def call_list(self):
        rows = [
            dict(row)
            for row in self.conn.execute("SELECT * FROM calls ORDER BY started_at DESC LIMIT 500")
        ]
        for row in rows:
            directory = self.recordings / row["id"]
            row["recording_available"] = row["state"] in ("ended", "failed", "unknown") and any(
                p.exists() and p.stat().st_size > 44
                for p in (directory / "up.wav", directory / "down.wav")
            )
            row["duration"] = (
                max(0, (row["ended_at"] or time.time()) - row["connected_at"])
                if row["connected_at"]
                else 0
            )
        return rows

    def list(self) -> list[dict]:
        return [
            dict(row)
            for row in self.conn.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT 500")
        ]

    def get(self, request_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM messages WHERE id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def incoming(self, message: dict, sim_id: str):
        phone, content = message.get("from"), message.get("content")
        if not isinstance(phone, str) or not isinstance(content, str):
            return
        timestamp = message.get("received_at") or time.time()
        identity = json.dumps([sim_id, phone, content, timestamp], ensure_ascii=False)
        key = "rx-" + hashlib.sha256(identity.encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO messages VALUES (?, 'incoming', ?, ?, ?, 'received', NULL, ?)",
            (key, phone, content, timestamp, sim_id),
        )
        self.conn.commit()

    def prepare(self, request_id: str, phone: str, content: str, sim_id: str) -> dict:
        self.conn.execute(
            "INSERT INTO messages VALUES (?, 'outgoing', ?, ?, ?, 'queued', NULL, ?)",
            (request_id, phone, content, time.time(), sim_id),
        )
        self.conn.commit()
        return self.get(request_id)

    def update(self, request_id: str, state: str, error: str | None = None):
        self.conn.execute(
            "UPDATE messages SET state=?,error=? WHERE id=? AND direction='outgoing'",
            (state, error, request_id),
        )
        self.conn.commit()

    def mark_unknown(self, error: str):
        self.conn.execute(
            "UPDATE messages SET state='unknown',error=? WHERE state IN ('queued','sending')",
            (error,),
        )
        self.conn.commit()

    def expire(self):
        self.conn.execute(
            "UPDATE messages SET state='unknown',error='result_timeout' "
            "WHERE state IN ('queued','sending') AND timestamp < ?",
            (time.time() - 90,),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
