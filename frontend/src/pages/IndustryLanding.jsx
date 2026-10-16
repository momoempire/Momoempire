import Landing from "./Landing";
import { industryPage } from "../config/industries";

// One reusable template: the waitlist landing page with the industry's draft copy.
export default function IndustryLanding({ slug, lang = "en" }) {
  return <Landing industry={industryPage(slug, lang)} />;
}
