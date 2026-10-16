import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  ShieldCheck, Plus, FileText, CalendarClock, AlertTriangle,
  CheckCircle2, Circle, Loader2, Info, Printer,
} from "lucide-react";

const BASE = "/compliance";
const DISCLAIMER =
  "Compliance checklists are reminders to help you stay organized — they are not legal advice. " +
  "Always verify requirements with a licensed attorney or compliance professional for your trade and state.";

const STATUS_ORDER = ["pending", "in_progress", "done"];
const STATUS_LABEL = { pending: "Pending", in_progress: "In progress", done: "Done" };
const COLOR_CLASS = {
  green: "text-emerald-400",
  amber: "text-amber-400",
  red: "text-red-400",
};
const URGENCY_BADGE = {
  expired: "bg-red-500/15 text-red-300 border-red-500/30",
  red: "bg-red-500/15 text-red-300 border-red-500/30",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  unknown: "bg-muted text-muted-foreground",
};

function ScoreDial({ score, color }) {
  const r = 54;
  const circ = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(100, score)) / 100;
  const stroke = color === "green" ? "#34d399" : color === "amber" ? "#fbbf24" : "#f87171";
  return (
    <div className="relative w-36 h-36">
      <svg viewBox="0 0 128 128" className="w-36 h-36 -rotate-90">
        <circle cx="64" cy="64" r={r} fill="none" stroke="currentColor" strokeWidth="10" className="text-muted/40" opacity="0.25" />
        <circle cx="64" cy="64" r={r} fill="none" stroke={stroke} strokeWidth="10"
          strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={circ * (1 - frac)} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold">{score}</span>
        <span className="text-xs text-muted-foreground">compliance score</span>
      </div>
    </div>
  );
}

function ItemModal({ onClose, onSaved }) {
  const [form, setForm] = useState({ name: "", category: "license", description: "", required: false, recurring: "annual" });
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (!form.name.trim()) return toast.error("Name is required");
    setSaving(true);
    try {
      await api.post(`${BASE}/tenants/checklist/items`, form);
      toast.success("Checklist item added");
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
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl">
        <h2 className="text-lg font-semibold mb-4">Add checklist item</h2>
        <div className="space-y-3">
          <div>
            <label className="text-sm font-medium">Name *</label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. City fire inspection" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Category</label>
              <select className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {["license", "insurance", "certifications", "environmental", "safety", "marketing", "tax"].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Recurrence</label>
              <select className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.recurring} onChange={(e) => setForm({ ...form, recurring: e.target.value })}>
                {["annual", "quarterly", "monthly", "one-time"].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Description</label>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What does 'done' look like?" />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.required}
              onChange={(e) => setForm({ ...form, required: e.target.checked })} />
            Required item
          </label>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
        </div>
      </div>
    </div>
  );
}

function DocModal({ onClose, onSaved }) {
  const [form, setForm] = useState({ name: "", type: "license", expiry_date: "", file_ref: "", notes: "" });
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (!form.name.trim()) return toast.error("Name is required");
    setSaving(true);
    try {
      await api.post(`${BASE}/tenants/documents`, form);
      toast.success("Document registered");
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
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl">
        <h2 className="text-lg font-semibold mb-4">Register document</h2>
        <div className="space-y-3">
          <div>
            <label className="text-sm font-medium">Name *</label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. GL policy 2026" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Type</label>
              <select className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
                {["license", "insurance", "cert", "other"].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Expiry date</label>
              <Input type="date" value={form.expiry_date}
                onChange={(e) => setForm({ ...form, expiry_date: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">File ref (stub)</label>
            <Input value={form.file_ref} onChange={(e) => setForm({ ...form, file_ref: e.target.value })}
              placeholder="vault/gl-2026.pdf" />
          </div>
          <div>
            <label className="text-sm font-medium">Notes</label>
            <Input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
        </div>
      </div>
    </div>
  );
}

function fmtDays(d) {
  if (d == null) return "no date";
  if (d < 0) return `${-d}d overdue`;
  if (d === 0) return "due today";
  return `${d}d left`;
}

export default function Compliance() {
  const [loading, setLoading] = useState(true);
  const [checklist, setChecklist] = useState(null);
  const [status, setStatus] = useState(null);
  const [expiring, setExpiring] = useState([]);
  const [trade, setTrade] = useState("");
  const [stateCode, setStateCode] = useState("");
  const [showItemModal, setShowItemModal] = useState(false);
  const [showDocModal, setShowDocModal] = useState(false);
  const [toggling, setToggling] = useState(null);

  const load = useCallback(async (seedTrade, seedState) => {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      if (seedTrade) qs.set("trade", seedTrade);
      if (seedState) qs.set("state", seedState);
      const qsStr = qs.toString() ? `?${qs}` : "";
      const [cl, st, ex] = await Promise.all([
        api.get(`${BASE}/tenants/checklist${qsStr}`),
        api.get(`${BASE}/tenants/status`),
        api.get(`${BASE}/tenants/documents/expiring`),
      ]);
      setChecklist(cl.data);
      setStatus(st.data);
      setExpiring(ex.data.documents || []);
      if (cl.data.trade && !trade) setTrade(cl.data.trade);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { load(); }, [load]);

  const grouped = useMemo(() => {
    const g = {};
    for (const i of checklist?.items || []) {
      (g[i.category] ||= []).push(i);
    }
    return g;
  }, [checklist]);

  const cycleStatus = async (item) => {
    const next = STATUS_ORDER[(STATUS_ORDER.indexOf(item.status) + 1) % STATUS_ORDER.length];
    setToggling(item.id);
    try {
      await api.put(`${BASE}/tenants/checklist/items/${item.id}`, { status: next });
      setChecklist((c) => ({
        ...c,
        items: c.items.map((i) => (i.id === item.id ? { ...i, status: next } : i)),
      }));
      const st = await api.get(`${BASE}/tenants/status`);
      setStatus(st.data);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setToggling(null);
    }
  };

  const seedWithTrade = () => {
    if (!trade.trim()) return toast.error("Enter your trade first (e.g. hvac, plumbing)");
    load(trade.trim(), stateCode.trim());
  };

  const printReport = () => window.print();

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-muted-foreground">
        <Loader2 className="h-6 w-6 animate-spin mr-2" /> Loading compliance…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Autopilot"
        title="Compliance Autopilot"
        description="License renewals, insurance certs, tech certifications, EPA/OSHA and 10DLC hygiene — tracked, reminded, audit-ready."
        actions={
          <Button variant="outline" onClick={printReport}>
            <Printer className="h-4 w-4 mr-1" /> Print report
          </Button>
        }
      />

      <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 flex gap-3 items-start">
        <Info className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
        <p className="text-sm text-amber-200/90">{checklist?.disclaimer || DISCLAIMER}</p>
      </div>

      {/* Score + categories */}
      <div className="glass-crystal rounded-2xl p-5">
        <div className="flex flex-wrap items-center gap-6">
          <ScoreDial score={status?.score ?? 0} color={status?.color} />
          <div className="flex-1 min-w-[240px]">
            <h3 className="font-semibold mb-2 flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" /> Compliance by category
            </h3>
            <div className="flex flex-wrap gap-2">
              {Object.entries(status?.categories || {}).map(([cat, v]) => (
                <Badge key={cat} variant="outline" className="gap-1">
                  <span className={COLOR_CLASS[v.color]}>●</span>
                  {cat} {v.done}/{v.total}
                </Badge>
              ))}
            </div>
            <p className="text-sm text-muted-foreground mt-3">
              {status?.done} of {status?.total} items complete · {status?.open_required_items} required items still open
            </p>
          </div>
          <div className="flex gap-2 items-end">
            <div>
              <label className="text-sm font-medium">Trade</label>
              <Input value={trade} onChange={(e) => setTrade(e.target.value)} placeholder="hvac" className="w-36" />
            </div>
            <div>
              <label className="text-sm font-medium">State</label>
              <Input value={stateCode} onChange={(e) => setStateCode(e.target.value)} placeholder="TX" className="w-20" />
            </div>
            <Button variant="outline" onClick={seedWithTrade}>Apply</Button>
          </div>
        </div>
      </div>

      {/* Deadlines timeline */}
      <div className="glass-crystal rounded-2xl p-5">
        <h3 className="font-semibold mb-3 flex items-center gap-2">
          <CalendarClock className="h-4 w-4" /> Upcoming deadlines
          {expiring.length > 0 && <Badge variant="destructive">{expiring.length}</Badge>}
        </h3>
        {expiring.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing expiring in the next 60 days. 🎉</p>
        ) : (
          <div className="space-y-2">
            {expiring.map((d) => (
              <div key={d.id} className="flex items-center justify-between rounded-lg border p-3 text-sm">
                <div className="flex items-center gap-3">
                  <AlertTriangle className={`h-4 w-4 ${COLOR_CLASS[d.urgency === "expired" ? "red" : d.urgency]}`} />
                  <div>
                    <div className="font-medium">{d.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {d.type} · expires {d.expiry_date || "—"}
                    </div>
                  </div>
                </div>
                <Badge variant="outline" className={URGENCY_BADGE[d.urgency] || ""}>
                  {fmtDays(d.days_to_expiry)}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Checklist */}
      <div className="glass-crystal rounded-2xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4" /> Compliance checklist
            <Badge variant="secondary">{checklist?.count || 0}</Badge>
          </h3>
          <Button size="sm" onClick={() => setShowItemModal(true)}>
            <Plus className="h-3 w-3 mr-1" /> Add item
          </Button>
        </div>
        <div className="space-y-4">
          {Object.entries(grouped).map(([cat, items]) => (
            <div key={cat}>
              <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                {cat} · {items.filter((i) => i.status === "done").length}/{items.length}
              </div>
              <div className="space-y-1.5">
                {items.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => cycleStatus(item)}
                    disabled={toggling === item.id}
                    className="w-full text-left flex items-center gap-3 rounded-lg border p-2.5 text-sm hover:bg-muted/30 transition"
                  >
                    {item.status === "done" ? (
                      <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0" />
                    ) : item.status === "in_progress" ? (
                      <Loader2 className="h-5 w-5 text-amber-400 shrink-0" />
                    ) : (
                      <Circle className="h-5 w-5 text-muted-foreground shrink-0" />
                    )}
                    <div className="flex-1">
                      <div className={item.status === "done" ? "line-through text-muted-foreground" : "font-medium"}>
                        {item.name}
                        {item.required && (
                          <Badge variant="outline" className="ml-2 text-[10px]">required</Badge>
                        )}
                      </div>
                      {item.description && (
                        <div className="text-xs text-muted-foreground">{item.description}</div>
                      )}
                    </div>
                    <Badge variant="outline" className="text-[10px]">
                      {STATUS_LABEL[item.status] || item.status}
                    </Badge>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Document vault */}
      <div className="glass-crystal rounded-2xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold flex items-center gap-2">
            <FileText className="h-4 w-4" /> Document vault
          </h3>
          <Button size="sm" variant="outline" onClick={() => setShowDocModal(true)}>
            <Plus className="h-3 w-3 mr-1" /> Register document
          </Button>
        </div>
        {expiring.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Register licenses, insurance policies, and certifications with expiry dates to get automatic reminders.
          </p>
        ) : (
          <div className="space-y-2">
            {expiring.map((d) => (
              <div key={d.id} className="flex items-center justify-between rounded-lg border p-3 text-sm">
                <div>
                  <div className="font-medium">{d.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {d.type}{d.file_ref ? ` · ${d.file_ref}` : ""}
                  </div>
                </div>
                <Badge variant="outline" className={URGENCY_BADGE[d.urgency] || ""}>
                  {fmtDays(d.days_to_expiry)}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </div>

      {showItemModal && (
        <ItemModal onClose={() => setShowItemModal(false)} onSaved={() => load()} />
      )}
      {showDocModal && (
        <DocModal
          onClose={() => setShowDocModal(false)}
          onSaved={async () => {
            const [st, ex] = await Promise.all([
              api.get(`${BASE}/tenants/status`),
              api.get(`${BASE}/tenants/documents/expiring`),
            ]);
            setStatus(st.data);
            setExpiring(ex.data.documents || []);
          }}
        />
      )}
    </div>
  );
}
