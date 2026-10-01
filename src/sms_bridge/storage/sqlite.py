import asyncio
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
from pydantic import BaseModel, ConfigDict

from sms_bridge.domain.models import ConversationTurn, InboundMessage
from sms_bridge.domain.states import MessageState, assert_transition


class EnqueueDisposition(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    RATE_LIMIT_REPLY = "rate_limit_reply"
    RATE_LIMIT_SUPPRESSED = "rate_limit_suppressed"


class EnqueueResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    conversation_id: UUID
    turn_id: int
    disposition: EnqueueDisposition

    @property
    def duplicate(self) -> bool:
        return self.disposition is EnqueueDisposition.DUPLICATE


class PendingWork(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    conversation_id: UUID
    turn_id: int
    sender: str
    user_id: str
    text: str
    history: tuple[ConversationTurn, ...]
    answer: str | None
    ai_attempts: int
    send_attempts: int


class SqliteStore:
    def __init__(
        self,
        database_path: str | Path,
        *,
        conversation_ttl_minutes: int = 30,
        history_limit: int = 5,
    ) -> None:
        self._database_path = Path(database_path)
        self._conversation_ttl = timedelta(minutes=conversation_ttl_minutes)
        self._history_limit = history_limit
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is None:
                self._database_path.parent.mkdir(parents=True, exist_ok=True)
                self._connection = await aiosqlite.connect(self._database_path)
                self._connection.row_factory = aiosqlite.Row
            connection = self._require_connection()
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    next_turn INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conversations_user_activity
                    ON conversations(user_id, last_activity_at DESC);

                CREATE TABLE IF NOT EXISTS inbound_events (
                    request_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    source_message_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    turn_id INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    answer TEXT,
                    ai_attempts INTEGER NOT NULL DEFAULT 0,
                    send_attempts INTEGER NOT NULL DEFAULT 0,
                    error_category TEXT,
                    rate_limit_reply INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(conversation_id, turn_id)
                );
                CREATE INDEX IF NOT EXISTS inbound_events_claim_order
                    ON inbound_events(state, received_at, turn_id);
                CREATE INDEX IF NOT EXISTS inbound_events_user_created
                    ON inbound_events(user_id, created_at);

                CREATE TABLE IF NOT EXISTS outbound_messages (
                    request_id TEXT PRIMARY KEY REFERENCES inbound_events(request_id),
                    recipient TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    gateway_message_id TEXT,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS turns (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    turn_id INTEGER NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, turn_id)
                );
                """
            )
            await connection.commit()

    async def healthcheck(self) -> bool:
        async with self._lock:
            cursor = await self._require_connection().execute("SELECT 1")
            row = await cursor.fetchone()
            return row is not None and row[0] == 1

    async def enqueue(
        self,
        inbound: InboundMessage,
        user_id: str,
        now: datetime,
        *,
        initial_answer: str | None = None,
        rate_limit_per_minute: int = 6,
        rate_limit_answer: str = "Bạn gửi quá nhiều tin nhắn, vui lòng chờ một phút.",
    ) -> EnqueueResult:
        timestamp = _utc_iso(now)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                duplicate = await self._find_event(inbound.event_id)
                if duplicate is not None:
                    await connection.commit()
                    return EnqueueResult(
                        request_id=UUID(duplicate["request_id"]),
                        conversation_id=UUID(duplicate["conversation_id"]),
                        turn_id=duplicate["turn_id"],
                        disposition=EnqueueDisposition.DUPLICATE,
                    )

                conversation = await self._active_conversation(user_id, now)
                if conversation is None:
                    conversation_id = uuid4()
                    turn_id = 1
                    await connection.execute(
                        """
                        INSERT INTO conversations
                            (id, user_id, sender, created_at, last_activity_at, next_turn)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(conversation_id),
                            user_id,
                            inbound.sender,
                            timestamp,
                            timestamp,
                            2,
                        ),
                    )
                else:
                    conversation_id = UUID(conversation["id"])
                    turn_id = conversation["next_turn"]
                    await connection.execute(
                        """
                        UPDATE conversations
                        SET sender = ?, last_activity_at = ?, next_turn = ?
                        WHERE id = ?
                        """,
                        (inbound.sender, timestamp, turn_id + 1, str(conversation_id)),
                    )

                cutoff = _utc_iso(now - timedelta(seconds=60))
                count_cursor = await connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM inbound_events
                    WHERE user_id = ? AND created_at >= ?
                    """,
                    (user_id, cutoff),
                )
                recent_count = (await count_cursor.fetchone())[0]
                rate_limit_reply = False
                if recent_count >= rate_limit_per_minute:
                    reply_cursor = await connection.execute(
                        """
                        SELECT 1
                        FROM inbound_events
                        WHERE user_id = ? AND created_at >= ? AND rate_limit_reply = 1
                        LIMIT 1
                        """,
                        (user_id, cutoff),
                    )
                    rate_limit_reply = await reply_cursor.fetchone() is None

                if recent_count >= rate_limit_per_minute and rate_limit_reply:
                    disposition = EnqueueDisposition.RATE_LIMIT_REPLY
                    state = MessageState.ANSWERED
                    answer = rate_limit_answer
                elif recent_count >= rate_limit_per_minute:
                    disposition = EnqueueDisposition.RATE_LIMIT_SUPPRESSED
                    state = MessageState.RATE_LIMITED
                    answer = None
                elif initial_answer is not None:
                    disposition = EnqueueDisposition.NEW
                    state = MessageState.ANSWERED
                    answer = initial_answer
                else:
                    disposition = EnqueueDisposition.NEW
                    state = MessageState.QUEUED
                    answer = None

                await connection.execute(
                    """
                    INSERT INTO inbound_events (
                        request_id, event_id, source_message_id, device_id, sender,
                        user_id, text, received_at, created_at, updated_at,
                        conversation_id, turn_id, state, answer, rate_limit_reply
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(inbound.request_id),
                        inbound.event_id,
                        inbound.source_message_id,
                        inbound.device_id,
                        inbound.sender,
                        user_id,
                        inbound.text,
                        _utc_iso(inbound.received_at),
                        timestamp,
                        timestamp,
                        str(conversation_id),
                        turn_id,
                        state.value,
                        answer,
                        int(rate_limit_reply),
                    ),
                )
                if answer is not None:
                    await connection.execute(
                        """
                        INSERT INTO outbound_messages
                            (request_id, recipient, answer, state, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(inbound.request_id),
                            inbound.sender,
                            answer,
                            state.value,
                            timestamp,
                        ),
                    )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

        return EnqueueResult(
            request_id=inbound.request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            disposition=disposition,
        )

    async def claim_next(self, now: datetime) -> PendingWork | None:
        timestamp = _utc_iso(now)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    """
                    SELECT *
                    FROM inbound_events candidate
                    WHERE candidate.state IN (?, ?)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM inbound_events active
                          WHERE active.conversation_id = candidate.conversation_id
                            AND active.state IN (?, ?)
                      )
                    ORDER BY candidate.received_at, candidate.created_at,
                             candidate.turn_id, candidate.rowid
                    LIMIT 1
                    """,
                    (
                        MessageState.QUEUED.value,
                        MessageState.ANSWERED.value,
                        MessageState.PROCESSING.value,
                        MessageState.SENDING.value,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    await connection.commit()
                    return None

                current = MessageState(row["state"])
                target = (
                    MessageState.PROCESSING
                    if current is MessageState.QUEUED
                    else MessageState.SENDING
                )
                await connection.execute(
                    "UPDATE inbound_events SET state = ?, updated_at = ? WHERE request_id = ?",
                    (target.value, timestamp, row["request_id"]),
                )
                history = await self._load_history(row["conversation_id"])
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

        return PendingWork(
            request_id=UUID(row["request_id"]),
            conversation_id=UUID(row["conversation_id"]),
            turn_id=row["turn_id"],
            sender=row["sender"],
            user_id=row["user_id"],
            text=row["text"],
            history=history,
            answer=row["answer"],
            ai_attempts=row["ai_attempts"],
            send_attempts=row["send_attempts"],
        )

    async def record_ai_attempt(self, request_id: UUID, now: datetime) -> None:
        await self._update_attempt(
            request_id,
            now,
            required_state=MessageState.PROCESSING,
            inbound_column="ai_attempts",
        )

    async def record_answer(
        self,
        request_id: UUID,
        answer: str,
        now: datetime,
        *,
        start_sending: bool = False,
    ) -> None:
        timestamp = _utc_iso(now)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._message_row(request_id)
                self._assert_stored_transition(row, MessageState.ANSWERED)
                target = (
                    MessageState.SENDING if start_sending else MessageState.ANSWERED
                )
                await connection.execute(
                    """
                    UPDATE inbound_events
                    SET state = ?, answer = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (target.value, answer, timestamp, str(request_id)),
                )
                await connection.execute(
                    """
                    INSERT INTO outbound_messages
                        (request_id, recipient, answer, state, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(request_id) DO UPDATE SET
                        answer = excluded.answer,
                        state = excluded.state,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(request_id),
                        row["sender"],
                        answer,
                        target.value,
                        timestamp,
                    ),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def record_send_attempt(self, request_id: UUID, now: datetime) -> None:
        timestamp = _utc_iso(now)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._message_row(request_id)
                if MessageState(row["state"]) is not MessageState.SENDING:
                    raise ValueError("send attempts require a message in sending state")
                await connection.execute(
                    """
                    UPDATE inbound_events
                    SET send_attempts = send_attempts + 1, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (timestamp, str(request_id)),
                )
                await connection.execute(
                    """
                    UPDATE outbound_messages
                    SET attempts = attempts + 1, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (timestamp, str(request_id)),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def record_sent(
        self,
        request_id: UUID,
        gateway_message_id: str,
        now: datetime,
    ) -> None:
        timestamp = _utc_iso(now)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._message_row(request_id)
                self._assert_stored_transition(row, MessageState.SENT)
                await connection.execute(
                    """
                    UPDATE inbound_events SET state = ?, updated_at = ? WHERE request_id = ?
                    """,
                    (MessageState.SENT.value, timestamp, str(request_id)),
                )
                await connection.execute(
                    """
                    UPDATE outbound_messages
                    SET state = ?, gateway_message_id = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (
                        MessageState.SENT.value,
                        gateway_message_id,
                        timestamp,
                        str(request_id),
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO turns (
                        conversation_id, turn_id, user_text, assistant_text, completed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id, turn_id) DO NOTHING
                    """,
                    (
                        row["conversation_id"],
                        row["turn_id"],
                        row["text"],
                        row["answer"],
                        timestamp,
                    ),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def record_ai_failed(
        self,
        request_id: UUID,
        error_category: str,
        now: datetime,
    ) -> None:
        await self._record_failure(
            request_id,
            error_category,
            now,
            target=MessageState.AI_FAILED,
        )

    async def record_send_failed(
        self,
        request_id: UUID,
        error_category: str,
        now: datetime,
    ) -> None:
        await self._record_failure(
            request_id,
            error_category,
            now,
            target=MessageState.SEND_FAILED,
        )

    async def recover_stale(self, now: datetime, stale_after_seconds: int = 60) -> int:
        timestamp = _utc_iso(now)
        cutoff = _utc_iso(now - timedelta(seconds=stale_after_seconds))
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                processing = await connection.execute(
                    """
                    UPDATE inbound_events
                    SET state = ?, updated_at = ?
                    WHERE state = ? AND updated_at < ?
                    """,
                    (
                        MessageState.QUEUED.value,
                        timestamp,
                        MessageState.PROCESSING.value,
                        cutoff,
                    ),
                )
                sending = await connection.execute(
                    """
                    UPDATE inbound_events
                    SET state = ?, updated_at = ?
                    WHERE state = ? AND updated_at < ?
                    """,
                    (
                        MessageState.ANSWERED.value,
                        timestamp,
                        MessageState.SENDING.value,
                        cutoff,
                    ),
                )
                await connection.execute(
                    """
                    UPDATE outbound_messages
                    SET state = ?, updated_at = ?
                    WHERE state = ? AND updated_at < ?
                    """,
                    (
                        MessageState.ANSWERED.value,
                        timestamp,
                        MessageState.SENDING.value,
                        cutoff,
                    ),
                )
                await connection.commit()
                return processing.rowcount + sending.rowcount
            except BaseException:
                await connection.rollback()
                raise

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def _find_event(self, event_id: str) -> aiosqlite.Row | None:
        cursor = await self._require_connection().execute(
            """
            SELECT request_id, conversation_id, turn_id
            FROM inbound_events
            WHERE event_id = ?
            """,
            (event_id,),
        )
        return await cursor.fetchone()

    async def _active_conversation(self, user_id: str, now: datetime) -> aiosqlite.Row | None:
        cursor = await self._require_connection().execute(
            """
            SELECT id, next_turn
            FROM conversations
            WHERE user_id = ? AND last_activity_at >= ?
            ORDER BY last_activity_at DESC
            LIMIT 1
            """,
            (user_id, _utc_iso(now - self._conversation_ttl)),
        )
        return await cursor.fetchone()

    async def _load_history(self, conversation_id: str) -> tuple[ConversationTurn, ...]:
        cursor = await self._require_connection().execute(
            """
            SELECT user_text, assistant_text
            FROM turns
            WHERE conversation_id = ?
            ORDER BY turn_id DESC
            LIMIT ?
            """,
            (conversation_id, self._history_limit),
        )
        rows = list(await cursor.fetchall())
        rows.reverse()
        return tuple(
            ConversationTurn(user=row["user_text"], assistant=row["assistant_text"])
            for row in rows
        )

    async def _message_row(self, request_id: UUID) -> aiosqlite.Row:
        cursor = await self._require_connection().execute(
            "SELECT * FROM inbound_events WHERE request_id = ?",
            (str(request_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"unknown request_id: {request_id}")
        return row

    async def _update_attempt(
        self,
        request_id: UUID,
        now: datetime,
        *,
        required_state: MessageState,
        inbound_column: str,
    ) -> None:
        if inbound_column != "ai_attempts":
            raise ValueError("unsupported attempt column")
        timestamp = _utc_iso(now)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._message_row(request_id)
                if MessageState(row["state"]) is not required_state:
                    raise ValueError(
                        f"attempt requires {required_state.value} state, got {row['state']}"
                    )
                await connection.execute(
                    """
                    UPDATE inbound_events
                    SET ai_attempts = ai_attempts + 1, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (timestamp, str(request_id)),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def _record_failure(
        self,
        request_id: UUID,
        error_category: str,
        now: datetime,
        *,
        target: MessageState,
    ) -> None:
        timestamp = _utc_iso(now)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._message_row(request_id)
                self._assert_stored_transition(row, target)
                await connection.execute(
                    """
                    UPDATE inbound_events
                    SET state = ?, error_category = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (target.value, error_category, timestamp, str(request_id)),
                )
                if target is MessageState.SEND_FAILED:
                    await connection.execute(
                        """
                        UPDATE outbound_messages
                        SET state = ?, updated_at = ?
                        WHERE request_id = ?
                        """,
                        (target.value, timestamp, str(request_id)),
                    )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    @staticmethod
    def _assert_stored_transition(row: aiosqlite.Row, target: MessageState) -> None:
        assert_transition(MessageState(row["state"]), target)

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SQLite store is not initialized")
        return self._connection


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()
