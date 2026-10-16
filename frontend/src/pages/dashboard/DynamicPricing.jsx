import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Plus, Pencil, Trash2, Zap, Play, BarChart3, ShieldCheck, X } from "lucide-react";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DEMAND = ["low", "normal", "high", "surge"];
const SEASONS = ["spring", "summer", "fall", "winter"];

function triggerSummary(t = {}) {
  const parts = [];
  if (t.day_of_week?.length) parts.push(t.day_of_week.map((d) => DAYS[d]).join(", "));
  if (t.time_window) parts.push(`${t.time_window.start}–${t.time_window.end}`);
  if (t.months?.length) parts.push(`months ${t.months.join(", ")}`);
  if (t.demand_level) parts.push(`${t.demand_level}+ demand`);
  return parts.join(" · ") || "—";
}

function adjLabel(a = {}) {
  if (a.mode === "percent") return `${a.value > 0 ? "+" : ""}${a.value}%`;
  return `${a.value > 0 ? "+" : ""}${fmtUSD(a.value)}`;
}

const EMPTY_RULE = {
  name: "",
  enabled: true,
  priority: 10,
  days: [],
  timeStart: "",
  timeEnd: "",
  season: "",
  demand: "",
  mode: "percent",
  value: "",
  capMin: "",
  capMax: "",
};

function RuleModal({ initial, onClose, onSaved }) {
  const [form, setForm] = useState(() => {
    if (!initial) return { ...EMPTY_RULE };
    const t = initial.trigger || {};
    return {
      name: initial.name,
      enabled: initial.enabled,
      priority: initial.priority,
      days: t.day_of_week || [],
      timeStart: t.time_window?.start || "",
      timeEnd: t.time_window?.end || "",
      season: "",
      demand: t.demand_level || "",
      mode: initial.adjustment?.mode || "percent",
      value: String(initial.adjustment?.value ?? ""),
      capMin: initial.cap?.min_price != null ? String(initial.cap.min_price) : "",
      capMax: initial.cap?.max_price != null ? String(initial.cap.max_price) : "",
    };
  });
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const toggleDay = (d) =>
    set({ days: form.days.includes(d) ? form.days.filter((x) => x !== d) : [...form.days, d] });

  const save = async () => {
    if (!form.name.trim()) return toast.error("Name is required");
    const value = Number(form.value);
    if (!(value !== 0)) return toast.error("Adjustment must be a non-zero number");
    const trigger = {};
    if (form.days.length) trigger.day_of_week = form.days;
    if (form.timeStart || form.timeEnd) trigger.time_window = { start: form.timeStart, end: form.timeEnd };
    if (form.season) trigger.season = form.season;
    if (form.demand) trigger.demand_level = form.demand;
    if (!Object.keys(trigger).length)
      return toast.error("Pick at least one trigger (days, time, season, or demand)");
    const payload = {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: Number(form.priority) || 10,
      trigger,
      adjustment: { mode: form.mode, value },
      cap:
        form.capMin !== "" || form.capMax !== ""
          ? {
              ...(form.capMin !== "" ? { min_price: Number(form.capMin) } : {}),
              ...(form.capMax !== "" ? { max_price: Number(form.capMax) } : {}),
            }
          : null,
    };
    setSaving(true);
    try {
      if (initial?.id) {
        await api.put(`/tenants/dynamic-pricing/rules/${initial.id}`, payload);
        toast.success("Rule updated");
      } else {
        await api.post("/tenants/dynamic-pricing/rules", payload);
        toast.success("Rule created");
      }
      onSaved();
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-4">{initial?.id ? "Edit rule" : "New pricing rule"}</h2>
        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">Name *</label>
            <Input value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="After-hours fee" />
          </div>
          <div>
            <label className="text-sm font-medium">Days of week</label>
            <div className="flex gap-1 mt-1">
              {DAYS.map((d, i) => (
                <Button
                  key={d}
                  size="sm"
                  variant={form.days.includes(i) ? "default" : "outline"}
                  onClick={() => toggleDay(i)}
                >
                  {d}
                </Button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Time from</label>
              <Input type="time" value={form.timeStart} onChange={(e) => set({ timeStart: e.target.value })} />
            </div>
            <div>
              <label className="text-sm font-medium">Time to</label>
              <Input type="time" value={form.timeEnd} onChange={(e) => set({ timeEnd: e.target.value })} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Season</label>
              <select
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.season}
                onChange={(e) => set({ season: e.target.value })}
              >
                <option value="">—</option>
                {SEASONS.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Demand at least</label>
              <select
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.demand}
                onChange={(e) => set({ demand: e.target.value })}
              >
                <option value="">—</option>
                {DEMAND.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-sm font-medium">Type</label>
              <select
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.mode}
                onChange={(e) => set({ mode: e.target.value })}
              >
                <option value="percent">Percent %</option>
                <option value="flat">Flat $</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Value *</label>
              <Input
                type="number"
                step="0.01"
                value={form.value}
                onChange={(e) => set({ value: e.target.value })}
                placeholder={form.mode === "percent" ? "15 or -10" : "20 or -15"}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Priority</label>
              <Input type="number" value={form.priority} onChange={(e) => set({ priority: e.target.value })} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Cap: min price</label>
              <Input type="number" min="0" step="0.01" value={form.capMin} onChange={(e) => set({ capMin: e.target.value })} placeholder="optional" />
            </div>
            <div>
              <label className="text-sm font-medium">Cap: max price</label>
              <Input type="number" min="0" step="0.01" value={form.capMax} onChange={(e) => set({ capMax: e.target.value })} placeholder="optional" />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
            Enabled
          </label>
          <p className="text-xs text-muted-foreground">
            Lower priority numbers apply first. All chosen triggers must hold for the rule to fire.
            Adjustments are validated against your shop bounds — never absurd.
          </p>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save rule"}</Button>
        </div>
      </div>
    </div>
  );
}

function BoundsCard({ bounds, onSaved }) {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (bounds && !form) setForm({ ...bounds });
  }, [bounds, form]);
  if (!form) return null;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const save = async () => {
    setSaving(true);
    try {
      const res = await api.put("/tenants/dynamic-pricing/bounds", {
        enabled: form.enabled,
        max_markup_pct: Number(form.max_markup_pct),
        max_discount_pct: Number(form.max_discount_pct),
        min_price: Number(form.min_price),
        max_price: Number(form.max_price),
      });
      onSaved(res.bounds);
      toast.success("Bounds saved");
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="glass-crystal rounded-2xl p-6 text-white">
      <div className="flex items-center gap-2 mb-4">
        <ShieldCheck className="h-5 w-5" />
        <h3 className="font-semibold">Shop guardrails</h3>
      </div>
      <p className="text-xs opacity-80 mb-4">
        Dynamic pricing can never go outside these bounds — set them once, the AI respects them forever.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className="text-xs opacity-80">Max markup %</label>
          <Input type="number" min="0" step="1" value={form.max_markup_pct} onChange={(e) => set({ max_markup_pct: e.target.value })} className="bg-white/10 border-white/20 text-white" />
        </div>
        <div>
          <label className="text-xs opacity-80">Max discount %</label>
          <Input type="number" min="0" step="1" value={form.max_discount_pct} onChange={(e) => set({ max_discount_pct: e.target.value })} className="bg-white/10 border-white/20 text-white" />
        </div>
        <div>
          <label className="text-xs opacity-80">Min price $</label>
          <Input type="number" min="0" step="0.01" value={form.min_price} onChange={(e) => set({ min_price: e.target.value })} className="bg-white/10 border-white/20 text-white" />
        </div>
        <div>
          <label className="text-xs opacity-80">Max price $</label>
          <Input type="number" min="0" step="0.01" value={form.max_price} onChange={(e) => set({ max_price: e.target.value })} className="bg-white/10 border-white/20 text-white" />
        </div>
      </div>
      <div className="flex justify-end mt-4">
        <Button size="sm" variant="secondary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save bounds"}
        </Button>
      </div>
    </div>
  );
}

export default function DynamicPricing() {
  useAuth();
  const [rules, setRules] = useState([]);
  const [bounds, setBounds] = useState(null);
  const [perf, setPerf] = useState(null);
  const [items, setItems] = useState([]);
  const [modal, setModal] = useState(null); // null | {initial}
  const [simResult, setSimResult] = useState(null);
  const [quote, setQuote] = useState(null);
  const [quoting, setQuoting] = useState(false);
  const [sel, setSel] = useState({}); // itemId -> qty
  const [quoteAt, setQuoteAt] = useState(() => {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  });
  const [demandCtx, setDemandCtx] = useState({ level: "", booked: "", capacity: "" });

  const load = useCallback(async () => {
    try {
      const [r1, r2, r3, r4] = await Promise.all([
        api.get("/tenants/dynamic-pricing/rules"),
        api.get("/tenants/dynamic-pricing/bounds"),
        api.get("/tenants/dynamic-pricing/rules/performance"),
        api.get("/tenants/pricebook/items"),
      ]);
      setRules(r1.rules);
      setBounds(r2.bounds);
      setPerf(r3);
      setItems(r4.items || []);
    } catch (e) {
      toast.error(errMessage(e));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const del = async (id) => {
    if (!window.confirm("Delete this pricing rule?")) return;
    try {
      await api.delete(`/tenants/dynamic-pricing/rules/${id}`);
      toast.success("Rule deleted");
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const simulate = async (ruleId) => {
    try {
      const res = await api.get("/tenants/dynamic-pricing/simulate", { params: { rule_id: ruleId } });
      setSimResult(res);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const toggleSel = (id) =>
    setSel((s) => (s[id] ? Object.fromEntries(Object.entries(s).filter(([k]) => k !== id)) : { ...s, [id]: 1 }));
  const setQty = (id, q) => setSel((s) => ({ ...s, [id]: Math.max(1, Number(q) || 1) }));

  const runQuote = async () => {
    const selections = Object.entries(sel).map(([item_id, qty]) => ({ item_id, qty }));
    if (!selections.length) return toast.error("Pick at least one pricebook item");
    const context = {};
    if (demandCtx.level) context.demand_level = demandCtx.level;
    if (demandCtx.booked !== "") context.bookings_today = Number(demandCtx.booked);
    if (demandCtx.capacity !== "") context.capacity_today = Number(demandCtx.capacity);
    setQuoting(true);
    try {
      const res = await api.post("/tenants/dynamic-pricing/quote", {
        selections,
        at: quoteAt ? new Date(quoteAt).toISOString() : undefined,
        context,
      });
      setQuote(res);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setQuoting(false);
    }
  };

  const perfById = useMemo(() => {
    const m = {};
    (perf?.rules || []).forEach((r) => {
      m[r.id] = r;
    });
    return m;
  }, [perf]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dynamic pricing"
        subtitle="Time, day, season & demand adjustments — rule-based, bounded, and fully logged."
        action={
          <Button onClick={() => setModal({ initial: null })}>
            <Plus className="h-4 w-4 mr-1" /> New rule
          </Button>
        }
      />

      <BoundsCard bounds={bounds} onSaved={setBounds} />

      {/* Rules manager */}
      <div className="rounded-xl border bg-card p-6">
        <div className="flex items-center gap-2 mb-4">
          <Zap className="h-5 w-5" />
          <h3 className="font-semibold">Pricing rules</h3>
          <span className="text-sm text-muted-foreground">({rules.length})</span>
        </div>
        {rules.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No rules yet. Add one — e.g. an after-hours fee, weekend rates, or a slow-day discount.
            Adjustments are overlays: your pricebook never changes.
          </p>
        ) : (
          <div className="space-y-2">
            {rules.map((r) => (
              <div key={r.id} className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
                <Badge variant={r.enabled ? "default" : "secondary"}>{r.enabled ? "on" : "off"}</Badge>
                <div className="flex-1 min-w-[180px]">
                  <div className="font-medium">{r.name}</div>
                  <div className="text-xs text-muted-foreground">{triggerSummary(r.trigger)}</div>
                </div>
                <Badge variant="outline" className={r.adjustment?.value < 0 ? "text-green-600" : ""}>
                  {adjLabel(r.adjustment)}
                </Badge>
                <span className="text-xs text-muted-foreground">prio {r.priority}</span>
                <span className="text-xs text-muted-foreground flex items-center gap-1">
                  <BarChart3 className="h-3 w-3" /> {perfById[r.id]?.fire_count ?? r.fire_count} fires
                </span>
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" title="Simulate vs last 30 days" onClick={() => simulate(r.id)}>
                    <Play className="h-3 w-3" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setModal({ initial: r })}>
                    <Pencil className="h-3 w-3" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => del(r.id)}>
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Simulation result */}
      {simResult && (
        <div className="rounded-xl border bg-card p-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Simulation: {simResult.rule_name}</h3>
            <Button variant="ghost" size="sm" onClick={() => setSimResult(null)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          <p className="text-sm text-muted-foreground mb-3">{simResult.note}</p>
          <div className="flex gap-6 text-sm">
            <div>
              <div className="text-2xl font-bold">{simResult.would_fire}</div>
              <div className="text-muted-foreground">would have fired</div>
            </div>
            <div>
              <div className="text-2xl font-bold">{simResult.appointments_evaluated}</div>
              <div className="text-muted-foreground">appointments checked</div>
            </div>
            <div>
              <div className="text-2xl font-bold">{(simResult.fire_rate * 100).toFixed(1)}%</div>
              <div className="text-muted-foreground">fire rate</div>
            </div>
          </div>
        </div>
      )}

      {/* Live quote simulator */}
      <div className="rounded-xl border bg-card p-6">
        <h3 className="font-semibold mb-4">Live quote simulator</h3>
        <div className="grid md:grid-cols-2 gap-6">
          <div className="space-y-3">
            <label className="text-sm font-medium">Job date/time</label>
            <Input type="datetime-local" value={quoteAt} onChange={(e) => setQuoteAt(e.target.value)} />
            <label className="text-sm font-medium">Pricebook items</label>
            <div className="max-h-56 overflow-y-auto space-y-1 border rounded-lg p-2">
              {items.map((it) => (
                <label key={it.id} className="flex items-center gap-2 text-sm p-1 rounded hover:bg-muted">
                  <input type="checkbox" checked={!!sel[it.id]} onChange={() => toggleSel(it.id)} />
                  <span className="flex-1">{it.name}</span>
                  <span className="text-muted-foreground">{fmtUSD(it.price)}</span>
                  {sel[it.id] && (
                    <Input
                      type="number"
                      min="1"
                      className="w-16 h-7"
                      value={sel[it.id]}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => setQty(it.id, e.target.value)}
                    />
                  )}
                </label>
              ))}
              {items.length === 0 && <p className="text-xs text-muted-foreground p-2">No pricebook items yet.</p>}
            </div>
            <label className="text-sm font-medium">Demand context (optional)</label>
            <div className="grid grid-cols-3 gap-2">
              <select
                className="rounded-md border bg-background px-2 py-2 text-sm"
                value={demandCtx.level}
                onChange={(e) => setDemandCtx((c) => ({ ...c, level: e.target.value }))}
              >
                <option value="">auto</option>
                {DEMAND.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
              <Input type="number" min="0" placeholder="booked today" value={demandCtx.booked} onChange={(e) => setDemandCtx((c) => ({ ...c, booked: e.target.value }))} />
              <Input type="number" min="0" placeholder="capacity" value={demandCtx.capacity} onChange={(e) => setDemandCtx((c) => ({ ...c, capacity: e.target.value }))} />
            </div>
            <Button onClick={runQuote} disabled={quoting}>
              {quoting ? "Computing…" : "Compute live price"}
            </Button>
          </div>
          <div>
            {!quote ? (
              <p className="text-sm text-muted-foreground">Run a quote to see the line-by-line breakdown — every adjustment, which rule fired, and the guardrails that applied.</p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {(quote.rules_fired || []).map((r) => (
                    <Badge key={r.id} variant="outline"><Zap className="h-3 w-3 mr-1" />{r.name}</Badge>
                  ))}
                  {quote.clamped_lines > 0 && <Badge variant="destructive">guardrail clamped {quote.clamped_lines} line(s)</Badge>}
                </div>
                {quote.lines.map((l) => (
                  <div key={l.item_id} className="rounded-lg border p-3 text-sm">
                    <div className="flex justify-between font-medium">
                      <span>{l.name} × {l.qty}</span>
                      <span>{fmtUSD(l.line_total)}</span>
                    </div>
                    <div className="text-xs text-muted-foreground mt-1 space-y-0.5">
                      <div className="flex justify-between"><span>Base</span><span>{fmtUSD(l.base_unit)} /unit</span></div>
                      {l.breakdown.map((b) => (
                        <div key={b.rule_id} className="flex justify-between">
                          <span>
                            {b.rule_name} ({b.mode === "percent" ? `${b.value}%` : fmtUSD(b.value)})
                            {b.rule_clamped && " · rule-capped"}
                          </span>
                          <span className={b.amount < 0 ? "text-green-600" : ""}>
                            {b.amount >= 0 ? "+" : ""}{fmtUSD(b.amount)}
                          </span>
                        </div>
                      ))}
                      <div className="flex justify-between font-medium text-foreground">
                        <span>Final /unit</span><span>{fmtUSD(l.final_unit)}</span>
                      </div>
                    </div>
                  </div>
                ))}
                <div className="rounded-lg bg-muted p-3 text-sm space-y-1">
                  <div className="flex justify-between"><span>Subtotal (base)</span><span>{fmtUSD(quote.subtotal_base)}</span></div>
                  <div className="flex justify-between"><span>Adjustments</span><span className={quote.adjustments_total < 0 ? "text-green-600" : ""}>{quote.adjustments_total >= 0 ? "+" : ""}{fmtUSD(quote.adjustments_total)}</span></div>
                  <div className="flex justify-between font-bold"><span>Quote total</span><span>{fmtUSD(quote.subtotal_final)}</span></div>
                  <div className="text-xs text-muted-foreground">Logged & reversible — pricebook untouched.</div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Rule performance */}
      <div className="rounded-xl border bg-card p-6">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 className="h-5 w-5" />
          <h3 className="font-semibold">Rule performance</h3>
          <span className="text-sm text-muted-foreground">({perf?.quotes_logged ?? 0} quotes logged)</span>
        </div>
        {(perf?.rules || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No rules yet — fire counts will appear here as quotes flow through.</p>
        ) : (
          <div className="space-y-2">
            {(perf.rules || []).map((r) => {
              const max = Math.max(1, ...(perf.rules || []).map((x) => x.fire_count));
              return (
                <div key={r.id} className="flex items-center gap-3 text-sm">
                  <span className="w-44 truncate font-medium">{r.name}</span>
                  <div className="flex-1 h-2 rounded bg-muted overflow-hidden">
                    <div className="h-full bg-primary" style={{ width: `${(r.fire_count / max) * 100}%` }} />
                  </div>
                  <span className="w-20 text-right text-muted-foreground">{r.fire_count} fires</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {modal && (
        <RuleModal initial={modal.initial} onClose={() => setModal(null)} onSaved={load} />
      )}
    </div>
  );
}
