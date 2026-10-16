import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Loader2, Plus, Package, Truck, Warehouse, ArrowLeftRight,
  ArrowDownToLine, Wrench, History, Bell, X, Trash2,
} from "lucide-react";

const BASE = "/inventory";

function Modal({ title, onClose, children, testid }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid={testid}>
      <div className="surface w-full max-w-md p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">{title}</h3>
          <Button variant="ghost" size="sm" onClick={onClose} data-testid={`${testid}-close`}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block space-y-1">
      <span className="text-sm text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

export default function Inventory() {
  const { t } = useTranslation("inventory");
  const [locations, setLocations] = useState([]);
  const [parts, setParts] = useState([]);
  const [stock, setStock] = useState([]);
  const [movements, setMovements] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [activeLoc, setActiveLoc] = useState("");
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState(null); // receive|transfer|use|adjust|addLocation|addPart
  const [busy, setBusy] = useState(false);
  const [jobView, setJobView] = useState(null); // {job_id, lines, total_qty, total_cost}

  // dialog form state
  const [form, setForm] = useState({});

  const load = async () => {
    setLoading(true);
    try {
      const [locs, prt, stk, mvs, al] = await Promise.all([
        api.get(`${BASE}/locations`),
        api.get(`${BASE}/parts`),
        api.get(`${BASE}/stock`),
        api.get(`${BASE}/movements`, { params: { limit: 100 } }),
        api.get(`${BASE}/alerts`),
      ]);
      setLocations(locs.data.locations || []);
      setParts(prt.data.parts || []);
      setStock(stk.data.stock || []);
      setMovements(mvs.data.movements || []);
      setAlerts(al.data.alerts || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const openDialog = (name, preset = {}) => {
    setForm(preset);
    setDialog(name);
  };

  const submitMovement = async (payload) => {
    setBusy(true);
    try {
      const res = await api.post(`${BASE}/movements`, payload);
      toast.success(t("movement_recorded"));
      setDialog(null);
      await load();
      return res.data.movement;
    } catch (e) {
      const msg = errMessage(e);
      toast.error(t("movement_failed", { msg }));
      throw e;
    } finally {
      setBusy(false);
    }
  };

  const submitAdjust = async () => {
    setBusy(true);
    try {
      await api.post(`${BASE}/movements/adjust`, {
        part_id: form.part_id, location_id: form.location_id,
        qty: parseInt(form.qty, 10) || 0, note: form.note || undefined,
      });
      toast.success(t("movement_recorded"));
      setDialog(null);
      await load();
    } catch (e) {
      toast.error(t("movement_failed", { msg: errMessage(e) }));
    } finally {
      setBusy(false);
    }
  };

  const submitLocation = async () => {
    if (!form.name?.trim()) return;
    setBusy(true);
    try {
      await api.post(`${BASE}/locations`, {
        type: form.type || "warehouse", name: form.name.trim(),
        tech_name: form.tech_name?.trim() || undefined,
      });
      toast.success(t("location_created"));
      setDialog(null);
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const submitPart = async () => {
    if (!form.sku?.trim() || !form.name?.trim()) return;
    setBusy(true);
    try {
      await api.post(`${BASE}/parts`, {
        sku: form.sku.trim(), name: form.name.trim(),
        reorder_point: parseInt(form.reorder_point, 10) || 0,
        unit: form.unit?.trim() || "ea",
        unit_cost: form.unit_cost ? parseFloat(form.unit_cost) : undefined,
      });
      toast.success(t("part_created"));
      setDialog(null);
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const deleteLocation = async (id) => {
    if (!window.confirm(t("confirm_delete_location"))) return;
    try {
      await api.delete(`${BASE}/locations/${id}`);
      toast.success(t("location_deleted"));
      if (activeLoc === id) setActiveLoc("");
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const viewJobUsage = async (jobId) => {
    try {
      const res = await api.get(`${BASE}/job-usage/${encodeURIComponent(jobId)}`);
      setJobView(res.data);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const shownStock = activeLoc ? stock.filter((s) => s.location_id === activeLoc) : stock;
  const typeIcon = (type) => (type === "truck"
    ? <Truck className="h-3.5 w-3.5 mr-1 inline" />
    : <Warehouse className="h-3.5 w-3.5 mr-1 inline" />);

  const movementVerb = (m) => ({
    receive: t("receive"),
    transfer: t("transfer"),
    use_on_job: t("use_on_job"),
    adjust: t("adjust"),
  })[m.type] || m.type;

  const partName = (id) => parts.find((p) => p.id === id)?.name || id;
  const locName = (id) => locations.find((l) => l.id === id)?.name || id;

  return (
    <div className="space-y-6" data-testid="inventory-page">
      <PageHeader
        eyebrow={t("eyebrow")}
        title={t("title")}
        description={t("sub")}
      />

      {/* location tabs */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button
          variant={activeLoc === "" ? "default" : "outline"} size="sm"
          onClick={() => setActiveLoc("")} data-testid="inventory-tab-all">
          <Package className="h-4 w-4 mr-1" />{t("all_locations")}
        </Button>
        {locations.map((l) => (
          <div key={l.id} className="inline-flex items-center">
            <Button
              variant={activeLoc === l.id ? "default" : "outline"} size="sm"
              onClick={() => setActiveLoc(l.id)} data-testid={`inventory-tab-${l.id}`}
              className="rounded-r-none">
              {typeIcon(l.type)}{l.name}
            </Button>
            <Button
              variant="outline" size="sm" className="rounded-l-none border-l-0 px-2"
              onClick={() => deleteLocation(l.id)} data-testid={`inventory-del-${l.id}`}>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        ))}
        <Button variant="ghost" size="sm" onClick={() => openDialog("addLocation")}
          data-testid="inventory-add-location">
          <Plus className="h-4 w-4 mr-1" />{t("add_location")}
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />{t("loading")}
        </div>
      ) : (
        <>
          {/* low-stock alerts */}
          <div className="surface p-4" data-testid="inventory-alerts">
            <div className="flex items-center gap-2 mb-2">
              <Bell className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium text-sm">
                {t("alerts")} ({alerts.length})
              </span>
            </div>
            {alerts.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("no_alerts")}</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {alerts.map((a) => (
                  <Badge key={`${a.part_id}-${a.location_id}`} variant="destructive"
                    data-testid={`inventory-alert-${a.part_id}-${a.location_id}`}>
                    {a.sku} · {a.location_name} — {a.qty} {t("at_or_below")}
                    {a.out_of_stock && ` · ${t("out_of_stock")}`}
                  </Badge>
                ))}
              </div>
            )}
          </div>

          {/* action bar */}
          <div className="flex items-center gap-2 flex-wrap">
            <Button size="sm" onClick={() => openDialog("receive")} data-testid="inventory-receive">
              <ArrowDownToLine className="h-4 w-4 mr-1" />{t("receive")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => openDialog("transfer")}
              data-testid="inventory-transfer">
              <ArrowLeftRight className="h-4 w-4 mr-1" />{t("transfer")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => openDialog("use")}
              data-testid="inventory-use">
              <Wrench className="h-4 w-4 mr-1" />{t("use_on_job")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => openDialog("adjust")}
              data-testid="inventory-adjust">
              <History className="h-4 w-4 mr-1" />{t("adjust")}
            </Button>
            <div className="flex-1" />
            <Button size="sm" variant="ghost" onClick={() => openDialog("addPart")}
              data-testid="inventory-add-part">
              <Plus className="h-4 w-4 mr-1" />{t("add_part")}
            </Button>
          </div>

          {/* stock table */}
          <div className="surface overflow-hidden" data-testid="inventory-stock">
            <div className="px-4 py-3 border-b font-medium text-sm">
              {t("stock")}{activeLoc && ` — ${locName(activeLoc)}`}
            </div>
            {shownStock.length === 0 ? (
              <p className="p-6 text-sm text-muted-foreground text-center">
                {t("no_stock")}
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-muted-foreground border-b">
                    <th className="px-4 py-2 font-medium">{t("sku")}</th>
                    <th className="px-4 py-2 font-medium">{t("part")}</th>
                    {!activeLoc && <th className="px-4 py-2 font-medium">{t("location")}</th>}
                    <th className="px-4 py-2 font-medium text-right">{t("qty")}</th>
                    <th className="px-4 py-2 font-medium">{t("status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {shownStock.map((s) => (
                    <tr key={`${s.part_id}-${s.location_id}`} className="border-b last:border-0"
                      data-testid={`inventory-row-${s.part_id}-${s.location_id}`}>
                      <td className="px-4 py-2 font-mono text-xs">{s.sku}</td>
                      <td className="px-4 py-2">{s.part_name}</td>
                      {!activeLoc && <td className="px-4 py-2">{s.location_name}</td>}
                      <td className="px-4 py-2 text-right font-medium">
                        {s.qty} {s.unit}
                      </td>
                      <td className="px-4 py-2">
                        {s.low_stock ? (
                          <Badge variant="destructive">
                            {s.qty === 0 ? t("out_of_stock") : t("low_stock")}
                            {` · ${t("reorder_point")} ${s.reorder_point}`}
                          </Badge>
                        ) : (
                          <Badge variant="secondary">{t("ok_stock")}</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* movement history */}
          <div className="surface overflow-hidden" data-testid="inventory-history">
            <div className="px-4 py-3 border-b font-medium text-sm">{t("history")}</div>
            {movements.length === 0 ? (
              <p className="p-6 text-sm text-muted-foreground text-center">
                {t("no_history")}
              </p>
            ) : (
              <ul className="divide-y">
                {movements.slice(0, 50).map((m) => (
                  <li key={m.id} className="px-4 py-2.5 text-sm flex items-center gap-3"
                    data-testid={`inventory-movement-${m.id}`}>
                    <Badge variant="outline">{movementVerb(m)}</Badge>
                    <span className="font-medium">{partName(m.part_id)}</span>
                    <span className="text-muted-foreground">×{m.qty}</span>
                    <span className="text-muted-foreground text-xs">
                      {m.from_location_id && ` ${locName(m.from_location_id)} →`}
                      {m.to_location_id && ` ${locName(m.to_location_id)}`}
                      {!m.from_location_id && !m.to_location_id && "—"}
                    </span>
                    {m.job_id && (
                      <Button variant="link" size="sm" className="p-0 h-auto text-xs"
                        onClick={() => viewJobUsage(m.job_id)}
                        data-testid={`inventory-view-job-${m.job_id}`}>
                        {t("job_id")}: {m.job_id.slice(0, 8)}
                      </Button>
                    )}
                    {m.note && <span className="text-xs text-muted-foreground truncate">{m.note}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {/* ---- dialogs ---- */}

      {dialog === "addLocation" && (
        <Modal title={t("add_location")} onClose={() => setDialog(null)}
          testid="inventory-dialog-location">
          <Field label={t("location_type")}>
            <div className="flex gap-2">
              {["warehouse", "truck"].map((type) => (
                <Button key={type} size="sm"
                  variant={(form.type || "warehouse") === type ? "default" : "outline"}
                  onClick={() => set("type", type)}>{t(`${type}`)}</Button>
              ))}
            </div>
          </Field>
          <Field label={t("location_name")}>
            <Input value={form.name || ""} onChange={(e) => set("name", e.target.value)}
              data-testid="inventory-dialog-location-name" />
          </Field>
          {(form.type || "warehouse") === "truck" && (
            <Field label={t("tech_name")}>
              <Input value={form.tech_name || ""} onChange={(e) => set("tech_name", e.target.value)}
                data-testid="inventory-dialog-location-tech" />
            </Field>
          )}
          <Button onClick={submitLocation} disabled={busy} data-testid="inventory-dialog-location-submit">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : t("create")}
          </Button>
        </Modal>
      )}

      {dialog === "addPart" && (
        <Modal title={t("add_part")} onClose={() => setDialog(null)}
          testid="inventory-dialog-part">
          <Field label={t("sku")}>
            <Input value={form.sku || ""} onChange={(e) => set("sku", e.target.value)}
              data-testid="inventory-dialog-part-sku" />
          </Field>
          <Field label={t("part_name")}>
            <Input value={form.name || ""} onChange={(e) => set("name", e.target.value)}
              data-testid="inventory-dialog-part-name" />
          </Field>
          <div className="grid grid-cols-3 gap-2">
            <Field label={t("reorder_point_label")}>
              <Input type="number" min="0" value={form.reorder_point ?? ""}
                onChange={(e) => set("reorder_point", e.target.value)} />
            </Field>
            <Field label={t("unit")}>
              <Input value={form.unit || ""} onChange={(e) => set("unit", e.target.value)} />
            </Field>
            <Field label={t("unit_cost")}>
              <Input type="number" min="0" step="0.01" value={form.unit_cost ?? ""}
                onChange={(e) => set("unit_cost", e.target.value)} />
            </Field>
          </div>
          <Button onClick={submitPart} disabled={busy} data-testid="inventory-dialog-part-submit">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : t("create")}
          </Button>
        </Modal>
      )}

      {["receive", "transfer", "use"].includes(dialog) && (
        <Modal title={t(`${dialog === "use" ? "use_on_job" : dialog}`)}
          onClose={() => setDialog(null)} testid={`inventory-dialog-${dialog}`}>
          <Field label={t("part")}>
            <select className="w-full border rounded-md px-3 py-2 bg-background text-sm"
              value={form.part_id || ""} onChange={(e) => set("part_id", e.target.value)}
              data-testid="inventory-dialog-part-select">
              <option value="">—</option>
              {parts.map((p) => <option key={p.id} value={p.id}>{p.sku} — {p.name}</option>)}
            </select>
          </Field>
          {dialog === "receive" && (
            <Field label={t("to")}>
              <select className="w-full border rounded-md px-3 py-2 bg-background text-sm"
                value={form.location_id || ""} onChange={(e) => set("location_id", e.target.value)}
                data-testid="inventory-dialog-to">
                <option value="">—</option>
                {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            </Field>
          )}
          {dialog === "use" && (
            <>
              <Field label={t("from")}>
                <select className="w-full border rounded-md px-3 py-2 bg-background text-sm"
                  value={form.from_location_id || ""} onChange={(e) => set("from_location_id", e.target.value)}
                  data-testid="inventory-dialog-from">
                  <option value="">—</option>
                  {locations.filter((l) => l.type === "truck").map((l) => (
                    <option key={l.id} value={l.id}>{l.name}</option>))}
                </select>
              </Field>
              <Field label={t("job_id")}>
                <Input value={form.job_id || ""} onChange={(e) => set("job_id", e.target.value)}
                  placeholder={t("job_id_placeholder")}
                  data-testid="inventory-dialog-job" />
              </Field>
            </>
          )}
          {dialog === "transfer" && (
            <div className="grid grid-cols-2 gap-2">
              <Field label={t("from")}>
                <select className="w-full border rounded-md px-3 py-2 bg-background text-sm"
                  value={form.from_location_id || ""} onChange={(e) => set("from_location_id", e.target.value)}
                  data-testid="inventory-dialog-from">
                  <option value="">—</option>
                  {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </Field>
              <Field label={t("to")}>
                <select className="w-full border rounded-md px-3 py-2 bg-background text-sm"
                  value={form.to_location_id || ""} onChange={(e) => set("to_location_id", e.target.value)}
                  data-testid="inventory-dialog-to">
                  <option value="">—</option>
                  {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </Field>
            </div>
          )}
          <Field label={t("qty_label")}>
            <Input type="number" min="1" value={form.qty ?? ""}
              onChange={(e) => set("qty", e.target.value)} data-testid="inventory-dialog-qty" />
          </Field>
          <Field label={t("note")}>
            <Input value={form.note || ""} onChange={(e) => set("note", e.target.value)}
              data-testid="inventory-dialog-note" />
          </Field>
          <Button
            disabled={busy || !form.part_id || !(parseInt(form.qty, 10) > 0)}
            onClick={() => {
              const q = parseInt(form.qty, 10);
              if (dialog === "receive") {
                submitMovement({ part_id: form.part_id, to_location_id: form.location_id,
                  qty: q, type: "receive", note: form.note || undefined });
              } else if (dialog === "transfer") {
                submitMovement({ part_id: form.part_id, from_location_id: form.from_location_id,
                  to_location_id: form.to_location_id, qty: q, type: "transfer",
                  note: form.note || undefined });
              } else {
                submitMovement({ part_id: form.part_id, from_location_id: form.from_location_id,
                  qty: q, type: "use_on_job", job_id: form.job_id?.trim() || undefined,
                  note: form.note || undefined });
              }
            }}
            data-testid="inventory-dialog-submit">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : t("submit")}
          </Button>
        </Modal>
      )}

      {dialog === "adjust" && (
        <Modal title={t("adjust")} onClose={() => setDialog(null)}
          testid="inventory-dialog-adjust">
          <Field label={t("part")}>
            <select className="w-full border rounded-md px-3 py-2 bg-background text-sm"
              value={form.part_id || ""} onChange={(e) => set("part_id", e.target.value)}
              data-testid="inventory-dialog-part-select">
              <option value="">—</option>
              {parts.map((p) => <option key={p.id} value={p.id}>{p.sku} — {p.name}</option>)}
            </select>
          </Field>
          <Field label={t("location")}>
            <select className="w-full border rounded-md px-3 py-2 bg-background text-sm"
              value={form.location_id || ""} onChange={(e) => set("location_id", e.target.value)}
              data-testid="inventory-dialog-to">
              <option value="">—</option>
              {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </Field>
          <Field label={`${t("qty_label")} (+/−)`}>
            <Input type="number" value={form.qty ?? ""}
              onChange={(e) => set("qty", e.target.value)} data-testid="inventory-dialog-qty" />
          </Field>
          <Field label={t("note")}>
            <Input value={form.note || ""} onChange={(e) => set("note", e.target.value)}
              data-testid="inventory-dialog-note" />
          </Field>
          <Button disabled={busy || !form.part_id || !form.location_id}
            onClick={submitAdjust} data-testid="inventory-dialog-submit">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : t("submit")}
          </Button>
        </Modal>
      )}

      {jobView && (
        <Modal title={t("job_usage", { jobId: (jobView.job_id || "").slice(0, 8) })}
          onClose={() => setJobView(null)} testid="inventory-dialog-jobview">
          {jobView.lines.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("no_history")}</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {jobView.lines.map((l) => (
                <li key={l.part_id} className="flex justify-between gap-2">
                  <span>{l.part_name} <span className="text-muted-foreground">×{l.qty_used}</span></span>
                  <span className="text-muted-foreground">${l.total_cost.toFixed(2)}</span>
                </li>
              ))}
              <li className="flex justify-between gap-2 pt-2 border-t font-medium">
                <span>{t("total_qty")}: {jobView.total_qty}</span>
                <span>{t("total_cost")}: ${jobView.total_cost.toFixed(2)}</span>
              </li>
            </ul>
          )}
          <Button variant="ghost" onClick={() => setJobView(null)}>{t("back")}</Button>
        </Modal>
      )}
    </div>
  );
}
