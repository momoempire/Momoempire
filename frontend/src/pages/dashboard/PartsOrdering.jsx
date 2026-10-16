import { useCallback, useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Package, PackagePlus, Send, CheckCheck, Plus, Search, Wrench, Truck } from "lucide-react";

const FULFILL_BADGE = {
  covered: "bg-emerald-500/15 text-emerald-300 border-emerald-400/30",
  partial: "bg-amber-500/15 text-amber-300 border-amber-400/30",
  needs_order: "bg-rose-500/15 text-rose-300 border-rose-400/30",
};

const STATUS_BADGE = {
  draft: "bg-slate-500/15 text-slate-300 border-slate-400/30",
  sent: "bg-sky-500/15 text-sky-300 border-sky-400/30",
  received: "bg-emerald-500/15 text-emerald-300 border-emerald-400/30",
};

const EMPTY_PART = { name: "", sku: "", qty: "1", supplier_ref: "" };

function JobPartsTab() {
  const [jobId, setJobId] = useState("");
  const [parts, setParts] = useState([]);
  const [summary, setSummary] = useState(null);
  const [form, setForm] = useState(EMPTY_PART);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id) => {
    const target = (id ?? jobId).trim();
    if (!target) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/parts-orders/tenants/jobs/${target}/parts`);
      setParts(data.parts || []);
      setSummary(data.summary || null);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  const addPart = async () => {
    const target = jobId.trim();
    if (!target || !form.name.trim()) {
      toast.error("Job ID and part name are required");
      return;
    }
    try {
      await api.post(`/parts-orders/tenants/jobs/${target}/parts`, {
        parts: [{
          name: form.name.trim(),
          sku: form.sku.trim() || null,
          qty: Math.max(1, parseInt(form.qty, 10) || 1),
          supplier_ref: form.supplier_ref.trim() || null,
        }],
      });
      setForm(EMPTY_PART);
      toast.success("Part added — truck stock checked");
      await load(target);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const createOrderFromJob = async () => {
    try {
      const { data } = await api.post("/parts-orders/tenants/orders", {
        supplier_name: "Default supplier",
        job_ids: [jobId.trim()],
      });
      toast.success(`Draft order created (${data.lines.length} lines)`);
      window.dispatchEvent(new CustomEvent("parts-orders:created", { detail: data }));
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="glass-crystal rounded-2xl p-5">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[220px]">
            <label className="text-sm font-medium">Job ID</label>
            <Input
              placeholder="e.g. job-12345"
              value={jobId}
              onChange={(e) => setJobId(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && load()}
            />
          </div>
          <Button onClick={() => load()} disabled={loading}>
            <Search className="h-4 w-4 mr-1" /> {loading ? "Loading…" : "Load parts"}
          </Button>
          {summary && summary.lines_to_order > 0 && (
            <Button variant="outline" onClick={createOrderFromJob}>
              <PackagePlus className="h-4 w-4 mr-1" /> Order unfulfilled ({summary.units_to_order})
            </Button>
          )}
        </div>
        {summary && (
          <div className="mt-3 flex flex-wrap gap-2 text-sm text-muted-foreground">
            <span>{summary.total_parts} parts</span>
            <span>·</span>
            <span className="text-emerald-300">{summary.covered_parts} covered by truck</span>
            <span>·</span>
            <span className="text-amber-300">{summary.lines_to_order} lines to order</span>
          </div>
        )}
      </div>

      {jobId.trim() && (
        <div className="glass-crystal rounded-2xl p-5">
          <h3 className="font-semibold mb-3 flex items-center gap-2">
            <Wrench className="h-4 w-4" /> Add part needed
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Input placeholder="Part name *" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <Input placeholder="SKU" value={form.sku}
              onChange={(e) => setForm({ ...form, sku: e.target.value })} />
            <Input type="number" min="1" placeholder="Qty" value={form.qty}
              onChange={(e) => setForm({ ...form, qty: e.target.value })} />
            <Input placeholder="Supplier ref" value={form.supplier_ref}
              onChange={(e) => setForm({ ...form, supplier_ref: e.target.value })} />
          </div>
          <div className="mt-3">
            <Button size="sm" onClick={addPart}><Plus className="h-3 w-3 mr-1" /> Add part</Button>
          </div>
        </div>
      )}

      {parts.length > 0 && (
        <div className="glass-crystal rounded-2xl p-5">
          <h3 className="font-semibold mb-3 flex items-center gap-2">
            <Truck className="h-4 w-4" /> Job parts — covered vs to order
          </h3>
          <div className="space-y-2">
            {parts.map((p) => (
              <div key={p.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-white/10 p-3">
                <div className="flex-1 min-w-[180px]">
                  <div className="font-medium">{p.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {p.sku || "no SKU"} · qty {p.qty}
                    {p.supplier_ref ? ` · ref ${p.supplier_ref}` : ""}
                  </div>
                </div>
                <Badge className={FULFILL_BADGE[p.fulfillment_status] || STATUS_BADGE.draft}>
                  {p.fulfillment_status === "covered" && `Covered (${p.truck_available} on truck)`}
                  {p.fulfillment_status === "partial" && `Partial — order ${p.to_order_qty} (${p.truck_available} on truck)`}
                  {p.fulfillment_status === "needs_order" && `Order ${p.to_order_qty}`}
                </Badge>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function OrderCard({ order, onAction }) {
  return (
    <div className="glass-crystal rounded-2xl p-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 min-w-[180px]">
          <div className="font-semibold">{order.supplier_name || "Unnamed supplier"}</div>
          <div className="text-xs text-muted-foreground">
            {order.lines?.length || 0} lines · est. {fmtUSD(order.estimated_total || 0)} ·
            created {order.created_at?.slice(0, 10)}
          </div>
        </div>
        <Badge className={STATUS_BADGE[order.status] || STATUS_BADGE.draft}>{order.status}</Badge>
        {order.status === "draft" && (
          <Button size="sm" onClick={() => onAction(order.id, "send")}>
            <Send className="h-3 w-3 mr-1" /> Send to supplier
          </Button>
        )}
        {order.status === "sent" && (
          <Button size="sm" variant="outline" onClick={() => onAction(order.id, "receive")}>
            <CheckCheck className="h-3 w-3 mr-1" /> Mark received
          </Button>
        )}
      </div>
      <div className="mt-3 space-y-1">
        {(order.lines || []).map((l, i) => (
          <div key={i} className="flex justify-between text-sm rounded-lg bg-white/5 px-3 py-1.5">
            <span>{l.name} {l.sku && <span className="text-muted-foreground">· {l.sku}</span>}</span>
            <span className="text-muted-foreground">×{l.qty}{l.unit_cost != null && ` @ ${fmtUSD(l.unit_cost)}`}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function OrdersTab() {
  const [status, setStatus] = useState("");
  const [orders, setOrders] = useState([]);
  const [showNew, setShowNew] = useState(false);
  const [newJobIds, setNewJobIds] = useState("");
  const [newSupplier, setNewSupplier] = useState("");

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/parts-orders/tenants/orders", {
        params: status ? { status } : {},
      });
      setOrders(data.orders || []);
    } catch (e) {
      toast.error(errMessage(e));
    }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const h = () => { setShowNew(false); load(); };
    window.addEventListener("parts-orders:created", h);
    return () => window.removeEventListener("parts-orders:created", h);
  }, [load]);

  const orderAction = async (id, action) => {
    try {
      await api.post(`/parts-orders/tenants/orders/${id}/${action}`);
      toast.success(action === "send" ? "Order sent to supplier (stub)" : "Order marked received");
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const createDraft = async () => {
    const jobs = newJobIds.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    if (!jobs.length) {
      toast.error("Enter at least one job ID");
      return;
    }
    try {
      const { data } = await api.post("/parts-orders/tenants/orders", {
        supplier_name: newSupplier.trim() || "Default supplier",
        job_ids: jobs,
      });
      toast.success(`Draft created with ${data.lines.length} lines`);
      setShowNew(false);
      setNewJobIds("");
      setNewSupplier("");
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2">
          {["", "draft", "sent", "received"].map((s) => (
            <Button key={s} size="sm" variant={status === s ? "default" : "outline"}
              onClick={() => setStatus(s)}>
              {s === "" ? "All" : s}
            </Button>
          ))}
        </div>
        <Button size="sm" onClick={() => setShowNew((v) => !v)} className="ml-auto">
          <Plus className="h-3 w-3 mr-1" /> New order
        </Button>
      </div>

      {showNew && (
        <div className="glass-crystal rounded-2xl p-5">
          <h3 className="font-semibold mb-3">New purchase order from job parts</h3>
          <div className="grid md:grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Job IDs (comma or space separated)</label>
              <Input placeholder="job-123, job-456" value={newJobIds}
                onChange={(e) => setNewJobIds(e.target.value)} />
            </div>
            <div>
              <label className="text-sm font-medium">Supplier name</label>
              <Input placeholder="ACME Parts" value={newSupplier}
                onChange={(e) => setNewSupplier(e.target.value)} />
            </div>
          </div>
          <div className="mt-3">
            <Button size="sm" onClick={createDraft}>
              <Package className="h-3 w-3 mr-1" /> Create draft from unfulfilled parts
            </Button>
          </div>
        </div>
      )}

      {orders.length === 0 ? (
        <div className="glass-crystal rounded-2xl p-8 text-center text-muted-foreground">
          No purchase orders{status ? ` with status "${status}"` : ""} yet.
        </div>
      ) : (
        orders.map((o) => <OrderCard key={o.id} order={o} onAction={orderAction} />)
      )}
    </div>
  );
}

export default function PartsOrdering() {
  const [tab, setTab] = useState("orders");
  return (
    <div className="space-y-6" data-testid="parts-ordering-page">
      <PageHeader
        eyebrow="Inventory"
        title="Parts Ordering"
        description="Order parts straight from a job — truck stock is checked first, and the rest goes on a supplier purchase order."
        actions={
          <div className="flex gap-2">
            <Button variant={tab === "orders" ? "default" : "outline"} size="sm"
              onClick={() => setTab("orders")}><Package className="h-3 w-3 mr-1" /> Orders</Button>
            <Button variant={tab === "jobs" ? "default" : "outline"} size="sm"
              onClick={() => setTab("jobs")}><Wrench className="h-3 w-3 mr-1" /> Job parts</Button>
          </div>
        }
      />
      {tab === "orders" ? <OrdersTab /> : <JobPartsTab />}
    </div>
  );
}
