import { useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { Upload, Loader2, CheckCircle2, Phone, ArrowRight, ArrowLeft } from "lucide-react";

/**
 * AI onboarding wizard (3 steps):
 *  1. Upload — drop a CSV of old invoices / customer list.
 *  2. Preview & mapping — review the AI-inferred column mapping, detected
 *     customers and pricebook candidates; optionally override mappings.
 *  3. Import + telephony — create the records, then provision telephony
 *     (fresh number, port-in, or both) via the bundled telephony plan.
 *
 * i18n: strings live under the `onboarding` namespace in the locale JSONs
 * (see INTEGRATION_ai_onboarding.md for the full en/es key list).
 */
const BASE = "/tenants/onboarding";

const STEPS = ["upload", "preview", "import"];

function Stepper({ step, t }) {
  return (
    <div className="flex items-center gap-2 mb-6" data-testid="onboarding-stepper">
      {STEPS.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <Badge variant={i <= step ? "default" : "outline"}>
            {i + 1}. {t(`onboarding.steps.${s}`)}
          </Badge>
          {i < STEPS.length - 1 && <ArrowRight className="h-4 w-4 text-muted-foreground" />}
        </div>
      ))}
    </div>
  );
}

export default function Onboarding() {
  const { t } = useTranslation();
  const [step, setStep] = useState(0);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const [mapping, setMapping] = useState({});
  const [batchId] = useState(() => `batch-${Date.now()}`);
  const [importResult, setImportResult] = useState(null);
  const [telephony, setTelephony] = useState(null);
  const [teleMode, setTeleMode] = useState("both");
  const [portNumber, setPortNumber] = useState("");

  const doUpload = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const { data } = await api.post(`${BASE}/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setPreview(data);
      setMapping(data.column_mapping || {});
      setStep(1);
      toast.success(t("onboarding.upload_ok"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const doImport = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`${BASE}/import`, {
        upload_id: preview.upload_id,
        onboarding_batch_id: batchId,
        mapping,
        create_customers: true,
        create_pricebook_items: true,
      });
      setImportResult(data);
      setStep(2);
      toast.success(t("onboarding.import_ok"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const doProvision = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`${BASE}/telephony/provision`, {
        mode: teleMode,
        port_numbers: portNumber ? [portNumber] : [],
        count: teleMode === "ported" ? 1 : 1,
      });
      setTelephony(data);
      toast.success(t("onboarding.telephony_ok"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const mappingFields = [
    "customer_name",
    "email",
    "phone",
    "address",
    "item",
    "price",
    "quantity",
    "date",
  ];

  return (
    <div data-testid="onboarding-page">
      <PageHeader
        eyebrow={t("onboarding.eyebrow")}
        title={t("onboarding.title")}
        description={t("onboarding.description")}
      />
      <Stepper step={step} t={t} />

      {/* Step 1: upload */}
      {step === 0 && (
        <div className="surface p-8 max-w-xl space-y-4" data-testid="onboarding-upload-step">
          <Label htmlFor="onboarding-file">{t("onboarding.upload_label")}</Label>
          <Input
            id="onboarding-file"
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            data-testid="onboarding-file-input"
          />
          <p className="text-sm text-muted-foreground">{t("onboarding.upload_hint")}</p>
          <Button onClick={doUpload} disabled={!file || busy} className="btn-tenant" data-testid="onboarding-upload-btn">
            {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Upload className="h-4 w-4 mr-2" />}
            {t("onboarding.upload_cta")}
          </Button>
        </div>
      )}

      {/* Step 2: preview + mapping */}
      {step === 1 && preview && (
        <div className="space-y-6" data-testid="onboarding-preview-step">
          <div className="surface p-6">
            <h3 className="font-semibold mb-3">{t("onboarding.mapping_title")}</h3>
            <div className="grid grid-cols-2 gap-3 max-w-2xl">
              {mappingFields.map((field) => (
                <div key={field} className="space-y-1">
                  <Label>{t(`onboarding.fields.${field}`)}</Label>
                  <Select
                    value={mapping[field] || "__none"}
                    onValueChange={(v) => setMapping({ ...mapping, [field]: v === "__none" ? "" : v })}
                  >
                    <SelectTrigger data-testid={`mapping-${field}`}>
                      <SelectValue placeholder={t("onboarding.not_mapped")} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none">{t("onboarding.not_mapped")}</SelectItem>
                      {(preview.unmapped_columns || [])
                        .concat(Object.values(preview.column_mapping || {}))
                        .filter((v, i, a) => a.indexOf(v) === i)
                        .map((h) => (
                          <SelectItem key={h} value={h}>{h}</SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-3">
              {t("onboarding.rows_detected", { count: preview.row_count, type: preview.file_type })}
            </p>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            <div className="surface p-6" data-testid="onboarding-customers-preview">
              <h3 className="font-semibold mb-3">
                {t("onboarding.customers_detected", { count: preview.customers.length })}
              </h3>
              <ul className="divide-y divide-border max-h-64 overflow-auto">
                {preview.customers.map((c) => (
                  <li key={c.name} className="py-2 text-sm">
                    <div className="font-medium">{c.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {[c.email, c.phone].filter(Boolean).join(" · ") || "—"}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
            <div className="surface p-6" data-testid="onboarding-pricebook-preview">
              <h3 className="font-semibold mb-3">
                {t("onboarding.pricebook_detected", { count: preview.pricebook_candidates.length })}
              </h3>
              <ul className="divide-y divide-border max-h-64 overflow-auto">
                {preview.pricebook_candidates.map((p) => (
                  <li key={p.name} className="py-2 text-sm flex justify-between">
                    <span className="font-medium">{p.name}</span>
                    <span className="text-muted-foreground">
                      ${p.suggested_price} × {p.occurrences}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="flex gap-3">
            <Button variant="outline" onClick={() => setStep(0)} data-testid="onboarding-back-btn">
              <ArrowLeft className="h-4 w-4 mr-2" />{t("onboarding.back")}
            </Button>
            <Button onClick={doImport} disabled={busy} className="btn-tenant" data-testid="onboarding-import-btn">
              {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <CheckCircle2 className="h-4 w-4 mr-2" />}
              {t("onboarding.import_cta")}
            </Button>
          </div>
        </div>
      )}

      {/* Step 3: import results + telephony */}
      {step === 2 && importResult && (
        <div className="space-y-6" data-testid="onboarding-done-step">
          <div className="surface p-6">
            <h3 className="font-semibold mb-3 flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-green-600" />
              {t("onboarding.import_done")}
            </h3>
            <div className="flex gap-6 text-sm">
              <div>
                <Badge>{importResult.customers_created.length}</Badge> {t("onboarding.customers_created")}
              </div>
              <div>
                <Badge>{importResult.pricebook_items_created.length}</Badge> {t("onboarding.items_created")}
              </div>
              {importResult.idempotent_replay && <Badge variant="secondary">{t("onboarding.replay_note")}</Badge>}
            </div>
          </div>

          <div className="surface p-6" data-testid="onboarding-telephony">
            <h3 className="font-semibold mb-1 flex items-center gap-2">
              <Phone className="h-5 w-5" />{t("onboarding.telephony_title")}
            </h3>
            <p className="text-sm text-muted-foreground mb-4">{t("onboarding.telephony_desc")}</p>
            {telephony ? (
              <div className="text-sm space-y-2">
                <div>{t("onboarding.subaccount")}: <code>{telephony.subaccount_sid}</code></div>
                <ul className="space-y-1">
                  {(telephony.numbers || []).map((n) => (
                    <li key={n.number}>
                      <Badge variant={n.type === "ported" ? "secondary" : "default"}>{n.type}</Badge>{" "}
                      {n.number} — {n.status}
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-muted-foreground">{t("onboarding.bundled_note")}</p>
              </div>
            ) : (
              <div className="space-y-3 max-w-md">
                <div className="space-y-1">
                  <Label>{t("onboarding.telephony_mode")}</Label>
                  <Select value={teleMode} onValueChange={setTeleMode}>
                    <SelectTrigger data-testid="telephony-mode">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="provisioned">{t("onboarding.mode_provisioned")}</SelectItem>
                      <SelectItem value="ported">{t("onboarding.mode_ported")}</SelectItem>
                      <SelectItem value="both">{t("onboarding.mode_both")}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {teleMode !== "provisioned" && (
                  <div className="space-y-1">
                    <Label>{t("onboarding.port_number_label")}</Label>
                    <Input
                      value={portNumber}
                      onChange={(e) => setPortNumber(e.target.value)}
                      placeholder="+15551234567"
                      data-testid="telephony-port-number"
                    />
                  </div>
                )}
                <Button onClick={doProvision} disabled={busy} className="btn-tenant" data-testid="telephony-provision-btn">
                  {busy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {t("onboarding.provision_cta")}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
