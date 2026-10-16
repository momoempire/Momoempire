import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Phone, Mail, Clock, CalendarCheck, Sparkles, MessageSquare } from "lucide-react";

import { BRAND_NAME } from "@/config/brand";
export default function CustomerPortalPublic() {
  const { slug } = useParams();
  const [biz, setBiz] = useState(null);
  const [err, setErr] = useState(null);
  const [reqOpen, setReqOpen] = useState(false);
  const [bookOpen, setBookOpen] = useState(false);
  const [req, setReq] = useState({ name: "", phone: "", email: "", service: "", preferred_time: "", notes: "" });
  const defaultStart = new Date(Date.now() + 24 * 3600 * 1000).toISOString().slice(0, 16);
  const [book, setBook] = useState({ customer_name: "", customer_phone: "", service_id: "", start_at: defaultStart, notes: "" });

  useEffect(() => { api.get(`/portal/${slug}`).then((r) => setBiz(r.data)).catch((e) => setErr(errMessage(e))); }, [slug]);

  useEffect(() => {
    if (biz?.branding?.primary_color) {
      document.documentElement.style.setProperty("--tenant-primary", biz.branding.primary_color);
    }
  }, [biz?.branding?.primary_color]);

  if (err) return <div className="min-h-screen grid place-items-center text-sm text-muted-foreground">{err}</div>;
  if (!biz) return <div className="min-h-screen grid place-items-center text-sm text-muted-foreground">Loading…</div>;

  const submitRequest = async () => {
    try {
      await api.post(`/portal/${slug}/service-request`, { ...req, email: req.email || "" });
      toast.success("Request sent! We'll be in touch shortly.");
      setReqOpen(false); setReq({ name: "", phone: "", email: "", service: "", preferred_time: "", notes: "" });
    } catch (e) { toast.error(errMessage(e)); }
  };
  const submitBooking = async () => {
    try {
      await api.post(`/portal/${slug}/book`, {
        customer_name: book.customer_name, customer_phone: book.customer_phone,
        service_id: book.service_id || null,
        start_at: new Date(book.start_at).toISOString(),
        notes: book.notes,
      });
      toast.success("Appointment booked!");
      setBookOpen(false); setBook({ customer_name: "", customer_phone: "", service_id: "", start_at: defaultStart, notes: "" });
    } catch (e) { toast.error(errMessage(e)); }
  };

  const brand = biz.branding || {};
  const name = brand.display_name || biz.name;

  return (
    <div className="min-h-screen bg-background" data-testid="public-portal-page">
      <header className="border-b border-border" style={{ background: brand.primary_color || undefined }}>
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            {brand.logo_url ? <img src={brand.logo_url} alt="" className="h-7 w-7 rounded" /> : <div className="h-7 w-7 rounded bg-white/20" />}
            <span className="font-display text-lg text-white tracking-tight">{name}</span>
          </div>
          <Badge className="bg-white/10 text-white/80 border-white/15 hover:bg-white/10">Customer portal</Badge>
        </div>
      </header>

      <section className="max-w-5xl mx-auto px-6 py-14">
        <h1 className="font-display text-5xl md:text-6xl tracking-tight">Welcome.</h1>
        {biz.description && <p className="text-lg text-muted-foreground mt-4 max-w-2xl">{biz.description}</p>}
        <div className="mt-8 flex flex-wrap gap-3">
          <Button className="btn-tenant h-11 px-6" onClick={() => setBookOpen(true)} data-testid="portal-book-btn"><CalendarCheck className="h-4 w-4 mr-2" />Book appointment</Button>
          <Button variant="outline" className="h-11 px-6" onClick={() => setReqOpen(true)} data-testid="portal-request-btn"><MessageSquare className="h-4 w-4 mr-2" />Request service</Button>
          {biz.contact_phone && <Button variant="outline" className="h-11 px-6" asChild><a href={`tel:${biz.contact_phone}`}><Phone className="h-4 w-4 mr-2" />Call us</a></Button>}
        </div>

        <div className="grid md:grid-cols-3 gap-5 mt-16">
          <div className="surface p-6">
            <div className="overline mb-2 flex items-center gap-1"><Sparkles className="h-3 w-3" /> AI receptionist</div>
            <div className="font-display text-2xl">{biz.ai_employee?.name || "Alex"}</div>
            <p className="text-sm text-muted-foreground mt-2">Always on. Answers questions, books jobs, takes messages.</p>
          </div>
          <div className="surface p-6">
            <div className="overline mb-2 flex items-center gap-1"><Clock className="h-3 w-3" /> Hours</div>
            <ul className="text-sm space-y-1">
              {biz.hours && Object.entries(biz.hours).map(([d, h]) => (
                <li key={d} className="flex justify-between"><span className="capitalize text-muted-foreground">{d}</span><span>{h}</span></li>
              ))}
            </ul>
          </div>
          <div className="surface p-6">
            <div className="overline mb-2">Contact</div>
            {biz.contact_phone && <div className="flex items-center gap-2 text-sm mt-1"><Phone className="h-3 w-3 text-muted-foreground" />{biz.contact_phone}</div>}
            {biz.contact_email && <div className="flex items-center gap-2 text-sm mt-1"><Mail className="h-3 w-3 text-muted-foreground" />{biz.contact_email}</div>}
            {biz.address?.city && <div className="text-sm text-muted-foreground mt-1">{biz.address.city}{biz.address.state ? `, ${biz.address.state}` : ""}</div>}
          </div>
        </div>

        {biz.services?.length > 0 && (
          <div className="mt-16">
            <div className="overline mb-3">Services</div>
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
              {biz.services.map((s) => (
                <div key={s.id} className="surface p-5 lift" data-testid={`portal-service-${s.id}`}>
                  <div className="font-medium">{s.name}</div>
                  <div className="text-xs text-muted-foreground mt-1 line-clamp-2">{s.description || "—"}</div>
                  <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground font-mono">
                    <span>{s.duration_minutes}min</span>
                    <span>{s.price > 0 ? `$${s.price}` : "Quote"}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {biz.faqs?.length > 0 && (
          <div className="mt-16">
            <div className="overline mb-3">FAQ</div>
            <div className="grid md:grid-cols-2 gap-4">
              {biz.faqs.map((f, i) => (
                <div key={i} className="surface p-5">
                  <div className="font-medium">{f.question}</div>
                  <p className="text-sm text-muted-foreground mt-2">{f.answer}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      <footer className="border-t border-border py-6">
        <div className="max-w-5xl mx-auto px-6 flex items-center justify-between flex-wrap gap-4 text-xs text-muted-foreground">
          <span>© {new Date().getFullYear()} {name}</span>
          <span>Powered by {BRAND_NAME}</span>
        </div>
      </footer>

      <Dialog open={bookOpen} onOpenChange={setBookOpen}>
        <DialogContent data-testid="portal-book-modal">
          <DialogHeader><DialogTitle>Book an appointment</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5"><Label>Your name</Label><Input value={book.customer_name} onChange={(e) => setBook({ ...book, customer_name: e.target.value })} data-testid="portal-book-name" /></div>
              <div className="space-y-1.5"><Label>Phone</Label><Input value={book.customer_phone} onChange={(e) => setBook({ ...book, customer_phone: e.target.value })} data-testid="portal-book-phone" /></div>
            </div>
            <div className="space-y-1.5">
              <Label>Service</Label>
              <select className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm" value={book.service_id} onChange={(e) => setBook({ ...book, service_id: e.target.value })} data-testid="portal-book-service">
                <option value="">—</option>
                {biz.services.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <div className="space-y-1.5"><Label>Preferred time</Label><Input type="datetime-local" value={book.start_at} onChange={(e) => setBook({ ...book, start_at: e.target.value })} data-testid="portal-book-time" /></div>
            <div className="space-y-1.5"><Label>Notes</Label><Textarea rows={2} value={book.notes} onChange={(e) => setBook({ ...book, notes: e.target.value })} /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setBookOpen(false)}>Cancel</Button>
            <Button className="btn-tenant" onClick={submitBooking} disabled={!book.customer_name || !book.customer_phone} data-testid="portal-book-submit">Book</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={reqOpen} onOpenChange={setReqOpen}>
        <DialogContent data-testid="portal-request-modal">
          <DialogHeader><DialogTitle>Request service</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5"><Label>Your name</Label><Input value={req.name} onChange={(e) => setReq({ ...req, name: e.target.value })} data-testid="portal-req-name" /></div>
              <div className="space-y-1.5"><Label>Phone</Label><Input value={req.phone} onChange={(e) => setReq({ ...req, phone: e.target.value })} data-testid="portal-req-phone" /></div>
            </div>
            <div className="space-y-1.5"><Label>Email (optional)</Label><Input type="email" value={req.email} onChange={(e) => setReq({ ...req, email: e.target.value })} /></div>
            <div className="space-y-1.5"><Label>Service needed</Label><Input value={req.service} onChange={(e) => setReq({ ...req, service: e.target.value })} /></div>
            <div className="space-y-1.5"><Label>Preferred time</Label><Input value={req.preferred_time} onChange={(e) => setReq({ ...req, preferred_time: e.target.value })} placeholder="Thursday afternoon" /></div>
            <div className="space-y-1.5"><Label>Describe what's going on</Label><Textarea rows={3} value={req.notes} onChange={(e) => setReq({ ...req, notes: e.target.value })} /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setReqOpen(false)}>Cancel</Button>
            <Button className="btn-tenant" onClick={submitRequest} disabled={!req.name || !req.phone} data-testid="portal-req-submit">Send request</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
