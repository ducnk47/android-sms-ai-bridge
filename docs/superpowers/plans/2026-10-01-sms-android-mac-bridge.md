# SMS Android-to-Mac Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate a durable Python bridge that receives carrier SMS from an Android gateway over USB, performs multi-turn echo or AI processing, and sends the reply through the same gateway.

**Architecture:** FastAPI accepts signed `sms-gate` webhooks, normalizes and persists each turn in SQLite, then a durable worker invokes a replaceable AI adapter and an SMS gateway adapter. Fake adapters make the complete loop testable on the Mac before the Local Server adapter is connected through ADB forward/reverse tunnels.

**Tech Stack:** Python 3.12, uv, FastAPI, Uvicorn, Pydantic Settings, HTTPX, aiosqlite, phonenumbers, structlog, Pytest, pytest-asyncio, and respx.

**Spec:** `docs/superpowers/specs/2026-10-01-sms-android-mac-bridge-design.md`

## Global Constraints

- Use Python 3.12 and commit `uv.lock`; do not add Docker, Redis, Celery, PostgreSQL, or a public server.
- Bind FastAPI to `127.0.0.1:8000`; expose it to Android only through ADB reverse TCP `9876 -> 8000`.
- Expose the Android Local Server to the Mac through ADB forward TCP `8081 -> <actual device port>`; default device port is 8080 but must remain configurable.
- Use `sms-gate` Local Server endpoints: `GET /health`, `POST /message`, and `POST /webhooks`; do not use Cloud API paths in Local mode.
- Use Basic Authentication for the Local Server and keep credentials, the webhook signing key, and the user-ID HMAC secret outside version control.
- Acknowledge a valid inbound webhook with HTTP 202 before AI or outbound SMS work begins.
- Use the top-level webhook `id` as the primary event deduplication key; retain payload `messageId` only as source metadata.
- Keep one active conversation per normalized sender for 30 minutes and provide at most five completed turns as AI history.
- Process turns FIFO for each sender; one inbound turn creates one logical response, which `sms-gate` may split into multipart SMS.
- Limit replies to 320 Unicode characters and inputs to 1,000 Unicode characters.
- Use a 20-second AI timeout with one retry; use three gateway send attempts with 2- and 10-second delays.
- Use the exact user-facing fallbacks `Vui lòng gửi câu hỏi bằng tin nhắn.`, `Tin nhắn quá dài, vui lòng chia câu hỏi thành nhiều tin ngắn hơn.`, `Bạn gửi quá nhiều tin nhắn, vui lòng chờ một phút.`, and `Hệ thống đang bận, vui lòng thử lại sau.`
- Store raw return phone numbers only in SQLite; mask them in logs and never send them to the AI adapter.
- Voice, YOLO, native Android UI, cloud SMS, production deployment, and foreign-carrier validation remain outside this plan.

## Review Focus

- A validly shaped webhook with a bad signature or a timestamp older than 300 seconds must be rejected before persistence; Task 2 pins both cases.
- Two gateway deliveries with the same top-level webhook `id`, including retries with different request arrival times, must create one turn and one reply; Tasks 3 and 7 pin this.
- Vietnamese numbers arriving as `090...`, `8490...`, or `+8490...`, plus null or invalid senders, must normalize consistently or fail without enqueueing; Task 2 pins this.
- A gateway request that times out after Android may have accepted it must retry with the same application message ID, never a newly generated ID; Tasks 4 and 6 pin this.
- A process restart during AI or SMS work must recover the persisted turn, reuse any persisted answer, and avoid crossing replies between senders; Tasks 3 and 6 pin this.

---

## File Map

- `pyproject.toml`: package metadata, runtime dependencies, test/lint configuration, and CLI entry point.
- `uv.lock`: reproducible dependency resolution for the Mac.
- `.env.example`: non-secret configuration names and documented defaults.
- `.gitignore`: virtual environment, databases, environment files, caches, logs, and evidence media.
- `src/sms_bridge/config.py`: validated environment settings.
- `src/sms_bridge/logging.py`: structlog configuration and field redaction.
- `src/sms_bridge/main.py`: FastAPI factory, lifespan, dependency wiring, and worker lifecycle.
- `src/sms_bridge/api/health.py`: `/health` and `/ready` routes.
- `src/sms_bridge/api/webhooks.py`: signed gateway webhook and development simulator routes.
- `src/sms_bridge/domain/models.py`: internal inbound, conversation, AI, gateway, and work-item models.
- `src/sms_bridge/domain/states.py`: message state enum and legal transition table.
- `src/sms_bridge/domain/identity.py`: Vietnamese phone normalization, masking, and HMAC user IDs.
- `src/sms_bridge/adapters/ai/base.py`: `AiAdapter` protocol.
- `src/sms_bridge/adapters/ai/echo.py`: deterministic echo implementation.
- `src/sms_bridge/adapters/ai/http.py`: teammate HTTP AI implementation added after transport acceptance.
- `src/sms_bridge/adapters/sms/base.py`: `SmsGateway` protocol.
- `src/sms_bridge/adapters/sms/fake.py`: deterministic, inspectable fake gateway.
- `src/sms_bridge/adapters/sms/sms_gate.py`: Local Server health, webhook registration, and send client.
- `src/sms_bridge/adapters/sms/webhook.py`: webhook envelope models, signature verification, and inbound normalization.
- `src/sms_bridge/storage/sqlite.py`: schema creation, transactions, deduplication, conversation selection, claiming, transitions, and recovery.
- `src/sms_bridge/services/message_processor.py`: AI and gateway orchestration with bounded retries.
- `src/sms_bridge/services/worker.py`: durable polling loop and clean shutdown.
- `src/sms_bridge/cli.py`: ADB tunnel setup and gateway setup/check commands.
- `tests/unit/`: domain, adapter, store, and processor tests.
- `tests/integration/`: API-to-worker-to-fake-gateway tests.
- `tests/contract/`: exact `sms-gate` Local Server request and webhook contract tests.
- `docs/setup-android.md`: Xiaomi, permissions, `sms-gate`, ADB, and webhook setup.
- `docs/test-runbook.md`: Mac-only and real-SIM acceptance procedure.
- `docs/evidence/`: committed masked test summaries; screenshots and videos stay local and are referenced by filename.

### Task 1: Project foundation and health API

**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/sms_bridge/__init__.py`
- Create: `src/sms_bridge/config.py`
- Create: `src/sms_bridge/logging.py`
- Create: `src/sms_bridge/main.py`
- Create: `src/sms_bridge/api/__init__.py`
- Create: `src/sms_bridge/api/health.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/integration/test_health_api.py`

**Interfaces:**
- Produces: `Settings(BaseSettings)` with the exact global defaults and secrets defined above.
- Produces: `configure_logging(*, include_message_bodies: bool = False) -> None` with secret-key and full-phone redaction.
- Produces: `create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI`.
- Produces: `GET /health -> {"status": "pass"}` and a placeholder `/ready -> 503 {"status": "not_initialized"}` until Task 7 wires storage and the gateway.

- [ ] **Step 1: Write failing foundation tests**

Add `test_settings_use_spec_defaults`, `test_secrets_are_not_in_repr`, `test_logging_redacts_secret_fields`, `test_health_returns_pass`, and `test_app_has_no_dev_simulator_in_production`. Assert port 8000, gateway base URL `http://127.0.0.1:8081`, AI timeout 20, history limit 5, conversation TTL 30, reply limit 320, input limit 1000, rate limit 6, and message-body logging disabled by default.

- [ ] **Step 2: Run the tests and confirm the package is absent**

Run: `uv run pytest tests/unit/test_config.py tests/integration/test_health_api.py -v`

Expected: FAIL during import because `sms_bridge` does not exist.

- [ ] **Step 3: Add project metadata, settings, and the app factory**

Configure a `src` layout, Python `>=3.12,<3.13`, runtime dependencies from the plan header, and development dependencies `pytest`, `pytest-asyncio`, `respx`, and `ruff`. Implement `Settings` with environment prefix `SMS_BRIDGE_`; secret fields use `SecretStr | None` and have no real defaults.

- [ ] **Step 4: Run foundation verification**

Run: `uv run pytest tests/unit/test_config.py tests/integration/test_health_api.py -v`

Expected: all tests PASS.

Run: `uv run ruff check src tests`

Expected: exit 0.

- [ ] **Step 5: Commit the foundation**

```bash
git add pyproject.toml uv.lock .gitignore .env.example src tests
git commit -m "chore: scaffold SMS bridge service"
```

### Task 2: Signed inbound webhook normalization and sender identity

**Files:**
- Create: `src/sms_bridge/domain/models.py`
- Create: `src/sms_bridge/domain/identity.py`
- Create: `src/sms_bridge/adapters/sms/__init__.py`
- Create: `src/sms_bridge/adapters/sms/webhook.py`
- Create: `tests/unit/test_identity.py`
- Create: `tests/unit/test_sms_gate_webhook.py`
- Create: `tests/contract/test_sms_gate_webhook_contract.py`

**Interfaces:**
- Produces: `InboundMessage(request_id: UUID, event_id: str, source_message_id: str, device_id: str, sender: str, text: str, received_at: datetime)`.
- Produces: `normalize_sender(value: str, *, default_region: str = "VN") -> str` and `mask_sender(value: str) -> str`.
- Produces: `derive_user_id(sender: str, secret: str) -> str` using lowercase hex HMAC-SHA256.
- Produces: `verify_signature(raw_body: bytes, timestamp: str | None, signature: str | None, signing_key: str, *, now: datetime, max_age_seconds: int = 300) -> None`.
- Produces: `parse_sms_received(raw_body: bytes) -> SmsGateWebhook` and `normalize_webhook(webhook: SmsGateWebhook) -> InboundMessage`.

- [ ] **Step 1: Write failing identity tests**

Add table-driven assertions that `0901234567`, `84901234567`, and `+84901234567` normalize to `+84901234567`; invalid, empty, and null-like senders raise `InvalidSender`; masking yields `+8490***567`; identical sender/secret pairs yield identical 64-character user IDs while a different secret changes the ID.

- [ ] **Step 2: Run identity tests and verify failure**

Run: `uv run pytest tests/unit/test_identity.py -v`

Expected: FAIL because identity functions are undefined.

- [ ] **Step 3: Implement identity functions**

Use `phonenumbers` with default region `VN`, format valid results as E.164, use `hmac.new(..., hashlib.sha256)`, and never include a full sender in exception text.

- [ ] **Step 4: Write failing webhook contract and security tests**

Use the official `sms:received` envelope fields `deviceId`, `event`, top-level `id`, `webhookId`, and payload fields `messageId`, `message`, `sender`, `recipient`, `simNumber`, `receivedAt`. Assert top-level `id` becomes `event_id`; if it is blank, assert the fallback digest is stable. Assert HMAC over `raw_body + X-Timestamp` is accepted, altered bodies/signatures are rejected, and timestamps older than 300 seconds are rejected.

- [ ] **Step 5: Implement webhook parsing, verification, and normalization**

Reject events other than `sms:received`, missing/blank messages, invalid timestamps, and invalid senders. Generate `request_id` once per accepted unique delivery. Keep `messageId` as `source_message_id`; do not use it as the primary deduplication key.

- [ ] **Step 6: Run Task 2 verification**

Run: `uv run pytest tests/unit/test_identity.py tests/unit/test_sms_gate_webhook.py tests/contract/test_sms_gate_webhook_contract.py -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit inbound normalization**

```bash
git add src/sms_bridge/domain src/sms_bridge/adapters/sms tests/unit tests/contract
git commit -m "feat: normalize signed SMS gateway webhooks"
```

### Task 3: SQLite conversations, durable queue, and recovery

**Files:**
- Create: `src/sms_bridge/domain/states.py`
- Modify: `src/sms_bridge/domain/models.py`
- Create: `src/sms_bridge/storage/__init__.py`
- Create: `src/sms_bridge/storage/sqlite.py`
- Create: `tests/unit/test_sqlite_store.py`

**Interfaces:**
- Produces: `MessageState` values `received`, `queued`, `processing`, `answered`, `sending`, `sent`, `ai_failed`, `send_failed`, and terminal `rate_limited` plus `assert_transition(current, target) -> None`.
- Produces: `EnqueueDisposition` values `new`, `duplicate`, `rate_limit_reply`, and `rate_limit_suppressed`.
- Produces: `ConversationTurn(user: str, assistant: str)`.
- Produces: `EnqueueResult(request_id: UUID, conversation_id: UUID, turn_id: int, disposition: EnqueueDisposition)` with convenience property `duplicate: bool`.
- Produces: `PendingWork(request_id: UUID, conversation_id: UUID, turn_id: int, sender: str, user_id: str, text: str, history: tuple[ConversationTurn, ...], answer: str | None, ai_attempts: int, send_attempts: int)`.
- Produces: async `SqliteStore.initialize()`, `healthcheck()`, `enqueue(inbound, user_id, now, *, initial_answer: str | None = None, rate_limit_per_minute: int = 6, rate_limit_answer: str = "Bạn gửi quá nhiều tin nhắn, vui lòng chờ một phút.")`, `claim_next(now)`, `record_ai_attempt(...)`, `record_answer(...)`, `record_send_attempt(...)`, `record_sent(...)`, `record_ai_failed(...)`, `record_send_failed(...)`, `recover_stale(now, stale_after_seconds=60)`, and `close()`.

- [ ] **Step 1: Write failing schema and deduplication tests**

Assert initialization is idempotent; the first `enqueue` returns disposition `new`; a second enqueue with the same `event_id` returns the same request/conversation/turn with disposition `duplicate`; two different gateway IDs create two turns even if payload `messageId` is equal.

- [ ] **Step 2: Run store tests and verify failure**

Run: `uv run pytest tests/unit/test_sqlite_store.py -v`

Expected: FAIL because the store is undefined.

- [ ] **Step 3: Implement schema, transactions, and legal transitions**

Use SQLite WAL mode and explicit transactions. Add unique constraints on `inbound_events.event_id`, `inbound_events.request_id`, `(conversation_id, turn_id)`, and `outbound_messages.request_id`. Store timestamps as UTC ISO-8601 strings. Add the immutable `ConversationTurn` model used by stored history.

- [ ] **Step 4: Add failing conversation and history tests**

Assert messages within 30 minutes share a conversation and increment turns; a message after 30 minutes starts conversation turn 1; `PendingWork.history` contains the latest five completed turns in chronological order and excludes the current turn.

- [ ] **Step 5: Add failing rate-limit and precomputed-answer tests**

Assert an `initial_answer` stores the turn in `answered` so AI is skipped; the seventh event for one user within 60 seconds receives disposition `rate_limit_reply` with the exact configured answer; further events in that window receive `rate_limit_suppressed` and terminal state `rate_limited`; the same gateway event ID remains deduplicated regardless of its rate-limit disposition.

- [ ] **Step 6: Add failing claiming and restart tests**

Assert one pending turn per conversation can be claimed; a second claimant may take work from a different conversation but not the next turn from the first conversation; an answered record is reclaimed with its persisted answer and does not require AI again; `recover_stale` returns `processing` to `queued` and `sending` to `answered`; interleaved senders retain their original return numbers.

- [ ] **Step 7: Implement conversation selection and bounded history**

Select or create the active conversation inside the enqueue transaction. Allocate monotonic turn numbers and load no more than the five latest completed turns in chronological order.

- [ ] **Step 8: Implement atomic rate-limit dispositions**

Make the seventh event in a rolling 60-second window pre-answered with the configured rate-limit text. Persist later events in the same window as terminal `rate_limited` records so webhook retries still deduplicate.

- [ ] **Step 9: Implement claiming and restart recovery**

Use `BEGIN IMMEDIATE` for enqueue and claim/update operations so deduplication and rate-limit decisions are atomic. Order claims by inbound receipt time and turn number. Never rewrite the persisted `request_id`, conversation ID, turn number, sender, or answer during recovery.

- [ ] **Step 10: Run Task 3 verification**

Run: `uv run pytest tests/unit/test_sqlite_store.py -v`

Expected: all tests PASS.

- [ ] **Step 11: Commit persistence**

```bash
git add src/sms_bridge/domain/models.py src/sms_bridge/domain/states.py src/sms_bridge/storage tests/unit/test_sqlite_store.py
git commit -m "feat: add durable SMS conversation store"
```

### Task 4: Fake and Local Server SMS gateway adapters

**Files:**
- Create: `src/sms_bridge/adapters/sms/base.py`
- Create: `src/sms_bridge/adapters/sms/fake.py`
- Create: `src/sms_bridge/adapters/sms/sms_gate.py`
- Create: `tests/unit/test_fake_sms_gateway.py`
- Create: `tests/contract/test_sms_gate_local_client.py`

**Interfaces:**
- Produces: `SmsSendResult(message_id: str, accepted: bool)`.
- Produces: `SmsGateway` protocol with async `send(request_id: UUID, recipient: str, text: str) -> SmsSendResult` and `health() -> bool`.
- Produces: `FakeSmsGateway.sent: list[SentSms]`, configurable failures, and deterministic message IDs.
- Produces: `SmsGateLocalClient(base_url: str, username: str, password: str, client: httpx.AsyncClient, priority: int = 100)` with `send`, `health`, and `register_webhook(url: str, event: str = "sms:received") -> str`.

- [ ] **Step 1: Write failing fake gateway tests**

Assert `send` records exactly one `SentSms(request_id, recipient, text)` and returns a deterministic ID; configured failures raise `SmsGatewayError` without appending to `sent`.

- [ ] **Step 2: Implement the protocol and fake gateway**

Keep the fake asynchronous and expose only read-only copies of sent records to tests.

- [ ] **Step 3: Write failing Local Server contract tests**

Using `respx`, assert:

- `health()` calls authenticated `GET /health` and accepts only JSON status `pass` or `warn`.
- `send()` calls authenticated `POST /message`, never `/3rdparty/v1/messages`.
- The JSON body is `{"id": request_id, "textMessage": {"text": text}, "phoneNumbers": [recipient], "withDeliveryReport": true, "priority": 100}`.
- A retry with the same `request_id` sends the identical `id` field.
- `register_webhook()` calls authenticated `POST /webhooks` with the Android loopback URL and `event: sms:received`.
- Non-2xx, malformed JSON, and transport timeouts raise categorized `SmsGatewayError` without leaking credentials.

- [ ] **Step 4: Implement the Local Server client**

Use HTTP Basic Auth and an injected `httpx.AsyncClient`. Treat HTTP 200 and 201 as successful submissions. Prefer the response `id` when present and fall back to the supplied `request_id` after a successful response.

- [ ] **Step 5: Run Task 4 verification**

Run: `uv run pytest tests/unit/test_fake_sms_gateway.py tests/contract/test_sms_gate_local_client.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit gateway adapters**

```bash
git add src/sms_bridge/adapters/sms tests/unit/test_fake_sms_gateway.py tests/contract/test_sms_gate_local_client.py
git commit -m "feat: add local Android SMS gateway client"
```

### Task 5: AI protocol and deterministic echo adapter

**Files:**
- Create: `src/sms_bridge/adapters/ai/__init__.py`
- Create: `src/sms_bridge/adapters/ai/base.py`
- Create: `src/sms_bridge/adapters/ai/echo.py`
- Modify: `src/sms_bridge/domain/models.py`
- Create: `tests/unit/test_echo_ai.py`

**Interfaces:**
- Consumes: `ConversationTurn` from Task 3.
- Produces: `AiRequest(request_id, conversation_id, turn_id, user_id, message, language, history)` and `AiResponse(request_id, answer)` in `domain/models.py`.
- Produces: `AiAdapter` protocol with async `answer(request: AiRequest) -> AiResponse`.
- Produces: `EchoAiAdapter(prefix: str = "AI: ")`.

- [ ] **Step 1: Write failing AI model and echo tests**

Assert first-turn history can be empty, at most five history turns are accepted, the echo response preserves Unicode, returns the same `request_id`, and returns `AI: ping` for input `ping`.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/test_echo_ai.py -v`

Expected: FAIL because AI interfaces do not exist.

- [ ] **Step 3: Implement the protocol, models, and echo adapter**

Reject blank messages and blank answers at model validation. Do not truncate in the adapter; reply length enforcement belongs to the processor.

- [ ] **Step 4: Run Task 5 verification**

Run: `uv run pytest tests/unit/test_echo_ai.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit the echo boundary**

```bash
git add src/sms_bridge/adapters/ai src/sms_bridge/domain/models.py tests/unit/test_echo_ai.py
git commit -m "feat: define AI adapter and echo mode"
```

### Task 6: Durable message processor and worker

**Files:**
- Create: `src/sms_bridge/services/__init__.py`
- Create: `src/sms_bridge/services/message_processor.py`
- Create: `src/sms_bridge/services/worker.py`
- Create: `tests/unit/test_message_processor.py`
- Create: `tests/unit/test_worker.py`

**Interfaces:**
- Produces: `AsyncSleep = Callable[[float], Awaitable[None]]`.
- Produces: `MessageProcessor(store: SqliteStore, ai: AiAdapter, gateway: SmsGateway, *, ai_timeout_seconds: float = 20, reply_max_chars: int = 320, sleep: AsyncSleep = asyncio.sleep)`.
- Produces: async `MessageProcessor.process(work: PendingWork) -> None`.
- Produces: `MessageWorker(store, processor, *, poll_interval_seconds: float = 0.25, concurrency: int = 4)` with async `run_once(now: datetime) -> bool`, `run()`, and `stop()`.

- [ ] **Step 1: Write failing happy-path and conversation tests**

Assert a claimed turn builds `AiRequest` with opaque `user_id`, correct five-turn history, and no phone number; echo output is persisted before sending; the gateway receives the original sender; success ends in `sent`; two interleaved senders never receive each other's answer.

- [ ] **Step 2: Run processor tests and verify failure**

Run: `uv run pytest tests/unit/test_message_processor.py -v`

Expected: FAIL because the processor is undefined.

- [ ] **Step 3: Implement the successful processing path**

If `PendingWork.answer` is already present, skip AI and resume at send. Truncate answers to 320 Unicode code points before persistence. Pass the persisted `request_id` into every gateway attempt.

- [ ] **Step 4: Add failing retry and failure tests**

Assert AI is attempted exactly twice with a 20-second timeout and then `Hệ thống đang bận, vui lòng thử lại sau.` is persisted; gateway is attempted exactly three times with injected sleeps `[2, 10]`; every gateway attempt uses the same request ID; exhausted sends become `send_failed`; restart from `answered` never calls AI again.

- [ ] **Step 5: Implement bounded retries and state recording**

Record each attempt before the external call. Categorize timeout, protocol, and validation errors without persisting secret-bearing exception strings.

- [ ] **Step 6: Add failing worker lifecycle tests**

Assert `run_once` returns `False` with no work and `True` after processing one claim; cancellation stops `run()` cleanly; startup calls `recover_stale` before the first claim. With a blocking fake AI, assert two different conversations overlap when concurrency is 4 while two turns in the same conversation never overlap.

- [ ] **Step 7: Implement the polling worker**

Start four in-process worker loops. `SqliteStore.claim_next` must atomically exclude conversations that already have a `processing` or `sending` turn, preserving per-sender FIFO while allowing different senders to progress concurrently. Do not add an external queue.

- [ ] **Step 8: Run Task 6 verification**

Run: `uv run pytest tests/unit/test_message_processor.py tests/unit/test_worker.py -v`

Expected: all tests PASS.

- [ ] **Step 9: Commit orchestration**

```bash
git add src/sms_bridge/services tests/unit/test_message_processor.py tests/unit/test_worker.py
git commit -m "feat: process SMS turns with durable retries"
```

### Task 7: Webhook API and full Mac-only echo loop

**Files:**
- Create: `src/sms_bridge/api/webhooks.py`
- Modify: `src/sms_bridge/api/health.py`
- Modify: `src/sms_bridge/main.py`
- Create: `tests/integration/test_webhook_api.py`
- Create: `tests/integration/test_echo_round_trip.py`

**Interfaces:**
- Consumes: `verify_signature`, `normalize_webhook`, `SqliteStore.enqueue`, `MessageWorker`, `EchoAiAdapter`, and `FakeSmsGateway`.
- Produces: `POST /webhooks/sms-gate -> 202`, development-only `POST /dev/simulate-inbound -> 202`, and fully wired `/ready`.

- [ ] **Step 1: Write failing webhook API tests**

Assert a signed official envelope returns 202 and enqueues once; the same top-level webhook `id` sent twice returns 202 twice but creates one turn; bad signatures and stale timestamps return 401 with no database row; malformed payload and invalid sender return 422; unsupported events return 204 without persistence.

- [ ] **Step 2: Run webhook tests and verify failure**

Run: `uv run pytest tests/integration/test_webhook_api.py -v`

Expected: FAIL because routes are not wired.

- [ ] **Step 3: Implement webhook and development simulator routes**

Read the raw body before JSON parsing. Validate `X-Signature` and `X-Timestamp` with the configured key. Register `/dev/simulate-inbound` only when `app_env` is `development` or `test`.

- [ ] **Step 4: Write the failing complete fake round-trip test**

Post Vietnamese text through the development route, call `worker.run_once`, and assert one fake outbound SMS to the normalized sender with `AI: <input>`. Post five follow-up turns and assert turn numbers and history; post two sender streams and assert recipient isolation. Post whitespace-only input and assert `Vui lòng gửi câu hỏi bằng tin nhắn.`; post 1,001 characters and assert `Tin nhắn quá dài, vui lòng chia câu hỏi thành nhiều tin ngắn hơn.`; neither case may call AI.

- [ ] **Step 5: Wire lifespan, dependency selection, readiness, and input controls**

Initialize/close SQLite in lifespan, recover stale work, create the configured adapters, and start/stop the worker only when `start_worker=True`. `/ready` returns 200 only when SQLite is healthy and `gateway.health()` succeeds; otherwise return 503 with redacted component states.

- [ ] **Step 6: Add rate-limit and logging assertions**

Assert the seventh inbound message in one minute produces at most one `Bạn gửi quá nhiều tin nhắn, vui lòng chờ một phút.` reply, logs contain masked senders and request IDs, and captured logs do not contain the full sender, Basic Auth password, signing key, or HMAC secret.

- [ ] **Step 7: Run Mac-only acceptance tests**

Run: `uv run pytest tests/unit tests/integration tests/contract -v`

Expected: all tests PASS.

Run: `uv run ruff check src tests`

Expected: exit 0.

- [ ] **Step 8: Commit the executable echo bridge**

```bash
git add src/sms_bridge/api src/sms_bridge/main.py tests/integration
git commit -m "feat: complete offline SMS echo bridge"
```

### Task 8: ADB setup CLI and teammate runbooks

**Files:**
- Create: `src/sms_bridge/cli.py`
- Create: `tests/unit/test_cli.py`
- Create: `docs/setup-android.md`
- Create: `docs/test-runbook.md`
- Modify: `README.md`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `build_tunnel_commands(device_api_port: int, *, mac_gateway_port: int = 8081, android_webhook_port: int = 9876, mac_api_port: int = 8000) -> tuple[tuple[str, ...], ...]`.
- Produces CLI commands `sms-bridge adb-tunnels --device-port PORT`, `sms-bridge gateway-health`, `sms-bridge register-webhook`, and `sms-bridge send-test PHONE TEXT`.

- [ ] **Step 1: Write failing CLI command-construction tests**

Assert the tunnel commands are exactly `adb forward tcp:8081 tcp:<device-port>` and `adb reverse tcp:9876 tcp:8000`; invalid ports fail before invoking subprocess; command failures preserve the ADB exit code without printing secrets.

- [ ] **Step 2: Implement the standard-library argparse CLI**

Use dependency-injected subprocess and `SmsGateLocalClient`; do not add Typer or Click. `register-webhook` uses `http://127.0.0.1:9876/webhooks/sms-gate` and event `sms:received`.

- [ ] **Step 3: Run CLI tests**

Run: `uv run pytest tests/unit/test_cli.py -v`

Expected: all tests PASS.

- [ ] **Step 4: Write the Android setup guide**

Document Xiaomi 14T Pro/Android 16 preparation, APK source and recorded version, USB debugging authorization, `SEND_SMS`/`RECEIVE_SMS`/SIM permissions, HyperOS battery/background exemptions, Local Server credentials/port, ADB tunnels, `GET /health`, webhook signing key, registration, screen-lock testing, and safe credential handling.

- [ ] **Step 5: Write the transport test runbook and README quick start**

Include Mac-only fake testing, outbound-only real SMS, inbound-only webhook capture, full echo, multi-turn, two-sender isolation, Vietnamese Unicode, duplicate replay, USB reconnect, process restart, and the ten-message offline acceptance run. Explicitly distinguish top-level webhook `id` from payload `messageId`.

- [ ] **Step 6: Verify commands and documentation references**

Run: `uv run sms-bridge --help`

Expected: all four subcommands are listed.

Run: `rg -n "POST /message|POST /webhooks|GET /health|adb forward|adb reverse|Android 16|offline" README.md docs/setup-android.md docs/test-runbook.md`

Expected: each required setup concept appears in the relevant guide and no Cloud API path appears in a Local Server command.

- [ ] **Step 7: Commit operational tooling**

```bash
git add pyproject.toml uv.lock README.md src/sms_bridge/cli.py tests/unit/test_cli.py docs/setup-android.md docs/test-runbook.md
git commit -m "docs: add Android SMS transport runbook"
```

### Task 9: Physical SMS transport checkpoint

**Files:**
- Create: `docs/evidence/sms-transport-2026-10-03.md`
- Modify only if evidence requires a fix: files owned by Tasks 2, 4, 6, 7, or 8 plus their focused tests.

**Interfaces:**
- Consumes: the executable bridge and runbooks from Tasks 1–8.
- Produces: evidence that the real Xiaomi/SIM/USB path satisfies the spec before teammate AI integration begins.

- [ ] **Step 1: Record the physical environment**

Record Android/HyperOS version, `sms-gate` version/build, SIM/operator, Mac model/macOS version, ADB version, actual device Local Server port, and masked test phone numbers.

- [ ] **Step 2: Prove each transport half independently**

Run gateway health, outbound-only SMS, and inbound-only signed webhook tests. Record timestamps, request IDs, webhook IDs, gateway message IDs, results, and sanitized errors.

- [ ] **Step 3: Run the automated echo and resilience matrix**

Execute multi-turn, two-sender, Unicode, multipart, duplicate webhook, screen lock/background, USB reconnect, FastAPI restart, and gateway restart cases from `docs/test-runbook.md`.

- [ ] **Step 4: Run the offline acceptance sequence**

Disable Internet on both Mac and gateway while leaving cellular SMS active. Send ten consecutive inbound messages and require ten correctly addressed replies, zero duplicate replies, and complete masked logs.

- [ ] **Step 5: Fix only evidence-backed transport defects with TDD**

For each observed defect, add the smallest failing automated regression test to the owning task's test file, verify it fails, implement the minimal fix, rerun that focused test, then rerun the complete suite.

- [ ] **Step 6: Run the full verification suite**

Run: `uv run pytest -v`

Expected: all tests PASS.

Run: `uv run ruff check src tests`

Expected: exit 0.

- [ ] **Step 7: Commit the masked evidence summary**

```bash
git add docs/evidence/sms-transport-2026-10-03.md
git commit -m "test: document real SMS transport evidence"
```

Stop here if the physical checkpoint fails. Do not integrate the teammate AI until the cause is understood and the transport acceptance criteria pass.

### Task 10: Teammate HTTP AI adapter after transport acceptance

**Files:**
- Create: `src/sms_bridge/adapters/ai/http.py`
- Create: `tests/contract/test_http_ai_adapter.py`
- Modify: `src/sms_bridge/main.py`
- Modify: `.env.example`
- Modify: `docs/test-runbook.md`

**Interfaces:**
- Consumes: `AiRequest`, `AiResponse`, and `AiAdapter` from Task 5.
- Produces: `HttpAiAdapter(base_url: str, client: httpx.AsyncClient)` with async `answer(request: AiRequest) -> AiResponse`.

- [ ] **Step 1: Write failing AI request contract tests**

Using `respx`, assert the request body contains exactly `request_id`, `conversation_id`, `turn_id`, `user_id`, `message`, `language`, and no more than five `history` turns. Assert it never contains `sender`, `phone`, gateway credentials, or signing secrets.

- [ ] **Step 2: Write failing AI response tests**

Assert matching `request_id` plus a nonblank `answer` succeeds; mismatched IDs, blank answers, malformed JSON, non-2xx responses, and transport timeouts raise categorized `AiAdapterError` for the processor retry policy.

- [ ] **Step 3: Implement the HTTP AI adapter**

Use the injected HTTPX client and the processor's outer 20-second timeout. Do not add model, prompt, RAG, or cacao-domain logic to this repository.

- [ ] **Step 4: Wire `SMS_BRIDGE_AI_MODE=http`**

Require `SMS_BRIDGE_AI_BASE_URL` only in HTTP mode. Keep echo as the default. App startup must fail with a redacted configuration error when HTTP mode lacks a URL.

- [ ] **Step 5: Run AI and regression verification**

Run: `uv run pytest tests/contract/test_http_ai_adapter.py tests/unit/test_message_processor.py tests/integration/test_echo_round_trip.py -v`

Expected: all tests PASS.

Run: `uv run pytest -v`

Expected: all tests PASS.

Run: `uv run ruff check src tests`

Expected: all tests PASS and lint exits 0.

- [ ] **Step 6: Commit teammate AI integration**

```bash
git add .env.example src/sms_bridge/main.py src/sms_bridge/adapters/ai/http.py tests/contract/test_http_ai_adapter.py docs/test-runbook.md
git commit -m "feat: add teammate AI HTTP adapter"
```

## Completion Gate

Before claiming completion:

1. Invoke `superpowers:verification-before-completion`.
2. Confirm Tasks 1–8 and 10 pass the automated suite.
3. Confirm Task 9 contains real-device evidence; if hardware is still unavailable, report transport as unverified rather than complete.
4. Confirm the working tree contains no secrets, database files, full phone numbers, or evidence media.
5. Request a whole-branch code review before merge or handoff.
