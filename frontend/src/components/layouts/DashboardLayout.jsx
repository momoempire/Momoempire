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
  LogOut, ShieldCheck, Rocket, Repeat, ClipboardList, BookOpen, Camera, Megaphone,
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
      { to: "/app/photo-estimates", icon: Camera, label: "nav.photo_estimates", testId: "nav-photo-estimates" },
      { to: "/app/onboarding", icon: Rocket, label: "nav.onboarding", testId: "nav-onboarding" },
      { to: "/app/equipment", icon: Wrench, label: "nav.equipment", testId: "nav-equipment" },
      { to: "/app/payments", icon: CreditCard, label: "nav.payments", testId: "nav-payments" },
      { to: "/app/website", icon: Globe, label: "nav.website", testId: "nav-website" },
      { to: "/app/customer-portal", icon: UserRound, label: "nav.customer_portal", testId: "nav-customer-portal" },
      { to: "/app/reviews", icon: Star, label: "nav.reviews", testId: "nav-reviews" },
      { to: "/app/testimonials", icon: Star, label: "nav.testimonials", testId: "nav-testimonials" },
    ],
  },
  {
    label: "nav.intelligence",
    items: [
      { to: "/app/analytics", icon: LineChart, label: "nav.analytics", testId: "nav-analytics" },
      { to: "/app/advisor", icon: Compass, label: "nav.advisor", testId: "nav-advisor" },
      { to: "/app/sales-intel", icon: Target, label: "nav.sales_intel", testId: "nav-sales-intel" },
      { to: "/app/growth", icon: Rocket, label: "nav.growth", testId: "nav-growth" },
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
