import { useTranslation } from "react-i18next";
import { Globe } from "lucide-react";
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

/**
 * Compact header widget. Switches i18n language instantly and — if the user
 * is logged in — persists the preference server-side so the AI receptionist
 * and emails use the same language.
 */
export default function LanguageSwitcher({ compact = false }) {
  const { i18n } = useTranslation();
  // Optional auth: the waitlist-only build has no AuthProvider (EMP-WL-026), so useAuth() is null.
  const user = useAuth()?.user;
  const current = (i18n.language || "en").slice(0, 2);

  async function change(lng) {
    try { await i18n.changeLanguage(lng); } catch {}
    try { localStorage.setItem("aiop_lang", lng); } catch {}
    if (user?.tenant_id) {
      try { await api.put("/tenants/me/language", { lang: lng }); } catch { /* silent */ }
    }
  }

  const label = current === "es" ? "ES" : "EN";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size={compact ? "sm" : "default"}
          className="gap-2"
          data-testid="language-switcher-btn"
          aria-label="Change language"
        >
          <Globe className="h-4 w-4" />
          <span className="text-[12px] font-medium">{label}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" data-testid="language-switcher-menu">
        <DropdownMenuItem onClick={() => change("en")} data-testid="lang-opt-en">
          English
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => change("es")} data-testid="lang-opt-es">
          Español
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
