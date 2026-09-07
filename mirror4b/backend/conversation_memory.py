from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.schemas import (
    AgentWorkflowState,
    MemoryStatus,
    NormalizedFilters,
    PendingAction,
    SlotOperation,
    SlotOperationType,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DATABASE_PATH = PROJECT_ROOT / "data" / "conversations.db"
RECENT_MESSAGE_LIMIT = 24
PREFERENCE_FIELDS = {
    "color",
    "size",
    "brand",
    "material",
    "min_price",
    "max_price",
    "price_currency",
    "occasions",
    "style_preferences",
    "body_goals",
    "excluded_colors",
    "excluded_materials",
    "excluded_brands",
    "excluded_styles",
    "negative_colors",
}


@dataclass(frozen=True)
class ConversationContext:
    session_id: str
    profile_id: str
    recent_messages: list[dict[str, str]]
    working_filters: NormalizedFilters | None
    current_product_id: str | None
    selected_product_ids: list[str]
    visible_product_ids: list[str]
    pending_action: PendingAction | None
    workflow_state: AgentWorkflowState
    remembered_preferences: dict[str, str | float | list[str]]
    turn_count: int


def apply_remembered_preferences(
    filters: NormalizedFilters | None,
    preferences: dict[str, str | float | list[str]],
) -> NormalizedFilters:
    defaults = NormalizedFilters().model_dump()
    values = filters.model_dump() if filters is not None else defaults.copy()
    for field, value in preferences.items():
        if field not in PREFERENCE_FIELDS:
            continue
        current = values[field]
        if current is None or current == [] or current == defaults[field]:
            values[field] = value
    return NormalizedFilters.model_validate(values)


def connect_memory() -> sqlite3.Connection:
    MEMORY_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(MEMORY_DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS working_memory (
            session_id TEXT PRIMARY KEY,
            filters_json TEXT NOT NULL,
            current_product_id TEXT,
            selected_product_ids_json TEXT NOT NULL DEFAULT '[]',
            visible_product_ids_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS preferences (
            profile_id TEXT NOT NULL,
            field TEXT NOT NULL,
            value_json TEXT NOT NULL,
            source_message TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (profile_id, field)
        );

        CREATE TABLE IF NOT EXISTS pending_actions (
            session_id TEXT PRIMARY KEY,
            action_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_workflows (
            session_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session_id
        ON messages(session_id, id DESC);
        """
    )
    return connection


def _load_preferences(
    connection: sqlite3.Connection,
    profile_id: str,
) -> dict[str, str | float | list[str]]:
    rows = connection.execute(
        "SELECT field, value_json FROM preferences WHERE profile_id = ? ORDER BY field",
        [profile_id],
    ).fetchall()
    return {str(row["field"]): json.loads(row["value_json"]) for row in rows}


def load_conversation_context(
    session_id: str,
    profile_id: str,
) -> ConversationContext:
    connection = connect_memory()
    session = connection.execute(
        "SELECT profile_id FROM sessions WHERE session_id = ?",
        [session_id],
    ).fetchone()
    if session is not None and session["profile_id"] != profile_id:
        raise ValueError("session_id does not belong to profile_id")

    message_rows = connection.execute(
        """
        SELECT role, content FROM (
            SELECT id, role, content
            FROM messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) ORDER BY id
        """,
        [session_id, RECENT_MESSAGE_LIMIT],
    ).fetchall()
    working = connection.execute(
        """
        SELECT filters_json, current_product_id, selected_product_ids_json,
               visible_product_ids_json
        FROM working_memory
        WHERE session_id = ?
        """,
        [session_id],
    ).fetchone()
    pending = connection.execute(
        "SELECT action_json FROM pending_actions WHERE session_id = ?",
        [session_id],
    ).fetchone()
    workflow = connection.execute(
        "SELECT state_json FROM agent_workflows WHERE session_id = ?",
        [session_id],
    ).fetchone()
    preferences = _load_preferences(connection, profile_id)
    turn_count = connection.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
        [session_id],
    ).fetchone()[0]
    connection.close()

    return ConversationContext(
        session_id=session_id,
        profile_id=profile_id,
        recent_messages=[
            {"role": str(row["role"]), "content": str(row["content"])}
            for row in message_rows
        ],
        working_filters=(
            NormalizedFilters.model_validate(json.loads(working["filters_json"]))
            if working is not None
            else None
        ),
        current_product_id=(
            str(working["current_product_id"])
            if working is not None and working["current_product_id"] is not None
            else None
        ),
        selected_product_ids=(
            list(json.loads(working["selected_product_ids_json"]))
            if working is not None
            else []
        ),
        visible_product_ids=(
            list(json.loads(working["visible_product_ids_json"]))
            if working is not None
            else []
        ),
        pending_action=(
            PendingAction.model_validate(json.loads(pending["action_json"]))
            if pending is not None
            else None
        ),
        workflow_state=(
            AgentWorkflowState.model_validate(json.loads(workflow["state_json"]))
            if workflow is not None
            else AgentWorkflowState()
        ),
        remembered_preferences=preferences,
        turn_count=int(turn_count),
    )


def record_turn(
    *,
    session_id: str,
    profile_id: str,
    user_message: str,
    assistant_message: str,
    filters: NormalizedFilters,
    current_product_id: str | None,
    selected_product_ids: list[str],
    visible_product_ids: list[str],
    pending_action: PendingAction | None,
    workflow_state: AgentWorkflowState | None = None,
) -> MemoryStatus:
    connection = connect_memory()
    connection.execute(
        """
        INSERT INTO sessions(session_id, profile_id)
        VALUES (?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            updated_at = CURRENT_TIMESTAMP
        """,
        [session_id, profile_id],
    )
    connection.executemany(
        "INSERT INTO messages(session_id, role, content) VALUES (?, ?, ?)",
        [
            (session_id, "user", user_message),
            (session_id, "assistant", assistant_message),
        ],
    )
    connection.execute(
        """
        INSERT INTO working_memory(
            session_id,
            filters_json,
            current_product_id,
            selected_product_ids_json,
            visible_product_ids_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            filters_json = excluded.filters_json,
            current_product_id = excluded.current_product_id,
            selected_product_ids_json = excluded.selected_product_ids_json,
            visible_product_ids_json = excluded.visible_product_ids_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        [
            session_id,
            json.dumps(filters.model_dump(mode="json"), ensure_ascii=False),
            current_product_id,
            json.dumps(selected_product_ids, ensure_ascii=False),
            json.dumps(visible_product_ids, ensure_ascii=False),
        ],
    )
    connection.execute(
        "DELETE FROM pending_actions WHERE session_id = ?",
        [session_id],
    )
    if pending_action is not None:
        connection.execute(
            "INSERT INTO pending_actions(session_id, action_json) VALUES (?, ?)",
            [
                session_id,
                json.dumps(pending_action.model_dump(mode="json"), ensure_ascii=False),
            ],
        )
    if workflow_state is not None:
        connection.execute(
            """
            INSERT INTO agent_workflows(session_id, state_json)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                session_id,
                json.dumps(
                    workflow_state.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
            ],
        )
    connection.commit()
    preferences = _load_preferences(connection, profile_id)
    turn_count = connection.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
        [session_id],
    ).fetchone()[0]
    connection.close()
    return MemoryStatus(
        session_id=session_id,
        profile_id=profile_id,
        recent_turn_count=int(turn_count),
        remembered_preferences=preferences,
    )


def remember_explicit_preferences(
    *,
    profile_id: str,
    operations: list[SlotOperation],
    source_message: str,
) -> dict[str, str | float | list[str]]:
    connection = connect_memory()
    existing_preferences = _load_preferences(connection, profile_id)
    for operation in operations:
        field = operation.field
        if field not in PREFERENCE_FIELDS or operation.operation == SlotOperationType.KEEP:
            continue
        if operation.operation == SlotOperationType.CLEAR:
            connection.execute(
                "DELETE FROM preferences WHERE profile_id = ? AND field = ?",
                [profile_id, field],
            )
            existing_preferences.pop(field, None)
            continue
        value: Any = operation.value
        if operation.operation in {SlotOperationType.ADD, SlotOperationType.REMOVE}:
            if not isinstance(value, list):
                raise ValueError(f"{operation.operation.value} requires list preference")
            existing = existing_preferences.get(field, [])
            if not isinstance(existing, list):
                raise ValueError(f"stored preference for {field} must be a list")
            if operation.operation == SlotOperationType.ADD:
                value = list(dict.fromkeys([*existing, *value]))
            else:
                removals = set(value)
                value = [item for item in existing if item not in removals]
        connection.execute(
            """
            INSERT INTO preferences(profile_id, field, value_json, source_message)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id, field) DO UPDATE SET
                value_json = excluded.value_json,
                source_message = excluded.source_message,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                profile_id,
                field,
                json.dumps(value, ensure_ascii=False),
                source_message,
            ],
        )
        existing_preferences[field] = value
    connection.commit()
    preferences = _load_preferences(connection, profile_id)
    connection.close()
    return preferences


def delete_profile_preferences(profile_id: str) -> None:
    connection = connect_memory()
    connection.execute("DELETE FROM preferences WHERE profile_id = ?", [profile_id])
    connection.commit()
    connection.close()


def delete_session(session_id: str, profile_id: str) -> None:
    connection = connect_memory()
    row = connection.execute(
        "SELECT profile_id FROM sessions WHERE session_id = ?",
        [session_id],
    ).fetchone()
    if row is not None and row["profile_id"] != profile_id:
        raise ValueError("session_id does not belong to profile_id")
    connection.execute("DELETE FROM sessions WHERE session_id = ?", [session_id])
    connection.commit()
    connection.close()
