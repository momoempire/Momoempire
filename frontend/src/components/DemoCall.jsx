import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Phone, Send, Mic, Sparkles, Zap, PhoneOff } from "lucide-react";
import { toast } from "sonner";
import { connectRealtimeVoice, formatVoiceCountdown } from "@/lib/realtimeVoice";

const INDUSTRY_KEYS = ["hvac", "dental", "legal", "salon"];

export default function DemoCall() {
  const { t, i18n } = useTranslation();
  const [industry, setIndustry] = useState("hvac");
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]); // {role,content}
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [starting, setStarting] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceState, setVoiceState] = useState("idle"); // idle | connecting | live | ended
  const [voiceMeta, setVoiceMeta] = useState(null);
  const [voiceSecondsLeft, setVoiceSecondsLeft] = useState(null);
  const endRef = useRef(null);
  const recRef = useRef(null);
  const voiceRef = useRef(null);
  const voiceTimerRef = useRef(null);
  const voiceStartedAt = useRef(0);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  useEffect(() => () => {
    // Cleanup on unmount
    try { voiceRef.current?.hangup?.(); } catch (_) {}
    clearInterval(voiceTimerRef.current);
  }, []);

  const industries = useMemo(() => (
    INDUSTRY_KEYS.map((key) => ({ key, label: t(`landing.industry_${key}_label`) }))
  ), [t]);

  const quickPrompts = useMemo(() => ({
    hvac: [1, 2, 3].map((n) => t(`landing.demo_prompt_hvac_${n}`)),
    dental: [1, 2, 3].map((n) => t(`landing.demo_prompt_dental_${n}`)),
    legal: [1, 2, 3].map((n) => t(`landing.demo_prompt_legal_${n}`)),
    salon: [1, 2, 3].map((n) => t(`landing.demo_prompt_salon_${n}`)),
  }), [t]);

  const start = async (chosen) => {
    setStarting(true);
    try {
      const lang = (i18n.language || "en").slice(0, 2);
      const { data } = await api.post("/public/demo/start", { industry: chosen || industry, lang });
      setSession(data);
      setMessages([{ role: "ai", content: data.greeting }]);
    } catch (e) { toast.error(errMessage(e)); }
    finally { setStarting(false); }
  };

  const send = async (text) => {
    const msg = (text ?? input).trim();
    if (!msg || !session) return;
    setMessages((m) => [...m, { role: "caller", content: msg }]);
    setInput("");
    setSending(true);
    try {
      const { data } = await api.post("/public/demo/turn", { session_id: session.session_id, text: msg });
      setMessages((m) => [...m, { role: "ai", content: data.reply }]);
      if (data.ended) setSession({ ...session, ended: true });
    } catch (e) { toast.error(errMessage(e)); }
    finally { setSending(false); }
  };

  const toggleMic = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { toast.error(t("landing.demo_mic_unsupported")); return; }
    if (listening) { try { recRef.current?.stop(); } catch (_) {} setListening(false); return; }
    const rec = new SR();
    rec.lang = (i18n.language || "en").startsWith("es") ? "es-ES" : "en-US"; rec.continuous = false; rec.interimResults = false;
    rec.onresult = (e) => { const transcript = e.results[0][0].transcript; setInput(transcript); send(transcript); };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    rec.start(); recRef.current = rec; setListening(true);
  };

  const stopVoice = (ended = true) => {
    clearInterval(voiceTimerRef.current);
    try { voiceRef.current?.hangup?.(); } catch (_) {}
    voiceRef.current = null;
    setVoiceSecondsLeft(null);
    setVoiceState(ended ? "ended" : "idle");
  };

  const startVoice = async () => {
    setVoiceState("connecting");
    setVoiceMeta(null);
    try {
      const lang = (i18n.language || "en").slice(0, 2);
      const { data } = await api.post("/public/demo/voice-token", { industry, lang });
      if (!data?.available || !data?.value) {
        setVoiceState("idle");
        toast.message(t("landing.voice_unavailable"), {
          description: data?.reason || t("landing.voice_fallback_hint"),
        });
        return;
      }
      setVoiceMeta(data);
      const conn = await connectRealtimeVoice({
        clientSecret: data.value,
        webrtcUrl: data.webrtc_url,
        onEvent: (ev) => {
          // Surface assistant transcripts into the chat pane when present.
          const typ = ev?.type || "";
          if (typ.includes("output_audio_transcript.done") || typ === "response.audio_transcript.done") {
            const text = ev.transcript || ev.text;
            if (text) setMessages((m) => [...m, { role: "ai", content: text }]);
          }
        },
      });
      voiceRef.current = conn;
      setVoiceState("live");
      voiceStartedAt.current = Date.now();
      const cap = data.max_duration_seconds || 60;
      setVoiceSecondsLeft(cap);
      voiceTimerRef.current = setInterval(() => {
        const left = formatVoiceCountdown(cap, Date.now() - voiceStartedAt.current);
        setVoiceSecondsLeft(left);
        if (left <= 0) {
          toast.message(t("landing.voice_time_up"));
          stopVoice(true);
        }
      }, 500);
    } catch (e) {
      setVoiceState("idle");
      if (e?.message === "microphone_permission_denied") {
        toast.error(t("landing.voice_mic_denied"));
      } else {
        toast.error(errMessage(e) || t("landing.voice_connect_failed"));
      }
    }
  };

  if (!session && voiceState === "idle") {
    return (
      <div className="glass-crystal rounded-2xl p-8 text-white" data-testid="demo-card-start">
        <div className="flex items-center gap-2 overline text-white/70"><Sparkles className="h-3.5 w-3.5" />{t("landing.demo_live_badge")}</div>
        <h3 className="font-display text-3xl md:text-4xl mt-2 tracking-tight">{t("landing.demo_pick_title")}</h3>
        <p className="text-white/70 mt-3 max-w-lg">{t("landing.demo_pick_sub")}</p>
        <div className="mt-6 flex flex-wrap gap-2" role="group" aria-label={t("landing.industry")}>
          {industries.map((i) => (
            <button key={i.key} type="button" onClick={() => setIndustry(i.key)}
              aria-pressed={industry === i.key}
              className={`px-4 py-2 rounded-full border text-sm transition ${industry === i.key ? "bg-white text-black border-white" : "border-white/20 text-white/80 hover:bg-white/10"}`}
              data-testid={`demo-industry-${i.key}`}>{i.label}</button>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button className="h-11 bg-white text-black hover:bg-white/90" onClick={() => start()} disabled={starting} data-testid="demo-start-btn">
            <Phone className="h-4 w-4 mr-1.5" />{starting ? t("landing.demo_starting") : t("landing.demo_start")}
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-11 bg-transparent border-white/30 text-white hover:bg-white/10"
            onClick={startVoice}
            data-testid="demo-voice-start-btn"
            aria-label={t("landing.voice_talk_cta")}
          >
            <Mic className="h-4 w-4 mr-1.5" />{t("landing.voice_talk_cta")}
          </Button>
        </div>
        <p className="text-[11px] text-white/50 mt-3">{t("landing.voice_off_by_default_hint")}</p>
      </div>
    );
  }

  if (voiceState === "connecting" || voiceState === "live" || (voiceState === "ended" && !session)) {
    return (
      <div className="glass-crystal rounded-2xl p-6 text-white flex flex-col" style={{ minHeight: 420 }} data-testid="demo-card-voice">
        <div className="flex items-center justify-between gap-3 pb-3 border-b border-white/10">
          <div>
            <div className="text-xs text-white/60 uppercase tracking-wide">
              {t("landing.voice_label")} · {voiceMeta?.business || t(`landing.industry_${industry}_label`)}
            </div>
            <div className="font-medium">
              {voiceMeta?.ai_name || "AI"} · {t("landing.demo_role_suffix")}
            </div>
          </div>
          <Badge
            className={
              voiceState === "live"
                ? "bg-emerald-400/20 text-emerald-200 border border-emerald-300/30"
                : voiceState === "connecting"
                  ? "bg-amber-400/20 text-amber-100 border border-amber-300/30"
                  : "bg-white/10 text-white/70 border border-white/20"
            }
            data-testid="demo-voice-status"
          >
            {voiceState === "connecting" && t("landing.voice_connecting")}
            {voiceState === "live" && t("landing.voice_live")}
            {voiceState === "ended" && t("landing.voice_ended")}
          </Badge>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center text-center px-4 py-10">
          {voiceState === "connecting" && (
            <p className="text-white/70" data-testid="demo-voice-connecting">{t("landing.voice_connecting_detail")}</p>
          )}
          {voiceState === "live" && (
            <>
              <div className="h-16 w-16 rounded-full bg-emerald-400/20 border border-emerald-300/40 grid place-items-center mb-4" aria-hidden>
                <Mic className="h-7 w-7 text-emerald-200" />
              </div>
              <p className="text-white/80">{t("landing.voice_speak_now")}</p>
              {voiceSecondsLeft != null && (
                <p className="text-xs text-white/50 mt-2 font-mono" data-testid="demo-voice-countdown">
                  {t("landing.voice_seconds_left", { n: voiceSecondsLeft })}
                </p>
              )}
            </>
          )}
          {voiceState === "ended" && (
            <>
              <p className="font-medium">{t("landing.voice_ended_title")}</p>
              <p className="text-sm text-white/70 mt-2">{t("landing.voice_ended_sub")}</p>
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-2 justify-center pt-2">
          {voiceState === "live" && (
            <Button
              type="button"
              variant="outline"
              className="bg-rose-500/20 border-rose-300/40 text-rose-100 hover:bg-rose-500/30"
              onClick={() => stopVoice(true)}
              data-testid="demo-voice-hangup-btn"
              aria-label={t("landing.voice_hangup")}
            >
              <PhoneOff className="h-4 w-4 mr-1.5" />{t("landing.voice_hangup")}
            </Button>
          )}
          {(voiceState === "ended" || voiceState === "connecting") && (
            <Button
              type="button"
              className="bg-white text-black hover:bg-white/90"
              onClick={() => { stopVoice(false); start(); }}
              data-testid="demo-voice-fallback-text-btn"
            >
              {t("landing.voice_try_text")}
            </Button>
          )}
          {voiceState === "ended" && (
            <a href="/signup"><Button className="bg-white/10 border border-white/20 text-white hover:bg-white/20" data-testid="demo-voice-signup-btn">{t("landing.demo_wrap_cta")}</Button></a>
          )}
        </div>
      </div>
    );
  }

  const turnsLeft = session.max_turns - (messages.filter((m) => m.role === "caller").length);

  return (
    <div className="glass-crystal rounded-2xl p-6 text-white flex flex-col" style={{ minHeight: 520 }} data-testid="demo-card-live">
      <div className="flex items-center justify-between gap-3 pb-3 border-b border-white/10">
        <div>
          <div className="text-xs text-white/60 uppercase tracking-wide">{t("landing.demo_label")} · {session.business}</div>
          <div className="font-medium">{session.ai_name} · {t("landing.demo_role_suffix")}</div>
        </div>
        <Badge className="bg-emerald-400/20 text-emerald-200 border border-emerald-300/30">{t("landing.demo_live")}</Badge>
      </div>
      <div className="flex-1 overflow-y-auto py-4 space-y-3" data-testid="demo-transcript">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "caller" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[82%] rounded-2xl px-4 py-2 text-[14px] ${m.role === "caller" ? "bg-white text-black" : "bg-white/10 text-white"}`}>
              {m.content}
            </div>
          </div>
        ))}
        {sending && <div className="flex justify-start"><div className="bg-white/10 rounded-2xl px-4 py-2 text-[14px] italic">{t("landing.demo_typing")}</div></div>}
        <div ref={endRef} />
      </div>

      {!session.ended ? (
        <>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {(quickPrompts[industry] || []).map((q, i) => (
              <button key={i} type="button" onClick={() => send(q)} className="text-[11px] px-2.5 py-1 rounded-full border border-white/15 text-white/80 hover:bg-white/10" data-testid={`demo-quick-${i}`}>{q}</button>
            ))}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); send(); }} className="flex gap-2 items-center">
            <Button type="button" variant="ghost" size="icon" onClick={toggleMic} className="text-white hover:bg-white/10" data-testid="demo-mic-btn" aria-label={t("landing.demo_say_something")}>
              <Mic className={`h-4 w-4 ${listening ? "text-rose-400" : ""}`} />
            </Button>
            <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder={t("landing.demo_say_something")} className="bg-white/5 border-white/20 text-white placeholder:text-white/40" data-testid="demo-input" aria-label={t("landing.demo_say_something")} />
            <Button type="submit" disabled={!input.trim() || sending} className="bg-white text-black hover:bg-white/90" data-testid="demo-send-btn" aria-label={t("landing.send")}><Send className="h-4 w-4" /></Button>
          </form>
          <div className="text-[11px] text-white/50 mt-2">{t("landing.demo_turns_left", { n: turnsLeft })}</div>
        </>
      ) : (
        <div className="rounded-xl bg-white/5 border border-white/10 p-5 text-center">
          <div className="font-medium flex items-center justify-center gap-2"><Zap className="h-4 w-4" />{t("landing.demo_wrap_title")}</div>
          <p className="text-sm text-white/70 mt-2">{t("landing.demo_wrap_sub")}</p>
          <a href="/signup"><Button className="mt-4 bg-white text-black hover:bg-white/90" data-testid="demo-signup-btn">{t("landing.demo_wrap_cta")}</Button></a>
        </div>
      )}
    </div>
  );
}
