import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { House, Wrench, CalendarClock, Receipt, BellRing, Phone } from "lucide-react";

export default function HomeHubView() {
  const { token } = useParams();
  const [profile, setProfile] = useState(null);
  const [err, setErr] = useState(null);
  const [req, setReq] = useState({ service: "", notes: "", preferred_time: "" });

  useEffect(() => {
    api.get(`/api/home-hub/profile/${token}`)
      .then((r) => setProfile(r.data))
      .catch((e) => setErr(errMessage(e)));
  }, [token]);

  if (err) return <div className="min-h-screen grid place-items-center text-sm text-muted-foreground" data-testid="homehub-error">{err}</div>;
  if (!profile) return <div className="min-h-screen grid place-items-center text-sm text-muted-foreground">Loading…</div>;

  const home = profile.home || {};
  const history = profile.history || [];
  const upcoming = profile.upcoming || [];
  const equipment = home.equipment || [];
  const reminders = profile.reminders || [];
  const shop = profile.shop_contact || {};

  const submitRequest = async () => {
    try {
      await api.post(`/api/home-hub/request/${token}`, { ...req });
      toast.success("Request sent! We'll be in touch shortly.");
      setReq({ service: "", notes: "", preferred_time: "" });
    } catch (e) { toast.error(errMessage(e)); }
  };

  return (
    <div className="min-h-screen bg-background" data-testid="homehub-public-page">
      <header className="border-b border-border bg-card">
        <div className="max-w-3xl mx-auto px-4 py-6 flex items-center gap-3">
          <House className="h-8 w-8 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">{home.street || "My Home"}</h1>
            <p className="text-sm text-muted-foreground">{[home.city, home.state, home.zip].filter(Boolean).join(", ")}</p>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6 space-y-6">
        {upcoming.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold flex items-center gap-2 mb-3"><CalendarClock className="h-5 w-5" /> Upcoming visits</h2>
            <div className="space-y-2">
              {upcoming.map((v, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4">
                  <div className="font-medium">{v.title || v.service_type}</div>
                  <div className="text-sm text-muted-foreground">{v.scheduled_date || v.due_date}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {reminders.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold flex items-center gap-2 mb-3"><BellRing className="h-5 w-5" /> Maintenance reminders</h2>
            <div className="space-y-2">
              {reminders.map((r, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4 flex justify-between items-center">
                  <span>{r.text || r.title}</span>
                  {r.due && <Badge variant="outline">{r.due}</Badge>}
                </div>
              ))}
            </div>
          </section>
        )}

        {equipment.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold flex items-center gap-2 mb-3"><Wrench className="h-5 w-5" /> Your equipment</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {equipment.map((e, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4">
                  <div className="font-medium">{e.name || e.type}</div>
                  <div className="text-sm text-muted-foreground">{[e.brand, e.model].filter(Boolean).join(" ")}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        <section>
          <h2 className="text-lg font-semibold flex items-center gap-2 mb-3"><Receipt className="h-5 w-5" /> Service history</h2>
          {history.length === 0 ? (
            <p className="text-sm text-muted-foreground">No service history yet.</p>
          ) : (
            <div className="space-y-2">
              {history.map((h, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4">
                  <div className="flex justify-between items-start">
                    <div className="font-medium">{h.title || h.service_type || "Service visit"}</div>
                    <span className="text-xs text-muted-foreground">{h.date || h.completed_at}</span>
                  </div>
                  {h.notes && <p className="text-sm text-muted-foreground mt-1">{h.notes}</p>}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-border bg-card p-6">
          <h2 className="text-lg font-semibold mb-4">Request service</h2>
          <div className="space-y-3">
            <Input placeholder="What do you need? (e.g. AC not cooling)" value={req.service} onChange={(e) => setReq({ ...req, service: e.target.value })} />
            <Input placeholder="Preferred time (optional)" value={req.preferred_time} onChange={(e) => setReq({ ...req, preferred_time: e.target.value })} />
            <Textarea placeholder="Details (optional)" value={req.notes} onChange={(e) => setReq({ ...req, notes: e.target.value })} />
            <Button onClick={submitRequest} disabled={!req.service.trim()}>Send request</Button>
            {shop.phone && (
              <p className="text-sm text-muted-foreground flex items-center gap-2">
                <Phone className="h-4 w-4" /> Prefer to call? {shop.phone}
              </p>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
