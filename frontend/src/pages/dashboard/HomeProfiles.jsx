import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Home, Plus, Wrench, History, ShieldCheck, ArrowRightLeft, Clock,
  CheckCircle, XCircle, ClipboardList, FileText, CalendarClock, CircleDot,
} from "lucide-react";

const TRANSFER_STYLES = {
  initiated: { cls: "bg-sky-100 text-sky-800", icon: CircleDot },
  approved: { cls: "bg-amber-100 text-amber-800", icon: Clock },
  claimed: { cls: "bg-emerald-100 text-emerald-800", icon: CheckCircle },
  expired: { cls: "bg-muted text-muted-foreground", icon: XCircle },
  cancelled: { cls: "bg-muted text-muted-foreground", icon: XCircle },
};

function NewProfileModal({ onClose, onCreated }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ property_address: "", unit: "", owner_name: "", owner_email: "", owner_phone: "", notes: "" });
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async () => {
    if (!form.property_address.trim()) return toast.error(t("homeProfiles.address_required", "Property address is required"));
    setSaving(true);
    try {
      const r = await api.post("/home-profiles/", {
        property_address: form.property_address.trim(),
        unit: form.unit.trim(),
        owner_name: form.owner_name.trim(),
        owner_contact: { name: form.owner_name.trim(), email: form.owner_email.trim(), phone: form.owner_phone.trim() },
        notes: form.notes.trim(),
      });
      toast.success(t("homeProfiles.created", "Home profile created."));
      onCreated(r.data);
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="hp-new-modal">
        <h3 className="text-lg font-semibold mb-1">{t("homeProfiles.new_title", "New home profile")}</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {t("homeProfiles.new_hint", "One persistent record per property: equipment, service history, warranties, and plans.")}
        </p>
        <div className="space-y-3">
          <Input placeholder={t("homeProfiles.address_ph", "Property address *")} value={form.property_address} onChange={(e) => set({ property_address: e.target.value })} />
          <Input placeholder={t("homeProfiles.unit_ph", "Unit / apt (optional)")} value={form.unit} onChange={(e) => set({ unit: e.target.value })} />
          <Input placeholder={t("homeProfiles.owner_name", "Current owner name")} value={form.owner_name} onChange={(e) => set({ owner_name: e.target.value })} />
          <div className="flex gap-2">
            <Input placeholder={t("homeProfiles.owner_email", "Owner email")} value={form.owner_email} onChange={(e) => set({ owner_email: e.target.value })} />
            <Input placeholder={t("homeProfiles.owner_phone", "Owner phone")} value={form.owner_phone} onChange={(e) => set({ owner_phone: e.target.value })} />
          </div>
          <Input placeholder={t("homeProfiles.notes_ph", "Notes (gate code, pets, …)")} value={form.notes} onChange={(e) => set({ notes: e.target.value })} />
          <p className="text-xs text-muted-foreground flex gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
            {t("homeProfiles.pii_note", "Owner contact and payment details stay with the shop and are redacted if the home is ever sold.")}
          </p>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t("homeProfiles.cancel", "Cancel")}</Button>
          <Button onClick={save} disabled={saving} data-testid="hp-new-save">
            <Plus className="h-4 w-4 mr-1" /> {saving ? t("homeProfiles.saving", "Saving…") : t("homeProfiles.create", "Create")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TransferModal({ profile, onClose, onInitiated }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ new_owner_name: "", new_owner_email: "", new_owner_phone: "", auto_approve_days: "30", note: "" });
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const start = async () => {
    if (!form.new_owner_name.trim()) return toast.error(t("homeProfiles.new_owner_required", "New owner name is required"));
    setSaving(true);
    try {
      const r = await api.post(`/home-profiles/${encodeURIComponent(profile.id)}/transfer/initiate`, {
        new_owner_name: form.new_owner_name.trim(),
        new_owner_contact: { name: form.new_owner_name.trim(), email: form.new_owner_email.trim(), phone: form.new_owner_phone.trim() },
        auto_approve_days: Number(form.auto_approve_days) || 30,
        note: form.note.trim(),
      });
      toast.success(t("homeProfiles.transfer_started", "Transfer started — the old owner gets an approve link."));
      onInitiated(r.data);
      onClose();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl" data-testid="hp-transfer-modal">
        <h3 className="text-lg font-semibold mb-1">{t("homeProfiles.transfer_title", "Transfer ownership")}</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {t("homeProfiles.transfer_hint", "Move {address} to a new owner. The old owner must approve first; the new owner then claims with consent.")}
        </p>
        <div className="space-y-3">
          <div className="rounded-lg bg-muted/50 p-3 text-sm font-medium">{profile.property_address}{profile.unit ? `, ${profile.unit}` : ""}</div>
          <Input placeholder={t("homeProfiles.new_owner_name", "New owner name *")} value={form.new_owner_name} onChange={(e) => set({ new_owner_name: e.target.value })} />
          <div className="flex gap-2">
            <Input placeholder={t("homeProfiles.new_owner_email", "New owner email")} value={form.new_owner_email} onChange={(e) => set({ new_owner_email: e.target.value })} />
            <Input placeholder={t("homeProfiles.new_owner_phone", "New owner phone")} value={form.new_owner_phone} onChange={(e) => set({ new_owner_phone: e.target.value })} />
          </div>
          <label className="block text-sm">
            <span className="text-muted-foreground">{t("homeProfiles.auto_approve_label", "Auto-approve after (days of no response)")}</span>
            <Input type="number" min="0" max="180" value={form.auto_approve_days} onChange={(e) => set({ auto_approve_days: e.target.value })} className="mt-1" />
          </label>
          <Input placeholder={t("homeProfiles.transfer_note", "Note (optional)")} value={form.note} onChange={(e) => set({ note: e.target.value })} />
          <p className="text-xs text-muted-foreground flex gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
            {t("homeProfiles.transfer_pii", "Only equipment and service data move. The old owner's contact and payment details are redacted on claim.")}
          </p>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={onClose}>{t("homeProfiles.cancel", "Cancel")}</Button>
          <Button onClick={start} disabled={saving} data-testid="hp-transfer-send">
            <ArrowRightLeft className="h-4 w-4 mr-1" /> {saving ? t("homeProfiles.sending", "Sending…") : t("homeProfiles.start_transfer", "Start transfer")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TransferTimeline({ transfers }) {
  const { t } = useTranslation();
  if (!transfers || transfers.length === 0) return null;
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2 mb-3">
        <ArrowRightLeft className="h-4 w-4 text-muted-foreground" />
        <h3 className="font-semibold text-sm">{t("homeProfiles.transfer_history", "Ownership transfer history")}</h3>
      </div>
      <div className="space-y-4">
        {transfers.map((tr) => {
          const style = TRANSFER_STYLES[tr.status] || TRANSFER_STYLES.initiated;
          const Icon = style.icon;
          return (
            <div key={tr.id} className="border-l-2 border-muted pl-4">
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <Badge className={style.cls}><Icon className="h-3 w-3 mr-1" />{tr.status}</Badge>
                <span className="text-sm text-muted-foreground">
                  {t("homeProfiles.transfer_to", "→")} {tr.new_owner_name}
                </span>
              </div>
              <div className="space-y-1.5 mt-2">
                {(tr.events || []).map((ev, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                    <CheckCircle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-emerald-500" />
                    <span>
                      <span className="font-medium text-foreground capitalize">{ev.event.replace(/_/g, " ")}</span>
                      {" — "}{new Date(ev.at).toLocaleString()}
                      {ev.detail && <span className="block">{ev.detail}</span>}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProfileDetail({ profile, transfers, onBack, canManage, onTransferInitiated }) {
  const { t } = useTranslation();
  const [showTransfer, setShowTransfer] = useState(false);
  const activeTransfer = (transfers || []).find((tr) => ["initiated", "approved"].includes(tr.status));

  const section = (icon, title, items, renderItem, emptyKey, emptyFallback) => (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2 mb-3">
        {icon}
        <h3 className="font-semibold text-sm">{title}</h3>
        <span className="text-xs text-muted-foreground ml-auto">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t(emptyKey, emptyFallback)}</p>
      ) : (
        <div className="space-y-2">{items.map(renderItem)}</div>
      )}
    </div>
  );

  return (
    <div>
      <Button variant="ghost" size="sm" onClick={onBack} className="mb-3" data-testid="hp-back">
        ← {t("homeProfiles.back", "All homes")}
      </Button>
      <PageHeader
        title={profile.property_address}
        description={[profile.unit, profile.owner_name && `${t("homeProfiles.owner", "Owner")}: ${profile.owner_name}`].filter(Boolean).join(" · ")}
        testId="hp-detail-header"
        actions={
          canManage && !activeTransfer && (
            <Button size="sm" variant="outline" onClick={() => setShowTransfer(true)} data-testid="hp-transfer-open">
              <ArrowRightLeft className="h-4 w-4 mr-1" /> {t("homeProfiles.transfer", "Transfer ownership")}
            </Button>
          )
        }
      />

      {activeTransfer && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 mb-4 text-sm flex items-center gap-2" data-testid="hp-active-transfer">
          <Clock className="h-4 w-4 text-amber-600" />
          {t("homeProfiles.active_transfer", "Transfer in progress — status: {status}", { status: activeTransfer.status })}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
        {section(
          <Wrench className="h-4 w-4 text-muted-foreground" />,
          t("homeProfiles.equipment", "Equipment"),
          profile.equipment || [],
          (e, i) => (
            <div key={i} className="rounded-lg bg-muted/50 p-3 text-sm">
              <div className="font-medium">{e.type}{e.brand && ` — ${e.brand} ${e.model || ""}`}</div>
              <div className="text-xs text-muted-foreground">
                {[e.serial && `S/N ${e.serial}`, e.installed_at && `${t("homeProfiles.installed", "Installed")} ${e.installed_at}`, e.warranty_expires && `${t("homeProfiles.warranty_until", "Warranty until")} ${e.warranty_expires}`].filter(Boolean).join(" · ")}
              </div>
              {e.notes && <div className="text-xs mt-1">{e.notes}</div>}
            </div>
          ),
          "homeProfiles.no_equipment", "No equipment recorded yet."
        )}
        {section(
          <History className="h-4 w-4 text-muted-foreground" />,
          t("homeProfiles.service_history", "Service history"),
          profile.service_history || [],
          (s, i) => (
            <div key={i} className="rounded-lg bg-muted/50 p-3 text-sm">
              <div className="flex justify-between gap-2">
                <span className="font-medium">{s.service_type || t("homeProfiles.service", "Service")}</span>
                <span className="text-xs text-muted-foreground">{s.date}</span>
              </div>
              {s.notes && <div className="text-xs mt-1">{s.notes}</div>}
              {(s.technician || s.job_id) && <div className="text-xs text-muted-foreground mt-1">{[s.technician, s.job_id].filter(Boolean).join(" · ")}</div>}
            </div>
          ),
          "homeProfiles.no_service", "No service visits recorded yet."
        )}
        {section(
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />,
          t("homeProfiles.warranties", "Warranties"),
          profile.warranties || [],
          (w, i) => (
            <div key={i} className="rounded-lg bg-muted/50 p-3 text-sm">
              <div className="font-medium">{w.title}</div>
              <div className="text-xs text-muted-foreground">{[w.provider, w.expires_at && `${t("homeProfiles.expires", "Expires")} ${w.expires_at}`].filter(Boolean).join(" · ")}</div>
              {w.coverage && <div className="text-xs mt-1">{w.coverage}</div>}
            </div>
          ),
          "homeProfiles.no_warranties", "No warranties on file."
        )}
        {section(
          <CalendarClock className="h-4 w-4 text-muted-foreground" />,
          t("homeProfiles.maintenance_plans", "Maintenance plans"),
          profile.maintenance_plans || [],
          (m, i) => (
            <div key={i} className="rounded-lg bg-muted/50 p-3 text-sm">
              <div className="flex justify-between gap-2">
                <span className="font-medium">{m.name}</span>
                <Badge variant="outline">{m.status || "active"}</Badge>
              </div>
              <div className="text-xs text-muted-foreground">{[m.frequency, m.next_due && `${t("homeProfiles.next_due", "Next due")} ${m.next_due}`].filter(Boolean).join(" · ")}</div>
            </div>
          ),
          "homeProfiles.no_plans", "No maintenance plans."
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
        <div className="rounded-xl border bg-card p-5">
          <div className="flex items-center gap-2 mb-3">
            <FileText className="h-4 w-4 text-muted-foreground" />
            <h3 className="font-semibold text-sm">{t("homeProfiles.notes", "Notes")}</h3>
          </div>
          <p className="text-sm">{profile.notes || <span className="text-muted-foreground">{t("homeProfiles.no_notes", "No notes.")}</span>}</p>
        </div>
        <div className="rounded-xl border bg-card p-5">
          <div className="flex items-center gap-2 mb-3">
            <ClipboardList className="h-4 w-4 text-muted-foreground" />
            <h3 className="font-semibold text-sm">{t("homeProfiles.owner_record", "Owner record (shop-visible)")}</h3>
          </div>
          <div className="text-sm space-y-1">
            <div><span className="text-muted-foreground">{t("homeProfiles.name", "Name")}: </span>{profile.owner_name || "—"}</div>
            <div><span className="text-muted-foreground">{t("homeProfiles.email", "Email")}: </span>{profile.owner_contact?.email || "—"}</div>
            <div><span className="text-muted-foreground">{t("homeProfiles.phone", "Phone")}: </span>{profile.owner_contact?.phone || "—"}</div>
          </div>
          <p className="text-xs text-muted-foreground mt-3 flex gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
            {t("homeProfiles.owner_pii_hint", "Never shown to a new owner — redacted automatically at transfer.")}
          </p>
        </div>
      </div>

      <TransferTimeline transfers={transfers} />

      {showTransfer && <TransferModal profile={profile} onClose={() => setShowTransfer(false)} onInitiated={onTransferInitiated} />}
    </div>
  );
}

export default function HomeProfiles() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [profiles, setProfiles] = useState([]);
  const [selected, setSelected] = useState(null);
  const [transfers, setTransfers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const canManage = user && ["owner", "admin"].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/home-profiles/");
      setProfiles(r.data.profiles || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const refreshTransfers = useCallback(async (id) => {
    try {
      const tr = await api.get(`/home-profiles/${encodeURIComponent(id)}/transfers`);
      setTransfers(tr.data.transfers || []);
    } catch (e) {
      toast.error(errMessage(e));
    }
  }, []);

  const openProfile = async (summary, refresh = false) => {
    try {
      const [detail, tr] = await Promise.all([
        api.get(`/home-profiles/${encodeURIComponent(summary.id)}`),
        api.get(`/home-profiles/${encodeURIComponent(summary.id)}/transfers`),
      ]);
      setSelected(detail.data);
      setTransfers(tr.data.transfers || []);
      if (refresh) load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  return (
    <div>
      <PageHeader
        title={t("homeProfiles.title", "Home Service Profiles")}
        description={t("homeProfiles.description", "A persistent service record for every home — equipment, history, warranties, plans — that transfers with the house when it's sold.")}
        testId="hp-header"
        actions={
          canManage && !selected && (
            <Button size="sm" onClick={() => setShowNew(true)} data-testid="hp-new-open">
              <Plus className="h-4 w-4 mr-1" /> {t("homeProfiles.new_profile", "New profile")}
            </Button>
          )
        }
      />

      {loading ? (
        <div className="text-muted-foreground">{t("homeProfiles.loading", "Loading home profiles…")}</div>
      ) : selected ? (
        <ProfileDetail
          profile={selected}
          transfers={transfers}
          canManage={canManage}
          onBack={() => { setSelected(null); setTransfers([]); }}
          onTransferInitiated={() => refreshTransfers(selected.id)}
        />
      ) : profiles.length === 0 ? (
        <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
          <Home className="h-6 w-6 mx-auto mb-2" />
          {t("homeProfiles.empty", "No home profiles yet. Create one to start building a service record for a property.")}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {profiles.map((p) => (
            <button
              key={p.id}
              onClick={() => openProfile(p)}
              className="rounded-xl border bg-card p-4 text-left hover:border-primary/50 transition-colors"
              data-testid={`hp-profile-${p.id}`}
            >
              <div className="flex items-start gap-2 mb-2">
                <Home className="h-4 w-4 mt-0.5 text-muted-foreground shrink-0" />
                <div className="font-medium text-sm">{p.property_address}{p.unit ? `, ${p.unit}` : ""}</div>
              </div>
              {p.owner_name && <div className="text-xs text-muted-foreground mb-2">{t("homeProfiles.owner", "Owner")}: {p.owner_name}</div>}
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge variant="secondary"><Wrench className="h-3 w-3 mr-1" />{p.equipment_count}</Badge>
                <Badge variant="secondary"><History className="h-3 w-3 mr-1" />{p.service_count}</Badge>
                <Badge variant="secondary"><ShieldCheck className="h-3 w-3 mr-1" />{p.warranty_count}</Badge>
              </div>
            </button>
          ))}
        </div>
      )}

      {showNew && <NewProfileModal onClose={() => setShowNew(false)} onCreated={load} />}
    </div>
  );
}
