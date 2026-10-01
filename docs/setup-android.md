# Setup Xiaomi 14T Pro làm SMS gateway qua USB

Tài liệu này dành cho thiết bị thử nghiệm đã xác định: Xiaomi 14T Pro, HyperOS, Android 16. Mục tiêu là kết nối máy với MacBook qua USB; không mở API hay webhook ra Internet.

Nguồn tham chiếu chính thức:

- [Cài APK và quyền Android](https://docs.sms-gate.app/installation/)
- [Local Server mode](https://docs.sms-gate.app/getting-started/local-server/)
- [Webhook, ADB reverse và payload signing](https://docs.sms-gate.app/features/webhooks/)
- [Xử lý Local Server bị Android dừng nền](https://docs.sms-gate.app/faq/local-server/)

## 1. Ghi lại baseline trước khi cài

Không đoán version từ tài liệu. Trên ngày test, ghi các giá trị thật vào evidence:

- Model Xiaomi 14T Pro, HyperOS build, Android 16 build và security patch.
- SIM slot, nhà mạng và số test đã mask.
- macOS, model Mac và kết quả `adb version`.
- Tên file APK, app version/build và SHA-256 của APK.
- Cổng Local Server hiển thị trong app; thường là 8080 nhưng phải dùng giá trị thật.

## 2. Cài SMS Gateway for Android

1. Tải prebuilt APK từ [GitHub Releases chính thức](https://github.com/android-sms-gateway/android-sms-gateway/releases). Không dùng file được gửi lại qua chat hay nguồn mirror.
2. Với Android 15+, Play Protect có thể chặn sideload. Nếu bắt buộc phải tắt Scan apps, chỉ tắt trong lúc cài rồi bật lại ngay.
3. Bản Standard/Secure cho phép HTTP tới `127.0.0.1`, nên phù hợp với ADB reverse. Không cần insecure build cho kiến trúc USB này.
4. Cấp `SEND_SMS`, `RECEIVE_SMS`, và `READ_PHONE_STATE`/quyền SIM khi app yêu cầu. Chọn đúng SIM gửi mặc định nếu máy có hai SIM.
5. Gửi và nhận thủ công một SMS trong app trước khi nối bridge.

## 3. Giữ app sống trên HyperOS

Trên Xiaomi, thực hiện cả các mục sau và ghi lại tên menu thực tế vì HyperOS có thể đổi nhãn:

- Cho phép Autostart/background activity.
- Battery saver: No restrictions cho SMS Gateway.
- Trong SMS Gateway: Settings → System → tắt battery optimization cho app.
- Khóa app trong màn hình Recent apps nếu ROM hỗ trợ.
- Cho phép notification; khi Local Server chạy phải thấy persistent notification.
- Trong demo dài, cắm sạc. Test lại cả lúc màn hình khóa ít nhất 5 phút.

Nếu notification biến mất hoặc `GET /health` ngừng phản hồi, coi như app đã bị OS kill; không đổ lỗi cho bridge trước khi kiểm tra battery policy.

## 4. Bật USB debugging

1. Settings → About phone → chạm nhiều lần vào OS version để bật Developer options.
2. Settings → Additional settings → Developer options → bật USB debugging.
3. Cắm cáp dữ liệu USB trực tiếp vào Mac, chọn chế độ cho phép data nếu được hỏi.
4. Chấp nhận RSA fingerprint trên điện thoại và chỉ chọn “Always allow” cho Mac test tin cậy.
5. Kiểm tra:

```bash
adb devices -l
```

Trạng thái phải là `device`, không phải `unauthorized` hoặc `offline`.

## 5. Bật Local Server

1. Mở SMS Gateway và bật Local Server.
2. Nhấn trạng thái `Offline` để chuyển thành `Online`.
3. Ghi lại username, password Basic Auth và cổng thực tế. Không chụp/commit màn hình có credential.
4. Settings → Webhooks → Signing Key: lấy hoặc tạo signing key. Giá trị này phải khớp `SMS_BRIDGE_WEBHOOK_SIGNING_KEY` trên Mac.
5. Với test offline, tắt yêu cầu Internet cho webhook nếu app có tùy chọn này.

Local mode chỉ dùng Basic Auth. Endpoint cần dùng là `GET /health`, `POST /message`, `POST /webhooks`.

## 6. Cấu hình bridge trên Mac

```bash
cp .env.example .env
```

Điền `.env`:

```dotenv
SMS_BRIDGE_APP_ENV=production
SMS_BRIDGE_GATEWAY_MODE=local
SMS_BRIDGE_GATEWAY_BASE_URL=http://127.0.0.1:8081
SMS_BRIDGE_GATEWAY_USERNAME=<local-server-username>
SMS_BRIDGE_GATEWAY_PASSWORD=<local-server-password>
SMS_BRIDGE_WEBHOOK_SIGNING_KEY=<same-signing-key-on-phone>
SMS_BRIDGE_HMAC_USER_SECRET=<new-random-secret>
SMS_BRIDGE_AI_MODE=echo
```

Giữ `.env` local, chmod phù hợp, không paste secret vào issue/chat/log. Dùng một HMAC user secret ngẫu nhiên riêng cho POC; không dùng lại password gateway.

## 7. Tạo hai tunnel USB

Nếu Local Server đang ở port 8080:

```bash
uv run sms-bridge adb-tunnels --device-port 8080
```

Lệnh tương đương:

```bash
adb forward tcp:8081 tcp:8080
adb reverse tcp:9876 tcp:8000
```

- `adb forward`: Mac gọi Android Local Server tại `127.0.0.1:8081`.
- `adb reverse`: Android gọi FastAPI trên Mac tại `127.0.0.1:9876`.

Mỗi lần rút/cắm USB hoặc reboot điện thoại, kiểm tra lại:

```bash
adb forward --list
adb reverse --list
```

## 8. Start và đăng ký webhook

Terminal 1:

```bash
uv sync
uv run uvicorn sms_bridge.main:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
uv run sms-bridge gateway-health
uv run sms-bridge register-webhook
```

`gateway-health` gọi `GET /health` qua ADB forward. `register-webhook` gọi `POST /webhooks` và đăng ký `sms:received` tới `http://127.0.0.1:9876/webhooks/sms-gate`.

Kiểm tra trong app: Settings → Webhooks → Registered webhooks. Chỉ giữ một registration đúng URL để tránh debug nhầm.

## 9. Smoke test

Outbound-only:

```bash
uv run sms-bridge send-test 0901234567 "POC outbound từ Mac"
```

Inbound/full echo: từ điện thoại thứ hai, gửi SMS tới SIM trong Xiaomi. Kỳ vọng nhận `AI: <nội dung>` về đúng số gửi. Lặp lại khi màn hình Xiaomi đã khóa; nếu fail, kiểm tra Local Server notification, ADB mappings và `GET /health` theo thứ tự.
