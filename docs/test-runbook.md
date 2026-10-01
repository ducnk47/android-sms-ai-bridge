# SMS transport acceptance runbook

Mục tiêu của runbook là tách lỗi Android/SIM/USB khỏi lỗi bridge/AI. Không tuyên bố POC pass nếu chưa có evidence từ carrier SMS thật.

## Quy tắc evidence

Với mỗi case, ghi thời gian, request ID, top-level webhook `id`, payload `messageId`, gateway outbound ID, số gửi/nhận đã mask, kết quả và sanitized error.

Hai ID inbound có vai trò khác nhau:

- Top-level webhook `id` là delivery ID và là khóa dedupe của bridge.
- Payload `messageId` là metadata/content ID; hai delivery khác nhau vẫn có thể có cùng giá trị này.

Không lưu credential, signing key, HMAC secret hoặc số điện thoại đầy đủ vào Git.

## A. Mac-only, chưa cần Android

```bash
uv sync
uv run pytest
uv run ruff check src tests
SMS_BRIDGE_APP_ENV=test uv run uvicorn sms_bridge.main:app --host 127.0.0.1 --port 8000
```

Trong terminal khác:

```bash
curl -X POST http://127.0.0.1:8000/dev/simulate-inbound \
  -H 'Content-Type: application/json' \
  -d '{"sender":"0901234567","text":"Cây cacao bị đốm đen","event_id":"mac-only-1"}'
```

Pass khi API trả 202, SQLite có turn và fake gateway ghi đúng một reply `AI: ...` tới số normalized. Đây chỉ là software acceptance, chưa chứng minh carrier SMS.

## B. Preflight với Android

Hoàn thành [setup Android](setup-android.md), rồi chạy:

```bash
adb devices -l
uv run sms-bridge adb-tunnels --device-port 8080
uv run sms-bridge gateway-health
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

Pass khi ADB là `device`, Local Server `GET /health` pass/warn, bridge `/health` pass và `/ready` pass.

## C. Chứng minh từng nửa transport

### C1. Outbound-only

```bash
uv run sms-bridge send-test <masked-test-phone> "OUTBOUND-<timestamp>"
```

Pass khi điện thoại nhận được carrier SMS và gateway trả message ID. Lệnh này gọi Local Server `POST /message`.

### C2. Inbound-only webhook capture

1. Chạy bridge với `SMS_BRIDGE_AI_MODE=echo`.
2. Đăng ký bằng `uv run sms-bridge register-webhook`; đây là Local Server `POST /webhooks`.
3. Gửi `INBOUND-<timestamp>` từ máy test tới Xiaomi.
4. Xác nhận log bridge có masked sender, request ID và webhook top-level `id`.

Pass khi webhook có HMAC headers hợp lệ, API trả 202 và SQLite có đúng một turn.

## D. Full echo acceptance

Chạy lần lượt:

1. Basic echo: gửi một SMS tiếng Việt có dấu; nhận đúng `AI: <input>`.
2. Multi-turn: gửi 6 câu liên tiếp từ một số; turn tăng 1→6 và AI history ở lượt 6 có đúng 5 lượt gần nhất.
3. Two-sender isolation: xen kẽ hai số A/B; mọi reply về đúng số gốc, không chéo câu trả lời.
4. Unicode/multipart: gửi nội dung có `đ`, dấu tiếng Việt và emoji; ghi số part thực tế. Một inbound logical turn chỉ sinh một outbound logical response.
5. Duplicate replay: phát lại cùng raw webhook với cùng top-level `id`; API vẫn ACK nhưng không có SMS reply thứ hai.
6. Same payload ID: hai webhook có top-level `id` khác nhau nhưng cùng payload `messageId`; phải tạo hai turn.
7. USB reconnect: rút/cắm cáp, tạo lại `adb forward` và `adb reverse`, rồi echo lại.
8. Process restart: dừng bridge khi turn đang processing hoặc sending; start lại và xác nhận answer đã lưu không gọi AI lần hai, không gửi sai người.
9. Locked-screen: khóa Xiaomi ít nhất 5 phút rồi gửi/nhận; Local Server phải còn persistent notification.
10. Offline: tắt Wi-Fi và mobile data trên điện thoại người dùng. Sau đó tắt Internet trên Xiaomi/Mac nếu Local Server vẫn hoạt động; giữ cellular voice/SMS và USB. Chạy lại echo.

## E. Ten-message offline acceptance

Trong trạng thái offline đã chọn, gửi 10 message có marker duy nhất từ hai máy/số, ví dụ A01, B01, A02, B02… Không gửi nhanh hơn rate limit POC; cách nhau trên 10 giây hoặc chia thành hai phút.

Pass khi:

- Đủ 10 inbound top-level webhook `id` duy nhất.
- Đủ 10 logical reply và mỗi marker xuất hiện đúng một lần.
- Reply A chỉ tới A, reply B chỉ tới B.
- Không cần Internet cho đường đã tuyên bố offline.
- Không mất Local Server khi màn hình khóa.
- Sau restart/reconnect, queued work được hoàn tất mà không nhân đôi reply.

## F. Failure triage

1. Không thấy Android trong ADB: cáp/data mode/RSA authorization.
2. `gateway-health` fail: Local Server Online, cổng thật, Basic Auth, `adb forward`.
3. Outbound pass nhưng inbound fail: registered URL, `adb reverse`, FastAPI port, signing key, Internet requirement trong webhook settings.
4. HTTP 401: kiểm tra cùng signing key, `X-Timestamp`, raw body và clock lệch không quá 5 phút.
5. HTTP 422: kiểm tra event `sms:received`, sender, timestamp và payload schema.
6. Chỉ fail khi khóa màn hình: HyperOS battery/background/Autostart và persistent notification.
7. Duplicate reply: so top-level `id`; payload `messageId` không phải khóa dedupe chính.

Sau khi tất cả mục C–E pass, tạo evidence file trong `docs/evidence/` và mới bắt đầu thay echo bằng HTTP AI adapter. Voice vẫn là spike riêng sau SMS.
