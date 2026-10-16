import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, errMessage } from "@/lib/api";

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  // undefined = checking, null = anon, object = user
  const [user, setUser] = useState(undefined);

  const refresh = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
      return data;
    } catch {
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    // CRITICAL: If returning from Google OAuth, let AuthCallback handle session exchange first.
    if (window.location.hash?.includes("session_id=")) {
      setUser(null);
      return;
    }
    refresh();
  }, [refresh]);

  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    if (data && data.requires_2fa) return data; // TOTP challenge next; no session yet
    setUser(data);
    return data;
  };

  const challenge2fa = async (two_fa_token, code, backup_code) => {
    const { data } = await api.post("/auth/2fa/challenge", { two_fa_token, code, backup_code });
    setUser(data);
    return data;
  };

  const register = async (payload) => {
    const { data } = await api.post("/auth/register", payload);
    setUser(data);
    return data;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (e) {
      /* ignore */
    }
    setUser(null);
  };

  return (
    <AuthCtx.Provider value={{ user, setUser, login, challenge2fa, register, logout, refresh, errMessage }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
