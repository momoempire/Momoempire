import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Truck, Upload, RefreshCw, History, Loader2, Plus, Trash2, Check } from "lucide-react";

const BASE = "/supplier-pricing";
const PRICEBOOK_BASE = "/tenants/pricebook";

const ROUNDINGS = ["none", "dollar", "five", "ten", "charm99"];
const FEED_TYPES = ["mock", "csv", "api"];

function Section({ title, description, children, testId, action }) {
  return (
    <section className="surface p-5 space-y-4" data-testid={testId}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold text-lg">{title}</h2>
          {description && <p className="text-sm text-muted-foreground mt-1">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Delta({ value }) {
  const v = Number(value) || 0;
  const cls = v > 0 ? "text-red-600" : v < 0 ? "text-emerald-600" : "text-muted-foreground";
  return <span className={cls}>{v > 0 ? "+" : ""}{fmtUSD(v)}</span>;
}

export default function SupplierPricing() {
  const { t: _t } = useTranslation();
  // Fragment convention: this feature's strings live in
  // locales/fragments/supplier-pricebook.{en,es}.json, merged under the
  // top-level "supplier_pricing" key of the default namespace (see i18n/index.js).
  const t = (k, opts) => _t(`supplier_pricing.${k}`, opts);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [suppliers, setSuppliers] = useState([]);
  const [newName, setNewName] = useState("");
  const [newFeedType, setNewFeedType] = useState("mock");
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [csvText, setCsvText] = useState("");

  const [rules, setRules] = useState({ margins: {}, default_margin: 50, rounding: "dollar" });

  const [preview, setPreview] = useState(null);
  const [confirming, setConfirming] = useState(false);

  const [mappings, setMappings] = useState([]);
  const [mapSku, setMapSku] = useState("");
  const [mapItemId, setMapItemId] = useState("");

  const [items, setItems] = useState([]);
  const [historyItemId, setHistoryItemId] = useState("");
  const [history, setHistory] = useState(null);

  const selected = suppliers.find((s) => s.id === selectedId) || null;

  const loadAll = async () => {
    setLoading(true);
    try {
      const [supRes, rulesRes, mapRes, itemsRes] = await Promise.all([
        api.get(`${BASE}/suppliers`),
        api.get(`${BASE}/margin-rules`),
        api.get(`${BASE}/sku-mappings`),
        api.get(`${PRICEBOOK_BASE}/items`),
      ]);
      const sups = supRes.data.suppliers || [];
      setSuppliers(sups);
      if (!selectedId && sups.length > 0) setSelectedId(sups[0].id);
      setRules(rulesRes.data.rules);
      setMappings(mapRes.data.mappings || []);
      setItems(itemsRes.data.items || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = async (sid) => {
    if (!sid) { setDetail(null); return; }
    try {
      const res = await api.get(`${BASE}/suppliers/${sid}`);
      setDetail(res.data);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  useEffect(() => { loadAll(); }, []);
  useEffect(() => { loadDetail(selectedId); }, [selectedId, suppliers.length]);

  const createSupplier = async () => {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      const res = await api.post(`${BASE}/suppliers`, { name: newName.trim(), feed_type: newFeedType });
      toast.success(t("supplier_created_toast"));
      setNewName("");
      await loadAll();
      setSelectedId(res.data.id);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const deleteSupplier = async (sid) => {
    if (!window.confirm(t("supplier_confirm_delete"))) return;
    setBusy(true);
    try {
      await api.delete(`${BASE}/suppliers/${sid}`);
      toast.success(t("supplier_deleted_toast"));
      setSelectedId("");
      setPreview(null);
      await loadAll();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const ingest = async (payload, isCsvText = false) => {
    setBusy(true);
    try {
      const res = isCsvText
        ? await api.post(`${BASE}/suppliers/${selectedId}/ingest`, csvText, {
            headers: { "Content-Type": "text/csv" },
          })
        : await api.post(`${BASE}/suppliers/${selectedId}/ingest`, payload);
      toast.success(t("ingest_done_toast", { n: res.data.lines_ingested }));
      setCsvText("");
      setPreview(null);
      await loadAll();
      await loadDetail(selectedId);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const saveRules = async () => {
    setBusy(true);
    try {
      const res = await api.put(`${BASE}/margin-rules`, rules);
      setRules(res.data.rules);
      toast.success(t("margin_saved_toast"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const runPreview = async () => {
    setBusy(true);
    try {
      const res = await api.post(`${BASE}/reprice/preview`, { supplier_id: selectedId || null });
      setPreview(res.data);
      setConfirming(false);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const runApply = async () => {
    setBusy(true);
    try {
      const res = await api.post(`${BASE}/reprice/apply`, { supplier_id: selectedId || null });
      toast.success(t("reprice_applied_toast", { n: res.data.count }));
      setPreview(null);
      setConfirming(false);
      await loadAll();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const addMapping = async () => {
    if (!mapSku.trim() || !mapItemId || !selectedId) return;
    setBusy(true);
    try {
      await api.post(`${BASE}/sku-mappings`, {
        supplier_id: selectedId, supplier_sku: mapSku.trim(), item_id: mapItemId,
      });
      toast.success(t("mapping_added_toast"));
      setMapSku("");
      const res = await api.get(`${BASE}/sku-mappings`);
      setMappings(res.data.mappings || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const deleteMapping = async (mid) => {
    try {
      await api.delete(`${BASE}/sku-mappings/${mid}`);
      setMappings((m) => m.filter((x) => x.id !== mid));
      toast.success(t("mapping_deleted_toast"));
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const loadHistory = async (iid) => {
    setHistoryItemId(iid);
    if (!iid) { setHistory(null); return; }
    try {
      const res = await api.get(`${BASE}/price-history`, { params: { item_id: iid } });
      setHistory(res.data);
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const setMargin = (cat, val) =>
    setRules((r) => ({ ...r, margins: { ...r.margins, [cat]: Number(val) || 0 } }));
  const categories = Object.keys(rules.margins || {});
  const changedRows = preview ? preview.rows.filter((r) => r.changed) : [];

  return (
    <div className="space-y-6" data-testid="supplier-pricing-page">
      <PageHeader
        eyebrow={t("eyebrow")}
        title={t("title")}
        description={t("description")}
      />

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("loading")}
        </div>
      ) : (
        <>
          <Section title={t("margin_rules_heading")} description={t("margin_rules_description")} testId="sp-margin-rules"
            action={<Button size="sm" onClick={saveRules} disabled={busy}><Check className="h-4 w-4 mr-1" />{t("margin_save")}</Button>}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {categories.map((cat) => (
                <label key={cat} className="space-y-1 block">
                  <span className="text-xs text-muted-foreground capitalize">{cat} %</span>
                  <Input type="number" min="0" max="99" value={rules.margins[cat]}
                    onChange={(e) => setMargin(cat, e.target.value)} />
                </label>
              ))}
              <label className="space-y-1 block">
                <span className="text-xs text-muted-foreground">{t("margin_default_label")}</span>
                <Input type="number" min="0" max="99" value={rules.default_margin}
                  onChange={(e) => setRules((r) => ({ ...r, default_margin: Number(e.target.value) || 0 }))} />
              </label>
              <label className="space-y-1 block">
                <span className="text-xs text-muted-foreground">{t("margin_rounding_label")}</span>
                <select className="w-full border rounded-md px-3 py-2 text-sm bg-background"
                  value={rules.rounding}
                  onChange={(e) => setRules((r) => ({ ...r, rounding: e.target.value }))}>
                  {ROUNDINGS.map((ro) => (
                    <option key={ro} value={ro}>{t(`rounding_${ro}`)}</option>
                  ))}
                </select>
              </label>
            </div>
          </Section>

          <Section title={t("suppliers_heading")} testId="sp-suppliers"
            action={
              <div className="flex items-center gap-2">
                <Input value={newName} onChange={(e) => setNewName(e.target.value)}
                  placeholder={t("supplier_name_placeholder")} className="w-56" />
                <select className="border rounded-md px-3 py-2 text-sm bg-background"
                  value={newFeedType} onChange={(e) => setNewFeedType(e.target.value)}>
                  {FEED_TYPES.map((ft) => (
                    <option key={ft} value={ft}>{t(`feed_type_${ft}`)}</option>
                  ))}
                </select>
                <Button size="sm" onClick={createSupplier} disabled={busy || !newName.trim()}>
                  <Plus className="h-4 w-4 mr-1" />{t("supplier_create")}
                </Button>
              </div>
            }>
            {suppliers.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("no_suppliers")}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-muted-foreground border-b">
                      <th className="py-2 pr-4">{t("col_item")}</th>
                      <th className="py-2 pr-4">{t("feed_type_label")}</th>
                      <th className="py-2 pr-4">{t("col_lines")}</th>
                      <th className="py-2 pr-4">{t("col_last_ingest")}</th>
                      <th className="py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {suppliers.map((s) => (
                      <tr key={s.id} className={`border-b last:border-0 ${s.id === selectedId ? "bg-muted/40" : ""}`}>
                        <td className="py-2 pr-4 font-medium">
                          <span className="inline-flex items-center gap-2">
                            <Truck className="h-4 w-4 text-muted-foreground" />{s.name}
                          </span>
                        </td>
                        <td className="py-2 pr-4"><Badge variant="secondary">{t(`feed_type_${s.feed_type}`)}</Badge></td>
                        <td className="py-2 pr-4">{s.line_count ?? 0}</td>
                        <td className="py-2 pr-4 text-muted-foreground">
                          {s.last_ingest_at ? s.last_ingest_at.slice(0, 16).replace("T", " ") : t("never")}
                        </td>
                        <td className="py-2 text-right space-x-2">
                          <Button variant="ghost" size="sm" onClick={() => setSelectedId(s.id)}>{t("select")}</Button>
                          <Button variant="ghost" size="sm" onClick={() => deleteSupplier(s.id)}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          {selected && (
            <Section title={`${t("ingest_heading")}: ${selected.name}`}
              description={selected.feed_type === "mock" ? t("ingest_mock_description") : selected.feed_type === "api" ? t("ingest_api_notice") : null}
              testId="sp-ingest">
              {selected.feed_type === "mock" && (
                <Button onClick={() => ingest({ mock: true })} disabled={busy}>
                  <RefreshCw className="h-4 w-4 mr-1" />{t("ingest_mock_button")}
                </Button>
              )}
              {selected.feed_type === "csv" && (
                <div className="space-y-2">
                  <textarea className="w-full border rounded-md p-3 text-sm font-mono bg-background" rows={5}
                    value={csvText} onChange={(e) => setCsvText(e.target.value)}
                    placeholder={t("ingest_csv_placeholder")} />
                  <Button onClick={() => ingest(null, true)} disabled={busy || !csvText.trim()}>
                    <Upload className="h-4 w-4 mr-1" />{t("ingest_csv_button")}
                  </Button>
                </div>
              )}
              {detail && detail.lines?.length > 0 ? (
                <div className="overflow-x-auto max-h-64 overflow-y-auto border rounded-md">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-background">
                      <tr className="text-left text-muted-foreground border-b">
                        <th className="py-2 px-3">{t("col_sku")}</th>
                        <th className="py-2 px-3 text-right">{t("col_cost")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.lines.map((l) => (
                        <tr key={l.id} className="border-b last:border-0">
                          <td className="py-1.5 px-3 font-mono">{l.sku}</td>
                          <td className="py-1.5 px-3 text-right">{fmtUSD(l.cost)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{t("ingest_empty")}</p>
              )}
            </Section>
          )}

          <Section title={t("reprice_heading")} description={t("reprice_description")} testId="sp-reprice"
            action={
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={runPreview} disabled={busy}>
                  {busy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}{t("reprice_preview_button")}
                </Button>
                {!confirming ? (
                  <Button size="sm" onClick={() => changedRows.length > 0 && setConfirming(true)}
                    disabled={busy || changedRows.length === 0}>
                    {t("reprice_apply_button")}
                  </Button>
                ) : (
                  <>
                    <span className="text-sm text-muted-foreground">{t("reprice_confirm", { n: changedRows.length })}</span>
                    <Button size="sm" onClick={runApply} disabled={busy}>{t("reprice_apply_button")}</Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>{t("cancel")}</Button>
                  </>
                )}
              </div>
            }>
            {!preview ? (
              <p className="text-sm text-muted-foreground">{t("reprice_empty")}</p>
            ) : preview.rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("reprice_no_changes")}</p>
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  {t("summary_line", {
                    changed: preview.summary.changed,
                    total: preview.summary.items_evaluated,
                    unmapped: preview.summary.unmapped_lines,
                    delta: fmtUSD(preview.summary.total_price_delta),
                  })}
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-muted-foreground border-b">
                        <th className="py-2 pr-3">{t("col_item")}</th>
                        <th className="py-2 pr-3">{t("col_sku")}</th>
                        <th className="py-2 pr-3 text-right">{t("col_old_cost")}</th>
                        <th className="py-2 pr-3 text-right">{t("col_new_cost")}</th>
                        <th className="py-2 pr-3 text-right">{t("col_old_price")}</th>
                        <th className="py-2 pr-3 text-right">{t("col_new_price")}</th>
                        <th className="py-2 pr-3 text-right">{t("col_change")}</th>
                        <th className="py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((r) => (
                        <tr key={r.item_id} className="border-b last:border-0">
                          <td className="py-2 pr-3 font-medium">{r.item_name}
                            <span className="text-xs text-muted-foreground ml-2">{r.margin_pct}% · {t(`rounding_${r.rounding}`)}</span>
                          </td>
                          <td className="py-2 pr-3 font-mono text-xs">{r.supplier_sku}</td>
                          <td className="py-2 pr-3 text-right text-muted-foreground">{fmtUSD(r.old_cost)}</td>
                          <td className="py-2 pr-3 text-right">{fmtUSD(r.new_cost)}</td>
                          <td className="py-2 pr-3 text-right text-muted-foreground">{fmtUSD(r.old_price)}</td>
                          <td className="py-2 pr-3 text-right font-medium">{fmtUSD(r.new_price)}</td>
                          <td className="py-2 pr-3 text-right"><Delta value={r.price_delta} /></td>
                          <td className="py-2 text-right">
                            {r.changed
                              ? <Badge data-testid={`sp-changed-${r.item_id}`}>{t("badge_changed")}</Badge>
                              : <Badge variant="secondary">{t("badge_unchanged")}</Badge>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Section>

          <Section title={t("mappings_heading")} description={t("mappings_description")} testId="sp-mappings"
            action={selected && (
              <div className="flex items-center gap-2">
                <Input value={mapSku} onChange={(e) => setMapSku(e.target.value)}
                  placeholder={t("mapping_supplier_sku_placeholder")} className="w-40" />
                <select className="border rounded-md px-3 py-2 text-sm bg-background max-w-56"
                  value={mapItemId} onChange={(e) => setMapItemId(e.target.value)}>
                  <option value="">{t("mapping_item_label")}</option>
                  {items.map((it) => (
                    <option key={it.id} value={it.id}>{it.name}{it.sku ? ` (${it.sku})` : ""}</option>
                  ))}
                </select>
                <Button size="sm" onClick={addMapping} disabled={busy || !mapSku.trim() || !mapItemId}>
                  <Plus className="h-4 w-4 mr-1" />{t("mapping_add")}
                </Button>
              </div>
            )}>
            {mappings.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("no_mappings")}</p>
            ) : (
              <ul className="text-sm space-y-2">
                {mappings.map((m) => (
                  <li key={m.id} className="flex items-center justify-between gap-3 border rounded-md px-3 py-2">
                    <span>
                      <span className="font-mono">{m.supplier_sku}</span>
                      <span className="text-muted-foreground"> → </span>
                      {(items.find((it) => it.id === m.item_id)?.name) || m.item_id}
                    </span>
                    <Button variant="ghost" size="sm" onClick={() => deleteMapping(m.id)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title={t("history_heading")} description={t("history_description")} testId="sp-history"
            action={
              <select className="border rounded-md px-3 py-2 text-sm bg-background max-w-64"
                value={historyItemId} onChange={(e) => loadHistory(e.target.value)}>
                <option value="">{t("history_item_label")}</option>
                {items.map((it) => (
                  <option key={it.id} value={it.id}>{it.name}</option>
                ))}
              </select>
            }>
            {!history || history.changes.length === 0 ? (
              <p className="text-sm text-muted-foreground flex items-center gap-2">
                <History className="h-4 w-4" />{t("history_empty")}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-muted-foreground border-b">
                      <th className="py-2 pr-3">{t("col_version")}</th>
                      <th className="py-2 pr-3">{t("col_date")}</th>
                      <th className="py-2 pr-3">{t("col_supplier")}</th>
                      <th className="py-2 pr-3 text-right">{t("col_old_cost")}</th>
                      <th className="py-2 pr-3 text-right">{t("col_old_price")}</th>
                      <th className="py-2 pr-3 text-right">{t("col_new_price")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.changes.map((c) => (
                      <tr key={c.id} className="border-b last:border-0">
                        <td className="py-2 pr-3">v{c.version}</td>
                        <td className="py-2 pr-3 text-muted-foreground">
                          {c.changed_at ? c.changed_at.slice(0, 16).replace("T", " ") : ""}
                        </td>
                        <td className="py-2 pr-3 font-mono text-xs">{c.supplier_sku || "—"}</td>
                        <td className="py-2 pr-3 text-right text-muted-foreground">{fmtUSD(c.old_cost)}</td>
                        <td className="py-2 pr-3 text-right text-muted-foreground">{fmtUSD(c.old_price)}</td>
                        <td className="py-2 pr-3 text-right font-medium">{fmtUSD(c.snapshot?.price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
