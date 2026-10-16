import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Plus, Pencil, History, Search, Wrench, MapPin, CalendarClock } from "lucide-react";

const EMPTY_FORM = {
  customer_id: "",
  customer_name: "",
  location_label: "",
  service_address: { street: "", city: "", state: "", zip: "" },
  equipment_type: "",
  make: "",
  model: "",
  serial_number: "",
  install_date: "",
  notes: "",
  active: true,
};

const SUGGESTED = [
  "furnace", "ac_condenser", "air_handler", "heat_pump", "thermostat",
  "water_heater", "boiler", "electrical_panel", "generator",
  "garbage_disposal", "well_pump", "septic_system", "other",
];

function AddressFields({ value, onChange }) {
  const set = (patch) => onChange({ ...value, ...patch });
  return (
    <div>
      <label className="text-sm font-medium flex items-center gap-1">
        <MapPin className="h-3.5 w-3.5" /> Service address *
      </label>
      <div className="grid grid-cols-1 gap-2 mt-1">
        <Input
          value={value.street}
          onChange={(e) => set({ street: e.target.value })}
          placeholder="Street (e.g. 123 Main St)"
        />
        <div className="grid grid-cols-3 gap-2">
          <Input value={value.city} onChange={(e) => set({ city: e.target.value })} placeholder="City" />
          <Input value={value.state} onChange={(e) => set({ state: e.target.value })} placeholder="State" />
          <Input value={value.zip} onChange={(e) => set({ zip: e.target.value })} placeholder="ZIP" />
        </div>
      </div>
    </div>
  );
}

function EquipmentModal({ initial, onClose, onSaved }) {
  const [form, setForm] = useState(() => ({
    ...EMPTY_FORM,
    ...(initial || {}),
    service_address: { ...EMPTY_FORM.service_address, ...(initial?.service_address || {}) },
    install_date: initial?.install_date || "",
  }));
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async () => {
    if (!form.customer_id.trim()) return toast.error("Customer ID is required");
    setSaving(true);
    try {
      const payload = {
        customer_id: form.customer_id.trim(),
        customer_name: form.customer_name.trim(),
        location_label: form.location_label.trim(),
        service_address: {
          street: form.service_address.street.trim(),
          city: form.service_address.city.trim(),
          state: form.service_address.state.trim(),
          zip: form.service_address.zip.trim(),
        },
        equipment_type: form.equipment_type.trim(),
        make: form.make.trim(),
        model: form.model.trim(),
        serial_number: form.serial_number.trim(),
        install_date: form.install_date.trim() || null,
        notes: form.notes.trim(),
        active: form.active,
      };
      if (initial?.id) {
        await api.put(`/tenants/equipment/${initial.id}`, payload);
        toast.success("Equipment updated");
      } else {
        await api.post("/tenants/equipment/", payload);
        toast.success("Equipment added");
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid="equipment-modal">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-4">{initial?.id ? "Edit equipment" : "Add equipment"}</h2>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Customer ID *</label>
              <Input
                value={form.customer_id}
                onChange={(e) => set({ customer_id: e.target.value })}
                placeholder="customer id"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Customer name</label>
              <Input
                value={form.customer_name}
                onChange={(e) => set({ customer_name: e.target.value })}
                placeholder="Jane Doe"
              />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Location label</label>
            <Input
              value={form.location_label}
              onChange={(e) => set({ location_label: e.target.value })}
              placeholder="Attic unit, basement, main floor…"
            />
          </div>
          <AddressFields value={form.service_address} onChange={(v) => set({ service_address: v })} />
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Equipment type</label>
              <Input
                value={form.equipment_type}
                onChange={(e) => set({ equipment_type: e.target.value })}
                list="eq-types"
                placeholder="ac_condenser"
              />
              <datalist id="eq-types">
                {SUGGESTED.map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </div>
            <div>
              <label className="text-sm font-medium">Install date</label>
              <Input
                type="date"
                value={form.install_date}
                onChange={(e) => set({ install_date: e.target.value })}
              />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-sm font-medium">Make</label>
              <Input value={form.make} onChange={(e) => set({ make: e.target.value })} placeholder="Trane" />
            </div>
            <div>
              <label className="text-sm font-medium">Model</label>
              <Input value={form.model} onChange={(e) => set({ model: e.target.value })} placeholder="XR16" />
            </div>
            <div>
              <label className="text-sm font-medium">Serial #</label>
              <Input
                value={form.serial_number}
                onChange={(e) => set({ serial_number: e.target.value })}
                placeholder="SN-001"
              />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Notes</label>
            <Input value={form.notes} onChange={(e) => set({ notes: e.target.value })} placeholder="Access notes, quirks…" />
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

function HistoryModal({ record, onClose }) {
  const [events, setEvents] = useState([]);
  const [desc, setDesc] = useState("");
  const [tech, setTech] = useState("");
  const [jobId, setJobId] = useState("");
  const [date, setDate] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    api
      .get(`/tenants/equipment/${record.id}/history`)
      .then((r) => setEvents(r.data.events || []))
      .catch((e) => toast.error(errMessage(e)));
  }, [record.id]);

  useEffect(() => { load(); }, [load]);

  const addEvent = async () => {
    if (!desc.trim()) return toast.error("Description is required");
    setSaving(true);
    try {
      await api.post(`/tenants/equipment/${record.id}/service-events`, {
        description: desc.trim(),
        tech: tech.trim(),
        job_id: jobId.trim() || null,
        service_date: date.trim() || null,
      });
      setDesc(""); setTech(""); setJobId(""); setDate("");
      load();
      toast.success("Service event logged");
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-1">Service history</h2>
        <p className="text-sm text-muted-foreground mb-4">
          {[record.make, record.model].filter(Boolean).join(" ")} · {record.serial_number || "no serial"} ·{" "}
          {record.address_summary || "no address"}
        </p>

        <div className="space-y-2 mb-6">
          {events.map((e) => (
            <div key={e.id} className="rounded-lg border p-3 text-sm">
              <div className="flex items-start justify-between gap-2">
                <div className="font-medium">{e.description}</div>
                <div className="text-xs text-muted-foreground whitespace-nowrap">
                  {e.service_date ? new Date(e.service_date).toLocaleDateString() : ""}
                </div>
              </div>
              <div className="text-xs text-muted-foreground mt-1 flex flex-wrap gap-2">
                {e.tech && <span>Tech: {e.tech}</span>}
                {e.job_id && <Badge variant="outline" className="text-xs">Job: {e.job_id}</Badge>}
              </div>
            </div>
          ))}
          {events.length === 0 && <div className="text-sm text-muted-foreground">No service events yet.</div>}
        </div>

        <div className="border-t pt-4 space-y-2">
          <div className="text-sm font-medium flex items-center gap-1">
            <CalendarClock className="h-3.5 w-3.5" /> Log a service visit
          </div>
          <Input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="What was done *" />
          <div className="grid grid-cols-3 gap-2">
            <Input value={tech} onChange={(e) => setTech(e.target.value)} placeholder="Tech" />
            <Input value={jobId} onChange={(e) => setJobId(e.target.value)} placeholder="Job ID (optional)" />
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Close</Button>
            <Button onClick={addEvent} disabled={saving}>{saving ? "Saving…" : "Log visit"}</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Equipment() {
  const { user } = useAuth();
  const canManage = user?.role === "owner" || user?.role === "admin" || user?.role === "platform_admin";
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [modal, setModal] = useState(null); // null | {mode:"add"} | {mode:"edit", record}
  const [historyRecord, setHistoryRecord] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get("/tenants/equipment/", {
        params: {
          q: search || undefined,
          customer_id: customerId.trim() || undefined,
          include_inactive: showInactive || undefined,
        },
      });
      setRecords(res.data.equipment || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [search, customerId, showInactive]);

  useEffect(() => {
    const t = setTimeout(load, 250); // debounce search
    return () => clearTimeout(t);
  }, [load]);

  const toggleActive = async (rec) => {
    try {
      if (rec.active) {
        await api.delete(`/tenants/equipment/${rec.id}`);
        toast.success("Equipment deactivated");
      } else {
        await api.put(`/tenants/equipment/${rec.id}`, { active: true });
        toast.success("Equipment reactivated");
      }
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const filtered = useMemo(() => records, [records]);

  return (
    <div>
      <PageHeader
        title="Equipment"
        description="Per-location equipment records with service history. Techs in the driveway see the unit, its install details, and every visit before it."
        testId="equipment-header"
        actions={
          canManage && (
            <Button size="sm" onClick={() => setModal({ mode: "add" })} data-testid="equipment-add">
              <Plus className="h-4 w-4 mr-1" /> Add equipment
            </Button>
          )
        }
      />

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search make, model, serial, address…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
            data-testid="equipment-search"
          />
        </div>
        <Input
          placeholder="Filter by customer ID"
          value={customerId}
          onChange={(e) => setCustomerId(e.target.value)}
          className="max-w-[220px]"
          data-testid="equipment-customer-filter"
        />
        <label className="flex items-center gap-1.5 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
            className="rounded"
          />
          Show inactive
        </label>
      </div>

      {loading ? (
        <div className="text-muted-foreground">Loading equipment…</div>
      ) : filtered.length === 0 ? (
        <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
          <Wrench className="h-6 w-6 mx-auto mb-2" />
          No equipment yet. {canManage && "Add your first unit to start building service history."}
        </div>
      ) : (
        <div className="rounded-xl border overflow-hidden">
          <table className="w-full text-sm" data-testid="equipment-table">
            <thead className="bg-muted/60 text-left">
              <tr>
                <th className="px-4 py-2.5 font-medium">Unit</th>
                <th className="px-4 py-2.5 font-medium">Service address</th>
                <th className="px-4 py-2.5 font-medium">Customer</th>
                <th className="px-4 py-2.5 font-medium text-right">Visits</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((rec) => (
                <tr key={rec.id} className="border-t hover:bg-muted/30" data-testid={`equipment-row-${rec.id}`}>
                  <td className="px-4 py-2.5">
                    <div className="font-medium">
                      {[rec.make, rec.model].filter(Boolean).join(" ") || "Unnamed unit"}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {rec.equipment_type && <span className="mr-2">{rec.equipment_type}</span>}
                      {rec.serial_number ? `S/N ${rec.serial_number}` : ""}
                      {rec.location_label ? ` · ${rec.location_label}` : ""}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground text-xs">
                    {rec.address_summary || "—"}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground text-xs">
                    {rec.customer_name || rec.customer_id || "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold">{rec.service_event_count}</td>
                  <td className="px-4 py-2.5">
                    <Badge className={rec.active ? "bg-emerald-100 text-emerald-900" : "bg-slate-200 text-slate-700"}>
                      {rec.active ? "Active" : "Inactive"}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="sm" title="Service history" onClick={() => setHistoryRecord(rec)}>
                        <History className="h-4 w-4" />
                      </Button>
                      {canManage && (
                        <>
                          <Button variant="ghost" size="sm" title="Edit" onClick={() => setModal({ mode: "edit", record: rec })}>
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            title={rec.active ? "Deactivate" : "Reactivate"}
                            onClick={() => toggleActive(rec)}
                          >
                            {rec.active ? "Deactivate" : "Reactivate"}
                          </Button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {modal && (
        <EquipmentModal
          initial={modal.mode === "edit" ? modal.record : null}
          onClose={() => setModal(null)}
          onSaved={load}
        />
      )}
      {historyRecord && <HistoryModal record={historyRecord} onClose={() => setHistoryRecord(null)} />}
    </div>
  );
}
