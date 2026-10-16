import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Loader2, MapPin, Navigation, Clock } from "lucide-react";

// Public customer page: "tech's on the way" live ETA view.
// Backend: GET /api/public/eta/:token — returns ONLY tech first name, live
// lat/lng, ETA text, and job status. No customer data ever reaches this page.
export default function ETATracking() {
  const { token } = useParams();
  const { t } = useTranslation();
  const [eta, setEta] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await api.get(`/public/eta/${token}`);
      setEta(r.data);
      setError("");
    } catch (e) {
      setError(e?.response?.status === 404 ? t("tracking.eta_invalid_link") : errMessage(e));
    }
  }, [token, t]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000); // keep the ETA fresh
    return () => clearInterval(id);
  }, [load]);

  if (!eta && !error) {
    return (
      <div className="min-h-screen grid place-items-center text-sm text-muted-foreground" role="status">
        <Loader2 className="h-5 w-5 animate-spin" aria-label={t("common.loading")} />
      </div>
    );
  }
  if (!eta) {
    return (
      <main className="min-h-screen grid place-items-center p-6">
        <p role="alert" className="text-sm text-center max-w-sm">{error}</p>
      </main>
    );
  }

  const statusKey =
    eta.status === "on_site"
      ? "tracking.eta_status_on_site"
      : eta.status === "completed"
        ? "tracking.eta_status_completed"
        : "tracking.eta_status_en_route";
  const hasPos = eta.lat != null && eta.lng != null;
  const updatedAt = eta.location_updated_at ? new Date(eta.location_updated_at) : null;

  return (
    <main className="min-h-screen bg-muted/40 p-4 sm:p-6">
      <div className="mx-auto max-w-md">
        <Card>
          <CardHeader className="text-center pb-2">
            <CardTitle className="text-lg">{t("tracking.eta_title")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="text-center">
              <div className="mx-auto mb-2 grid h-14 w-14 place-items-center rounded-full bg-blue-100">
                <Navigation className="h-7 w-7 text-blue-700" />
              </div>
              <p className="text-base font-medium">
                {t("tracking.eta_heading", { name: eta.tech_first_name })}
              </p>
              <div className="mt-2">
                <Badge className="bg-orange-100 text-orange-900">{t(statusKey)}</Badge>
              </div>
            </div>

            <div className="rounded-xl bg-card border p-4 text-center">
              <div className="text-2xl font-semibold">{eta.eta_text}</div>
              <div className="mt-1 flex items-center justify-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                {hasPos ? (
                  updatedAt ? (
                    <span>
                      {t("tracking.updated_min_ago", {
                        n: Math.max(0, Math.round((Date.now() - updatedAt.getTime()) / 60000)),
                      })}
                    </span>
                  ) : (
                    <span>{t("tracking.live")}</span>
                  )
                ) : (
                  <span>{t("tracking.eta_no_position")}</span>
                )}
              </div>
            </div>

            {hasPos && (
              <a
                href={`https://www.google.com/maps?q=${encodeURIComponent(`${eta.lat},${eta.lng}`)}`}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-center gap-1.5 text-sm text-blue-700 underline decoration-dotted"
              >
                <MapPin className="h-4 w-4" />
                {t("tracking.view_map")}
              </a>
            )}

            <p className="text-center text-[11px] text-muted-foreground">{t("tracking.eta_auto_refresh")}</p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
