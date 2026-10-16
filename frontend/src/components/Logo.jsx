import { BRAND_NAME } from "@/config/brand";
// variant: "auto" (default) follows the light/dark theme via currentColor; "dark"/"light" are fixed inks.
export function Logo({ className = "", mark = true, variant = "auto" }) {
  const auto = variant === "auto";
  const inkMain = auto ? "currentColor" : variant === "dark" ? "#0A0A0A" : "#FFFFFF";
  const inkSub = auto ? "#3B82F6" : variant === "dark" ? "#2563EB" : "#9CA3FF";
  const tone = auto ? "text-[#0A0A0A] dark:text-white" : "";
  return (
    <div className={`inline-flex items-center gap-2 ${tone} ${className}`} data-testid="brand-logo">
      {mark && (
        <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden>
          <rect x="1" y="1" width="26" height="26" rx="7" stroke={inkMain} strokeWidth="1.6" />
          <rect x="7" y="7" width="14" height="14" rx="3" fill={inkMain} />
          <circle cx="14" cy="14" r="2.5" fill={inkSub} />
        </svg>
      )}
      <span className="font-display text-[18px] font-semibold tracking-tight" style={{ color: inkMain }}>
        {BRAND_NAME}
      </span>
    </div>
  );
}
