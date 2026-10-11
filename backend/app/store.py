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
        self.conn.execute("""CREATE TABLE IF NOT EXISTS sim_numbers (
            sim_id TEXT PRIMARY KEY, phone TEXT NOT NULL, confirmed_at REAL NOT NULL)""")
        self.conn.execute(
            "UPDATE calls SET state='unknown',reason='service_restarted',ended_at=? "
            "WHERE state IN ('dialing','incoming','agent_connecting','answering',"
            "'connected','streaming',"
            "'hanging_up')",
            (time.time(),),
        )
        self.conn.commit()
        self.mark_unknown("service_restarted")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS sms_pushes (
            message_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            error TEXT, updated_at REAL NOT NULL)""")
        self.conn.execute(
            "UPDATE sms_pushes SET state='unknown',error='服务重启，未自动重试',updated_at=? "
            "WHERE state IN ('pending','sending')",
            (time.time(),),
        )
        self.conn.execute("""CREATE TABLE IF NOT EXISTS dingtalk_replies (
            request_id TEXT PRIMARY KEY, state TEXT NOT NULL, reference TEXT,
            error TEXT, updated_at REAL NOT NULL)""")
        self.conn.execute(
            "UPDATE dingtalk_replies SET state='unknown',error='服务重启，未自动重试',updated_at=? "
            "WHERE state IN ('claimed','sending','queued')",
            (time.time(),),
        )
        self.conn.commit()

    def reply_claim(self, request_id):
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO dingtalk_replies VALUES (?, 'claimed', NULL, NULL, ?)",
            (request_id, time.time()),
        )
        self.conn.commit()
        return bool(cursor.rowcount)

    def reply_update(self, request_id, state, reference=None, error=None):
        self.conn.execute(
            "UPDATE dingtalk_replies SET state=?,reference=COALESCE(?,reference),"
            "error=?,updated_at=? "
            "WHERE request_id=?",
            (state, reference, error, time.time(), request_id),
        )
        self.conn.commit()

    def reply_list(self):
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM dingtalk_replies ORDER BY updated_at DESC LIMIT 20"
            )
        ]

    def reply_source(self, reference):
        rows = self.conn.execute(
            "SELECT m.* FROM messages m JOIN sms_pushes p ON p.message_id=m.id "
            "WHERE m.direction='incoming' AND p.state='sent' AND substr(m.id,4,16)=? LIMIT 2",
            (reference,),
        ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    def sim_number(self, sim_id):
        row = self.conn.execute(
            "SELECT phone,confirmed_at FROM sim_numbers WHERE sim_id=?", (sim_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_sim_number(self, sim_id, phone):
        self.conn.execute(
            "INSERT INTO sim_numbers VALUES (?,?,?) ON CONFLICT(sim_id) "
            "DO UPDATE SET phone=excluded.phone,confirmed_at=excluded.confirmed_at",
            (sim_id, phone, time.time()),
        )
        self.conn.commit()

    def clear_sim_number(self, sim_id):
        self.conn.execute("DELETE FROM sim_numbers WHERE sim_id=?", (sim_id,))
        self.conn.commit()

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
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO messages VALUES (?, 'incoming', ?, ?, ?, 'received', NULL, ?)",
            (key, phone, content, timestamp, sim_id),
        )
        self.conn.commit()
        return self.get(key) if cursor.rowcount else None

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

    def push_claim(self, message_id):
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO sms_pushes VALUES (?, 'pending', NULL, ?)",
            (message_id, time.time()),
        )
        self.conn.commit()
        return bool(cursor.rowcount)

    def push_update(self, message_id, state, error=None):
        self.conn.execute(
            "UPDATE sms_pushes SET state=?,error=?,updated_at=? WHERE message_id=?",
            (state, error, time.time(), message_id),
        )
        self.conn.commit()

    def push_list(self):
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT message_id,state,error,updated_at FROM sms_pushes "
                "ORDER BY updated_at DESC LIMIT 20"
            )
        ]

    def close(self):
        self.conn.close()
