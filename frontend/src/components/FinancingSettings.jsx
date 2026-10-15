import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

// Financing settings tab content for the dashboard Settings page.
// Backend: GET/PUT /api/tenants/financing/settings (PUT is owner/admin only).
// v1: demo provider is the default and only working mode; Wisetack is the
// live target and shops connect their OWN merchant accounts later, so the
// API key field stays disabled until the encrypted key vault lands.
export default function FinancingSettings() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/tenants/financing/settings").then((r) => setSettings(r.data)).catch(() => setSettings({}));
  }, []);
  if (!settings) return null;

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await api.put("/tenants/financing/settings", {
        enabled: settings.enabled !== false,
        provider: settings.provider || "demo",
        merchant_id: settings.merchant_id || null,
      });
      setSettings(data);
      toast.success(t("financing.saved"));
    } catch (e) { toast.error(errMessage(e)); }
    finally { setSaving(false); }
  };

  return (
    <div className="surface p-7 max-w-2xl" data-testid="financing-settings">
      <div className="flex items-center gap-2">
        <div className="overline">{t("financing.settings_title")}</div>
        {settings.demo !== false && settings.provider === "demo" && <Badge variant="secondary">{t("financing.demo_badge")}</Badge>}
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{t("financing.settings_sub")}</p>

      <label className="mt-5 flex items-center gap-3 text-sm">
        <input
          type="checkbox"
          checked={settings.enabled !== false}
          onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })}
          data-testid="financing-enabled-toggle"
          className="h-4 w-4"
        />
        {t("financing.enable_label")}
      </label>

      <div className="mt-5 space-y-1.5">
        <Label>{t("financing.provider_label")}</Label>
        <select
          className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
          value={settings.provider || "demo"}
          onChange={(e) => setSettings({ ...settings, provider: e.target.value })}
          data-testid="financing-provider-select"
        >
          <option value="demo">{t("financing.provider_demo")}</option>
          <option value="wisetack">{t("financing.provider_wisetack")}</option>
        </select>
      </div>

      {(settings.provider || "demo") === "wisetack" && (
        <div className="mt-5 space-y-4">
          <div className="space-y-1.5">
            <Label>{t("financing.merchant_id_label")}</Label>
            <Input
              value={settings.merchant_id || ""}
              onChange={(e) => setSettings({ ...settings, merchant_id: e.target.value })}
              placeholder="wisetack_merchant_…"
              data-testid="financing-merchant-id-input"
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("financing.api_key_label")}</Label>
            <Input disabled placeholder="••••••••" data-testid="financing-api-key-input" />
            <p className="text-xs text-muted-foreground">{t("financing.api_key_hint")}</p>
          </div>
          <p className="text-xs text-muted-foreground">{t("financing.not_live")}</p>
        </div>
      )}

      <Button className="btn-tenant mt-6" onClick={save} disabled={saving} data-testid="financing-save-btn">
        {saving ? "…" : t("financing.save")}
      </Button>
    </div>
  );
}
