import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { Plus, Trash2, Check, Send } from "lucide-react";

const LineEditor = ({ lines, onChange }) => (
  <div className="space-y-2">
    {lines.map((l, i) => (
      <div key={i} className="grid grid-cols-12 gap-2 items-center">
        <Input className="col-span-6" value={l.description} placeholder="Description" onChange={(e) => { const n = [...lines]; n[i] = { ...n[i], description: e.target.value }; onChange(n); }} />
        <Input className="col-span-2" type="number" value={l.quantity} onChange={(e) => { const n = [...lines]; n[i] = { ...n[i], quantity: Number(e.target.value) }; onChange(n); }} />
        <Input className="col-span-3" type="number" step="0.01" value={l.unit_price} placeholder="$" onChange={(e) => { const n = [...lines]; n[i] = { ...n[i], unit_price: Number(e.target.value) }; onChange(n); }} />
        <Button variant="ghost" size="icon" className="col-span-1" onClick={() => onChange(lines.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
      </div>
    ))}
    <Button variant="outline" size="sm" onClick={() => onChange([...lines, { description: "", quantity: 1, unit_price: 0 }])}><Plus className="h-3 w-3 mr-1" />Add line</Button>
  </div>
);

export default function Payments() {
  const [estimates, setEstimates] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [estOpen, setEstOpen] = useState(false);
  const [invOpen, setInvOpen] = useState(false);
  const [estForm, setEstForm] = useState({ customer_name: "", customer_phone: "", title: "", lines: [{ description: "", quantity: 1, unit_price: 0 }], notes: "" });
  const [invForm, setInvForm] = useState({ customer_name: "", customer_phone: "", title: "", lines: [{ description: "", quantity: 1, unit_price: 0 }], notes: "" });

  const load = () => {
    api.get("/tenants/estimates").then((r) => setEstimates(r.data));
    api.get("/tenants/invoices").then((r) => setInvoices(r.data));
  };
  useEffect(() => { load(); }, []);

  const saveEstimate = async () => {
    try { await api.post("/tenants/estimates", estForm); setEstOpen(false); setEstForm({ customer_name: "", customer_phone: "", title: "", lines: [{ description: "", quantity: 1, unit_price: 0 }], notes: "" }); load(); toast.success("Estimate created"); }
    catch (e) { toast.error(errMessage(e)); }
  };
  const sendEstimate = async (id) => { try { await api.post(`/tenants/estimates/${id}/send`); load(); toast.success("Marked sent"); } catch (e) { toast.error(errMessage(e)); } };
  const delEstimate = async (id) => { if (!confirm("Delete?")) return; try { await api.delete(`/tenants/estimates/${id}`); load(); } catch (e) { toast.error(errMessage(e)); } };
  const copyEstimateLink = (e) => { navigator.clipboard.writeText(`${window.location.origin}/q/${e.public_token}`); toast.success("Public link copied"); };

  const saveInvoice = async () => {
    try { await api.post("/tenants/invoices", invForm); setInvOpen(false); setInvForm({ customer_name: "", customer_phone: "", title: "", lines: [{ description: "", quantity: 1, unit_price: 0 }], notes: "" }); load(); toast.success("Invoice created"); }
    catch (e) { toast.error(errMessage(e)); }
  };
  const markPaid = async (id) => { try { await api.post(`/tenants/invoices/${id}/mark-paid`); load(); toast.success("Marked paid"); } catch (e) { toast.error(errMessage(e)); } };
  const delInvoice = async (id) => { if (!confirm("Delete?")) return; try { await api.delete(`/tenants/invoices/${id}`); load(); } catch (e) { toast.error(errMessage(e)); } };

  return (
    <div data-testid="payments-page">
      <PageHeader eyebrow="Business" title="Payments" description="Estimates and invoices in one place. Stripe-powered when you're ready." />
      <Tabs defaultValue="estimates">
        <TabsList>
          <TabsTrigger value="estimates" data-testid="tab-estimates">Estimates ({estimates.length})</TabsTrigger>
          <TabsTrigger value="invoices" data-testid="tab-invoices">Invoices ({invoices.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="estimates">
          <div className="mb-4 flex justify-end"><Button className="btn-tenant" onClick={() => setEstOpen(true)} data-testid="estimate-add-btn"><Plus className="h-4 w-4 mr-1" />New estimate</Button></div>
          <div className="surface">
            {estimates.length === 0 ? <div className="p-10 text-center text-sm text-muted-foreground">No estimates yet.</div> : (
              <ul className="divide-y divide-border">
                {estimates.map((e) => (
                  <li key={e.id} className="flex items-center gap-5 px-6 py-4" data-testid={`estimate-row-${e.id}`}>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium">{e.title}</div>
                      <div className="text-xs text-muted-foreground">{e.customer_name} · {e.customer_phone || "—"}</div>
                    </div>
                    <Badge variant="secondary">{e.status}</Badge>
                    <div className="font-mono text-sm w-24 text-right">${Number(e.total || 0).toFixed(2)}</div>
                    <Button variant="outline" size="sm" onClick={() => copyEstimateLink(e)} data-testid={`estimate-link-${e.id}`}>Copy link</Button>
                    {e.status === "draft" && <Button variant="outline" size="sm" onClick={() => sendEstimate(e.id)} data-testid={`estimate-send-${e.id}`}><Send className="h-3 w-3 mr-1" />Send</Button>}
                    <Button variant="ghost" size="icon" onClick={() => delEstimate(e.id)}><Trash2 className="h-4 w-4" /></Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </TabsContent>

        <TabsContent value="invoices">
          <div className="mb-4 flex justify-end"><Button className="btn-tenant" onClick={() => setInvOpen(true)} data-testid="invoice-add-btn"><Plus className="h-4 w-4 mr-1" />New invoice</Button></div>
          <div className="surface">
            {invoices.length === 0 ? <div className="p-10 text-center text-sm text-muted-foreground">No invoices yet.</div> : (
              <ul className="divide-y divide-border">
                {invoices.map((v) => (
                  <li key={v.id} className="flex items-center gap-5 px-6 py-4" data-testid={`invoice-row-${v.id}`}>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium">{v.title}</div>
                      <div className="text-xs text-muted-foreground">{v.customer_name} · {v.customer_phone || "—"}</div>
                    </div>
                    <Badge variant={v.status === "paid" ? "default" : "secondary"}>{v.status}</Badge>
                    <div className="font-mono text-sm w-24 text-right">${Number(v.total || 0).toFixed(2)}</div>
                    {v.status !== "paid" && <Button variant="outline" size="sm" onClick={() => markPaid(v.id)} data-testid={`invoice-paid-${v.id}`}><Check className="h-3 w-3 mr-1" />Mark paid</Button>}
                    <Button variant="ghost" size="icon" onClick={() => delInvoice(v.id)}><Trash2 className="h-4 w-4" /></Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </TabsContent>
      </Tabs>

      <Dialog open={estOpen} onOpenChange={setEstOpen}>
        <DialogContent data-testid="estimate-modal">
          <DialogHeader><DialogTitle>New estimate</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5"><Label>Customer name</Label><Input value={estForm.customer_name} onChange={(e) => setEstForm({ ...estForm, customer_name: e.target.value })} data-testid="estimate-customer" /></div>
              <div className="space-y-1.5"><Label>Phone</Label><Input value={estForm.customer_phone} onChange={(e) => setEstForm({ ...estForm, customer_phone: e.target.value })} /></div>
            </div>
            <div className="space-y-1.5"><Label>Title</Label><Input value={estForm.title} onChange={(e) => setEstForm({ ...estForm, title: e.target.value })} data-testid="estimate-title" /></div>
            <div className="space-y-1.5"><Label>Lines</Label><LineEditor lines={estForm.lines} onChange={(lines) => setEstForm({ ...estForm, lines })} /></div>
            <div className="space-y-1.5"><Label>Notes</Label><Textarea rows={2} value={estForm.notes} onChange={(e) => setEstForm({ ...estForm, notes: e.target.value })} /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEstOpen(false)}>Cancel</Button>
            <Button className="btn-tenant" onClick={saveEstimate} disabled={!estForm.customer_name || !estForm.title} data-testid="estimate-save-btn">Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={invOpen} onOpenChange={setInvOpen}>
        <DialogContent data-testid="invoice-modal">
          <DialogHeader><DialogTitle>New invoice</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5"><Label>Customer name</Label><Input value={invForm.customer_name} onChange={(e) => setInvForm({ ...invForm, customer_name: e.target.value })} data-testid="invoice-customer" /></div>
              <div className="space-y-1.5"><Label>Phone</Label><Input value={invForm.customer_phone} onChange={(e) => setInvForm({ ...invForm, customer_phone: e.target.value })} /></div>
            </div>
            <div className="space-y-1.5"><Label>Title</Label><Input value={invForm.title} onChange={(e) => setInvForm({ ...invForm, title: e.target.value })} data-testid="invoice-title" /></div>
            <div className="space-y-1.5"><Label>Lines</Label><LineEditor lines={invForm.lines} onChange={(lines) => setInvForm({ ...invForm, lines })} /></div>
            <div className="space-y-1.5"><Label>Notes</Label><Textarea rows={2} value={invForm.notes} onChange={(e) => setInvForm({ ...invForm, notes: e.target.value })} /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setInvOpen(false)}>Cancel</Button>
            <Button className="btn-tenant" onClick={saveInvoice} disabled={!invForm.customer_name || !invForm.title} data-testid="invoice-save-btn">Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
