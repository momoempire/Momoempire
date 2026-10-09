# Public WebRTC voice demo (EMP-DEV-007)

OFF by default. Enable only after Brann approves spend:

```
PUBLIC_VOICE_DEMO_ENABLED=true
OPENAI_API_KEY=...
PUBLIC_VOICE_DEMO_MODEL=gpt-realtime          # optional
PUBLIC_VOICE_DEMO_VOICE=alloy                 # optional
PUBLIC_VOICE_DEMO_MAX_SECONDS=60              # client auto hang-up; default 60
```

Flow (official ephemeral-token WebRTC):

1. Browser → `POST /api/public/demo/voice-token` (rate-limited; requires flag + key).
2. Server → `POST https://api.openai.com/v1/realtime/client_secrets` with industry-grounded instructions; returns `ek_…` only.
3. Browser WebRTC → `POST https://api.openai.com/v1/realtime/calls` with `Authorization: Bearer ek_…` and SDP.

Docs: [Realtime WebRTC](https://developers.openai.com/api/docs/guides/realtime-webrtc), [client secrets](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create/).

## Cost estimate (not a purchase)

Audio for `gpt-realtime` / `gpt-realtime-2.1` family: **$32 / 1M input audio tokens**, **$64 / 1M output audio tokens** ([OpenAI pricing](https://developers.openai.com/api/docs/pricing), [realtime costs](https://developers.openai.com/api/docs/guides/realtime-costs)).

Tokenization note from OpenAI: ~1 input audio token per 100 ms user audio; ~1 output audio token per 50 ms assistant audio.

**Estimate for 1 minute balanced talk** (30s user + 30s assistant):  
user ≈ 300 tokens × $32/1M ≈ **$0.01**; assistant ≈ 600 tokens × $64/1M ≈ **$0.04**; total ≈ **$0.05 / demo-minute** (estimate). Caps (`PUBLIC_VOICE_DEMO_MAX_SECONDS`) bound worst-case cost per visitor.
