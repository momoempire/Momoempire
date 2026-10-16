import { Link, NavLink, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Logo } from "@/components/Logo";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import ThemeToggle from "@/components/ThemeToggle";
import { useTranslation } from "react-i18next";
import {
  Home, Bot, PhoneCall, MessageSquare, Target, Users, CalendarClock,
  Wrench, CreditCard, Globe, UserRound, Star, LineChart, Compass,
  BookOpenText, Workflow, Plug, PhoneForwarded, Gauge, Receipt, Settings,
  LogOut, ShieldCheck, Rocket, Repeat, ClipboardList, BookOpen, Camera, Megaphone, PhoneOutgoing, TrendingUp,
  Headset, CalendarCheck, Truck, HardHat, Clock, Package, MapPin, Code2, ClipboardCheck,
  Trophy, Video, PackagePlus, HandCoins, House,
  Zap, HeartPulse, Wallet, BadgeCheck, Star, Sparkles,
  AlertTriangle, FileText, LifeBuoy, Activity, Sparkle, MailOpen, BrainCircuit, Megaphone,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

import { BRAND_NAME } from "@/config/brand";
const GROUPS = [
  {
    label: "nav.workspace",
    items: [
      { to: "/app", icon: Home, label: "nav.home", end: true, testId: "nav-home" },
      { to: "/app/ai-employee", icon: Bot, label: "nav.ai_employee", testId: "nav-ai-employee" },
    ],
  },
  {
    label: "nav.communications",
    items: [
      { to: "/app/calls", icon: PhoneCall, label: "nav.calls", testId: "nav-calls" },
      { to: "/app/voice", icon: PhoneOutgoing, label: "nav.voice", testId: "nav-voice" },
      { to: "/app/call-coaching", icon: Headset, label: "nav.call_coaching", testId: "nav-call-coaching" },
      { to: "/app/messages", icon: MessageSquare, label: "nav.messages", testId: "nav-messages" },
    ],
  },
  {
    label: "nav.business",
    items: [
      { to: "/app/leads", icon: Target, label: "nav.leads", testId: "nav-leads" },
      { to: "/app/customers", icon: Users, label: "nav.customers", testId: "nav-customers" },
      { to: "/app/appointments", icon: CalendarClock, label: "nav.appointments", testId: "nav-appointments" },
      { to: "/app/dispatch", icon: ClipboardList, label: "nav.dispatch", testId: "nav-dispatch" },
      { to: "/app/ai-dispatcher", icon: Bot, label: "nav.ai_dispatcher", testId: "nav-ai-dispatcher" },
      { to: "/app/repeat", icon: Repeat, label: "nav.repeat", testId: "nav-repeat" },
      { to: "/app/services", icon: Wrench, label: "nav.services", testId: "nav-services" },
      { to: "/app/pricebook", icon: BookOpen, label: "nav.pricebook", testId: "nav-pricebook" },
      { to: "/app/trade-packs", icon: Package, label: "nav.trade_packs", testId: "nav-trade-packs" },
      { to: "/app/price-intel", icon: LineChart, label: "nav.price_intel", testId: "nav-price-intel" },
      { to: "/app/photo-estimates", icon: Camera, label: "nav.photo_estimates", testId: "nav-photo-estimates" },
      { to: "/app/video-estimates", icon: Video, label: "nav.video_estimates", testId: "nav-video-estimates" },
      { to: "/app/home-hub", icon: House, label: "nav.home_hub", testId: "nav-home-hub" },
      { to: "/app/parts-ordering", icon: PackagePlus, label: "nav.parts_ordering", testId: "nav-parts-ordering" },
      { to: "/app/ai-collections", icon: HandCoins, label: "nav.ai_collections", testId: "nav-ai-collections" },
      { to: "/app/maintenance", icon: CalendarClock, label: "nav.maintenance", testId: "nav-maintenance" },
      { to: "/app/gamification", icon: Trophy, label: "nav.gamification", testId: "nav-gamification" },
      { to: "/app/zero-touch", icon: Sparkles, label: "nav.zero_touch", testId: "nav-zero-touch" },
      { to: "/app/dynamic-pricing", icon: Zap, label: "nav.dynamic_pricing", testId: "nav-dynamic-pricing" },
      { to: "/app/self-healing", icon: HeartPulse, label: "nav.self_healing", testId: "nav-self-healing" },
      { to: "/app/cash-flow", icon: Wallet, label: "nav.cash_flow", testId: "nav-cash-flow" },
      { to: "/app/compliance", icon: BadgeCheck, label: "nav.compliance", testId: "nav-compliance" },
      { to: "/app/reputation", icon: Star, label: "nav.reputation", testId: "nav-reputation" },
      { to: "/app/failure-alerts", icon: AlertTriangle, label: "nav.failure_alerts", testId: "nav-failure-alerts" },
      { to: "/app/home-reports", icon: FileText, label: "nav.home_reports", testId: "nav-home-reports" },
      { to: "/app/home-profiles", icon: House, label: "nav.home_profiles", testId: "nav-home-profiles" },
      { to: "/app/support-tickets", icon: LifeBuoy, label: "nav.support_tickets", testId: "nav-support-tickets" },
      { to: "/app/platform-status", icon: Activity, label: "nav.platform_status", testId: "nav-platform-status" },
      { to: "/app/support-ai", icon: Sparkle, label: "nav.support_ai", testId: "nav-support-ai" },
      { to: "/app/inbox", icon: MailOpen, label: "nav.inbox", testId: "nav-inbox" },
      { to: "/app/conversation-intel", icon: BrainCircuit, label: "nav.conversation_intel", testId: "nav-conversation-intel" },
      { to: "/app/outreach", icon: Megaphone, label: "nav.outreach", testId: "nav-outreach" },
      { to: "/app/onboarding", icon: Rocket, label: "nav.onboarding", testId: "nav-onboarding" },
      { to: "/app/equipment", icon: Wrench, label: "nav.equipment", testId: "nav-equipment" },
      { to: "/app/payments", icon: CreditCard, label: "nav.payments", testId: "nav-payments" },
      { to: "/app/website", icon: Globe, label: "nav.website", testId: "nav-website" },
      { to: "/app/customer-portal", icon: UserRound, label: "nav.customer_portal", testId: "nav-customer-portal" },
      { to: "/app/reviews", icon: Star, label: "nav.reviews", testId: "nav-reviews" },
      { to: "/app/testimonials", icon: Star, label: "nav.testimonials", testId: "nav-testimonials" },
      { to: "/app/triage", icon: CalendarCheck, label: "nav.triage", testId: "nav-triage" },
      { to: "/app/warranty", icon: ShieldCheck, label: "nav.warranty", testId: "nav-warranty" },
      { to: "/app/supplier-pricing", icon: Truck, label: "nav.supplier_pricing", testId: "nav-supplier-pricing" },
      { to: "/app/subcontractors", icon: HardHat, label: "nav.subcontractors", testId: "nav-subcontractors" },
      { to: "/app/time-tracking", icon: Clock, label: "nav.time_tracking", testId: "nav-time-tracking" },
      { to: "/app/inventory", icon: Package, label: "nav.inventory", testId: "nav-inventory" },
      { to: "/app/locations", icon: MapPin, label: "nav.locations", testId: "nav-locations" },
      { to: "/app/checklists", icon: ClipboardCheck, label: "nav.checklists", testId: "nav-checklists" },
    ],
  },
  {
    label: "nav.intelligence",
    items: [
      { to: "/app/analytics", icon: LineChart, label: "nav.analytics", testId: "nav-analytics" },
      { to: "/app/profitability", icon: Gauge, label: "nav.profitability", testId: "nav-profitability" },
      { to: "/app/advisor", icon: Compass, label: "nav.advisor", testId: "nav-advisor" },
      { to: "/app/sales-intel", icon: Target, label: "nav.sales_intel", testId: "nav-sales-intel" },
      { to: "/app/growth", icon: Rocket, label: "nav.growth", testId: "nav-growth" },
      { to: "/app/revenue", icon: TrendingUp, label: "nav.revenue", testId: "nav-revenue" },
      { to: "/app/marketing", icon: Megaphone, label: "nav.marketing", testId: "nav-marketing" },
      { to: "/app/knowledge", icon: BookOpenText, label: "nav.knowledge", testId: "nav-knowledge" },
    ],
  },
  {
    label: "nav.setup",
    items: [
      { to: "/app/automations", icon: Workflow, label: "nav.automations", testId: "nav-automations" },
      { to: "/app/integrations", icon: Plug, label: "nav.integrations", testId: "nav-integrations" },
      { to: "/app/phone-numbers", icon: PhoneForwarded, label: "nav.phone_numbers", testId: "nav-phone-numbers" },
      { to: "/app/usage", icon: Gauge, label: "nav.usage", testId: "nav-usage" },
      { to: "/app/developers", icon: Code2, label: "nav.developers", testId: "nav-developers" },
      { to: "/app/billing", icon: Receipt, label: "nav.billing", testId: "nav-billing" },
      { to: "/app/settings", icon: Settings, label: "nav.settings", testId: "nav-settings" },
    ],
  },
];

export default function DashboardLayout({ children }) {
  const { user, logout } = useAuth();
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const [tenant, setTenant] = useState(null);

  useEffect(() => {
    if (!user?.tenant_id) return;
    api.get("/tenants/me").then((r) => {
      setTenant(r.data);
      // Hydrate language preference from server-side tenant/user if set.
      const serverLang = r.data?.lang || user?.lang;
      if (serverLang && serverLang !== i18n.language) {
        i18n.changeLanguage(serverLang).catch(() => {});
      }
    }).catch(() => {});
  }, [user?.tenant_id, location.pathname, i18n, user?.lang]);

  // Apply tenant branding tokens to CSS vars
  // Same deps as before (the two colors), read into locals so the hook lint can see them.
  const brandPrimary = tenant?.branding?.primary_color;
  const brandAccent = tenant?.branding?.accent_color;
  useEffect(() => {
    if (brandPrimary) document.documentElement.style.setProperty("--tenant-primary", brandPrimary);
    if (brandAccent) document.documentElement.style.setProperty("--tenant-accent", brandAccent);
  }, [brandPrimary, brandAccent]);

  const needsOnboarding = user?.tenant_id && tenant && !tenant.onboarding_complete;

  return (
    <div className="min-h-screen flex bg-background">
      <aside className="w-[260px] shrink-0 border-r border-border bg-card flex flex-col sticky top-0 h-screen" data-testid="dashboard-sidebar">
        <div className="px-5 py-5 border-b border-border">
          <Link to="/app"><Logo /></Link>
          <div className="mt-3 text-[11px] overline">Workspace</div>
          <div className="font-display text-[15px] leading-tight mt-1 truncate" title={tenant?.name}>
            {tenant?.name || "Your business"}
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-5">
          {GROUPS.map((g) => (
            <div key={g.label}>
              <div className="px-2 overline mb-2">{t(g.label)}</div>
              <ul className="space-y-0.5">
                {g.items.map((it) => {
                  const Icon = it.icon;
                  return (
                    <li key={it.to}>
                      <NavLink
                        to={it.to}
                        end={it.end}
                        data-testid={it.testId}
                        className={({ isActive }) =>
                          [
                            "group flex items-center gap-3 rounded-lg px-3 py-2 text-[13px]",
                            "transition-colors",
                            isActive
                              ? "bg-foreground text-background"
                              : "text-foreground/80 hover:bg-muted",
                          ].join(" ")
                        }
                      >
                        <Icon className="h-4 w-4 shrink-0" />
                        <span className="truncate">{t(it.label)}</span>
                      </NavLink>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
        <div className="p-3 border-t border-border">
          {user?.role === "platform_admin" && (
            <Link to="/admin" className="block mb-2">
              <Button variant="outline" className="w-full justify-start gap-2" data-testid="go-admin-btn">
                <ShieldCheck className="h-4 w-4" /> Platform Admin
              </Button>
            </Link>
          )}
          <div className="flex items-center gap-3 px-2 py-2">
            <Avatar className="h-8 w-8">
              {user?.picture && <img src={user.picture} alt="" className="h-full w-full object-cover" />}
              <AvatarFallback className="bg-foreground text-background text-[11px]">
                {(user?.name || user?.email || "?").slice(0, 2).toUpperCase()}
              </AvatarFallback>
            </Avatar>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-medium truncate">{user?.name}</div>
              <div className="text-[11px] text-muted-foreground truncate">{user?.email}</div>
            </div>
            <Button variant="ghost" size="icon" onClick={logout} data-testid="logout-btn" aria-label="Log out">
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </aside>
      <main className="flex-1 min-w-0">
        <div className="flex items-center justify-end border-b border-border px-6 py-2 bg-card">
          <LanguageSwitcher compact />
          <ThemeToggle lang={(i18n.language || "en").slice(0, 2)} />
        </div>
        {needsOnboarding && (
          <div className="border-b border-amber-200/70 bg-amber-50 text-amber-900 px-6 py-2 text-[13px] flex items-center justify-between">
            <span data-testid="onboarding-banner">Finish setting up your {BRAND_NAME} to unlock the full dashboard.</span>
            <Link to="/onboarding" className="font-medium underline underline-offset-4" data-testid="resume-onboarding-link">Resume setup</Link>
          </div>
        )}
        <div className="px-8 py-8 max-w-[1400px]">{children}</div>
      </main>
    </div>
  );
}
