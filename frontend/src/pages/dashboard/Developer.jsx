import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { toast } from "sonner";
import {
  KeyRound, Webhook, Copy, Trash2, Play, Loader2, CheckCircle2, ShieldAlert, X,
} from "lucide-react";

const BASE = "/developer";
const SCOPES = ["customers:read", "customers:write", "jobs:read", "jobs:write", "invoices:read"];
const EVENTS = ["job.created", "job.completed", "invoice.paid", "customer.created"];

function SecretReveal({ title, warn, secret, onDone, onCopy, t }) {
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950 p-4 space-y-3" data-testid="developer-reveal">
      <div className="flex items-center gap-2 font-medium">
        <ShieldAlert className="h-4 w-4 text-amber-600" />
        {title}
      </div>
      <p className="text-sm text-muted-foreground">{warn}</p>
      <div className="flex items-center gap-2">
        <code className="flex-1 break-all rounded bg-background border px-3 py-2 text-sm font-mono" data-testid="developer-reveal-secret">
          {secret}
        </code>
        <Button size="sm" variant="outline" onClick={() => onCopy(secret)} data-testid="developer-copy-secret">
          <Copy className="h-4 w-4 mr-1" />{t("developer.copy")}
        </Button>
      </div>
      <Button size="sm" onClick={onDone} data-testid="developer-reveal-done">
        <CheckCircle2 className="h-4 w-4 mr-1" />{t("developer.done")}
      </Button>
    </div>
  );
}

function CheckRow({ id, label, checked, onToggle }) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm cursor-pointer">
      <Checkbox id={id} checked={checked} onCheckedChange={onToggle} />
      <span>{label}</span>
    </label>
  );
}

export default function Developer() {
  const { t } = useTranslation();

  // ---- API keys ----
  const [keys, setKeys] = useState([]);
  const [loadingKeys, setLoadingKeys] = useState(true);
  const [newName, setNewName] = useState("");
  const [newScopes, setNewScopes] = useState(["customers:read", "jobs:read", "invoices:read"]);
  const [creatingKey, setCreatingKey] = useState(false);
  const [revealedKey, setRevealedKey] = useState(null);
  const [revoking, setRevoking] = useState(null);

  // ---- Webhooks ----
  const [hooks, setHooks] = useState([]);
  const [loadingHooks, setLoadingHooks] = useState(true);
  const [newUrl, setNewUrl] = useState("");
  const [newEvents, setNewEvents] = useState(["job.created"]);
  const [creatingHook, setCreatingHook] = useState(false);
  const [revealedSecret, setRevealedSecret] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [testingId, setTestingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [selectedHook, setSelectedHook] = useState(null);
  const [deliveries, setDeliveries] = useState([]);
  const [loadingDeliveries, setLoadingDeliveries] = useState(false);

  const loadKeys = async () => {
    setLoadingKeys(true);
    try {
      const res = await api.get(`${BASE}/keys`);
      setKeys(res.data || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoadingKeys(false);
    }
  };

  const loadHooks = async () => {
    setLoadingHooks(true);
    try {
      const res = await api.get(`${BASE}/webhooks`);
      setHooks(res.data || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoadingHooks(false);
    }
  };

  const loadDeliveries = async (hook) => {
    setSelectedHook(hook);
    setLoadingDeliveries(true);
    try {
      const res = await api.get(`${BASE}/webhooks/${hook.id}/deliveries`);
      setDeliveries(res.data || []);
    } catch (e) {
      toast.error(errMessage(e));
      setDeliveries([]);
    } finally {
      setLoadingDeliveries(false);
    }
  };

  useEffect(() => { loadKeys(); loadHooks(); }, []);

  const toggleScope = (s) =>
    setNewScopes((list) => (list.includes(s) ? list.filter((x) => x !== s) : [...list, s]));
  const toggleEvent = (e) =>
    setNewEvents((list) => (list.includes(e) ? list.filter((x) => x !== e) : [...list, e]));

  const createKey = async () => {
    if (!newName.trim() || newScopes.length === 0) return;
    setCreatingKey(true);
    try {
      const res = await api.post(`${BASE}/keys`, { name: newName.trim(), scopes: newScopes });
      setRevealedKey(res.data); // raw key shown exactly once
      setNewName("");
      toast.success(t("developer.created_toast"));
      loadKeys();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setCreatingKey(false);
    }
  };

  const revokeKey = async (kid) => {
    if (!window.confirm(t("developer.revoke_confirm"))) return;
    setRevoking(kid);
    try {
      await api.post(`${BASE}/keys/${kid}/revoke`);
      toast.success(t("developer.revoked_toast"));
      loadKeys();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setRevoking(null);
    }
  };

  const createHook = async () => {
    if (!newUrl.trim() || newEvents.length === 0) return;
    setCreatingHook(true);
    try {
      const res = await api.post(`${BASE}/webhooks`, { url: newUrl.trim(), events: newEvents });
      setRevealedSecret(res.data); // signing secret shown exactly once
      setNewUrl("");
      toast.success(t("developer.created_webhook_toast"));
      loadHooks();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setCreatingHook(false);
    }
  };

  const deleteHook = async (wid) => {
    if (!window.confirm(t("developer.delete_confirm"))) return;
    setDeletingId(wid);
    try {
      await api.delete(`${BASE}/webhooks/${wid}`);
      toast.success(t("developer.deleted_toast"));
      if (selectedHook?.id === wid) { setSelectedHook(null); setDeliveries([]); }
      loadHooks();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setDeletingId(null);
    }
  };

  const testHook = async (wid) => {
    setTestingId(wid);
    try {
      const res = await api.post(`${BASE}/webhooks/${wid}/test`);
      setTestResult(res.data); // signed, NOT sent
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setTestingId(null);
    }
  };

  const doCopy = (text) => {
    const ok = () => toast.success(t("developer.copied"));
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(ok, () => toast.error(errMessage(null)));
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); ok(); } catch { /* noop */ }
      document.body.removeChild(ta);
    }
  };

  const fmtTime = (iso) => {
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  return (
    <div className="space-y-6" data-testid="developer-page">
      <PageHeader
        eyebrow={t("developer.eyebrow")}
        title={t("developer.title")}
        description={t("developer.description")}
      />
      <p className="text-sm text-muted-foreground">{t("developer.api_base")}</p>

      {/* ============ API KEYS ============ */}
      <Card data-testid="developer-keys-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="h-5 w-5" />{t("developer.keys_title")}
          </CardTitle>
          <CardDescription>{t("developer.keys_empty")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {revealedKey && (
            <SecretReveal
              title={t("developer.key_created_title")}
              warn={t("developer.key_created_warn")}
              secret={revealedKey.key}
              onDone={() => setRevealedKey(null)}
              onCopy={doCopy}
              t={t}
            />
          )}

          <div className="rounded-lg border p-4 space-y-3">
            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <div className="space-y-1">
                <label className="text-sm font-medium">{t("developer.key_name_label")}</label>
                <Input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder={t("developer.key_name_placeholder")}
                  data-testid="developer-key-name"
                />
              </div>
              <div className="flex items-end">
                <Button onClick={createKey} disabled={creatingKey || !newName.trim() || newScopes.length === 0} data-testid="developer-create-key">
                  {creatingKey ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}
                  {creatingKey ? t("developer.creating") : t("developer.create_key")}
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-sm font-medium">{t("developer.key_scopes_label")}</div>
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                {SCOPES.map((s) => (
                  <CheckRow key={s} id={`scope-${s}`} label={t(`developer.scopes.${s}`)} checked={newScopes.includes(s)} onToggle={() => toggleScope(s)} />
                ))}
              </div>
            </div>
          </div>

          {loadingKeys ? (
            <div className="flex items-center gap-2 text-muted-foreground text-sm">
              <Loader2 className="h-4 w-4 animate-spin" />…
            </div>
          ) : (
            <div className="space-y-2">
              {keys.map((k) => (
                <div key={k.id} className="flex flex-wrap items-center gap-2 rounded-lg border p-3" data-testid={`developer-key-${k.id}`}>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium flex items-center gap-2">
                      {k.name}
                      {k.revoked && <Badge variant="destructive">{t("developer.revoked")}</Badge>}
                    </div>
                    <div className="text-xs text-muted-foreground font-mono">{k.key_hint}</div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {t("developer.created")}: {fmtTime(k.created_at)} · {t("developer.last_used")}: {k.last_used_at ? fmtTime(k.last_used_at) : t("developer.never_used")}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {k.scopes.map((s) => <Badge key={s} variant="secondary" className="text-xs">{s}</Badge>)}
                  </div>
                  {!k.revoked && (
                    <Button variant="ghost" size="sm" onClick={() => revokeKey(k.id)} disabled={revoking === k.id} data-testid={`developer-revoke-${k.id}`}>
                      {revoking === k.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4 mr-1" />}
                      {revoking === k.id ? t("developer.revoking") : t("developer.revoke")}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ============ WEBHOOKS ============ */}
      <Card data-testid="developer-webhooks-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Webhook className="h-5 w-5" />{t("developer.webhooks_title")}
          </CardTitle>
          <CardDescription>{t("developer.webhooks_empty")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {revealedSecret && (
            <SecretReveal
              title={t("developer.secret_created_title")}
              warn={t("developer.secret_created_warn")}
              secret={revealedSecret.secret}
              onDone={() => setRevealedSecret(null)}
              onCopy={doCopy}
              t={t}
            />
          )}

          <div className="rounded-lg border p-4 space-y-3">
            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <div className="space-y-1">
                <label className="text-sm font-medium">{t("developer.webhook_url_label")}</label>
                <Input
                  value={newUrl}
                  onChange={(e) => setNewUrl(e.target.value)}
                  placeholder={t("developer.webhook_url_placeholder")}
                  data-testid="developer-webhook-url"
                />
              </div>
              <div className="flex items-end">
                <Button onClick={createHook} disabled={creatingHook || !newUrl.trim() || newEvents.length === 0} data-testid="developer-subscribe">
                  {creatingHook ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}
                  {creatingHook ? t("developer.subscribing") : t("developer.subscribe")}
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-sm font-medium">{t("developer.webhook_events_label")}</div>
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                {EVENTS.map((e) => (
                  <CheckRow key={e} id={`event-${e}`} label={t(`developer.events.${e}`)} checked={newEvents.includes(e)} onToggle={() => toggleEvent(e)} />
                ))}
              </div>
            </div>
          </div>

          {loadingHooks ? (
            <div className="flex items-center gap-2 text-muted-foreground text-sm">
              <Loader2 className="h-4 w-4 animate-spin" />…
            </div>
          ) : (
            <div className="space-y-2">
              {hooks.map((h) => (
                <div key={h.id} className="rounded-lg border p-3 space-y-2" data-testid={`developer-hook-${h.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-sm truncate">{h.url}</div>
                      <div className="text-xs text-muted-foreground font-mono">{h.secret_hint}</div>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {h.events.map((e) => <Badge key={e} variant="secondary" className="text-xs">{e}</Badge>)}
                    </div>
                    <Button
                      variant={selectedHook?.id === h.id ? "default" : "outline"}
                      size="sm"
                      onClick={() => loadDeliveries(h)}
                      data-testid={`developer-deliveries-${h.id}`}
                    >
                      {t("developer.deliveries_title")}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => testHook(h.id)} disabled={testingId === h.id} data-testid={`developer-test-${h.id}`}>
                      {testingId === h.id ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Play className="h-4 w-4 mr-1" />}
                      {testingId === h.id ? t("developer.testing") : t("developer.test")}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => deleteHook(h.id)} disabled={deletingId === h.id} data-testid={`developer-delete-hook-${h.id}`}>
                      {deletingId === h.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4 mr-1" />}
                      {deletingId === h.id ? t("developer.deleting") : t("developer.delete")}
                    </Button>
                  </div>

                  {selectedHook?.id === h.id && (
                    <div className="border-t pt-2 space-y-2" data-testid="developer-deliveries-log">
                      {loadingDeliveries ? (
                        <div className="text-sm text-muted-foreground">{t("developer.deliveries_loading")}</div>
                      ) : deliveries.length === 0 ? (
                        <div className="text-sm text-muted-foreground">{t("developer.deliveries_empty")}</div>
                      ) : (
                        deliveries.map((d) => (
                          <div key={d.id} className="flex flex-wrap items-center gap-2 text-sm rounded border p-2">
                            <Badge variant={d.status === "delivered" ? "default" : "destructive"}>
                              {d.status === "delivered" ? t("developer.delivered") : t("developer.failed")}
                            </Badge>
                            <code className="text-xs">{d.event}</code>
                            <span className="text-xs text-muted-foreground">{fmtTime(d.created_at)}</span>
                            <span className="text-xs text-muted-foreground">
                              {d.attempts} {t("developer.attempts")}{d.status_code ? ` · HTTP ${d.status_code}` : ""}
                            </span>
                            {d.error && <span className="text-xs text-red-600 truncate max-w-full">{d.error}</span>}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {testResult && (
            <div className="rounded-lg border p-4 space-y-3" data-testid="developer-test-result">
              <div className="flex items-center justify-between">
                <div className="font-medium">{t("developer.test_title")} — {testResult.event}</div>
                <Button variant="ghost" size="sm" onClick={() => setTestResult(null)}><X className="h-4 w-4" /></Button>
              </div>
              <p className="text-sm text-muted-foreground">{t("developer.test_note")}</p>
              <div className="space-y-1">
                <div className="text-xs font-medium">X-Momo-Signature</div>
                <div className="flex items-center gap-2">
                  <code className="flex-1 break-all text-xs font-mono rounded border bg-background px-2 py-1">
                    {testResult.headers["X-Momo-Signature"]}
                  </code>
                  <Button size="sm" variant="outline" onClick={() => doCopy(testResult.headers["X-Momo-Signature"])}>
                    <Copy className="h-3 w-3 mr-1" />{t("developer.copy")}
                  </Button>
                </div>
              </div>
              <div className="space-y-1">
                <div className="text-xs font-medium">Payload</div>
                <pre className="text-xs font-mono rounded border bg-background p-3 overflow-auto max-h-64" data-testid="developer-test-payload">
                  {JSON.stringify(testResult.payload, null, 2)}
                </pre>
                <Button size="sm" variant="outline" onClick={() => doCopy(JSON.stringify(testResult.payload, null, 2))}>
                  <Copy className="h-3 w-3 mr-1" />{t("developer.copy")}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
