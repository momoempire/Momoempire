import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { api, errMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

// Mirrors backend/password_policy.py (server is the source of truth).
export function passwordProblems(pw = "") {
  const out = [];
  if (pw.length < 12) out.push("At least 12 characters");
  if (!/[a-z]/.test(pw)) out.push("A lowercase letter");
  if (!/[A-Z]/.test(pw)) out.push("An uppercase letter");
  if (!/\d/.test(pw)) out.push("A digit");
  if (!/[^A-Za-z0-9]/.test(pw)) out.push("A symbol");
  return out;
}

export default function SetPassword() {
  const { user, setUser, logout } = useAuth();
  const navigate = useNavigate();
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [current, setCurrent] = useState("");
  const [loading, setLoading] = useState(false);
  const forced = !!user?.must_change_password;
  const problems = passwordProblems(pw);
  const mismatch = confirm.length > 0 && pw !== confirm;

  const submit = async (e) => {
    e.preventDefault();
    if (problems.length || pw !== confirm) return;
    setLoading(true);
    try {
      await api.post("/auth/set-password", {
        new_password: pw,
        current_password: current,
      });
      setUser({ ...user, must_change_password: false });
      toast.success("Password updated");
      navigate(user?.role === "platform_admin" ? "/admin" : "/app", { replace: true });
    } catch (err) {
      toast.error(errMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4" data-testid="set-password-form" aria-labelledby="set-password-title">
        <h1 id="set-password-title" className="text-2xl font-semibold">Set a new password</h1>
        {forced && (
          <p className="text-sm text-muted-foreground" role="status">
            For security, you must choose a new strong password before continuing.
            If you don't know your current password, log out and use "Forgot password".
          </p>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="current-password">Current password</Label>
          <Input id="current-password" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} data-testid="set-password-current" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-password">New password</Label>
          <Input id="new-password" type="password" autoComplete="new-password" required value={pw} onChange={(e) => setPw(e.target.value)} aria-describedby="password-rules" data-testid="set-password-new" />
          <ul id="password-rules" className="text-xs text-muted-foreground list-disc pl-5">
            {passwordProblems("").map((rule) => (
              <li key={rule} className={problems.includes(rule) ? "" : "text-emerald-600"}>{rule}</li>
            ))}
          </ul>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm-password">Confirm new password</Label>
          <Input id="confirm-password" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={mismatch} data-testid="set-password-confirm" />
          {mismatch && <p className="text-xs text-rose-600" role="alert">Passwords do not match</p>}
        </div>
        <Button type="submit" className="w-full" disabled={loading || !current || problems.length > 0 || pw !== confirm} data-testid="set-password-submit">
          {loading ? "Saving…" : "Save password"}
        </Button>
        <button type="button" className="text-sm underline text-muted-foreground" onClick={logout}>Log out</button>
      </form>
    </div>
  );
}
