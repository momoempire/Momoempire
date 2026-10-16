/**
 * Browser WebRTC helper for OpenAI Realtime (ephemeral client secret flow).
 * Docs: https://developers.openai.com/api/docs/guides/realtime-webrtc
 * Never pass a sk_ API key here — only ek_ ephemeral secrets from our backend.
 */

export const REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

export function assertEphemeralSecret(value) {
  if (!value || typeof value !== "string") {
    throw new Error("Missing ephemeral client secret");
  }
  if (value.startsWith("sk_")) {
    throw new Error("Refusing to use a non-ephemeral API key in the browser");
  }
  if (!value.startsWith("ek_")) {
    throw new Error("Unexpected client secret format");
  }
  return value;
}

/**
 * Connect mic → OpenAI Realtime via WebRTC using an ephemeral token.
 * @returns {Promise<{ pc: RTCPeerConnection, dc: RTCDataChannel, stream: MediaStream, audio: HTMLAudioElement, hangup: () => void }>}
 */
export async function connectRealtimeVoice({
  clientSecret,
  webrtcUrl = REALTIME_CALLS_URL,
  onEvent,
  onRemoteTrack,
}) {
  const secret = assertEphemeralSecret(clientSecret);
  const pc = new RTCPeerConnection();
  const audio = document.createElement("audio");
  audio.autoplay = true;

  pc.ontrack = (e) => {
    const [track] = e.streams?.[0] ? e.streams[0].getAudioTracks() : [e.track];
    if (track) {
      audio.srcObject = new MediaStream([track]);
      audio.play?.().catch(() => {});
      onRemoteTrack?.(track);
    }
  };

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    pc.close();
    const e = new Error("microphone_permission_denied");
    e.cause = err;
    throw e;
  }
  for (const track of stream.getAudioTracks()) {
    pc.addTrack(track, stream);
  }

  const dc = pc.createDataChannel("oai-events");
  dc.addEventListener("message", (ev) => {
    try {
      const event = JSON.parse(ev.data);
      onEvent?.(event);
    } catch (_) { /* ignore non-JSON */ }
  });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  // Wait briefly for ICE gathering (best-effort).
  if (pc.iceGatheringState !== "complete") {
    await new Promise((resolve) => {
      const t = setTimeout(resolve, 2500);
      const check = () => {
        if (pc.iceGatheringState === "complete") {
          clearTimeout(t);
          pc.removeEventListener("icegatheringstatechange", check);
          resolve();
        }
      };
      pc.addEventListener("icegatheringstatechange", check);
    });
  }

  const sdp = pc.localDescription?.sdp;
  if (!sdp) {
    hangupAll(pc, dc, stream, audio);
    throw new Error("Missing local SDP offer");
  }

  const sdpResponse = await fetch(webrtcUrl, {
    method: "POST",
    body: sdp,
    headers: {
      Authorization: `Bearer ${secret}`,
      "Content-Type": "application/sdp",
    },
  });
  if (!sdpResponse.ok) {
    hangupAll(pc, dc, stream, audio);
    throw new Error(`WebRTC negotiate failed (${sdpResponse.status})`);
  }
  const answerSdp = await sdpResponse.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

  const hangup = () => hangupAll(pc, dc, stream, audio);
  return { pc, dc, stream, audio, hangup };
}

function hangupAll(pc, dc, stream, audio) {
  try { dc?.close(); } catch (_) {}
  try { stream?.getTracks?.().forEach((t) => t.stop()); } catch (_) {}
  try { pc?.close(); } catch (_) {}
  if (audio) {
    try { audio.srcObject = null; } catch (_) {}
  }
}

/** Pure helper for unit tests / UI: format remaining seconds. */
export function formatVoiceCountdown(totalSeconds, elapsedMs) {
  const left = Math.max(0, Math.ceil(totalSeconds - elapsedMs / 1000));
  return left;
}
