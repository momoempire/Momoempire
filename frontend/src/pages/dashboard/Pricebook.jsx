import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Plus, Pencil, History, Search, Tag } from "lucide-react";

const EMPTY_FORM = {
  name: "",
  category: "service",
  price: "",
  cost: "",
  sku: "",
  description: "",
  options: [],
  active: true,
};

function OptionEditor({ options, onChange }) {
  const set = (idx, patch) =>
    onChange(options.map((o, i) => (i === idx ? { ...o, ...patch } : o)));
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">Add-ons / options</div>
      {options.map((o, i) => (
        <div key={i} className="flex gap-2">
          <Input
            placeholder="Option name (e.g. After-hours)"
            value={o.name}
            onChange={(e) => set(i, { name: e.target.value })}
            className="flex-1"
          />
          <Input
            type="number"
            min="0"
            step="0.01"
            placeholder="+ $"
            value={o.price}
            onChange={(e) => set(i, { price: e.target.value })}
            className="w-28"
          />
          <Button variant="ghost" size="sm" onClick={() => onChange(options.filter((_, j) => j !== i))}>
            ✕
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...options, { name: "", price: "" }])}>
        <Plus className="h-3 w-3 mr-1" /> Add option
      </Button>
    </div>
  );
}

function ItemModal({ initial, categories, onClose, onSaved }) {
  const [form, setForm] = useState(() => ({
    ...EMPTY_FORM,
    ...(initial || {}),
    price: initial?.price ?? "",
    cost: initial?.cost ?? "",
    options: (initial?.options || []).map((o) => ({ name: o.name, price: String(o.price ?? "") })),
  }));
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async () => {
    if (!form.name.trim()) return toast.error("Name is required");
    const price = Number(form.price);
    if (!(price >= 0)) return toast.error("Price must be a number ≥ 0");
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        category: form.category.trim() || "service",
        price,
        cost: Number(form.cost) || 0,
        sku: form.sku.trim() || null,
        description: form.description.trim(),
        options: form.options
          .filter((o) => o.name.trim())
          .map((o) => ({ name: o.name.trim(), price: Number(o.price) || 0 })),
        active: form.active,
      };
      if (initial?.id) {
        await api.put(`/tenants/pricebook/items/${initial.id}`, payload);
        toast.success("Item updated");
      } else {
        await api.post("/tenants/pricebook/items", payload);
        toast.success("Item added");
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid="pricebook-modal">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-4">{initial?.id ? "Edit item" : "Add pricebook item"}</h2>
        <div className="space-y-3">
          <div>
            <label className="text-sm font-medium">Name *</label>
            <Input value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="Capacitor replacement" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Category</label>
              <Input
                value={form.category}
                onChange={(e) => set({ category: e.target.value })}
                list="pb-categories"
                placeholder="service"
              />
              <datalist id="pb-categories">
                {categories.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
            <div>
              <label className="text-sm font-medium">SKU / part #</label>
              <Input value={form.sku} onChange={(e) => set({ sku: e.target.value })} placeholder="CAP-45-5" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium">Flat price *</label>
              <Input type="number" min="0" step="0.01" value={form.price} onChange={(e) => set({ price: e.target.value })} placeholder="189.00" />
            </div>
            <div>
              <label className="text-sm font-medium">Cost (for margin)</label>
              <Input type="number" min="0" step="0.01" value={form.cost} onChange={(e) => set({ cost: e.target.value })} placeholder="42.00" />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Description</label>
            <Input value={form.description} onChange={(e) => set({ description: e.target.value })} placeholder="Includes part + labor" />
          </div>
          <OptionEditor options={form.options} onChange={(options) => set({ options })} />
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
        </div>
      </div>
    </div>
  );
}

function HistoryModal({ item, onClose }) {
  const [versions, setVersions] = useState([]);
  useEffect(() => {
    api
      .get(`/tenants/pricebook/items/${item.id}/history`)
      .then((r) => setVersions(r.data.versions || []))
      .catch((e) => toast.error(errMessage(e)));
  }, [item.id]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-1">Price history</h2>
        <p className="text-sm text-muted-foreground mb-4">{item.name}</p>
        <div className="space-y-2">
          {versions.map((v) => (
            <div key={v.id || v.version} className="flex items-center justify-between rounded-lg border p-3 text-sm">
              <div>
                <span className="font-medium">v{v.version}</span>
                <span className="text-muted-foreground ml-2">
                  {v.changed_at ? new Date(v.changed_at).toLocaleDateString() : ""}
                </span>
              </div>
              <div className="font-semibold">{fmtUSD(v.snapshot?.price)}</div>
            </div>
          ))}
          {versions.length === 0 && <div className="text-sm text-muted-foreground">No history yet.</div>}
        </div>
        <div className="flex justify-end mt-6">
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}

export default function Pricebook() {
  const { user } = useAuth();
  const canManage = user?.role === "owner" || user?.role === "admin" || user?.role === "platform_admin";
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [modal, setModal] = useState(null); // null | {mode:"add"} | {mode:"edit", item}
  const [historyItem, setHistoryItem] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [itemsRes, catRes] = await Promise.all([
        api.get("/tenants/pricebook/items", {
          params: { q: search, category: category || undefined, active_only: !showInactive },
        }),
        api.get("/tenants/pricebook/categories"),
      ]);
      setItems(itemsRes.data.items || []);
      setCategories(catRes.data.categories || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [search, category, showInactive]);

  useEffect(() => {
    const t = setTimeout(load, 250); // debounce search
    return () => clearTimeout(t);
  }, [load]);

  const toggleActive = async (item) => {
    try {
      if (item.active) {
        await api.delete(`/tenants/pricebook/items/${item.id}`);
        toast.success("Item deactivated");
      } else {
        await api.put(`/tenants/pricebook/items/${item.id}`, { active: true });
        toast.success("Item reactivated");
      }
      load();
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const filtered = useMemo(() => items, [items]);

  return (
    <div>
      <PageHeader
        title="Pricebook"
        description="Flat-rate catalog: services, parts, labor, and add-ons. Price changes are versioned — quotes already sent keep the price they went out with."
        testId="pricebook-header"
        actions={
          canManage && (
            <Button size="sm" onClick={() => setModal({ mode: "add" })} data-testid="pricebook-add">
              <Plus className="h-4 w-4 mr-1" /> Add item
            </Button>
          )
        }
      />

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search name, SKU, description…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
            data-testid="pricebook-search"
          />
        </div>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-md border bg-card px-3 py-2 text-sm"
          data-testid="pricebook-category"
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
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
        <div className="text-muted-foreground">Loading pricebook…</div>
      ) : filtered.length === 0 ? (
        <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
          <Tag className="h-6 w-6 mx-auto mb-2" />
          No items yet. {canManage && "Add your first flat-rate item to get started."}
        </div>
      ) : (
        <div className="rounded-xl border overflow-hidden">
          <table className="w-full text-sm" data-testid="pricebook-table">
            <thead className="bg-muted/60 text-left">
              <tr>
                <th className="px-4 py-2.5 font-medium">Item</th>
                <th className="px-4 py-2.5 font-medium">Category</th>
                <th className="px-4 py-2.5 font-medium text-right">Price</th>
                <th className="px-4 py-2.5 font-medium text-right">Cost</th>
                <th className="px-4 py-2.5 font-medium text-right">Margin</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((it) => (
                <tr key={it.id} className="border-t hover:bg-muted/30" data-testid={`pricebook-row-${it.id}`}>
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{it.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {it.sku ? `${it.sku} · ` : ""}v{it.version}
                      {it.options?.length ? ` · ${it.options.length} option${it.options.length > 1 ? "s" : ""}` : ""}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge variant="outline">{it.category}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold">{fmtUSD(it.price)}</td>
                  <td className="px-4 py-2.5 text-right text-muted-foreground">{fmtUSD(it.cost)}</td>
                  <td className="px-4 py-2.5 text-right text-emerald-700">{fmtUSD(it.margin)}</td>
                  <td className="px-4 py-2.5">
                    <Badge className={it.active ? "bg-emerald-100 text-emerald-900" : "bg-slate-200 text-slate-700"}>
                      {it.active ? "Active" : "Inactive"}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="sm" title="Price history" onClick={() => setHistoryItem(it)}>
                        <History className="h-4 w-4" />
                      </Button>
                      {canManage && (
                        <>
                          <Button variant="ghost" size="sm" title="Edit" onClick={() => setModal({ mode: "edit", item: it })}>
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            title={it.active ? "Deactivate" : "Reactivate"}
                            onClick={() => toggleActive(it)}
                          >
                            {it.active ? "Deactivate" : "Reactivate"}
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
        <ItemModal
          initial={modal.mode === "edit" ? modal.item : null}
          categories={categories}
          onClose={() => setModal(null)}
          onSaved={load}
        />
      )}
      {historyItem && <HistoryModal item={historyItem} onClose={() => setHistoryItem(null)} />}
    </div>
  );
}
