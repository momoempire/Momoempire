import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

/* Two-factor enrollment + disable UI. Mounted in Settings -> Security tab.
 * The server never returns the TOTP secret after enrollment; backup codes are
 * shown exactly once.
 */
export default function TwoFactorSettings({ user, onChanged }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState(null); // null | "setup" | "backup" | "disable"
  const [secret, setSecret] = useState("");
  const [uri, setUri] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [backupCodes, setBackupCodes] = useState([]);
  const [busy, setBusy] = useState(false);
  const enabled = !!user?.mfa_enabled;

  const startSetup = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/auth/2fa/setup");
      setSecret(data.secret);
      setUri(data.otpauth_uri);
      setMode("setup");
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const confirmSetup = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/auth/2fa/verify", { code });
      setBackupCodes(data.backup_codes || []);
      setMode("backup");
      toast.success(t("two_factor.enabled_toast"));
      onChanged && onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const doDisable = async () => {
    setBusy(true);
    try {
      await api.post("/auth/2fa/disable", { password });
      setMode(null);
      setPassword("");
      toast.success(t("two_factor.disabled_toast"));
      onChanged && onChanged();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const copySecret = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      toast.success(t("two_factor.copied"));
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  return (
    <div className="col-span-12 surface p-6" data-testid="security-2fa">
      <div className="flex items-center gap-3 mb-1">
        <div className="overline">{t("two_factor.title")}</div>
        <Badge variant={enabled ? "default" : "secondary"}>
          {enabled ? t("two_factor.status_on") : t("two_factor.status_off")}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground mb-4">{t("two_factor.subtitle")}</p>

      {mode === null && !enabled && (
        <Button onClick={startSetup} disabled={busy} data-testid="2fa-enable-btn">
          {t("two_factor.enable")}
        </Button>
      )}
      {mode === null && enabled && (
        <Button variant="outline" onClick={() => setMode("disable")} data-testid="2fa-disable-btn">
          {t("two_factor.disable")}
        </Button>
      )}

      {mode === "setup" && (
        <div className="space-y-4 max-w-md">
          <div>
            <div className="font-medium text-sm mb-1">{t("two_factor.step1_title")}</div>
            <p className="text-sm text-muted-foreground mb-2">{t("two_factor.step1_hint")}</p>
            <Label>{t("two_factor.manual_key")}</Label>
            <div className="flex gap-2 mt-1">
              <Input value={secret} readOnly className="font-mono" data-testid="2fa-secret" />
              <Button variant="outline" onClick={copySecret}>{t("two_factor.copy")}</Button>
            </div>
            <a className="hidden" href={uri} data-testid="2fa-uri">{uri}</a>
          </div>
          <div>
            <div className="font-medium text-sm mb-1">{t("two_factor.step2_title")}</div>
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder={t("two_factor.code_placeholder")}
              inputMode="numeric"
              className="font-mono tracking-widest"
              data-testid="2fa-code"
            />
          </div>
          <div className="flex gap-2">
            <Button onClick={confirmSetup} disabled={busy || code.length !== 6} data-testid="2fa-confirm-btn">
              {t("two_factor.confirm")}
            </Button>
            <Button variant="ghost" onClick={() => setMode(null)}>{t("two_factor.cancel")}</Button>
          </div>
        </div>
      )}

      {mode === "backup" && (
        <div className="space-y-4 max-w-md">
          <div className="font-medium text-sm">{t("two_factor.backup_title")}</div>
          <p className="text-sm text-muted-foreground">{t("two_factor.backup_hint")}</p>
          <ul className="grid grid-cols-2 gap-2 font-mono text-sm">
            {backupCodes.map((c) => (
              <li key={c} className="border border-border rounded px-3 py-2" data-testid="2fa-backup-code">{c}</li>
            ))}
          </ul>
          <Button onClick={() => { setMode(null); setBackupCodes([]); }} data-testid="2fa-done-btn">
            {t("two_factor.done")}
          </Button>
        </div>
      )}

      {mode === "disable" && (
        <div className="space-y-4 max-w-md">
          <div className="font-medium text-sm">{t("two_factor.disable_title")}</div>
          <p className="text-sm text-muted-foreground">{t("two_factor.disable_hint")}</p>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t("two_factor.password_placeholder")}
            data-testid="2fa-disable-password"
          />
          <div className="flex gap-2">
            <Button variant="destructive" onClick={doDisable} disabled={busy || !password} data-testid="2fa-disable-confirm">
              {t("two_factor.disable")}
            </Button>
            <Button variant="ghost" onClick={() => setMode(null)}>{t("two_factor.cancel")}</Button>
          </div>
        </div>
      )}
    </div>
  );
}
