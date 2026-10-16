import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Trophy, Medal, Settings, Zap, Award, Clock, Sparkles, Flame } from "lucide-react";

const EVENT_TYPES = ["on_time", "5_star_review", "upsell", "photo_estimate", "no_callback"];
const EVENT_LABELS = {
  on_time: "On-time arrival",
  "5_star_review": "5-star review",
  upsell: "Upsell",
  photo_estimate: "Photo estimate",
  no_callback: "Callback-free job",
};
const PERIODS = ["week", "month", "all"];
const MEDAL = [
  { ring: "border-amber-300 bg-amber-50", text: "text-amber-600", icon: <Trophy className="h-6 w-6" /> },
  { ring: "border-slate-300 bg-slate-50", text: "text-slate-500", icon: <Medal className="h-6 w-6" /> },
  { ring: "border-orange-300 bg-orange-50", text: "text-orange-600", icon: <Medal className="h-6 w-6" /> },
];

function TechProfileModal({ techId, onClose }) {
  const { t } = useTranslation();
  const [profile, setProfile] = useState(null);
  useEffect(() => {
    api
      .get(`/api/gamification/tenants/techs/${encodeURIComponent(techId)}`)
      .then((r) => setProfile(r.data))
      .catch((e) => toast.error(errMessage(e)));
  }, [techId]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl max-h-[90vh] overflow-y-auto" data-testid="gamif-profile-modal">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">{profile?.tech_name || techId}</h2>
            <p className="text-sm text-muted-foreground">{t("gamif.profile_subtitle", "Points, badges & streaks")}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>✕</Button>
        </div>
        {!profile ? (
          <div className="text-muted-foreground">{t("gamif.loading", "Loading…")}</div>
        ) : (
          <div className="space-y-5">
            <div className="grid grid-cols-3 gap-2 text-center">
              {[
                [t("gamif.points_all", "All-time"), profile.points],
                [t("gamif.points_month", "This month"), profile.points_month],
                [t("gamif.points_week", "This week"), profile.points_week],
              ].map(([label, v]) => (
                <div key={label} className="rounded-xl border p-3">
                  <div className="text-2xl font-bold">{v}</div>
                  <div className="text-xs text-muted-foreground">{label}</div>
                </div>
              ))}
            </div>
            <div className="rounded-xl border p-3">
              <div className="flex items-center gap-2 text-sm font-medium mb-2">
                <Flame className="h-4 w-4 text-orange-500" /> {t("gamif.streaks", "Streaks")}
              </div>
              <div className="flex gap-4 text-sm">
                <span>
                  {t("gamif.current_streak", "Current")}: <strong>{profile.streaks.current_streak_days}</strong> {t("gamif.days", "days")}
                </span>
                <span>
                  {t("gamif.longest_streak", "Longest")}: <strong>{profile.streaks.longest_streak_days}</strong> {t("gamif.days", "days")}
                </span>
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2 text-sm font-medium mb-2">
                <Award className="h-4 w-4 text-amber-500" /> {t("gamif.badges", "Badges")} ({profile.badges.length})
              </div>
              {profile.badges.length === 0 ? (
                <div className="text-sm text-muted-foreground">{t("gamif.no_badges", "No badges yet — keep scoring!")}</div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {profile.badges.map((b) => (
                    <span key={b.badge_id} className="inline-flex items-center gap-1.5 rounded-full border bg-amber-50 px-3 py-1 text-sm" title={b.description}>
                      <span className="text-base">{b.icon}</span> {b.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div>
              <div className="text-sm font-medium mb-2">{t("gamif.recent_activity", "Recent activity")}</div>
              <div className="space-y-1.5">
                {profile.recent_events.map((e) => (
                  <div key={e.id} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
                    <div>
                      <span className="font-medium">{e.label}</span>
                      <span className="text-muted-foreground ml-2 text-xs">
                        {e.created_at ? new Date(e.created_at).toLocaleDateString() : ""}
                      </span>
                    </div>
                    <Badge className="bg-emerald-100 text-emerald-900 hover:bg-emerald-100">+{e.points}</Badge>
                  </div>
                ))}
                {profile.recent_events.length === 0 && (
                  <div className="text-sm text-muted-foreground">{t("gamif.no_events", "No scored events yet.")}</div>
                )}
              </div>
            </div>
          </div>
        )}
        <div className="flex justify-end mt-6">
          <Button variant="ghost" onClick={onClose}>{t("gamif.close", "Close")}</Button>
        </div>
      </div>
    </div>
  );
}

function RulesEditor() {
  const { t } = useTranslation();
  const [values, setValues] = useState({});
  const [defaults, setDefaults] = useState({});
  const [saving, setSaving] = useState(false);
  const load = useCallback(async () => {
    try {
      const r = await api.get("/gamification/tenants/rules");
      setValues(r.data.point_values || {});
      setDefaults(r.data.defaults || {});
    } catch (e) {
      toast.error(errMessage(e));
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const save = async () => {
    setSaving(true);
    try {
      const payload = {};
      for (const k of EVENT_TYPES) {
        const n = Number(values[k]);
        if (!(Number.isInteger(n) && n >= 0)) return toast.error(t("gamif.rules_invalid", "Point values must be whole numbers ≥ 0"));
        payload[k] = n;
      }
      const r = await api.post("/gamification/tenants/rules", { point_values: payload });
      setValues(r.data.point_values || {});
      toast.success(t("gamif.rules_saved", "Point values saved"));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="rounded-xl border p-5 max-w-2xl">
      <div className="flex items-center gap-2 mb-1">
        <Settings className="h-4 w-4" />
        <h3 className="font-semibold">{t("gamif.rules_title", "Point values")}</h3>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        {t("gamif.rules_subtitle", "Set how many points each achievement is worth. Applies to new events going forward.")}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {EVENT_TYPES.map((et) => (
          <div key={et}>
            <label className="text-sm font-medium">{EVENT_LABELS[et]}</label>
            <div className="flex items-center gap-2 mt-1">
              <Input
                type="number"
                min="0"
                step="1"
                value={values[et] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [et]: e.target.value }))}
                className="w-28"
                data-testid={`gamif-rule-${et}`}
              />
              {defaults[et] !== undefined && Number(values[et]) !== Number(defaults[et]) && (
                <span className="text-xs text-muted-foreground">{t("gamif.default", "default")}: {defaults[et]}</span>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4">
        <Button size="sm" onClick={save} disabled={saving} data-testid="gamif-rules-save">
          {saving ? t("gamif.saving", "Saving…") : t("gamif.save", "Save")}
        </Button>
      </div>
    </div>
  );
}

export default function Gamification() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canManage = user?.role === "owner" || user?.role === "admin" || user?.role === "platform_admin";
  const [tab, setTab] = useState("board");
  const [period, setPeriod] = useState("week");
  const [board, setBoard] = useState([]);
  const [badges, setBadges] = useState([]);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [profileTech, setProfileTech] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [boardRes, badgeRes, eventsRes] = await Promise.all([
        api.get("/gamification/tenants/leaderboard", { params: { period } }),
        api.get("/gamification/tenants/badges"),
        api.get("/gamification/tenants/events", { params: { limit: 50 } }),
      ]);
      setBoard(boardRes.data.entries || []);
      setBadges(badgeRes.data.badges || []);
      setEvents(eventsRes.data.events || []);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { load(); }, [load]);

  const podium = useMemo(() => board.slice(0, 3), [board]);
  const rest = useMemo(() => board.slice(3), [board]);

  const tabs = [
    ["board", t("gamif.tab_board", "Leaderboard"), <Trophy key="i" className="h-4 w-4 mr-1" />],
    ["badges", t("gamif.tab_badges", "Badges"), <Award key="i" className="h-4 w-4 mr-1" />],
    ["events", t("gamif.tab_events", "Activity"), <Zap key="i" className="h-4 w-4 mr-1" />],
    ...(canManage ? [["rules", t("gamif.tab_rules", "Point values"), <Settings key="i" className="h-4 w-4 mr-1" />]] : []),
  ];

  return (
    <div>
      <PageHeader
        title={t("gamif.title", "Team Gamification")}
        description={t("gamif.subtitle", "Celebrate the wins: on-time arrivals, 5-star reviews, upsells, photo estimates, and callback-free jobs earn points and badges.")}
        testId="gamif-header"
      />

      <div className="flex gap-1 mb-5 border-b">
        {tabs.map(([id, label, icon]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            data-testid={`gamif-tab-${id}`}
            className={`flex items-center px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
              tab === id ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {icon}{label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-muted-foreground">{t("gamif.loading", "Loading…")}</div>
      ) : tab === "board" ? (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <label className="text-sm text-muted-foreground">{t("gamif.period", "Period")}</label>
            <select
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="rounded-md border bg-card px-3 py-2 text-sm"
              data-testid="gamif-period"
            >
              {PERIODS.map((p) => (
                <option key={p} value={p}>{t(`gamif.period_${p}`, p === "week" ? "This week" : p === "month" ? "This month" : "All time")}</option>
              ))}
            </select>
          </div>
          {board.length === 0 ? (
            <div className="rounded-xl border bg-muted/40 p-8 text-center text-muted-foreground">
              <Sparkles className="h-6 w-6 mx-auto mb-2" />
              {t("gamif.empty_board", "No points on the board yet — the first win starts the race!")}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
                {podium.map((e, i) => (
                  <button
                    key={e.tech_id}
                    onClick={() => setProfileTech(e.tech_id)}
                    data-testid={`gamif-podium-${i + 1}`}
                    className={`rounded-xl border-2 ${MEDAL[i].ring} p-5 text-center hover:shadow-md transition-shadow`}
                  >
                    <div className={`flex justify-center mb-2 ${MEDAL[i].text}`}>{MEDAL[i].icon}</div>
                    <div className="font-semibold truncate">{e.tech_name}</div>
                    <div className="text-3xl font-bold mt-1">{e.points}</div>
                    <div className="text-xs text-muted-foreground">{t("gamif.points", "points")}</div>
                    <div className="mt-2 flex justify-center gap-2 text-xs text-muted-foreground">
                      <span>🏅 {e.badges}</span>
                      <span>⚡ {e.events}</span>
                    </div>
                  </button>
                ))}
              </div>
              {rest.length > 0 && (
                <div className="rounded-xl border overflow-hidden">
                  <table className="w-full text-sm" data-testid="gamif-table">
                    <thead className="bg-muted/60 text-left">
                      <tr>
                        <th className="px-4 py-2.5 font-medium">{t("gamif.rank", "Rank")}</th>
                        <th className="px-4 py-2.5 font-medium">{t("gamif.tech", "Tech")}</th>
                        <th className="px-4 py-2.5 font-medium text-right">{t("gamif.points", "Points")}</th>
                        <th className="px-4 py-2.5 font-medium text-right">{t("gamif.badges_col", "Badges")}</th>
                        <th className="px-4 py-2.5 font-medium text-right">{t("gamif.events_col", "Events")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rest.map((e) => (
                        <tr
                          key={e.tech_id}
                          className="border-t hover:bg-muted/30 cursor-pointer"
                          onClick={() => setProfileTech(e.tech_id)}
                          data-testid={`gamif-row-${e.tech_id}`}
                        >
                          <td className="px-4 py-2.5 font-semibold">#{e.rank}</td>
                          <td className="px-4 py-2.5 font-medium">{e.tech_name}</td>
                          <td className="px-4 py-2.5 text-right font-bold">{e.points}</td>
                          <td className="px-4 py-2.5 text-right">{e.badges}</td>
                          <td className="px-4 py-2.5 text-right text-muted-foreground">{e.events}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      ) : tab === "badges" ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {badges.map((b) => (
            <div key={b.id} className="rounded-xl border p-5 text-center" data-testid={`gamif-badge-${b.id}`}>
              <div className="text-4xl mb-2">{b.icon}</div>
              <div className="font-semibold">{b.name}</div>
              <div className="text-sm text-muted-foreground">{b.description}</div>
              <div className="mt-3">
                <Badge variant="outline">
                  {b.earned_count} {t("gamif.earned", "earned")}
                </Badge>
              </div>
            </div>
          ))}
        </div>
      ) : tab === "events" ? (
        <div className="rounded-xl border overflow-hidden max-w-3xl">
          <table className="w-full text-sm" data-testid="gamif-events">
            <thead className="bg-muted/60 text-left">
              <tr>
                <th className="px-4 py-2.5 font-medium">{t("gamif.tech", "Tech")}</th>
                <th className="px-4 py-2.5 font-medium">{t("gamif.event", "Achievement")}</th>
                <th className="px-4 py-2.5 font-medium text-right">{t("gamif.points", "Points")}</th>
                <th className="px-4 py-2.5 font-medium">{t("gamif.when", "When")}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className="border-t hover:bg-muted/30">
                  <td className="px-4 py-2.5 font-medium">{e.tech_name}</td>
                  <td className="px-4 py-2.5">{e.label}</td>
                  <td className="px-4 py-2.5 text-right">
                    <Badge className="bg-emerald-100 text-emerald-900 hover:bg-emerald-100">+{e.points}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {e.created_at ? new Date(e.created_at).toLocaleString() : ""}
                  </td>
                </tr>
              ))}
              {events.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">{t("gamif.no_events", "No scored events yet.")}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        canManage && <RulesEditor />
      )}

      {profileTech && <TechProfileModal techId={profileTech} onClose={() => setProfileTech(null)} />}
    </div>
  );
}
