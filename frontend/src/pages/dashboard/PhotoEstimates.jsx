import { useState } from "react";
import { api, errMessage } from "@/lib/api";
import { fmtUSD } from "@/lib/money";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Camera, Loader2, Trash2, Plus, Sparkles, CheckCircle2 } from "lucide-react";

const BASE = "/tenants/photo-estimates";

function ConfidenceBadge({ value }) {
  const pct = Math.round((value || 0) * 100);
  const tone = pct >= 80 ? "default" : pct >= 50 ? "secondary" : "outline";
  return <Badge variant={tone}>{pct}%</Badge>;
}

function SuggestionRow({ row, onChange, onRemove, t }) {
  const set = (patch) => onChange({ ...row, ...patch });
  return (
    <div className="border rounded-lg p-3 space-y-2">
      <div className="flex items-start gap-2">
        <Input
          value={row.description}
          onChange={(e) => set({ description: e.target.value })}
          placeholder={t("photo_estimates.line_desc_placeholder")}
          className="flex-1"
        />
        <ConfidenceBadge value={row.confidence} />
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t("photo_estimates.remove_line")}>
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <label className="flex items-center gap-1">
          {t("photo_estimates.qty")}
          <Input
            type="number"
            min="0.1"
            step="0.1"
            value={row.qty}
            onChange={(e) => set({ qty: e.target.value })}
            className="w-20"
          />
        </label>
        {row.pricebook_item_id ? (
          <span className="text-muted-foreground">
            <Sparkles className="inline h-3 w-3 mr-1" />
            {row.pricebook_match_name} — {t("photo_estimates.price_from_book")}
          </span>
        ) : (
          <label className="flex items-center gap-1">
            {t("photo_estimates.price")}
            <Input
              type="number"
              min="0"
              step="0.01"
              value={row.unit_price}
              onChange={(e) => set({ unit_price: e.target.value })}
              className="w-28"
            />
          </label>
        )}
      </div>
    </div>
  );
}

export default function PhotoEstimates() {
  const { t } = useTranslation();
  const [file, setFile] = useState(null);
  const [trade, setTrade] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [estimateId, setEstimateId] = useState(null);
  const [provider, setProvider] = useState(null);
  const [rows, setRows] = useState([]);
  const [customerName, setCustomerName] = useState("");
  const [title, setTitle] = useState("");
  const [quoteId, setQuoteId] = useState(null);

  const uploadAndAnalyze = async () => {
    if (!file) {
      toast.error(t("photo_estimates.pick_file_first"));
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("trade", trade);
      form.append("notes", notes);
      const { data: up } = await api.post(`${BASE}/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setEstimateId(up.id);
      const { data: an } = await api.post(`${BASE}/${up.id}/analyze`);
      setProvider(an.provider);
      setRows(
        (an.suggestions || []).map((s) => ({
          description: s.description,
          confidence: s.confidence,
          qty: s.qty || 1,
          unit_price: "",
          pricebook_item_id: s.suggested_pricebook_item_id,
          pricebook_match_name: s.pricebook_match_name,
        }))
      );
      toast.success(t("photo_estimates.analyzed"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!customerName.trim()) {
      toast.error(t("photo_estimates.customer_required"));
      return;
    }
    const lines = rows
      .filter((r) => r.description.trim())
      .map((r) => ({
        description: r.description.trim(),
        qty: Math.max(0.1, Number(r.qty) || 1),
        unit_price: r.pricebook_item_id ? 0 : Math.max(0, Number(r.unit_price) || 0),
        pricebook_item_id: r.pricebook_item_id || null,
      }));
    if (!lines.length) {
      toast.error(t("photo_estimates.one_line_required"));
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post(`${BASE}/${estimateId}/approve`, {
        customer_name: customerName.trim(),
        title: title.trim() || t("photo_estimates.default_title"),
        lines,
        notes,
      });
      setQuoteId(data.quote_id);
      toast.success(t("photo_estimates.draft_created"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setFile(null);
    setTrade("");
    setNotes("");
    setEstimateId(null);
    setProvider(null);
    setRows([]);
    setCustomerName("");
    setTitle("");
    setQuoteId(null);
  };

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <PageHeader
        title={t("photo_estimates.title")}
        description={t("photo_estimates.subtitle")}
      />

      {!estimateId && (
        <div className="border rounded-lg p-4 space-y-3">
          <h3 className="font-medium">{t("photo_estimates.step_upload")}</h3>
          <div className="flex flex-col sm:flex-row gap-3">
            <Input
              type="file"
              accept="image/*"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              className="flex-1"
            />
            <Input
              value={trade}
              onChange={(e) => setTrade(e.target.value)}
              placeholder={t("photo_estimates.trade_placeholder")}
              className="sm:w-48"
            />
          </div>
          <Input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={t("photo_estimates.notes_placeholder")}
          />
          <Button onClick={uploadAndAnalyze} disabled={busy || !file}>
            {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Camera className="h-4 w-4 mr-2" />}
            {t("photo_estimates.upload_analyze")}
          </Button>
        </div>
      )}

      {estimateId && !quoteId && (
        <>
          <div className="border rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-medium">{t("photo_estimates.step_review")}</h3>
              <Badge variant="outline">
                {t("photo_estimates.via_provider", { provider: provider || "…" })}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">{t("photo_estimates.review_hint")}</p>
            <div className="space-y-3">
              {rows.map((row, i) => (
                <SuggestionRow
                  key={i}
                  row={row}
                  t={t}
                  onChange={(next) => setRows(rows.map((r, j) => (j === i ? next : r)))}
                  onRemove={() => setRows(rows.filter((_, j) => j !== i))}
                />
              ))}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setRows([...rows, { description: "", confidence: 1, qty: 1, unit_price: "", pricebook_item_id: null }])
              }
            >
              <Plus className="h-3 w-3 mr-1" /> {t("photo_estimates.add_line")}
            </Button>
          </div>

          <div className="border rounded-lg p-4 space-y-3">
            <h3 className="font-medium">{t("photo_estimates.step_approve")}</h3>
            <div className="grid sm:grid-cols-2 gap-3">
              <Input
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
                placeholder={t("photo_estimates.customer_placeholder")}
              />
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t("photo_estimates.title_placeholder")}
              />
            </div>
            <div className="flex gap-2">
              <Button onClick={approve} disabled={busy || !rows.length}>
                {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <CheckCircle2 className="h-4 w-4 mr-2" />}
                {t("photo_estimates.create_draft")}
              </Button>
              <Button variant="ghost" onClick={reset}>{t("photo_estimates.start_over")}</Button>
            </div>
          </div>
        </>
      )}

      {quoteId && (
        <div className="border rounded-lg p-6 text-center space-y-3">
          <CheckCircle2 className="h-10 w-10 mx-auto text-green-600" />
          <h3 className="font-medium text-lg">{t("photo_estimates.draft_created")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("photo_estimates.draft_hint", { quoteId })}
          </p>
          <Button variant="outline" onClick={reset}>{t("photo_estimates.estimate_another")}</Button>
        </div>
      )}
    </div>
  );
}
