import { useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import {
  MapPin, Loader2, Plus, Pencil, Building2, Users, Banknote,
  Briefcase, Info, X, Trash2,
} from "lucide-react";

const BASE = "/locations";
const CTX_KEY = "momo_location_context";
const ENTITY_TYPES = ["job", "tech", "equipment"];

const emptyForm = {
  name: "", street: "", city: "", state: "", zip: "",
  timezone: "", phone: "", manager: "", active: true,
};

function addressLine(loc) {
  const a = loc.address || {};
  return [a.street, [a.city, a.state].filter(Boolean).join(", "), a.zip]
    .filter(Boolean)
    .join(" · ");
}

function LocationForm({ initial, busy, onSave, onCancel, t }) {
  const [form, setForm] = useState(initial);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const submit = () => {
    if (!form.name.trim()) {
      toast.error(t("locations.name_required"));
      return;
    }
    onSave({
      name: form.name.trim(),
      address: { street: form.street.trim(), city: form.city.trim(), state: form.state.trim(), zip: form.zip.trim() },
      timezone: form.timezone.trim(),
      phone: form.phone.trim(),
      manager: form.manager.trim(),
      active: form.active,
    });
  };
  const field = (key, label, ph = "") => (
    <label className="space-y-1 block">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Input value={form[key]} onChange={set(key)} placeholder={ph} data-testid={`location-field-${key}`} />
    </label>
  );
  return (
    <div className="surface p-4 space-y-3" data-testid="location-form">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {field("name", t("locations.name"))}
        {field("manager", t("locations.manager"))}
        {field("street", t("locations.street"))}
        {field("city", t("locations.city"))}
        {field("state", t("locations.state"))}
        {field("zip", t("locations.zip"))}
        {field("timezone", t("locations.timezone"), "America/Chicago")}
        {field("phone", t("locations.phone"))}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.active} onChange={set("active")} />
        {t("locations.active")}
      </label>
      <div className="flex gap-2">
        <Button size="sm" onClick={submit} disabled={busy} data-testid="location-save">
          {busy && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
          {t("locations.save")}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>{t("locations.cancel")}</Button>
      </div>
    </div>
  );
}

function LocationCard({ loc, onEdit, onDashboard, onDeactivate, confirming, setConfirming, t }) {
  return (
    <div className="surface p-4 space-y-2" data-testid={`location-card-${loc.id}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium truncate flex items-center gap-2">
            <Building2 className="h-4 w-4 text-muted-foreground shrink-0" />
            {loc.name}
          </div>
          {addressLine(loc) && <div className="text-sm text-muted-foreground truncate">{addressLine(loc)}</div>}
        </div>
        <div className="flex gap-1 shrink-0">
          {loc.is_default && <Badge variant="secondary">{t("locations.default_badge")}</Badge>}
          {!loc.active && <Badge variant="destructive">{t("locations.inactive_badge")}</Badge>}
        </div>
      </div>
      <div className="text-sm text-muted-foreground space-y-0.5">
        {loc.phone && <div>{loc.phone}</div>}
        {loc.manager && <div>{loc.manager}</div>}
        {loc.timezone && <div>{loc.timezone}</div>}
      </div>
      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" variant="outline" onClick={() => onDashboard(loc.id)} data-testid={`location-dash-${loc.id}`}>
          <Briefcase className="h-3 w-3 mr-1" />
          {t("locations.view_dashboard")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onEdit(loc)} data-testid={`location-edit-${loc.id}`}>
          <Pencil className="h-3 w-3 mr-1" />
          {t("locations.edit_location")}
        </Button>
        {loc.active && (
          confirming ? (
            <>
              <Button size="sm" variant="destructive" onClick={() => onDeactivate(loc.id)} data-testid={`location-deactivate-confirm-${loc.id}`}>
                {t("locations.confirm_yes")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>{t("locations.cancel")}</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(loc.id)} data-testid={`location-deactivate-${loc.id}`}>
              <Trash2 className="h-3 w-3 mr-1" />
              {t("locations.deactivate")}
            </Button>
          )
        )}
      </div>
      {confirming === loc.id && (
        <div className="text-xs text-muted-foreground">{t("locations.deactivate_confirm")}</div>
      )}
    </div>
  );
}

function StatCard({ icon: Icon, label, value, testId }) {
  return (
    <div className="surface p-4" data-testid={testId}>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Icon className="h-4 w-4" />
        {label}
      </div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}

export default function Locations() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canSeeConsolidated = ["owner", "admin", "platform_admin"].includes(user?.role);

  const [tab, setTab] = useState("locations");
  const [locations, setLocations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(null);

  const [selectedId, setSelectedId] = useState("");
  const [dash, setDash] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [dashLoading, setDashLoading] = useState(false);

  const [assignEntity, setAssignEntity] = useState("job");
  const [assignId, setAssignId] = useState("");
  const [assignBusy, setAssignBusy] = useState(false);

  const [cons, setCons] = useState(null);
  const [consForbidden, setConsForbidden] = useState(false);
  const [consLoading, setConsLoading] = useState(false);

  const [viewCtx, setViewCtx] = useState(() => {
    try { return localStorage.getItem(CTX_KEY) || "all"; } catch { return "all"; }
  });

  const selected = useMemo(
    () => locations.find((l) => l.id === selectedId) || null,
    [locations, selectedId]
  );

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${BASE}/`);
      const list = res.data.locations || [];
      setLocations(list);
      if (!list.find((l) => l.id === selectedId)) {
        const def = list.find((l) => l.is_default) || list[0];
        setSelectedId(def ? def.id : "");
      }
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const loadDashboard = async (locId) => {
    if (!locId) return;
    setDashLoading(true);
    try {
      const [d, j] = await Promise.all([
        api.get(`${BASE}/${locId}/dashboard`),
        api.get(`${BASE}/${locId}/jobs`),
      ]);
      setDash(d.data);
      setJobs(j.data.jobs || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setDashLoading(false);
    }
  };

  useEffect(() => {
    if (tab === "dashboard" && selectedId) loadDashboard(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, selectedId]);

  const loadConsolidated = async () => {
    setConsLoading(true);
    setConsForbidden(false);
    try {
      const res = await api.get(`${BASE}/consolidated`);
      setCons(res.data);
    } catch (e) {
      if (e?.response?.status === 403) setConsForbidden(true);
      else toast.error(errMessage(e));
    } finally {
      setConsLoading(false);
    }
  };

  useEffect(() => {
    if (tab === "consolidated" && canSeeConsolidated) loadConsolidated();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, canSeeConsolidated]);

  const saveLocation = async (payload) => {
    setBusy(true);
    try {
      if (editing) {
        await api.put(`${BASE}/${editing.id}`, payload);
        toast.success(t("locations.saved_toast"));
      } else {
        await api.post(`${BASE}/`, payload);
        toast.success(t("locations.created_toast"));
      }
      setFormOpen(false);
      setEditing(null);
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const deactivate = async (locId) => {
    try {
      await api.delete(`${BASE}/${locId}`);
      toast.success(t("locations.deactivated_toast"));
      setConfirming(null);
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const doAssign = async () => {
    if (!assignId.trim() || !selectedId) return;
    setAssignBusy(true);
    try {
      const res = await api.post(`${BASE}/assign`, {
        entity: assignEntity,
        entity_id: assignId.trim(),
        location_id: selectedId,
      });
      toast.success(t("locations.assign.assigned_toast", { name: res.data.location_name || selected?.name || "" }));
      setAssignId("");
      await loadDashboard(selectedId);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setAssignBusy(false);
    }
  };

  const doUnassign = async (entity, entityId) => {
    try {
      await api.delete(`${BASE}/assign`, { params: { entity, entity_id: entityId } });
      toast.success(t("locations.assign.unassigned_toast"));
      await loadDashboard(selectedId);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const setCtx = (v) => {
    setViewCtx(v);
    try { localStorage.setItem(CTX_KEY, v); } catch { /* private mode */ }
  };

  const tabs = [
    { id: "locations", label: t("locations.tabs.locations") },
    { id: "dashboard", label: t("locations.tabs.dashboard") },
    { id: "consolidated", label: t("locations.tabs.consolidated") },
  ];

  return (
    <div className="space-y-6" data-testid="locations-page">
      <PageHeader
        eyebrow={t("locations.eyebrow")}
        title={t("locations.title")}
        description={t("locations.sub")}
        actions={
          tab === "locations" && !formOpen ? (
            <Button size="sm" onClick={() => { setEditing(null); setFormOpen(true); }} data-testid="location-add">
              <Plus className="h-4 w-4 mr-1" />
              {t("locations.add_location")}
            </Button>
          ) : null
        }
      />

      {/* Location switcher: viewing context + honest note about dispatch scoping */}
      <div className="surface p-4 flex flex-col sm:flex-row sm:items-center gap-3" data-testid="location-switcher">
        <div className="flex items-center gap-2 shrink-0">
          <MapPin className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">{t("locations.switcher.viewing_as")}</span>
        </div>
        <select
          className="border rounded-md px-2 py-1 text-sm bg-background"
          value={viewCtx}
          onChange={(e) => setCtx(e.target.value)}
          data-testid="location-switcher-select"
        >
          <option value="all">{t("locations.switcher.all_locations")}</option>
          {locations.map((l) => (
            <option key={l.id} value={l.id}>{l.name}</option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground flex items-start gap-1">
          <Info className="h-3 w-3 mt-0.5 shrink-0" />
          {t("locations.switcher.note")}
        </p>
      </div>

      <div className="flex gap-2 border-b" role="tablist">
        {tabs.map((tb) => (
          <button
            key={tb.id}
            role="tab"
            aria-selected={tab === tb.id}
            onClick={() => setTab(tb.id)}
            data-testid={`locations-tab-${tb.id}`}
            className={`px-3 py-2 text-sm border-b-2 -mb-px ${
              tab === tb.id
                ? "border-primary font-medium"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tb.label}
          </button>
        ))}
      </div>

      {tab === "locations" && (
        <div className="space-y-4">
          {formOpen && (
            <LocationForm
              initial={editing ? {
                name: editing.name || "",
                street: editing.address?.street || "",
                city: editing.address?.city || "",
                state: editing.address?.state || "",
                zip: editing.address?.zip || "",
                timezone: editing.timezone || "",
                phone: editing.phone || "",
                manager: editing.manager || "",
                active: editing.active !== false,
              } : emptyForm}
              busy={busy}
              onSave={saveLocation}
              onCancel={() => { setFormOpen(false); setEditing(null); }}
              t={t}
            />
          )}
          {loading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("locations.loading")}
            </div>
          ) : locations.length === 0 ? (
            <div className="surface p-8 text-center text-muted-foreground">{t("locations.empty")}</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {locations.map((loc) => (
                <LocationCard
                  key={loc.id}
                  loc={loc}
                  t={t}
                  confirming={confirming}
                  setConfirming={setConfirming}
                  onEdit={(l) => { setEditing(l); setFormOpen(true); }}
                  onDashboard={(id) => { setSelectedId(id); setTab("dashboard"); }}
                  onDeactivate={deactivate}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "dashboard" && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground">{t("locations.dashboard.select_location")}</label>
            <select
              className="border rounded-md px-2 py-1 text-sm bg-background"
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              data-testid="dashboard-location-select"
            >
              {locations.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}{!l.active ? ` (${t("locations.inactive_badge")})` : ""}
                </option>
              ))}
            </select>
          </div>

          {dashLoading || !dash ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("locations.loading")}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <StatCard icon={Banknote} label={t("locations.stats.revenue")} value={fmtUSD(dash.revenue)} testId="dash-stat-revenue" />
                <StatCard icon={Briefcase} label={t("locations.stats.jobs")} value={dash.jobs_count} testId="dash-stat-jobs" />
                <StatCard icon={Users} label={t("locations.stats.techs")} value={dash.active_techs} testId="dash-stat-techs" />
              </div>

              {Object.keys(dash.jobs_by_status || {}).length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">{t("locations.dashboard.by_status")}:</span>
                  {Object.entries(dash.jobs_by_status).map(([st, n]) => (
                    <Badge key={st} variant="secondary" data-testid={`dash-status-${st}`}>{st}: {n}</Badge>
                  ))}
                </div>
              )}

              <div className="surface p-4 space-y-3" data-testid="assign-panel">
                <div className="font-medium text-sm">{t("locations.assign.title")}</div>
                <div className="flex flex-col sm:flex-row gap-2">
                  <select
                    className="border rounded-md px-2 py-1 text-sm bg-background"
                    value={assignEntity}
                    onChange={(e) => setAssignEntity(e.target.value)}
                    data-testid="assign-entity"
                  >
                    {ENTITY_TYPES.map((e) => (
                      <option key={e} value={e}>{t(`locations.assign.entity_${e}`)}</option>
                    ))}
                  </select>
                  <Input
                    className="flex-1"
                    value={assignId}
                    onChange={(e) => setAssignId(e.target.value)}
                    placeholder={t("locations.assign.entity_id_placeholder")}
                    data-testid="assign-entity-id"
                  />
                  <Button size="sm" onClick={doAssign} disabled={assignBusy || !assignId.trim()} data-testid="assign-button">
                    {assignBusy && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
                    {t("locations.assign.assign_button")}
                  </Button>
                </div>
              </div>

              <div className="surface p-4 space-y-2">
                <div className="font-medium text-sm">
                  {t("locations.dashboard.jobs_at", { name: dash.location?.name || "" })}
                </div>
                {jobs.length === 0 ? (
                  <div className="text-sm text-muted-foreground">{t("locations.dashboard.no_jobs")}</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-muted-foreground border-b">
                          <th className="py-2 pr-2 font-medium">{t("locations.dashboard.customer")}</th>
                          <th className="py-2 pr-2 font-medium">{t("locations.dashboard.service")}</th>
                          <th className="py-2 pr-2 font-medium">{t("locations.dashboard.starts")}</th>
                          <th className="py-2 pr-2 font-medium">{t("locations.dashboard.status")}</th>
                          <th className="py-2 font-medium" aria-label="actions" />
                        </tr>
                      </thead>
                      <tbody>
                        {jobs.map((j) => (
                          <tr key={j.id} className="border-b last:border-0" data-testid={`dash-job-${j.id}`}>
                            <td className="py-2 pr-2">{j.customer_name || "—"}</td>
                            <td className="py-2 pr-2">{j.service_name || "—"}</td>
                            <td className="py-2 pr-2 text-muted-foreground">
                              {(j.start_at || "").slice(0, 16).replace("T", " ") || "—"}
                            </td>
                            <td className="py-2 pr-2"><Badge variant="secondary">{j.status || "—"}</Badge></td>
                            <td className="py-2 text-right">
                              <Button
                                size="sm"
                                variant="ghost"
                                title={t("locations.assign.unassign_button")}
                                onClick={() => doUnassign("job", j.id)}
                                data-testid={`dash-unassign-${j.id}`}
                              >
                                <X className="h-3 w-3" />
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {tab === "consolidated" && (
        <div className="space-y-4">
          {!canSeeConsolidated || consForbidden ? (
            <div className="surface p-8 text-center text-muted-foreground" data-testid="consolidated-forbidden">
              {t("locations.consolidated.forbidden")}
            </div>
          ) : consLoading || !cons ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("locations.loading")}
            </div>
          ) : (
            <>
              <div className="surface p-4 overflow-x-auto" data-testid="consolidated-table">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-muted-foreground border-b">
                      <th className="py-2 pr-2 font-medium">{t("locations.consolidated.location_col")}</th>
                      <th className="py-2 pr-2 font-medium text-right">{t("locations.consolidated.jobs_col")}</th>
                      <th className="py-2 pr-2 font-medium text-right">{t("locations.consolidated.revenue_col")}</th>
                      <th className="py-2 font-medium text-right">{t("locations.consolidated.techs_col")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cons.locations.map((r) => (
                      <tr key={r.location_id} className="border-b last:border-0" data-testid={`cons-row-${r.location_id}`}>
                        <td className="py-2 pr-2">
                          <span className="inline-flex items-center gap-2">
                            {r.name}
                            {r.is_default && <Badge variant="secondary">{t("locations.default_badge")}</Badge>}
                            {!r.active && <Badge variant="destructive">{t("locations.inactive_badge")}</Badge>}
                          </span>
                        </td>
                        <td className="py-2 pr-2 text-right">{r.jobs_count}</td>
                        <td className="py-2 pr-2 text-right">{fmtUSD(r.revenue)}</td>
                        <td className="py-2 text-right">{r.active_techs}</td>
                      </tr>
                    ))}
                    <tr className="font-semibold border-t-2" data-testid="cons-totals">
                      <td className="py-2 pr-2">{t("locations.consolidated.totals_row")}</td>
                      <td className="py-2 pr-2 text-right">{cons.totals.jobs_count}</td>
                      <td className="py-2 pr-2 text-right">{fmtUSD(cons.totals.revenue)}</td>
                      <td className="py-2 text-right">{cons.totals.active_techs}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              {cons.unassigned && cons.unassigned.jobs_count > 0 && (
                <p className="text-xs text-muted-foreground flex items-start gap-1" data-testid="cons-unassigned">
                  <Info className="h-3 w-3 mt-0.5 shrink-0" />
                  {t("locations.consolidated.unassigned_note", {
                    n: cons.unassigned.jobs_count,
                    v: fmtUSD(cons.unassigned.revenue),
                  })}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
