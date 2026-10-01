# Android SMS ↔ Mac AI Bridge

POC Python cho luồng carrier SMS hai chiều:

```text
điện thoại người dùng
  → SMS nhà mạng
  → Xiaomi 14T Pro + SIM + SMS Gateway for Android
  → USB/ADB
  → bridge Python trên MacBook
  → echo adapter hoặc AI adapter
  → SMS trả về đúng người gửi
```

Bridge chạy local, không cần public server. Hai tunnel USB tách biệt:

- Mac gọi Android Local Server qua `adb forward` (`127.0.0.1:8081` → cổng trên máy Android).
- Android gọi webhook trên Mac qua `adb reverse` (`127.0.0.1:9876` → `127.0.0.1:8000`).

## Trạng thái hiện tại

- Webhook `sms:received` có HMAC-SHA256, timestamp chống replay và dedupe.
- SQLite giữ conversation 30 phút, tối đa 5 turn history và phục hồi sau restart.
- Mỗi inbound SMS tạo tối đa một logical response; `sms-gate` có thể tự chia thành multipart SMS.
- Có echo AI để test toàn bộ vòng lặp trước khi nối model của team.
- Có fake gateway để chạy Mac-only khi chưa có Android.
- Chưa có bằng chứng SIM thật; cần chạy physical checkpoint trên Android trước khi coi transport đã được xác nhận.

## Chạy test trên Mac, chưa cần Android

Yêu cầu Python 3.12 và `uv`.

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run sms-bridge --help
```

Chạy bridge ở fake mode:

```bash
cp .env.example .env
uv run uvicorn sms_bridge.main:app --host 127.0.0.1 --port 8000
```

Trong terminal khác, mô phỏng inbound SMS:

```bash
curl -X POST http://127.0.0.1:8000/dev/simulate-inbound \
  -H 'Content-Type: application/json' \
  -d '{"sender":"0901234567","text":"Cây cacao bị vàng lá","event_id":"demo-1"}'
```

`GET /health` kiểm tra process; `GET /ready` kiểm tra cả SQLite và gateway.

## Chạy với Xiaomi qua USB

Sau khi cài SMS Gateway for Android, bật Local Server và USB debugging:

```bash
uv run sms-bridge adb-tunnels --device-port 8080
uv run sms-bridge gateway-health
uv run sms-bridge register-webhook
uv run sms-bridge send-test 0901234567 "POC outbound"
```

CLI Local Server dùng đúng `GET /health`, `POST /message`, và `POST /webhooks` với Basic Auth. Webhook được đăng ký tới `http://127.0.0.1:9876/webhooks/sms-gate`.

## Cấu hình

Tạo `.env` từ `.env.example` và điền tối thiểu:

```dotenv
SMS_BRIDGE_GATEWAY_MODE=local
SMS_BRIDGE_GATEWAY_BASE_URL=http://127.0.0.1:8081
SMS_BRIDGE_GATEWAY_USERNAME=<from-android-app>
SMS_BRIDGE_GATEWAY_PASSWORD=<from-android-app>
SMS_BRIDGE_WEBHOOK_SIGNING_KEY=<same-key-as-android-app>
SMS_BRIDGE_HMAC_USER_SECRET=<random-local-secret>
```

Không commit `.env`, credential, signing key, số điện thoại đầy đủ, ảnh QR, hoặc screenshot có secret. `SMS_BRIDGE_HMAC_USER_SECRET` chỉ dùng tạo user ID ẩn danh trước khi gọi AI.

## Contract AI tối thiểu

AI nhận `request_id`, `conversation_id`, `turn_id`, `user_id` ẩn danh, `message`, `language`, và tối đa 5 turn history. AI không nhận số điện thoại. Response trả cùng `request_id` và một `answer`; bridge giới hạn answer ở 320 Unicode code point.

## Phạm vi

POC hiện ưu tiên SMS. Native Android app/YOLO, HTTP AI thật, voice/Asterisk, cloud deployment và kiểm chứng nhà mạng ngoài Việt Nam là các workstream riêng. Chỉ mở voice spike sau khi physical SMS checkpoint pass.
