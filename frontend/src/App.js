import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import ProtectedRoute from "@/components/ProtectedRoute";
import DashboardLayout from "@/components/layouts/DashboardLayout";
import AdminLayout from "@/components/layouts/AdminLayout";

// Public
import Landing from "@/pages/Landing";
import Pricing from "@/pages/Pricing";
import Estimate from "@/pages/Estimate";
import { Privacy, Terms } from "@/pages/Legal";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import ForgotPassword from "@/pages/ForgotPassword";
import ResetPassword from "@/pages/ResetPassword";
import Onboarding from "@/pages/Onboarding";
import PublicBusiness from "@/pages/PublicBusiness";
import PaymentSuccess from "@/pages/PaymentSuccess";
import PaymentCancel from "@/pages/PaymentCancel";
import CustomerPortalPublic from "@/pages/CustomerPortalPublic";
import InviteAccept from "@/pages/InviteAccept";
import ReferralLanding from "@/pages/ReferralLanding";
import ReviewPublic from "@/pages/ReviewPublic";
import AuthCallback from "@/pages/AuthCallback";
import TestimonialConsent from "@/pages/TestimonialConsent";

// Dashboard
import Home from "@/pages/dashboard/Home";
import AIEmployee from "@/pages/dashboard/AIEmployee";
import ServicesPage from "@/pages/dashboard/Services";
import Customers from "@/pages/dashboard/Customers";
import Leads from "@/pages/dashboard/Leads";
import Appointments from "@/pages/dashboard/Appointments";
import KnowledgeBase from "@/pages/dashboard/KnowledgeBase";
import BusinessAdvisor from "@/pages/dashboard/BusinessAdvisor";
import Analytics from "@/pages/dashboard/Analytics";
import Billing from "@/pages/dashboard/Billing";
import Usage from "@/pages/dashboard/Usage";
import Settings from "@/pages/dashboard/Settings";
import Calls from "@/pages/dashboard/Calls";
import Messages from "@/pages/dashboard/Messages";
import Reviews from "@/pages/dashboard/Reviews";
import Payments from "@/pages/dashboard/Payments";
import Website from "@/pages/dashboard/Website";
import CustomerPortal from "@/pages/dashboard/CustomerPortal";
import PhoneNumbers from "@/pages/dashboard/PhoneNumbers";
import Integrations from "@/pages/dashboard/Integrations";
import Automations from "@/pages/dashboard/Automations";
import SalesIntel from "@/pages/dashboard/SalesIntel";
import Growth from "@/pages/dashboard/Growth";
import RepeatScheduling from "@/pages/dashboard/RepeatScheduling";
import Testimonials from "@/pages/dashboard/Testimonials";

// Admin
import AdminOverview from "@/pages/admin/Overview";
import AdminTenants from "@/pages/admin/Tenants";
import AdminIndustries from "@/pages/admin/Industries";
import AdminCountries from "@/pages/admin/Countries";
import FeatureFlags from "@/pages/admin/FeatureFlags";
import SystemHealth from "@/pages/admin/SystemHealth";
import AdminAIQuality from "@/pages/admin/AIQuality";
import AdminPlans from "@/pages/admin/Plans";
import PlatformAnalytics from "@/pages/admin/PlatformAnalytics";

const AppShell = ({ children }) => <DashboardLayout>{children}</DashboardLayout>;
const AdminShell = ({ children }) => <AdminLayout>{children}</AdminLayout>;
const Protected = ({ children, ...rest }) => <ProtectedRoute {...rest}><AppShell>{children}</AppShell></ProtectedRoute>;
const AdminGuard = ({ children }) => <ProtectedRoute platformAdmin><AdminShell>{children}</AdminShell></ProtectedRoute>;

// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
function Router() {
  const location = useLocation();
  // Google OAuth returns to /auth/callback#session_id=... — handle before any protected route runs.
  // Exception: /invite?token=... has its own Google acceptance flow that reads the hash locally.
  if (location.hash?.includes("session_id=") && location.pathname !== "/invite") {
    return <AuthCallback />;
  }
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/estimate" element={<Estimate />} />
      <Route path="/privacy" element={<Privacy />} />
      <Route path="/terms" element={<Terms />} />
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route path="/b/:slug" element={<PublicBusiness />} />
      <Route path="/portal/:slug" element={<CustomerPortalPublic />} />
      <Route path="/invite" element={<InviteAccept />} />
      <Route path="/r/:code" element={<ReferralLanding />} />
      <Route path="/reviews/:token" element={<ReviewPublic />} />
      <Route path="/t/consent/:token" element={<TestimonialConsent />} />
      <Route path="/payment/success" element={<PaymentSuccess />} />
      <Route path="/payment/cancel" element={<PaymentCancel />} />

      <Route path="/onboarding" element={<ProtectedRoute><Onboarding /></ProtectedRoute>} />

      <Route path="/app" element={<Protected><Home /></Protected>} />
      <Route path="/app/ai-employee" element={<Protected><AIEmployee /></Protected>} />
      <Route path="/app/calls" element={<Protected><Calls /></Protected>} />
      <Route path="/app/messages" element={<Protected><Messages /></Protected>} />
      <Route path="/app/leads" element={<Protected><Leads /></Protected>} />
      <Route path="/app/customers" element={<Protected><Customers /></Protected>} />
      <Route path="/app/appointments" element={<Protected><Appointments /></Protected>} />
      <Route path="/app/services" element={<Protected><ServicesPage /></Protected>} />
      <Route path="/app/payments" element={<Protected><Payments /></Protected>} />
      <Route path="/app/website" element={<Protected><Website /></Protected>} />
      <Route path="/app/customer-portal" element={<Protected><CustomerPortal /></Protected>} />
      <Route path="/app/reviews" element={<Protected><Reviews /></Protected>} />
      <Route path="/app/analytics" element={<Protected><Analytics /></Protected>} />
      <Route path="/app/advisor" element={<Protected><BusinessAdvisor /></Protected>} />
      <Route path="/app/sales-intel" element={<Protected><SalesIntel /></Protected>} />
      <Route path="/app/growth" element={<Protected><Growth /></Protected>} />
      <Route path="/app/repeat" element={<Protected><RepeatScheduling /></Protected>} />
      <Route path="/app/testimonials" element={<Protected><Testimonials /></Protected>} />
      <Route path="/app/knowledge" element={<Protected><KnowledgeBase /></Protected>} />
      <Route path="/app/automations" element={<Protected><Automations /></Protected>} />
      <Route path="/app/integrations" element={<Protected><Integrations /></Protected>} />
      <Route path="/app/phone-numbers" element={<Protected><PhoneNumbers /></Protected>} />
      <Route path="/app/usage" element={<Protected><Usage /></Protected>} />
      <Route path="/app/billing" element={<Protected><Billing /></Protected>} />
      <Route path="/app/settings" element={<Protected><Settings /></Protected>} />

      <Route path="/admin" element={<AdminGuard><AdminOverview /></AdminGuard>} />
      <Route path="/admin/tenants" element={<AdminGuard><AdminTenants /></AdminGuard>} />
      <Route path="/admin/industries" element={<AdminGuard><AdminIndustries /></AdminGuard>} />
      <Route path="/admin/countries" element={<AdminGuard><AdminCountries /></AdminGuard>} />
      <Route path="/admin/feature-flags" element={<AdminGuard><FeatureFlags /></AdminGuard>} />
      <Route path="/admin/health" element={<AdminGuard><SystemHealth /></AdminGuard>} />
      <Route path="/admin/ai-quality" element={<AdminGuard><AdminAIQuality /></AdminGuard>} />
      <Route path="/admin/plans" element={<AdminGuard><AdminPlans /></AdminGuard>} />
      <Route path="/admin/analytics" element={<AdminGuard><PlatformAnalytics /></AdminGuard>} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Router />
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </AuthProvider>
  );
}

export default App;
