import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Phone, Send, Mic, Sparkles, Zap } from "lucide-react";
import { toast } from "sonner";

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
  const endRef = useRef(null);
  const recRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

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

  if (!session) {
    return (
      <div className="glass-crystal rounded-2xl p-8 text-white" data-testid="demo-card-start">
        <div className="flex items-center gap-2 overline text-white/70"><Sparkles className="h-3.5 w-3.5" />{t("landing.demo_live_badge")}</div>
        <h3 className="font-display text-3xl md:text-4xl mt-2 tracking-tight">{t("landing.demo_pick_title")}</h3>
        <p className="text-white/70 mt-3 max-w-lg">{t("landing.demo_pick_sub")}</p>
        <div className="mt-6 flex flex-wrap gap-2">
          {industries.map((i) => (
            <button key={i.key} onClick={() => setIndustry(i.key)}
              className={`px-4 py-2 rounded-full border text-sm transition ${industry === i.key ? "bg-white text-black border-white" : "border-white/20 text-white/80 hover:bg-white/10"}`}
              data-testid={`demo-industry-${i.key}`}>{i.label}</button>
          ))}
        </div>
        <Button className="mt-6 h-11 bg-white text-black hover:bg-white/90" onClick={() => start()} disabled={starting} data-testid="demo-start-btn">
          <Phone className="h-4 w-4 mr-1.5" />{starting ? t("landing.demo_starting") : t("landing.demo_start")}
        </Button>
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
              <button key={i} onClick={() => send(q)} className="text-[11px] px-2.5 py-1 rounded-full border border-white/15 text-white/80 hover:bg-white/10" data-testid={`demo-quick-${i}`}>{q}</button>
            ))}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); send(); }} className="flex gap-2 items-center">
            <Button type="button" variant="ghost" size="icon" onClick={toggleMic} className="text-white hover:bg-white/10" data-testid="demo-mic-btn">
              <Mic className={`h-4 w-4 ${listening ? "text-rose-400" : ""}`} />
            </Button>
            <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder={t("landing.demo_say_something")} className="bg-white/5 border-white/20 text-white placeholder:text-white/40" data-testid="demo-input" />
            <Button type="submit" disabled={!input.trim() || sending} className="bg-white text-black hover:bg-white/90" data-testid="demo-send-btn"><Send className="h-4 w-4" /></Button>
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
