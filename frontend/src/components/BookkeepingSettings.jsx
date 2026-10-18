import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

// Bookkeeping settings tab content for the dashboard Settings page.
// Backend: GET/PUT /api/tenants/bookkeeping-link/settings (PUT is owner/admin
// only), POST /api/tenants/bookkeeping-link/test-connection,
// POST /api/tenants/bookkeeping-link/health-check,
// POST /api/tenants/bookkeeping-link/rotate-credential,
// POST /api/tenants/bookkeeping-link/disconnect,
// GET /api/tenants/bookkeeping-link/sync-status.
//
// Links the shop to the owner's OWN bookkeeping app (the QuickBooks
// competitor) — there is deliberately no QuickBooks integration. Paid
// invoices auto-sync (customer → invoice → payment) when enabled; the raw
// API token is write-only and is never returned by the API. The stored
// credential is either a dedicated integration credential (target state)
// or a legacy pasted login token — the API flags the latter via
// `migration.required` so the tenant is prompted to reconnect before the
// token's ~7-day expiry can silently stop sync. Connection health
// (healthy/degraded/revoked) comes from settings/sync-status `health`.
//
// The CSV bridge section below (GET /tenants/csv-bridge/export/*,
// POST /tenants/csv-bridge/import/customers) is the file-based handoff for
// accountants in QuickBooks/spreadsheets: downloads + a dry-run-first
// customer import. Files only — still no QuickBooks connection.
export default function BookkeepingSettings() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState(null);
  const [status, setStatus] = useState(null);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [healthBusy, setHealthBusy] = useState(false);
  const [newCredential, setNewCredential] = useState("");
  const [rotating, setRotating] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  // CSV bridge (files for QuickBooks/spreadsheet accountants — no live sync)
  const [csvStart, setCsvStart] = useState("");
  const [csvEnd, setCsvEnd] = useState("");
  const [csvFile, setCsvFile] = useState(null);
  const [csvPlan, setCsvPlan] = useState(null);
  const [csvPlanFile, setCsvPlanFile] = useState(null);
  const [csvBusy, setCsvBusy] = useState("");

  const load = () => {
    api.get("/tenants/bookkeeping-link/settings").then((r) => setSettings(r.data)).catch(() => setSettings({}));
    api.get("/tenants/bookkeeping-link/sync-status").then((r) => setStatus(r.data)).catch(() => setStatus({}));
  };
  useEffect(load, []);
  if (!settings) return null;

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        enabled: settings.enabled === true,
        auto_sync_on_paid: settings.auto_sync_on_paid !== false,
      };
      if (settings.api_base_url) payload.api_base_url = settings.api_base_url;
      if (token) payload.api_token = token;
      const { data } = await api.put("/tenants/bookkeeping-link/settings", payload);
      setSettings(data);
      setToken("");
      toast.success(t("bookkeeping.saved"));
      load();
    } catch (e) { toast.error(errMessage(e)); }
    finally { setSaving(false); }
  };

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await api.post("/tenants/bookkeeping-link/test-connection");
      setTestResult(data);
      if (data.ok) toast.success(t("bookkeeping.test_ok"));
      else toast.error(t("bookkeeping.test_failed"));
      load();
    } catch (e) { toast.error(errMessage(e)); }
    finally { setTesting(false); }
  };

  const checkHealth = async () => {
    setHealthBusy(true);
    try {
      const { data } = await api.post("/tenants/bookkeeping-link/health-check");
      if (data.ok) toast.success(t("bookkeeping.test_ok"));
      else toast.error(data.detail || t("bookkeeping.test_failed"));
      load();
    } catch (e) { toast.error(errMessage(e)); }
    finally { setHealthBusy(false); }
  };

  const rotateCredential = async () => {
    if (!newCredential) return;
    setRotating(true);
    try {
      const { data } = await api.post("/tenants/bookkeeping-link/rotate-credential", {
        api_token: newCredential,
      });
      setSettings(data);
      setNewCredential("");
      toast.success(t("bookkeeping.rotate_ok"));
      load();
    } catch (e) { toast.error(errMessage(e) || t("bookkeeping.rotate_failed")); }
    finally { setRotating(false); }
  };

  const disconnect = async () => {
    if (!window.confirm(t("bookkeeping.disconnect_confirm"))) return;
    setDisconnecting(true);
    try {
      const { data } = await api.post("/tenants/bookkeeping-link/disconnect");
      setSettings(data);
      toast.success(t("bookkeeping.disconnect_ok"));
      load();
    } catch (e) { toast.error(errMessage(e)); }
    finally { setDisconnecting(false); }
  };

  const downloadCsv = async (kind) => {
    setCsvBusy(`export-${kind}`);
    try {
      const params = {};
      if (csvStart) params.start = csvStart;
      if (csvEnd) params.end = csvEnd;
      const { data } = await api.get(`/tenants/csv-bridge/export/${kind}`, { params, responseType: "blob" });
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `momo-${kind}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error(errMessage(e) || t("bookkeeping.csv_download_failed")); }
    finally { setCsvBusy(""); }
  };

  const runImport = async (dryRun) => {
    if (!csvFile) { toast.error(t("bookkeeping.csv_need_file")); return; }
    setCsvBusy(dryRun ? "preview" : "run");
    try {
      const fd = new FormData();
      fd.append("file", csvFile);
      const { data } = await api.post("/tenants/csv-bridge/import/customers", fd, { params: { dry_run: dryRun } });
      setCsvPlan(data);
      if (dryRun) setCsvPlanFile(csvFile);
      else toast.success(t("bookkeeping.csv_done", data.summary));
    } catch (e) { toast.error(errMessage(e) || t("bookkeeping.csv_read_failed")); }
    finally { setCsvBusy(""); }
  };

  const counts = status?.counts || {};
  const pushedTotal = (counts.customers || 0) + (counts.invoices || 0) + (counts.payments || 0);

  return (
    <div className="surface p-7 max-w-2xl" data-testid="bookkeeping-settings">
      <div className="flex items-center gap-2">
        <div className="overline">{t("bookkeeping.settings_title")}</div>
        {settings.enabled ? (
          <Badge variant="default" data-testid="bookkeeping-enabled-badge">{t("bookkeeping.connected_badge")}</Badge>
        ) : (
          <Badge variant="secondary" data-testid="bookkeeping-disabled-badge">{t("bookkeeping.disabled_badge")}</Badge>
        )}
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{t("bookkeeping.settings_sub")}</p>

      {settings.migration?.required && (
        <div className="mt-4 rounded-lg border border-amber-500/50 bg-amber-500/10 p-4" data-testid="bookkeeping-migration-banner">
          <div className="text-sm font-medium">{t("bookkeeping.migration_title")}</div>
          <p className="mt-1 text-sm text-muted-foreground">
            {t(settings.migration.reason === "credential_revoked" ? "bookkeeping.migration_revoked" : "bookkeeping.migration_legacy")}
          </p>
        </div>
      )}

      <label className="mt-5 flex items-center gap-3 text-sm">
        <input
          type="checkbox"
          checked={settings.enabled === true}
          onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })}
          data-testid="bookkeeping-enabled-toggle"
          className="h-4 w-4"
        />
        {t("bookkeeping.enable_label")}
      </label>

      <div className="mt-5 space-y-1.5">
        <Label>{t("bookkeeping.base_url_label")}</Label>
        <Input
          value={settings.api_base_url || ""}
          onChange={(e) => setSettings({ ...settings, api_base_url: e.target.value })}
          placeholder="https://bookkeeping-api-ymh6.onrender.com/api"
          data-testid="bookkeeping-base-url-input"
        />
      </div>

      <div className="mt-5 space-y-1.5">
        <Label>{t("bookkeeping.token_label")}</Label>
        <Input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder={settings.api_token_set ? t("bookkeeping.token_saved_placeholder") : t("bookkeeping.token_placeholder")}
          data-testid="bookkeeping-token-input"
          autoComplete="off"
        />
        <p className="text-xs text-muted-foreground">{t("bookkeeping.token_hint")}</p>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant="outline" onClick={testConnection} disabled={testing || !settings.api_token_set} data-testid="bookkeeping-test-btn">
          {testing ? "…" : t("bookkeeping.test_button")}
        </Button>
        {testResult && (
          <span className={`text-sm ${testResult.ok ? "text-green-600" : "text-red-600"}`} data-testid="bookkeeping-test-result">
            {testResult.ok ? "✓ " : "✗ "}{testResult.detail}
          </span>
        )}
      </div>

      <label className="mt-6 flex items-center gap-3 text-sm">
        <input
          type="checkbox"
          checked={settings.auto_sync_on_paid !== false}
          onChange={(e) => setSettings({ ...settings, auto_sync_on_paid: e.target.checked })}
          data-testid="bookkeeping-autosync-toggle"
          className="h-4 w-4"
        />
        {t("bookkeeping.autosync_label")}
      </label>
      <p className="mt-1 text-xs text-muted-foreground">{t("bookkeeping.autosync_hint")}</p>

      <div className="mt-6 rounded-lg border border-border p-4" data-testid="bookkeeping-sync-status">
        <div className="overline mb-2">{t("bookkeeping.status_title")}</div>
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt className="text-muted-foreground">{t("bookkeeping.status_last_sync")}</dt>
          <dd data-testid="bookkeeping-last-sync">{status?.last_sync_at ? new Date(status.last_sync_at).toLocaleString() : t("bookkeeping.status_never")}</dd>
          <dt className="text-muted-foreground">{t("bookkeeping.status_pushed")}</dt>
          <dd data-testid="bookkeeping-pushed-counts">
            {t("bookkeeping.status_counts", { customers: counts.customers || 0, invoices: counts.invoices || 0, payments: counts.payments || 0, total: pushedTotal })}
          </dd>
        </dl>
        {status?.last_error && (
          <p className="mt-2 text-sm text-red-600" data-testid="bookkeeping-last-error">✗ {status.last_error}</p>
        )}
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <dt className="text-muted-foreground">{t("bookkeeping.health_label")}</dt>
          <dd data-testid="bookkeeping-health">
            <Badge
              variant={status?.health?.status === "healthy" ? "default" : status?.health?.status === "revoked" ? "destructive" : "secondary"}
              data-testid={`bookkeeping-health-${status?.health?.status || "unknown"}`}
            >
              {t(`bookkeeping.health_${status?.health?.status || "unknown"}`)}
            </Badge>
            {status?.health?.checked_at && (
              <span className="ml-2 text-xs text-muted-foreground">
                {t("bookkeeping.health_checked_at", { when: new Date(status.health.checked_at).toLocaleString() })}
              </span>
            )}
          </dd>
          {settings.api_token_set && (
            <>
              <dt className="text-muted-foreground">{t("bookkeeping.token_label")}</dt>
              <dd data-testid="bookkeeping-credential-meta">
                {t(settings.credential_kind === "legacy_jwt" ? "bookkeeping.credential_kind_legacy" : "bookkeeping.credential_kind_integration")}
                {settings.credential_fingerprint && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    {t("bookkeeping.credential_fingerprint", { fingerprint: settings.credential_fingerprint })}
                  </span>
                )}
              </dd>
            </>
          )}
        </dl>
        {status?.health?.detail && status.health.status !== "healthy" && (
          <p className="mt-2 text-sm text-red-600" data-testid="bookkeeping-health-detail">✗ {status.health.detail}</p>
        )}
        {Array.isArray(status?.recent_failures) && status.recent_failures.length > 0 && (
          <div className="mt-3" data-testid="bookkeeping-recent-failures">
            <div className="text-xs text-muted-foreground">{t("bookkeeping.recent_failures")}</div>
            <ul className="mt-1 space-y-1 text-sm text-red-600">
              {status.recent_failures.map((f, i) => (
                <li key={i}>✗ {f.error}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant="outline" onClick={checkHealth} disabled={healthBusy || !settings.api_token_set} data-testid="bookkeeping-health-btn">
            {healthBusy ? "…" : t("bookkeeping.health_check_button")}
          </Button>
          {settings.api_token_set && (
            <Button variant="outline" onClick={disconnect} disabled={disconnecting} data-testid="bookkeeping-disconnect-btn">
              {disconnecting ? "…" : t("bookkeeping.disconnect_button")}
            </Button>
          )}
        </div>
        {settings.api_token_set && (
          <div className="mt-4 space-y-1.5">
            <Label>{t("bookkeeping.rotate_label")}</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                type="password"
                value={newCredential}
                onChange={(e) => setNewCredential(e.target.value)}
                placeholder={t("bookkeeping.rotate_placeholder")}
                data-testid="bookkeeping-rotate-input"
                autoComplete="off"
                className="max-w-md"
              />
              <Button variant="outline" onClick={rotateCredential} disabled={rotating || !newCredential} data-testid="bookkeeping-rotate-btn">
                {rotating ? "…" : t("bookkeeping.rotate_button")}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{t("bookkeeping.rotate_hint")}</p>
          </div>
        )}
      </div>

      <div className="mt-6 rounded-lg border border-border p-4" data-testid="bookkeeping-csv-bridge">
        <div className="overline mb-2">{t("bookkeeping.csv_title")}</div>
        <p className="text-sm text-muted-foreground">{t("bookkeeping.csv_sub")}</p>

        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <Label>{t("bookkeeping.csv_start_label")}</Label>
            <Input type="date" value={csvStart} onChange={(e) => setCsvStart(e.target.value)} data-testid="bookkeeping-csv-start" />
          </div>
          <div className="space-y-1.5">
            <Label>{t("bookkeeping.csv_end_label")}</Label>
            <Input type="date" value={csvEnd} onChange={(e) => setCsvEnd(e.target.value)} data-testid="bookkeeping-csv-end" />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => downloadCsv("customers")} disabled={csvBusy !== ""} data-testid="bookkeeping-csv-export-customers">
            {csvBusy === "export-customers" ? "…" : t("bookkeeping.csv_export_customers")}
          </Button>
          <Button variant="outline" onClick={() => downloadCsv("invoices")} disabled={csvBusy !== ""} data-testid="bookkeeping-csv-export-invoices">
            {csvBusy === "export-invoices" ? "…" : t("bookkeeping.csv_export_invoices")}
          </Button>
          <Button variant="outline" onClick={() => downloadCsv("payments")} disabled={csvBusy !== ""} data-testid="bookkeeping-csv-export-payments">
            {csvBusy === "export-payments" ? "…" : t("bookkeeping.csv_export_payments")}
          </Button>
        </div>

        <div className="overline mt-6 mb-2">{t("bookkeeping.csv_import_title")}</div>
        <p className="text-sm text-muted-foreground">{t("bookkeeping.csv_import_sub")}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => { setCsvFile(e.target.files?.[0] || null); setCsvPlan(null); }}
            data-testid="bookkeeping-csv-import-file"
            className="max-w-xs"
          />
          <Button variant="outline" onClick={() => runImport(true)} disabled={csvBusy !== "" || !csvFile} data-testid="bookkeeping-csv-preview-btn">
            {csvBusy === "preview" ? "…" : t("bookkeeping.csv_preview_button")}
          </Button>
          <Button
            onClick={() => runImport(false)}
            disabled={csvBusy !== "" || !csvPlan?.dry_run || csvPlanFile !== csvFile}
            data-testid="bookkeeping-csv-run-btn"
          >
            {csvBusy === "run" ? "…" : t("bookkeeping.csv_run_button")}
          </Button>
        </div>

        {csvPlan && (
          <div className="mt-4" data-testid="bookkeeping-csv-plan">
            <p className="text-sm font-medium">{t("bookkeeping.csv_summary", csvPlan.summary)}</p>
            <table className="mt-2 w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1 pr-2">{t("bookkeeping.csv_col_row")}</th>
                  <th className="py-1 pr-2">{t("bookkeeping.csv_col_action")}</th>
                  <th className="py-1 pr-2">{t("bookkeeping.csv_col_customer")}</th>
                  <th className="py-1">{t("bookkeeping.csv_col_detail")}</th>
                </tr>
              </thead>
              <tbody>
                {csvPlan.rows.map((row) => (
                  <tr key={row.row} className="border-t border-border" data-testid={`bookkeeping-csv-plan-row-${row.row}`}>
                    <td className="py-1 pr-2">{row.row}</td>
                    <td className="py-1 pr-2">{t(`bookkeeping.csv_action_${row.action}`)}</td>
                    <td className="py-1 pr-2">{row.name || row.email || "—"}</td>
                    <td className="py-1">{row.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Button className="btn-tenant mt-6" onClick={save} disabled={saving} data-testid="bookkeeping-save-btn">
        {saving ? "…" : t("bookkeeping.save")}
      </Button>
    </div>
  );
}
