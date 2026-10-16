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
// GET /api/tenants/bookkeeping-link/sync-status.
//
// Links the shop to the owner's OWN bookkeeping app (the QuickBooks
// competitor) — there is deliberately no QuickBooks integration. Paid
// invoices auto-sync (customer → invoice → payment) when enabled; the raw
// API token is write-only and is never returned by the API.
export default function BookkeepingSettings() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState(null);
  const [status, setStatus] = useState(null);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

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
          placeholder="https://bookkeeping-api-ymh6.onrender.com"
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
      </div>

      <Button className="btn-tenant mt-6" onClick={save} disabled={saving} data-testid="bookkeeping-save-btn">
        {saving ? "…" : t("bookkeeping.save")}
      </Button>
    </div>
  );
}
