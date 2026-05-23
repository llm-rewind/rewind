"""DuckDB session and step metadata store.

Single source of truth for schema. All schema changes require a migration note here.
See ADR-002 for why DuckDB over SQLite/PostgreSQL.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from rewind.constants import DB_PATH


class Session(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = "unknown"
    git_hash: str | None = None
    command: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    total_cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Step(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    parent_id: str | None = None
    order_idx: int
    type: str  # llm_call | tool_call | event
    provider: str | None = None
    model: str | None = None
    match_key: str | None = None
    req_blob: str | None = None   # SHA-256 hash
    resp_blob: str | None = None  # SHA-256 hash
    input_tok: int = 0
    output_tok: int = 0
    latency_ms: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_streaming: bool = False


_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              VARCHAR PRIMARY KEY,
    agent_name      VARCHAR NOT NULL,
    git_hash        VARCHAR,
    command         VARCHAR,
    started_at      VARCHAR NOT NULL,
    ended_at        VARCHAR,
    total_cost_usd  FLOAT DEFAULT 0.0,
    metadata        JSON DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS steps (
    id           VARCHAR PRIMARY KEY,
    session_id   VARCHAR NOT NULL REFERENCES sessions(id),
    parent_id    VARCHAR,
    order_idx    INTEGER NOT NULL,
    type         VARCHAR NOT NULL,
    provider     VARCHAR,
    model        VARCHAR,
    match_key    VARCHAR,
    req_blob     VARCHAR,
    resp_blob    VARCHAR,
    input_tok    INTEGER DEFAULT 0,
    output_tok   INTEGER DEFAULT 0,
    latency_ms   INTEGER DEFAULT 0,
    started_at   VARCHAR NOT NULL,
    is_streaming BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_steps_session ON steps(session_id, order_idx);
CREATE INDEX IF NOT EXISTS idx_steps_match_key ON steps(match_key);
"""


class RewindDB:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Use ":memory:" for tests (passed as str)
        conn_str = str(path) if str(path) != ":memory:" else ":memory:"
        self._conn = duckdb.connect(conn_str)
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(_CREATE_SCHEMA)

    @classmethod
    def get_or_create(cls, path: Path | str = DB_PATH) -> RewindDB:
        return cls(path)

    # --- Sessions ---

    @staticmethod
    def _dt_to_str(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    @staticmethod
    def _str_to_dt(s: str | None) -> datetime | None:
        if s is None:
            return None
        return datetime.fromisoformat(s)

    def save_session(self, session: Session) -> None:
        self._conn.execute(
            """
            INSERT INTO sessions
                (id, agent_name, git_hash, command, started_at, ended_at, total_cost_usd, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                ended_at = excluded.ended_at,
                total_cost_usd = excluded.total_cost_usd,
                metadata = excluded.metadata
            """,
            [
                session.id,
                session.agent_name,
                session.git_hash,
                session.command,
                self._dt_to_str(session.started_at),
                self._dt_to_str(session.ended_at),
                session.total_cost_usd,
                session.metadata,
            ],
        )

    def get_session(self, session_id: str) -> Session | None:
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ? OR id LIKE ? LIMIT 1",
            [session_id, f"{session_id}%"],
        ).fetchall()
        if not rows:
            return None
        return self._row_to_session(rows[0])

    def list_sessions(self, limit: int = 20) -> list[Session]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", [limit]
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def _row_to_session(self, row: tuple[Any, ...]) -> Session:
        return Session(
            id=row[0],
            agent_name=row[1],
            git_hash=row[2],
            command=row[3],
            started_at=self._str_to_dt(row[4]) or datetime.now(UTC),
            ended_at=self._str_to_dt(row[5]),
            total_cost_usd=row[6] or 0.0,
            metadata=json.loads(row[7]) if isinstance(row[7], str) else (row[7] or {}),
        )

    # --- Steps ---

    def save_step(self, step: Step) -> None:
        self._conn.execute(
            """
            INSERT INTO steps
                (id, session_id, parent_id, order_idx, type, provider, model,
                 match_key, req_blob, resp_blob, input_tok, output_tok,
                 latency_ms, started_at, is_streaming)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            [
                step.id,
                step.session_id,
                step.parent_id,
                step.order_idx,
                step.type,
                step.provider,
                step.model,
                step.match_key,
                step.req_blob,
                step.resp_blob,
                step.input_tok,
                step.output_tok,
                step.latency_ms,
                self._dt_to_str(step.started_at),
                step.is_streaming,
            ],
        )

    def get_steps(self, session_id: str) -> list[Step]:
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE session_id = ? ORDER BY order_idx ASC",
            [session_id],
        ).fetchall()
        return [self._row_to_step(r) for r in rows]

    def count_steps(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM steps WHERE session_id = ?", [session_id]
        ).fetchone()
        return int(row[0]) if row else 0

    def get_steps_by_match_key(self, session_id: str, match_key: str) -> list[Step]:
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE session_id = ? AND match_key = ? ORDER BY order_idx ASC",
            [session_id, match_key],
        ).fetchall()
        return [self._row_to_step(r) for r in rows]

    def _row_to_step(self, row: tuple[Any, ...]) -> Step:
        return Step(
            id=row[0],
            session_id=row[1],
            parent_id=row[2],
            order_idx=row[3],
            type=row[4],
            provider=row[5],
            model=row[6],
            match_key=row[7],
            req_blob=row[8],
            resp_blob=row[9],
            input_tok=row[10] or 0,
            output_tok=row[11] or 0,
            latency_ms=row[12] or 0,
            started_at=self._str_to_dt(row[13]) or datetime.now(UTC),
            is_streaming=bool(row[14]),
        )

    def close(self) -> None:
        self._conn.close()
