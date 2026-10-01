# SMS Android-to-Mac Bridge Design

**Date:** 2026-10-01

**Status:** Draft for written review

**Event:** Small AI for Development Hackathon, October 3–4, 2026

## 1. Purpose

Build and validate a carrier-SMS transport path for an offline-capable cacao advisor. A farmer or tester sends an SMS to an Android gateway. The gateway passes the message over USB to a Python service on a MacBook, the service obtains a response from an AI adapter, and the gateway sends the response back by SMS.

This design validates transport before integrating the native Android cacao application or a production AI model. The first end-to-end response is deterministic echo behavior. After the SMS path is proven, the same bridge can call the teammate-owned AI API without changing the gateway-facing interface.

## 2. Goals and success criteria

The design must support:

- A Xiaomi 14T Pro running Android 16 and HyperOS as the gateway device.
- A real Vietnamese SIM in the gateway device.
- USB connectivity between the gateway and an Apple Silicon MacBook Pro.
- SMS Gateway for Android (`sms-gate`) in Local Server mode.
- A Python 3.12 and FastAPI integration service on the Mac.
- Multi-turn, text-chat-style conversations keyed by the sender's phone number.
- A replaceable AI adapter: deterministic echo first, teammate HTTP API later.
- Operation without Internet on the sender phone, gateway phone, or Mac once required software is installed.
- Durable correlation, deduplication, retry state, and evidence-quality logs.

The transport spike passes when all of the following are demonstrated:

1. A tester sends an SMS using a real Vietnamese SIM.
2. The gateway receives it and delivers an inbound webhook to the Mac through USB.
3. FastAPI processes the message once and produces an echo response.
4. The gateway sends the response to the original sender.
5. Ten consecutive round trips succeed with correct sender mapping, Vietnamese Unicode, and no duplicate replies.
6. The full round trip works while Internet access is disabled on both the gateway and Mac.

## 3. System boundary

### In scope

- Local development and tests that do not require an Android device.
- USB/ADB tunnel setup and diagnostics.
- `sms-gate` Local Server integration for inbound and outbound SMS.
- FastAPI webhook ingestion, validation, persistence, queuing, correlation, and response delivery.
- Multi-turn conversation identity and ordering.
- Echo and HTTP AI adapters.
- SQLite persistence, structured logging, retries, and deduplication.
- A reproducible Android setup guide and test runbook.

### Out of scope

- The native Android cacao application and YOLO integration.
- AI model selection, RAG, cacao knowledge quality, and model optimization.
- Production deployment, public Internet exposure, or an administration UI.
- Cloud SMS providers.
- Production-scale multi-gateway or multi-SIM routing.
- Carrier voice automation.
- Proof of operation in Côte d'Ivoire or Papua New Guinea. Those locations remain architecture-compatible only, subject to local operator, SIM, device, and service validation.

## 4. Architecture

```text
tester phone
  -> carrier SMS
  -> Xiaomi 14T Pro + active SIM + sms-gate Local Server
       -> inbound webhook through adb reverse
       -> FastAPI integration service on Mac
            -> normalize, deduplicate, persist, and queue
            -> echo AI adapter or teammate HTTP AI adapter
            -> sms-gate client through adb forward
       -> outbound carrier SMS
  -> tester phone
```

### 4.1 Android gateway

The Xiaomi runs `sms-gate` in Local Server mode and owns all Android SMS permissions and carrier interaction. It is a transport bridge, never the AI runtime.

The test operator enables Developer Options and USB debugging, installs the APK, grants `SEND_SMS`, `RECEIVE_SMS`, and the optional SIM-selection permission, and disables battery/background restrictions for the gateway app during the spike.

### 4.2 USB transport

Two ADB tunnels keep the integration private and Internet-independent:

- **Mac to Android:** `adb forward` exposes the device's Local Server API on a loopback port on the Mac.
- **Android to Mac:** `adb reverse` exposes the FastAPI webhook endpoint as an Android loopback address.

Ports are configuration, not constants. The proposed defaults are:

- FastAPI on Mac: `127.0.0.1:8000`.
- Android webhook target: `http://127.0.0.1:9876/webhooks/sms-gate`.
- ADB reverse mapping: Android TCP `9876` to Mac TCP `8000`.
- Mac gateway base URL: `http://127.0.0.1:8081`.
- ADB forward mapping: Mac TCP `8081` to the Local Server port displayed by `sms-gate`.

The runbook must discover and record the actual `sms-gate` device port instead of assuming a fixed value.

### 4.3 FastAPI integration service

The service contains small units with explicit interfaces:

- A webhook API that accepts and acknowledges gateway events.
- A gateway payload adapter that maps the installed `sms-gate` version to internal models.
- A durable message processor that polls pending SQLite records.
- An AI adapter interface with echo and HTTP implementations.
- An SMS gateway client interface with fake and `sms-gate` implementations.
- SQLite repositories for conversations, inbound events, processing state, and outbound attempts.

The service binds to loopback only. It does not require Docker, Redis, Celery, PostgreSQL, or a public HTTPS endpoint.

## 5. Data model and contracts

### 5.1 Normalized inbound message

The gateway adapter converts the provider-specific webhook into:

```json
{
  "request_id": "2bcf47b8-b780-45ff-af19-319e0c7893ee",
  "event_id": "gateway-stable-message-id",
  "sender": "+84901234567",
  "text": "Cay cacao bi dom den, xu ly the nao?",
  "received_at": "2026-10-01T12:00:00Z"
}
```

`request_id` is generated by the bridge. `event_id` uses the stable ID supplied by the gateway. If the installed gateway version supplies no stable ID, the adapter creates a deterministic SHA-256 digest from device ID, normalized sender, gateway timestamp, and message body.

Phone numbers are normalized to an E.164-like representation when the source data permits it. The raw sender value is retained only for diagnostics and fallback reply routing.

### 5.2 AI request

```json
{
  "request_id": "2bcf47b8-b780-45ff-af19-319e0c7893ee",
  "conversation_id": "2f143e2a-9c3c-4a66-9fcf-4c8e0b8e8656",
  "turn_id": 3,
  "user_id": "opaque-hmac-user-id",
  "message": "Nếu đất quá ẩm thì sao?",
  "language": "vi",
  "history": [
    {
      "user": "Cây cacao bị vàng lá.",
      "assistant": "Hãy kiểm tra độ ẩm đất và tình trạng bộ rễ."
    }
  ]
}
```

The bridge derives `user_id` using HMAC-SHA256 over the normalized phone number and an application secret. The AI does not receive the phone number. `history` contains no more than the five latest completed turns and excludes the current message. It is an empty array on the first turn, making the HTTP AI contract stateless and deterministic from the bridge's perspective.

### 5.3 AI response

```json
{
  "request_id": "2bcf47b8-b780-45ff-af19-319e0c7893ee",
  "answer": "Giảm tưới, khai thông nước và kiểm tra rễ để phát hiện dấu hiệu thối."
}
```

The response `request_id` must equal the request. The initial maximum reply length is configurable and defaults to 320 Unicode characters. `sms-gate` may encode the logical response as multipart SMS. The bridge creates only one logical response per inbound turn.

### 5.4 Conversation behavior

Each normalized sender maps to one active conversation. A conversation remains active for 30 minutes after its latest inbound message and retains the latest five completed turns for AI context. A message arriving after that 30-minute inactivity window starts a new conversation.

Messages from one sender are processed FIFO. Different senders may be processed concurrently. Multiple user messages and responses form a normal multi-turn text conversation; the one-response rule applies to each inbound turn, not to the entire conversation.

The bridge owns transport identity, ordering, and the recent-turn record. It sends the recent turns in `history`; a downstream AI may also use `conversation_id` for caching or tracing but must not depend on hidden server-side conversation state for correctness.

## 6. Processing and recovery

### 6.1 State machine

```text
received -> queued -> processing -> answered -> sending -> sent
                         |             |           |
                         v             v           v
                     ai_failed     send_failed  send_failed
```

Webhook handling performs validation, inserts a unique inbound event, and returns HTTP `202 Accepted` before AI processing. A duplicate event returns a successful acknowledgment without enqueuing another job.

The worker polls SQLite rather than relying only on an in-memory queue. On service restart, it returns stale `processing` or `sending` records to a recoverable state and resumes them without creating a second logical response.

### 6.2 Retry policy

- AI request timeout: 20 seconds.
- AI retry count: one retry after the first failure.
- Final AI failure response: `Hệ thống đang bận, vui lòng thử lại sau.` Tests cover both GSM-compatible ASCII and Unicode text.
- Gateway send attempts: three total attempts, with retry delays of 2 and 10 seconds after the first and second failures.
- Exhausted sends remain `send_failed` for inspection and explicit manual replay; they are not retried forever.

Retry operations use the existing `request_id` and persisted outbound record. They never create a new logical answer.

### 6.3 Input controls

- Empty or whitespace-only input receives a short usage message.
- Input above 1,000 Unicode characters receives a request to split the question.
- The worker listens only to inbound-received events, preventing sent-message events from producing loops.
- A configurable per-sender rate limit defaults to six inbound messages per minute. Exceeding it creates at most one rate-limit response per minute.

## 7. Persistence

SQLite contains four logical tables:

- `conversations`: conversation ID, protected sender identity, timestamps, and next turn number.
- `inbound_events`: unique event ID, request ID, sender routing data, text, timestamps, state, and processing attempts.
- `outbound_messages`: request ID, recipient routing data, answer, gateway message ID, state, and send attempts.
- `turns`: conversation ID, turn number, user text, assistant text, and completion timestamp.

The database is local to the Mac and excluded from version control. Full phone numbers may be stored locally because the bridge needs a return address, but logs expose only masked numbers. The POC does not claim encrypted-at-rest storage.

## 8. Observability and security

Every state transition emits structured logs with:

- `request_id`
- gateway `event_id`
- masked sender
- conversation and turn IDs
- new state
- AI and gateway latency
- retry count
- sanitized error category

Logs do not contain credentials, full phone numbers, or full message bodies by default. A development-only configuration may log message bodies for the controlled hackathon test.

Gateway credentials and the HMAC secret are loaded from environment variables and documented in `.env.example`; real values are never committed. FastAPI binds to `127.0.0.1`. The Local Server API and webhook are reachable only through physical USB/ADB loopback mappings during the POC.

The endpoints are:

```text
GET  /health
GET  /ready
POST /webhooks/sms-gate
POST /dev/simulate-inbound
```

`/health` proves the process and database are responsive. `/ready` additionally reports gateway configuration and reachability. `/dev/simulate-inbound` is registered only in development mode.

## 9. Code organization

```text
hackathon-fd/
├── README.md
├── pyproject.toml
├── .env.example
├── src/sms_bridge/
│   ├── main.py
│   ├── config.py
│   ├── api/
│   │   ├── health.py
│   │   └── webhooks.py
│   ├── domain/
│   │   ├── models.py
│   │   └── states.py
│   ├── services/
│   │   ├── message_processor.py
│   │   └── worker.py
│   ├── adapters/
│   │   ├── ai/
│   │   │   ├── base.py
│   │   │   ├── echo.py
│   │   │   └── http.py
│   │   └── sms/
│   │       ├── base.py
│   │       ├── fake.py
│   │       └── sms_gate.py
│   └── storage/
│       └── sqlite.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
└── docs/
    ├── setup-android.md
    └── test-runbook.md
```

Runtime dependencies are FastAPI, Uvicorn, HTTPX, Pydantic Settings, and `aiosqlite`. Test dependencies are Pytest, pytest-asyncio, and HTTP mocking support. The project runs natively on macOS.

## 10. Test strategy

### 10.1 Mac-only tests

Before an Android device is available, automated tests cover:

- Fake inbound webhook to echo AI to fake outbound gateway.
- Conversation creation, five-turn context, and 30-minute expiry.
- FIFO processing for one sender and concurrency across two senders.
- Duplicate webhook acknowledgment without duplicate processing.
- AI timeout, one retry, and fallback response.
- Gateway timeout, bounded retries, and recovery after restart.
- Vietnamese Unicode and configured length limits.
- Multipart logical-response handling at the adapter boundary.
- Service restart while a message is in each recoverable state.
- Secret redaction and phone masking in logs.

### 10.2 Physical-device tests

With the Xiaomi and SIM present, the runbook verifies:

1. ADB detects and authorizes the device.
2. Both forward and reverse port mappings work.
3. The Mac sends an SMS through the gateway API.
4. A received carrier SMS reaches FastAPI as a webhook.
5. Echo produces an automated reply to the original sender.
6. Multi-turn chat maintains the correct conversation.
7. Two senders can interleave messages without crossed replies.
8. Vietnamese Unicode survives both directions.
9. Duplicate delivery does not create duplicate replies.
10. The path survives screen lock and the app running in the background.
11. USB disconnect/reconnect and service restart have documented recovery steps.
12. The complete ten-message acceptance run succeeds with Internet disabled.

The evidence bundle consists of masked structured logs, timestamps for each stage, screenshots of gateway states, and a short screen recording of the round trip.

## 11. Delivery stages

1. Scaffold the Python service, SQLite schema, fake adapters, and automated tests.
2. Complete the Mac-only echo loop and failure tests.
3. Add the real `sms-gate` client and gateway webhook adapter against the documented API.
4. Write the Android/ADB setup guide and physical test runbook.
5. Run outbound-only and inbound-only device spikes.
6. Run the full automated echo round trip and offline acceptance test.
7. Stop and document evidence before integrating the teammate AI.
8. Add the HTTP AI adapter and rerun the same transport suite.

## 12. Voice decision

Carrier voice is deferred and does not block SMS success. Asterisk `chan_mobile` requires a Linux Bluetooth subsystem, BlueZ packages, and a Linux-supported Bluetooth adapter. The current team hardware is only a MacBook Pro M4, so `chan_mobile` is not a reliable path for this hackathon.

An optional, explicitly throwaway experiment may use `scrcpy` to capture Android call audio and speakerphone acoustic playback for TTS. Because this does not provide a robust bidirectional audio injection path, it is not a supported gateway architecture or acceptance criterion.

Future carrier-voice work should use either:

- a Linux host or Raspberry Pi with a dedicated USB Bluetooth adapter and `chan_mobile`, or
- a verified LTE/VoLTE SIP gateway connected to Asterisk.

Turn-based speech-to-text, AI response, and text-to-speech is sufficient; realtime streaming is not required.

## 13. Primary references

- [SMS Gateway for Android getting started](https://docs.sms-gate.app/getting-started/)
- [SMS Gateway webhook and ADB guidance](https://docs.sms-gate.app/features/webhooks/)
- [Android Debug Bridge documentation](https://developer.android.com/tools/adb)
- [Asterisk Mobile Channel requirements](https://docs.asterisk.org/Configuration/Channel-Drivers/Mobile-Channel/Mobile-Channel-Requirements/)
- [Asterisk Mobile Channel features](https://docs.asterisk.org/Configuration/Channel-Drivers/Mobile-Channel/Mobile-Channel-Features/)
- [scrcpy audio documentation](https://github.com/Genymobile/scrcpy/blob/master/doc/audio.md)
