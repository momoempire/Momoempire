import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Star, Check, X, Trash2 } from "lucide-react";

const STATUS_BADGE = {
  consent_pending: "bg-amber-100 text-amber-800",
  consent_received: "bg-sky-100 text-sky-800",
  admin_review: "bg-indigo-100 text-indigo-800",
  published: "bg-emerald-100 text-emerald-800",
  rejected: "bg-neutral-200 text-neutral-700",
};

export default function Testimonials() {
  const { t } = useTranslation();
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("");
  const [form, setForm] = useState({
    customer_name: "", customer_email: "", customer_phone: "", rating: 5, original_text: "",
  });
  const [loading, setLoading] = useState(false);

  async function load() {
    try {
      const url = filter ? `/testimonials?status=${filter}` : "/testimonials";
      const { data } = await api.get(url);
      setRows(data || []);
    } catch (e) { toast.error(errMessage(e)); }
  }

  // Reload only when the filter changes (load is recreated every render). The old inline
  // disable comment sat on the wrong line, so the warning still fired.
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  async function seed() {
    if (!form.customer_name || !form.original_text) return toast.error("Name + review text required");
    setLoading(true);
    try {
      await api.post("/testimonials/seed", { ...form, rating: 5 });
      toast.success("Consent request sent");
      setForm({ customer_name: "", customer_email: "", customer_phone: "", rating: 5, original_text: "" });
      load();
    } catch (e) { toast.error(errMessage(e)); }
    setLoading(false);
  }

  async function decide(id, decision) {
    try {
      await api.post(`/testimonials/${id}/decision`, { decision });
      toast.success(decision === "approve" ? "Published" : "Rejected");
      load();
    } catch (e) { toast.error(errMessage(e)); }
  }

  async function remove(id) {
    try {
      await api.delete(`/testimonials/${id}`);
      load();
    } catch (e) { toast.error(errMessage(e)); }
  }

  return (
    <div className="space-y-6" data-testid="testimonials-page">
      <div>
        <h1 className="text-3xl font-display tracking-tight flex items-center gap-2">
          <Star className="h-7 w-7 fill-amber-400 text-amber-400" /> {t("testimonials.title")}
        </h1>
        <p className="text-muted-foreground mt-1">{t("testimonials.sub")}</p>
      </div>

      <Card data-testid="testi-seed-card">
        <CardHeader><CardTitle className="text-base">{t("testimonials.seed_title")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <Label>{t("testimonials.seed_name")}</Label>
            <Input data-testid="testi-seed-name" value={form.customer_name} onChange={(e) => setForm({ ...form, customer_name: e.target.value })} />
          </div>
          <div>
            <Label>{t("testimonials.seed_email")}</Label>
            <Input data-testid="testi-seed-email" value={form.customer_email} onChange={(e) => setForm({ ...form, customer_email: e.target.value })} />
          </div>
          <div>
            <Label>{t("testimonials.seed_phone")}</Label>
            <Input data-testid="testi-seed-phone" value={form.customer_phone} onChange={(e) => setForm({ ...form, customer_phone: e.target.value })} />
          </div>
          <div className="md:col-span-3">
            <Label>{t("testimonials.seed_text")}</Label>
            <Textarea data-testid="testi-seed-text" rows={3} value={form.original_text} onChange={(e) => setForm({ ...form, original_text: e.target.value })} />
          </div>
          <div className="md:col-span-3 flex justify-end">
            <Button onClick={seed} disabled={loading} data-testid="testi-seed-btn">{t("testimonials.seed_add")}</Button>
          </div>
        </CardContent>
      </Card>

      <div className="flex gap-2 text-[12px]">
        {["", "consent_pending", "admin_review", "published", "rejected"].map((s) => (
          <button key={s} onClick={() => setFilter(s)}
                  data-testid={`testi-filter-${s || "all"}`}
                  className={`px-3 py-1 rounded-full border ${filter === s ? "bg-foreground text-background" : "bg-background text-foreground"}`}>
            {s === "" ? "All" : t(`testimonials.status_${s}`)}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <div className="text-center text-sm text-muted-foreground py-10" data-testid="testi-empty">{t("testimonials.none")}</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {rows.map((r) => (
            <Card key={r.id} data-testid={`testi-card-${r.id}`}>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <div className="font-medium">{r.customer_name}</div>
                  <span className={`text-[11px] px-2 py-1 rounded ${STATUS_BADGE[r.status] || "bg-neutral-100"}`}>
                    {t(`testimonials.status_${r.status}`)}
                  </span>
                </div>
                <div className="flex gap-0.5 mt-1">
                  {Array.from({ length: r.rating }).map((_, i) => (
                    <Star key={i} className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                  ))}
                </div>
              </CardHeader>
              <CardContent className="space-y-2 pt-0">
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{t("testimonials.polished")}</div>
                  <blockquote className="text-sm mt-1 italic border-l-2 border-foreground/30 pl-3">
                    "{r.polished_text || "—"}"
                  </blockquote>
                </div>
                <details>
                  <summary className="text-[11px] text-muted-foreground cursor-pointer">{t("testimonials.original")}</summary>
                  <p className="text-[12px] mt-1 text-muted-foreground">{r.original_text}</p>
                </details>
                <div className="flex justify-end gap-2 pt-2">
                  {r.status === "admin_review" && (
                    <>
                      <Button size="sm" variant="outline" onClick={() => decide(r.id, "reject")} data-testid={`testi-reject-${r.id}`}>
                        <X className="h-3 w-3 mr-1" /> {t("testimonials.reject")}
                      </Button>
                      <Button size="sm" onClick={() => decide(r.id, "approve")} data-testid={`testi-approve-${r.id}`}>
                        <Check className="h-3 w-3 mr-1" /> {t("testimonials.approve")}
                      </Button>
                    </>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => remove(r.id)} data-testid={`testi-delete-${r.id}`}>
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
