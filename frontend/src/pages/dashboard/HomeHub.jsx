import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Plus, Search, Home, Wrench, Bell, History, ClipboardList, Copy, RefreshCw, X,
} from "lucide-react";

const BASE = "/home-hub";

const EMPTY_FORM = {
  homeowner_name: "",
  email: "",
  phone: "",
  customer_id: "",
  street: "",
  city: "",
  state: "",
  zip: "",
  property_type: "",
  year_built: "",
  sqft: "",
  notes: "",
  equipment: [],
  reminders: [],
};

const KIND_LABEL = { appointment: "Visit", invoice: "Invoice", service_event: "Service" };

function hubLink(token) {
  return `${window.location.origin}/home-hub/${token}`;
}

// ---------- small editors ----------

function EquipmentEditor({ items, onChange }) {
  const set = (idx, patch) => onChange(items.map((e, i) => (i === idx ? { ...e, ...patch } : e)));
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">Equipment in the home</div>
      {items.map((e, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
          <Input placeholder="Type (e.g. furnace)" value={e.equipment_type || ""} onChange={(ev) => set(i, { equipment_type: ev.target.value })} />
          <Input placeholder="Make · Model" value={[e.make, e.model].filter(Boolean).join(" ") || ""} onChange={(ev) => {
            const [make = "", ...rest] = ev.target.value.split(" ");
            set(i, { make, model: rest.join(" ") });
          }} />
          <Button variant="ghost" size="sm" onClick={() => onChange(items.filter((_, j) => j !== i))}>✕</Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, { equipment_type: "", make: "", model: "" }])}>
        <Plus className="h-3 w-3 mr-1" /> Add equipment
      </Button>
    </div>
  );
}

function RemindersEditor({ items, onChange }) {
  const set = (idx, patch) => onChange(items.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">Maintenance reminders</div>
      {items.map((r, i) => (
        <div key={i} className="grid grid-cols-[1fr_auto_auto_auto] gap-2">
          <Input placeholder="Reminder (e.g. Change filter)" value={r.title || ""} onChange={(e) => set(i, { title: e.target.value })} />
          <Input type="date" value={r.due_date || ""} onChange={(e) => set(i, { due_date: e.target.value })} />
          <select
            value={r.recurring || "never"}
            onChange={(e) => set(i, { recurring: e.target.value })}
            className="rounded-md border bg-background px-2 text-sm"
          >
            {["never", "monthly", "quarterly", "yearly"].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <Button variant="ghost" size="sm" onClick={() => onChange(items.filter((_, j) => j !== i))}>✕</Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, { title: "", due_date: "", recurring: "never" }])}>
        <Plus className="h-3 w-3 mr-1" /> Add reminder
      </Button>
    </div>
  );
}

function HomeFormModal({ initial, onClose, onSaved }) {
  const [form, setForm] = useState(() => ({
    ...EMPTY_FORM,
    ...(initial || {}),
    street: initial?.address?.street || "",
    city: initial?.address?.city || "",
    state: initial?.address?.state || "",
    zip: initial?.address?.zip || "",
    year_built: initial?.year_built ? String(initial.year_built) : "",
    sqft: initial?.sqft ? String(initial.sqft) : "",
  }));
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async () => {
    if (!form.homeowner_name.trim()) return toast.error("Homeowner name is required");
    setSaving(true);
    try {
      const payload = {
        homeowner_name: form.homeowner_name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim(),
        customer_id: form.customer_id.trim(),
        address: { street: form.street.trim(), city: form.city.trim(), state: form.state.trim(), zip: form.zip.trim() },
        property_type: form.property_type.trim(),
        year_built: form.year_built ? Number(form.year_built) : null,
        sqft: form.sqft ? Number(form.sqft) : null,
        notes: form.notes.trim(),
        equipment: form.equipment.filter((e) => e.equipment_type || e.make || e.model),
        reminders: form.reminders.filter((r) => r.title),
      };
      const url = initial?.id ? `${BASE}/tenants/homes?home_id=${initial.id}` : `${BASE}/tenants/homes`;
      const res = await api.post(url, payload);
      if (!initial?.id && res.data?.share_token) {
        toast.success("Home added — share link copied");
        try { await navigator.clipboard.writeText(hubLink(res.data.share_token)); } catch { /* clipboard optional */ }
      } else {
        toast.success(initial?.id ? "Home updated" : "Home added");
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid="homehub-modal">
      <div className="w-full max-w-2xl rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-4">{initial?.id ? "Edit home profile" : "Add home profile"}</h2>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div><label className="text-sm font-medium">Homeowner name *</label>
              <Input value={form.homeowner_name} onChange={(e) => set({ homeowner_name: e.target.value })} placeholder="Jane Doe" /></div>
            <div><label className="text-sm font-medium">Phone</label>
              <Input value={form.phone} onChange={(e) => set({ phone: e.target.value })} placeholder="555-0100" /></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="text-sm font-medium">Email</label>
              <Input value={form.email} onChange={(e) => set({ email: e.target.value })} placeholder="jane@example.com" /></div>
            <div><label className="text-sm font-medium">Customer ID (optional link)</label>
              <Input value={form.customer_id} onChange={(e) => set({ customer_id: e.target.value })} placeholder="cust-123" /></div>
          </div>
          <div>
            <label className="text-sm font-medium">Service address</label>
            <div className="grid grid-cols-2 gap-3 mt-1">
              <Input className="col-span-2" placeholder="Street" value={form.street} onChange={(e) => set({ street: e.target.value })} />
              <Input placeholder="City" value={form.city} onChange={(e) => set({ city: e.target.value })} />
              <div className="grid grid-cols-2 gap-3">
                <Input placeholder="State" value={form.state} onChange={(e) => set({ state: e.target.value })} />
                <Input placeholder="ZIP" value={form.zip} onChange={(e) => set({ zip: e.target.value })} />
              </div>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div><label className="text-sm font-medium">Property type</label>
              <Input value={form.property_type} onChange={(e) => set({ property_type: e.target.value })} placeholder="single_family" /></div>
            <div><label className="text-sm font-medium">Year built</label>
              <Input type="number" value={form.year_built} onChange={(e) => set({ year_built: e.target.value })} placeholder="1998" /></div>
            <div><label className="text-sm font-medium">Sq ft</label>
              <Input type="number" value={form.sqft} onChange={(e) => set({ sqft: e.target.value })} placeholder="2100" /></div>
          </div>
          <EquipmentEditor items={form.equipment} onChange={(equipment) => set({ equipment })} />
          <RemindersEditor items={form.reminders} onChange={(reminders) => set({ reminders })} />
          <div><label className="text-sm font-medium">Notes</label>
            <Input value={form.notes} onChange={(e) => set({ notes: e.target.value })} placeholder="Gate code, pets, access notes…" /></div>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save home"}</Button>
        </div>
      </div>
    </div>
  );
}

// ---------- detail drawer ----------

function HomeDrawer({ home, onClose, onEdit, onLinkRotated }) {
  const [tab, setTab] = useState("overview");
  const [history, setHistory] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [reqForm, setReqForm] = useState({ description: "", service_type: "" });
  const [savingReq, setSavingReq] = useState(false);

  useEffect(() => {
    if (tab !== "history" || !home?.id) return;
    setLoadingHistory(true);
    api.get(`${BASE}/tenants/homes/${home.id}/history`)
      .then((r) => setHistory(r.data.history || []))
      .catch((e) => toast.error(errMessage(e)))
      .finally(() => setLoadingHistory(false));
  }, [tab, home?.id]);

  const copyLink = async () => {
    if (!home._share_token) return toast.error("No share token — rotate the link to get one");
    try {
      await navigator.clipboard.writeText(hubLink(home._share_token));
      toast.success("Homeowner link copied");
    } catch {
      toast.error("Could not copy link");
    }
  };

  const rotateLink = async () => {
    try {
      const res = await api.post(`${BASE}/tenants/homes/${home.id}/token`);
      onLinkRotated(home.id, res.data.share_token);
      try { await navigator.clipboard.writeText(hubLink(res.data.share_token)); } catch { /* optional */ }
      toast.success("New link issued and copied (old link revoked)");
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const submitRequest = async () => {
    if (!reqForm.description.trim()) return toast.error("Describe the request");
    setSavingReq(true);
    try {
      await api.post(`${BASE}/tenants/homes/${home.id}/request`, {
        description: reqForm.description.trim(),
        service_type: reqForm.service_type.trim(),
      });
      toast.success("Service request logged as a lead");
      setReqForm({ description: "", service_type: "" });
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSavingReq(false);
    }
  };

  if (!home) return null;
  return (
    <div className="fixed inset-0 z-50" data-testid="homehub-drawer">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="absolute right-0 top-0 h-full w-full max-w-lg bg-card shadow-xl flex flex-col">
        <div className="p-5 border-b">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-lg font-semibold flex items-center gap-2"><Home className="h-4 w-4" />{home.homeowner_name}</h2>
              <p className="text-sm text-muted-foreground">{home.address_summary || "No address"}</p>
            </div>
            <Button variant="ghost" size="sm" onClick={onClose}><X className="h-4 w-4" /></Button>
          </div>
          <div className="flex gap-1 mt-3 text-sm">
            {[["overview", "Overview"], ["history", "History"], ["request", "Request"]].map(([k, label]) => (
              <button key={k} onClick={() => setTab(k)}
                className={`px-3 py-1.5 rounded-md ${tab === k ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"}`}>
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {tab === "overview" && (
            <>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => onEdit(home)}>Edit profile</Button>
                <Button variant="outline" size="sm" onClick={copyLink}><Copy className="h-3.5 w-3.5 mr-1" /> Copy hub link</Button>
                <Button variant="outline" size="sm" onClick={rotateLink}><RefreshCw className="h-3.5 w-3.5 mr-1" /> New link</Button>
              </div>
              <div className="text-sm grid grid-cols-2 gap-2">
                <div><span className="text-muted-foreground">Phone</span><div>{home.phone || "—"}</div></div>
                <div><span className="text-muted-foreground">Email</span><div>{home.email || "—"}</div></div>
                <div><span className="text-muted-foreground">Property</span><div>{[home.property_type, home.year_built].filter(Boolean).join(" · ") || "—"}</div></div>
                <div><span className="text-muted-foreground">Sq ft</span><div>{home.sqft || "—"}</div></div>
              </div>
              {home.notes && <div className="text-sm"><span className="text-muted-foreground">Notes</span><div>{home.notes}</div></div>}

              <div>
                <div className="text-sm font-medium flex items-center gap-1 mb-2"><Wrench className="h-3.5 w-3.5" /> Equipment ({home.equipment?.length || 0})</div>
                {(home.equipment || []).length === 0
                  ? <div className="text-sm text-muted-foreground">No equipment recorded.</div>
                  : (home.equipment || []).map((e, i) => (
                    <div key={i} className="rounded-lg border p-3 text-sm mb-2">
                      <div className="font-medium">{[e.make, e.model].filter(Boolean).join(" ") || e.equipment_type || "Unit"}</div>
                      <div className="text-xs text-muted-foreground">{[e.equipment_type, e.serial_number, e.install_date && `installed ${e.install_date}`].filter(Boolean).join(" · ")}</div>
                    </div>
                  ))}
              </div>

              <div>
                <div className="text-sm font-medium flex items-center gap-1 mb-2"><Bell className="h-3.5 w-3.5" /> Maintenance reminders ({home.reminders?.length || 0})</div>
                {(home.reminders || []).length === 0
                  ? <div className="text-sm text-muted-foreground">No reminders set.</div>
                  : (home.reminders || []).map((r, i) => (
                    <div key={i} className="rounded-lg border p-3 text-sm mb-2 flex items-center justify-between">
                      <div><div className="font-medium">{r.title}</div>
                        <div className="text-xs text-muted-foreground">due {r.due_date || "—"}</div></div>
                      {r.recurring && r.recurring !== "never" && <Badge variant="outline">{r.recurring}</Badge>}
                    </div>
                  ))}
              </div>
            </>
          )}

          {tab === "history" && (
            <div>
              <div className="text-sm font-medium flex items-center gap-1 mb-2"><History className="h-3.5 w-3.5" /> Service history</div>
              {loadingHistory
                ? <div className="text-sm text-muted-foreground">Loading…</div>
                : history.length === 0
                  ? <div className="text-sm text-muted-foreground">No service history yet.</div>
                  : history.map((h) => (
                    <div key={`${h.type}-${h.ref_id}`} className="rounded-lg border p-3 text-sm mb-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="font-medium">{h.title}</div>
                        <Badge variant="outline" className="text-xs shrink-0">{KIND_LABEL[h.type] || h.type}</Badge>
                      </div>
                      <div className="text-xs text-muted-foreground mt-1">{h.detail}</div>
                      <div className="text-xs text-muted-foreground">{h.date ? new Date(h.date).toLocaleDateString() : ""}</div>
                    </div>
                  ))}
            </div>
          )}

          {tab === "request" && (
            <div className="space-y-3">
              <div className="text-sm font-medium">Log a service request for this home</div>
              <Input placeholder="Service type (e.g. AC repair)" value={reqForm.service_type} onChange={(e) => setReqForm((f) => ({ ...f, service_type: e.target.value }))} />
              <Input placeholder="What does the homeowner need? *" value={reqForm.description} onChange={(e) => setReqForm((f) => ({ ...f, description: e.target.value }))} />
              <Button onClick={submitRequest} disabled={savingReq}>{savingReq ? "Logging…" : "Log request (creates lead)"}</Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- page ----------

export default function HomeHub() {
  const { user } = useAuth();
  const canManage = user?.role === "owner" || user?.role === "admin" || user?.role === "platform_admin";
  const [tab, setTab] = useState("homes");
  const [homes, setHomes] = useState([]);
  const [inbox, setInbox] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState(null); // null | {mode:"add"} | {mode:"edit", home}
  const [drawerHome, setDrawerHome] = useState(null);
  const [tokens, setTokens] = useState({}); // home_id -> share_token (session memory only)

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [homesRes, leadsRes] = await Promise.all([
        api.get(`${BASE}/tenants/homes`, { params: { search: search || undefined } }),
        api.get("/tenants/leads"),
      ]);
      const hs = homesRes.data.homes || [];
      setHomes(hs.map((h) => ({ ...h, _share_token: tokens[h.id] })));
      setInbox((leadsRes.data || []).filter((l) => l.source === "home-hub"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const handleLinkRotated = (homeId, token) => {
    setTokens((prev) => ({ ...prev, [homeId]: token }));
    setDrawerHome((h) => (h && h.id === homeId ? { ...h, _share_token: token } : h));
  };

  const openHome = (h) => setDrawerHome(h);
  const filtered = useMemo(() => homes, [homes]);

  return (
    <div>
      <PageHeader
        title="Home Hub"
        description="Every homeowner gets a Carfax-like home profile: service history, equipment, invoices, and maintenance reminders — shareable via a private link."
        testId="homehub-header"
        actions={canManage && (
          <Button size="sm" onClick={() => setModal({ mode: "add" })} data-testid="homehub-add">
            <Plus className="h-4 w-4 mr-1" /> Add home
          </Button>
        )}
      />

      <div className="flex gap-1 mb-4 text-sm">
        {[["homes", `Homes (${homes.length})`], ["inbox", `Request inbox (${inbox.length})`]].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-3 py-1.5 rounded-md ${tab === k ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "homes" && (
        <>
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <div className="relative flex-1 min-w-[200px] max-w-sm">
              <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input placeholder="Search name, phone, street…" value={search}
                onChange={(e) => setSearch(e.target.value)} className="pl-9" data-testid="homehub-search" />
            </div>
          </div>

          {loading ? (
            <div className="text-muted-foreground">Loading homes…</div>
          ) : filtered.length === 0 ? (
            <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground" data-testid="homehub-empty">
              <Home className="h-6 w-6 mx-auto mb-2" />
              No home profiles yet. {canManage && "Add your first home to start building homeowner profiles."}
            </div>
          ) : (
            <div className="rounded-xl border overflow-hidden">
              <table className="w-full text-sm" data-testid="homehub-table">
                <thead className="bg-muted/60 text-left">
                  <tr>
                    <th className="px-4 py-2 font-medium">Homeowner</th>
                    <th className="px-4 py-2 font-medium">Address</th>
                    <th className="px-4 py-2 font-medium">Equipment</th>
                    <th className="px-4 py-2 font-medium">Reminders</th>
                    <th className="px-4 py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((h) => (
                    <tr key={h.id} className="border-t hover:bg-muted/40">
                      <td className="px-4 py-2">
                        <div className="font-medium">{h.homeowner_name}</div>
                        <div className="text-xs text-muted-foreground">{h.phone || h.email || "—"}</div>
                      </td>
                      <td className="px-4 py-2 text-muted-foreground">{h.address_summary || "—"}</td>
                      <td className="px-4 py-2"><Badge variant="outline">{h.equipment?.length || 0}</Badge></td>
                      <td className="px-4 py-2"><Badge variant="outline">{h.reminders?.length || 0}</Badge></td>
                      <td className="px-4 py-2 text-right">
                        <Button variant="ghost" size="sm" onClick={() => openHome(h)}>View</Button>
                        {canManage && <Button variant="ghost" size="sm" onClick={() => setModal({ mode: "edit", home: h })}>Edit</Button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {tab === "inbox" && (
        inbox.length === 0 ? (
          <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground" data-testid="homehub-inbox-empty">
            <ClipboardList className="h-6 w-6 mx-auto mb-2" />
            No homeowner service requests yet. Requests made from a home hub page land here as leads.
          </div>
        ) : (
          <div className="rounded-xl border overflow-hidden">
            <table className="w-full text-sm" data-testid="homehub-inbox-table">
              <thead className="bg-muted/60 text-left">
                <tr>
                  <th className="px-4 py-2 font-medium">Homeowner</th>
                  <th className="px-4 py-2 font-medium">Details</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Received</th>
                </tr>
              </thead>
              <tbody>
                {inbox.map((l) => (
                  <tr key={l.id} className="border-t hover:bg-muted/40">
                    <td className="px-4 py-2">
                      <div className="font-medium">{l.name}</div>
                      <div className="text-xs text-muted-foreground">{l.phone || l.email || "—"}</div>
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">{l.notes || "—"}</td>
                    <td className="px-4 py-2"><Badge variant="outline">{l.status}</Badge></td>
                    <td className="px-4 py-2 text-muted-foreground">{l.created_at ? new Date(l.created_at).toLocaleDateString() : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {modal && (
        <HomeFormModal
          initial={modal.mode === "edit" ? modal.home : null}
          onClose={() => setModal(null)}
          onSaved={load}
        />
      )}
      {drawerHome && (
        <HomeDrawer
          home={drawerHome}
          onClose={() => setDrawerHome(null)}
          onEdit={(h) => { setDrawerHome(null); setModal({ mode: "edit", home: h }); }}
          onLinkRotated={handleLinkRotated}
        />
      )}
    </div>
  );
}
