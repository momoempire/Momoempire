import { useEffect } from "react";
import { useLocation } from "react-router-dom";

// EMP-WL-072: <link rel="canonical"> built from REACT_APP_SITE_URL (the same value site-meta.js
// validates at build time). It's per route, because the SPA serves one index.html for every
// path: a single static tag would mark /privacy and /terms as duplicates of "/". Query string
// and hash are dropped (token links, tracking params). Without REACT_APP_SITE_URL, no tag is
// added (TODO(Brann): domain).
export function canonicalHref(siteUrl, pathname) {
  const base = (siteUrl || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^/]+/i.test(base)) return "";
  const path = (pathname || "/").replace(/\/+$/, "") || "/";
  return `${base}${path}`;
}

export default function CanonicalLink() {
  const { pathname } = useLocation();
  useEffect(() => {
    const href = canonicalHref(process.env.REACT_APP_SITE_URL, pathname);
    let link = document.head.querySelector('link[rel="canonical"]');
    if (!href) {
      if (link) link.remove();
      return;
    }
    if (!link) {
      link = document.createElement("link");
      link.setAttribute("rel", "canonical");
      document.head.appendChild(link);
    }
    link.setAttribute("href", href);
  }, [pathname]);
  return null;
}
