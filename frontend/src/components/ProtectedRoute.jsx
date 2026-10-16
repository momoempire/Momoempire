import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Loader2 } from "lucide-react";

export default function ProtectedRoute({ children, roles, platformAdmin }) {
  const { user } = useAuth();
  const location = useLocation();

  if (user === undefined) {
    return (
      <div className="h-screen w-full grid place-items-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (user === null) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  // Forced first-login password change: block every protected page until done.
  if (user.must_change_password && location.pathname !== "/set-password") {
    return <Navigate to="/set-password" replace />;
  }
  if (platformAdmin && user.role !== "platform_admin") {
    return <Navigate to="/app" replace />;
  }
  if (roles && !roles.includes(user.role)) {
    return <Navigate to="/app" replace />;
  }
  return children;
}
