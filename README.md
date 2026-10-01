# Small AI for Development Hackathon — SMS/Voice Access Spike

## Context

- Event: Small AI for Development Hackathon, October 3–4, 2026.
- Product concept: an offline-capable cacao advisor.
- This workstream is limited to investigating how farmers reach a teammate-owned AI agent through carrier SMS or voice lines.
- Target: a credible POC that fits in roughly 20 hours. Do not build or choose the AI/model here.

## Primary technical direction

Build the SMS path first:

```text
farmer phone
  -> carrier SMS
  -> Android phone + active SIM + SMS Gateway for Android (sms-gate)
  -> USB/ADB port forwarding or local LAN
  -> laptop integration service
  -> teammate AI API or laptop-local AI
  -> reply through sms-gate and the carrier
```

`sms-gate` is only the SMS-to-HTTP/HTTP-to-SMS bridge. It is not the AI. The AI may be local on the laptop or behind an API owned by teammates.

Keep two deployment cases explicit:

1. Farmer phone has no Internet, while the backend/laptop may have Internet.
2. Farmer phone and backend/laptop are both offline; cellular SMS/voice still works. This is the strongest demo if achievable.

Do not assume Twilio supports suitable two-way SMS in Vietnam, Côte d'Ivoire, or Papua New Guinea.

## Optional voice paths

Time-box voice work until SMS succeeds. Accept turn-based voice for the POC; realtime streaming is not required.

- Android + Bluetooth + Linux/Asterisk `chan_mobile`.
- A borrowed, verified-working LTE/VoLTE SIP gateway + Asterisk.

## Hardware baseline

- 2 Android phones.
- 2 active SIMs.
- 1 laptop.
- 1 USB cable.
- Optional local Wi-Fi router/access point.
- For serious voice testing: Linux Bluetooth/Asterisk or a working VoLTE/SIP gateway.

## Success criteria

- Automated two-way SMS round trip works using real Vietnamese SIMs.
- The farmer/test phone needs no Internet.
- Ideally the laptop/backend also needs no Internet.
- Demo shows the complete request/response path through the teammate AI interface (a stub is acceptable only while isolating the SMS transport).
- Claims for Côte d'Ivoire and Papua New Guinea are framed as architectural compatibility subject to operator, SIM, device, and service validation—not as proof of deployment there.

## Assumptions and boundaries

- AI/model selection, RAG, cacao knowledge, and answer quality are owned by other teammates.
- This workstream owns gateway connectivity, message ingestion, AI-interface handoff, correlation, reply delivery, and a reproducible demo.
- Avoid product implementation before the transport spike answers the high-risk questions.

## Open questions

- Which Android device/OS version and `sms-gate` build are available, and can it become the default SMS app if required?
- Is USB/ADB forwarding reliable for both the send API and inbound webhook, or is a local Wi-Fi LAN simpler?
- What exact request/response contract will the teammate AI expose, and what timeout/error behavior should the bridge use?
- How will multipart SMS, Unicode/Vietnamese text, sender normalization, duplicate delivery, and reply correlation behave?
- Can the selected SIM/operator send and receive the expected traffic without anti-spam or permission issues?
- Which voice hardware is actually available, and what is the strict time box after SMS success?

## Suggested first spike

1. Install and configure `sms-gate` on the gateway Android with a real Vietnamese SIM.
2. Prove laptop-to-phone SMS sending over local connectivity.
3. Prove inbound SMS reaches a tiny laptop webhook.
4. Join both directions with a deterministic echo response, recording timestamps and failures.
5. Replace the echo handler with an adapter for the teammate AI contract while preserving an offline/local option.
6. Run the full test with Internet disabled on the farmer phone; if feasible, disable laptop Internet too.

Stop after the spike and document evidence before expanding into voice.
