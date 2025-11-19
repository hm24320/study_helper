from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, validator


DB_PATH = Path("study_assistant.db")
SCHEMA_PATH = Path("schema.sql")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    if not SCHEMA_PATH.exists():
        raise RuntimeError("schema.sql not found. Cannot initialize database.")

    conn = get_db()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


class TaskState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EXPIRED = "EXPIRED"


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class CreateTaskRequest(BaseModel):
    title: str
    verify_method: str
    due_at_iso: datetime

    @validator("title")
    def title_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title cannot be empty")
        return value

    @validator("verify_method")
    def verify_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("verify_method cannot be empty")
        return value

    @validator("due_at_iso")
    def ensure_timezone(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class VerificationAttemptRequest(BaseModel):
    proof_url: str
    verdict: bool
    score: Optional[float] = None
    reasons: Optional[str] = None
    raw_features: Optional[dict[str, Any]] = None

    @validator("proof_url")
    def proof_url_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("proof_url cannot be empty")
        return value


class TaskResponse(BaseModel):
    id: str
    title: str
    verify_method: str
    due_at_iso: str
    state: str


class TaskListResponse(BaseModel):
    items: List[TaskResponse]


class VerifyAttemptResponse(BaseModel):
    task_id: str
    state: str


app = FastAPI(
    title="Study Assistant Tasks API",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def expire_overdue_tasks(conn: sqlite3.Connection) -> None:
    now = ensure_utc(datetime.utcnow())
    conn.execute(
        """
        UPDATE tasks
        SET state = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE state = ?
          AND due_at <= ?
        """,
        (TaskState.EXPIRED, TaskState.PENDING, now.isoformat()),
    )
    conn.commit()


def fetch_tasks(
    conn: sqlite3.Connection,
    state: Optional[TaskState],
    q: Optional[str],
    due_before: Optional[datetime],
    due_after: Optional[datetime],
) -> Iterable[sqlite3.Row]:
    query = ["SELECT id, title, verify_method, due_at, state FROM tasks WHERE 1=1"]
    params: List[Any] = []

    if state:
        query.append("AND state = ?")
        params.append(state.value)

    if q:
        query.append("AND title LIKE ?")
        params.append(f"%{q}%")

    if due_before:
        query.append("AND due_at < ?")
        params.append(ensure_utc(due_before).isoformat())

    if due_after:
        query.append("AND due_at > ?")
        params.append(ensure_utc(due_after).isoformat())

    query.append("ORDER BY due_at ASC")

    return conn.execute("\n".join(query), params)


@app.post("/v1/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(payload: CreateTaskRequest, conn: sqlite3.Connection = Depends(get_db)) -> TaskResponse:
    task_id = str(uuid.uuid4())
    due_at = payload.due_at_iso.isoformat()
    conn.execute(
        """
        INSERT INTO tasks (id, title, verify_method, due_at, state)
        VALUES (?, ?, ?, ?, ?)
        """,
        (task_id, payload.title.strip(), payload.verify_method.strip(), due_at, TaskState.PENDING),
    )
    conn.commit()
    conn.close()
    return TaskResponse(
        id=task_id,
        title=payload.title.strip(),
        verify_method=payload.verify_method.strip(),
        due_at_iso=ensure_utc(payload.due_at_iso).isoformat().replace("+00:00", "Z"),
        state=TaskState.PENDING,
    )


@app.get("/v1/tasks", response_model=TaskListResponse)
def list_tasks(
    state: Optional[TaskState] = Query(None),
    q: Optional[str] = Query(None),
    due_before: Optional[datetime] = Query(None),
    due_after: Optional[datetime] = Query(None),
    conn: sqlite3.Connection = Depends(get_db),
) -> TaskListResponse:
    expire_overdue_tasks(conn)
    rows = fetch_tasks(conn, state, q, due_before, due_after)
    items = [
        TaskResponse(
            id=row["id"],
            title=row["title"],
            verify_method=row["verify_method"],
            due_at_iso=ensure_utc(datetime.fromisoformat(row["due_at"])).isoformat().replace("+00:00", "Z"),
            state=row["state"],
        )
        for row in rows
    ]
    conn.close()
    return TaskListResponse(items=items)


@app.post("/v1/tasks/{task_id}/verify-attempt", response_model=VerifyAttemptResponse)
def verify_task(
    task_id: str,
    payload: VerificationAttemptRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> VerifyAttemptResponse:
    task = conn.execute("SELECT id, state FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    attempt_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO verification_attempts (
            id,
            task_id,
            proof_url,
            verdict,
            score,
            reasons,
            raw_features
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            task_id,
            payload.proof_url.strip(),
            int(payload.verdict),
            payload.score,
            payload.reasons,
            json.dumps(payload.raw_features) if payload.raw_features is not None else None,
        ),
    )

    new_state = task["state"]
    if payload.verdict:
        conn.execute(
            """
            UPDATE tasks
            SET state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (TaskState.APPROVED, task_id),
        )
        new_state = TaskState.APPROVED

    conn.commit()
    conn.close()

    return VerifyAttemptResponse(task_id=task_id, state=new_state)
