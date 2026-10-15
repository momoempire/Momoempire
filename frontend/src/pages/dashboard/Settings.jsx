import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Copy, ExternalLink, Mail, Trash2, UserPlus } from "lucide-react";
import { Link } from "react-router-dom";
import FinancingSettings from "@/components/FinancingSettings";
import BookkeepingSettings from "@/components/BookkeepingSettings";
import TwoFactorSettings from "@/components/TwoFactorSettings";
import { useAuth } from "@/context/AuthContext";

const PRESET_COLORS = ["#0A0A0A", "#2563EB", "#059669", "#DC2626", "#7C3AED", "#D97706", "#0F766E", "#DB2777"];

function SecurityTab() {
  const { user, refresh } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const load = () => {
    api.get("/auth/sessions").then((r) => setSessions(r.data)).catch(() => {});
    api.get("/auth/workspaces").then((r) => setWorkspaces(r.data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  const revoke = async (id) => { if (!confirm("Revoke this session?")) return; try { await api.delete(`/auth/sessions/${id}`); load(); toast.success("Revoked"); } catch (e) { toast.error(errMessage(e)); } };
  const switchTo = async (tid) => { try { await api.post("/auth/switch", null, { params: { tenant_id: tid } }); window.location.href = "/app"; } catch (e) { toast.error(errMessage(e)); } };
  return (
    <div className="grid grid-cols-12 gap-6">
      <div className="col-span-12 lg:col-span-7 surface p-6" data-testid="security-sessions">
        <div className="overline mb-3">Active sessions</div>
        <ul className="divide-y divide-border">
          {sessions.map((s) => (
            <li key={s.id} className="flex items-center gap-4 py-3" data-testid={`session-${s.id}`}>
              <div className="flex-1 min-w-0">
                <div className="font-mono text-sm">…{s.session_token_tail}</div>
                <div className="text-[11px] text-muted-foreground">created {new Date(s.created_at).toLocaleString()} · expires {new Date(s.expires_at).toLocaleString()}</div>
              </div>
              {s.is_current && <Badge variant="secondary">this session</Badge>}
              <Button variant="ghost" size="sm" onClick={() => revoke(s.id)} disabled={s.is_current} data-testid={`revoke-${s.id}`}>Revoke</Button>
            </li>
          ))}
          {sessions.length === 0 && <li className="py-6 text-sm text-muted-foreground text-center">No Google sessions.</li>}
        </ul>
      </div>
      <div className="col-span-12 lg:col-span-5 surface p-6" data-testid="security-workspaces">
        <div className="overline mb-3">Your workspaces</div>
        <ul className="divide-y divide-border">
          {workspaces.map((w) => (
            <li key={w.tenant_id} className="flex items-center gap-3 py-2.5" data-testid={`workspace-${w.tenant_id}`}>
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">{w.tenant_name}</div>
                <div className="text-[11px] text-muted-foreground">role {w.role}</div>
              </div>
              {w.is_current ? <Badge variant="secondary">current</Badge>
                : <Button variant="outline" size="sm" onClick={() => switchTo(w.tenant_id)} data-testid={`switch-${w.tenant_id}`}>Switch</Button>}
            </li>
          ))}
          {workspaces.length === 0 && <li className="py-6 text-sm text-muted-foreground text-center">Just this workspace.</li>}
        </ul>
      </div>
      <TwoFactorSettings user={user} onChanged={refresh} />
    </div>
  );
}

export default function Settings() {
  const [tenant, setTenant] = useState(null);
  const [saving, setSaving] = useState(false);
  const [staff, setStaff] = useState([]);
  const [invites, setInvites] = useState([]);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: "", role: "staff" });

  const load = () => {
    api.get("/tenants/me").then((r) => setTenant(r.data));
    api.get("/tenants/invitations/staff").then((r) => setStaff(r.data)).catch(() => {});
    api.get("/tenants/invitations").then((r) => setInvites(r.data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  if (!tenant) return null;

  const branding = tenant.branding || {};
  const setBranding = (patch) => setTenant({ ...tenant, branding: { ...branding, ...patch } });

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/tenants/me", {
        name: tenant.name, description: tenant.description,
        contact_phone: tenant.contact_phone, contact_email: tenant.contact_email || null,
        website: tenant.website, branding: tenant.branding,
        review_url: tenant.review_url || null,
      });
      toast.success("Workspace saved");
    } catch (e) { toast.error(errMessage(e)); }
    finally { setSaving(false); }
  };

  const sendInvite = async () => {
    try {
      const { data } = await api.post("/tenants/invitations", inviteForm);
      toast.success("Invite created — copy the link for the invitee");
      navigator.clipboard.writeText(`${window.location.origin}/invite?token=${data.token}`);
      setInviteOpen(false); setInviteForm({ email: "", role: "staff" }); load();
    } catch (e) { toast.error(errMessage(e)); }
  };
  const revokeInvite = async (id) => { try { await api.post(`/tenants/invitations/${id}/revoke`); load(); } catch (e) { toast.error(errMessage(e)); } };
  const copyInvite = (inv) => { navigator.clipboard.writeText(`${window.location.origin}/invite?token=${inv.token}`); toast.success("Invite link copied"); };
  const changeRole = async (u, role) => { try { await api.put(`/tenants/invitations/staff/${u.id}/role`, null, { params: { role } }); load(); } catch (e) { toast.error(errMessage(e)); } };

  const publicUrl = `${window.location.origin}/b/${tenant.slug}`;

  return (
    <div data-testid="settings-page">
      <PageHeader eyebrow="Setup" title="Workspace settings" description="Branding, team, and your public page." actions={
        <Button className="btn-tenant" onClick={save} disabled={saving} data-testid="settings-save-btn">{saving ? "Saving…" : "Save changes"}</Button>
      } />

      <Tabs defaultValue="profile">
        <TabsList>
          <TabsTrigger value="profile" data-testid="settings-tab-profile">Profile</TabsTrigger>
          <TabsTrigger value="branding" data-testid="settings-tab-branding">Branding</TabsTrigger>
          <TabsTrigger value="team" data-testid="settings-tab-team">Team</TabsTrigger>
          <TabsTrigger value="public" data-testid="settings-tab-public">Public page</TabsTrigger>
          <TabsTrigger value="security" data-testid="settings-tab-security">Security</TabsTrigger>
          <TabsTrigger value="financing" data-testid="settings-tab-financing">Financing</TabsTrigger>
          <TabsTrigger value="bookkeeping" data-testid="settings-tab-bookkeeping">Bookkeeping</TabsTrigger>
        </TabsList>

        <TabsContent value="profile">
          <div className="surface p-7 space-y-5 max-w-2xl">
            <div className="space-y-1.5"><Label>Business name</Label><Input value={tenant.name || ""} onChange={(e) => setTenant({ ...tenant, name: e.target.value })} data-testid="settings-name-input" /></div>
            <div className="space-y-1.5"><Label>Description</Label><Textarea rows={3} value={tenant.description || ""} onChange={(e) => setTenant({ ...tenant, description: e.target.value })} data-testid="settings-description-input" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5"><Label>Phone</Label><Input value={tenant.contact_phone || ""} onChange={(e) => setTenant({ ...tenant, contact_phone: e.target.value })} data-testid="settings-phone-input" /></div>
              <div className="space-y-1.5"><Label>Email</Label><Input type="email" value={tenant.contact_email || ""} onChange={(e) => setTenant({ ...tenant, contact_email: e.target.value })} data-testid="settings-email-input" /></div>
            </div>
            <div className="space-y-1.5"><Label>Website</Label><Input value={tenant.website || ""} onChange={(e) => setTenant({ ...tenant, website: e.target.value })} placeholder="https://" /></div>
            <div className="space-y-1.5">
              <Label>Google Business review URL <span className="text-[11px] text-muted-foreground">(used by AI review requests)</span></Label>
              <Input value={tenant.review_url || ""} onChange={(e) => setTenant({ ...tenant, review_url: e.target.value })} placeholder="https://g.page/r/your-business/review" data-testid="settings-review-url-input" />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="branding">
          <div className="surface p-7 max-w-xl">
            <div className="overline mb-3">Primary color</div>
            <div className="flex gap-2 flex-wrap">
              {PRESET_COLORS.map((c) => (
                <button type="button" key={c} onClick={() => setBranding({ primary_color: c })} className={`h-10 w-10 rounded-lg border ${branding.primary_color === c ? "ring-2 ring-offset-2 ring-foreground" : "border-border"}`} style={{ background: c }} data-testid={`settings-color-${c.replace("#", "")}`} aria-label={c} />
              ))}
            </div>
            <Input className="mt-3" value={branding.primary_color || "#0A0A0A"} onChange={(e) => setBranding({ primary_color: e.target.value })} />
            <div className="space-y-1.5 mt-5">
              <Label>Display name</Label>
              <Input value={branding.display_name || ""} onChange={(e) => setBranding({ display_name: e.target.value })} />
            </div>
            <div className="space-y-1.5 mt-5">
              <Label>Logo URL</Label>
              <Input value={branding.logo_url || ""} onChange={(e) => setBranding({ logo_url: e.target.value })} placeholder="https://" />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="team">
          <div className="flex justify-end mb-4">
            <Button className="btn-tenant" onClick={() => setInviteOpen(true)} data-testid="invite-create-btn"><UserPlus className="h-4 w-4 mr-1" />Invite teammate</Button>
          </div>
          <div className="surface mb-6">
            <div className="px-6 py-3 overline border-b border-border">Members</div>
            <ul className="divide-y divide-border">
              {staff.map((u) => (
                <li key={u.id} className="flex items-center gap-5 px-6 py-3" data-testid={`staff-row-${u.id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium">{u.name}</div>
                    <div className="text-xs text-muted-foreground">{u.email}</div>
                  </div>
                  <select className="h-9 rounded-md border border-input bg-background px-2 text-sm" value={u.role} onChange={(e) => changeRole(u, e.target.value)} data-testid={`staff-role-${u.id}`}>
                    <option value="owner">owner</option>
                    <option value="admin">admin</option>
                    <option value="staff">staff</option>
                  </select>
                </li>
              ))}
              {staff.length === 0 && <li className="p-6 text-sm text-muted-foreground text-center">Just you right now.</li>}
            </ul>
          </div>

          <div className="surface">
            <div className="px-6 py-3 overline border-b border-border">Pending invites</div>
            <ul className="divide-y divide-border">
              {invites.filter((i) => i.status === "pending").map((inv) => (
                <li key={inv.id} className="flex items-center gap-5 px-6 py-3" data-testid={`invite-row-${inv.id}`}>
                  <Mail className="h-4 w-4 text-muted-foreground" />
                  <div className="flex-1 min-w-0 truncate">{inv.email}</div>
                  <Badge variant="secondary">{inv.role}</Badge>
                  <Button variant="outline" size="sm" onClick={() => copyInvite(inv)} data-testid={`invite-copy-${inv.id}`}><Copy className="h-3 w-3 mr-1" />Copy link</Button>
                  <Button variant="ghost" size="icon" onClick={() => revokeInvite(inv.id)} data-testid={`invite-revoke-${inv.id}`}><Trash2 className="h-4 w-4" /></Button>
                </li>
              ))}
              {invites.filter((i) => i.status === "pending").length === 0 && <li className="p-6 text-sm text-muted-foreground text-center">No pending invites.</li>}
            </ul>
          </div>
        </TabsContent>

        <TabsContent value="public">
          <div className="surface p-7 max-w-xl">
            <div className="overline mb-3">Public page URL</div>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs font-mono bg-muted rounded px-2 py-2 truncate">{publicUrl}</code>
              <Button variant="outline" size="icon" onClick={() => { navigator.clipboard.writeText(publicUrl); toast.success("Copied"); }}><Copy className="h-4 w-4" /></Button>
              <Link to={`/b/${tenant.slug}`} target="_blank"><Button variant="outline" size="icon"><ExternalLink className="h-4 w-4" /></Button></Link>
            </div>
            <p className="text-xs text-muted-foreground mt-4">To connect a custom domain, visit the Website page.</p>
          </div>
        </TabsContent>
        <TabsContent value="security">
          <SecurityTab />
        </TabsContent>
        <TabsContent value="financing">
          <FinancingSettings />
        </TabsContent>
        <TabsContent value="bookkeeping">
          <BookkeepingSettings />
        </TabsContent>
      </Tabs>

      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent data-testid="invite-modal">
          <DialogHeader><DialogTitle>Invite a teammate</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5"><Label>Email</Label><Input type="email" value={inviteForm.email} onChange={(e) => setInviteForm({ ...inviteForm, email: e.target.value })} data-testid="invite-email-input" /></div>
            <div className="space-y-1.5">
              <Label>Role</Label>
              <div className="flex gap-2">
                {["owner", "admin", "staff"].map((r) => (
                  <button key={r} type="button" onClick={() => setInviteForm({ ...inviteForm, role: r })} data-testid={`invite-role-${r}`}
                    className={`px-3 py-2 rounded-lg text-[13px] border ${inviteForm.role === r ? "border-foreground bg-foreground text-background" : "border-border hover:bg-muted"}`}>{r}</button>
                ))}
              </div>
            </div>
            <p className="text-xs text-muted-foreground">We'll copy the invite link to your clipboard. In Phase 3 we'll email it automatically.</p>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setInviteOpen(false)}>Cancel</Button>
            <Button className="btn-tenant" onClick={sendInvite} disabled={!inviteForm.email} data-testid="invite-send-btn">Create invite</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
