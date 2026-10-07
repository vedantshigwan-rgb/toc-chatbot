import { useState } from "react";
import { useAuth } from "../context/AuthContext";

export default function AuthScreen() {
  const {
    login,
    register,
    verifyRegistration,
    resendRegistrationOtp,
  } = useAuth();

  const [mode, setMode] = useState("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    if (busy) return;

    setBusy(true);
    setError("");
    setNotice("");

    try {
      if (mode === "login") {
        await login({ email: email.trim(), password });
        return;
      }

      if (mode === "register") {
        const response = await register({
          email: email.trim(),
          password,
          name,
        });
        setNotice(response?.message || "Verification code sent.");
        setMode("verify");
        return;
      }

      const response = await verifyRegistration({
        email: email.trim(),
        otp: otp.trim(),
      });
      setNotice(response?.message || "Email verified. You can now log in.");
      setPassword("");
      setOtp("");
      setMode("login");
    } catch (requestError) {
      setError(requestError.message || "The request could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  async function resendOtp() {
    if (busy || !email.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");

    try {
      const response = await resendRegistrationOtp({ email: email.trim() });
      setNotice(response?.message || "A new verification code was sent.");
    } catch (requestError) {
      setError(requestError.message || "Could not resend the verification code.");
    } finally {
      setBusy(false);
    }
  }

  const title =
    mode === "login"
      ? "Sign in"
      : mode === "register"
        ? "Create account"
        : "Verify email";

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">TOC Report Assistant</div>
        <h1>{title}</h1>
        <p className="auth-subtitle">
          {mode === "verify"
            ? `Enter the 6-digit code sent to ${email}.`
            : "Use your TOC account to continue."}
        </p>

        <form onSubmit={handleSubmit} className="auth-form">
          {mode === "register" && (
            <label>
              Name
              <input
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={100}
                autoComplete="name"
              />
            </label>
          )}

          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              disabled={mode === "verify"}
              autoComplete="email"
            />
          </label>

          {mode !== "verify" && (
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={mode === "register" ? 8 : 1}
                maxLength={128}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </label>
          )}

          {mode === "verify" && (
            <label>
              Verification code
              <input
                type="text"
                value={otp}
                onChange={(event) =>
                  setOtp(event.target.value.replace(/\D/g, "").slice(0, 6))
                }
                required
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                autoComplete="one-time-code"
              />
            </label>
          )}

          {error && <div className="auth-error">{error}</div>}
          {notice && <div className="auth-notice">{notice}</div>}

          <button type="submit" className="auth-primary" disabled={busy}>
            {busy
              ? "Please wait…"
              : mode === "login"
                ? "Sign in"
                : mode === "register"
                  ? "Send verification code"
                  : "Verify email"}
          </button>
        </form>

        <div className="auth-actions">
          {mode === "login" && (
            <button type="button" onClick={() => { setMode("register"); setError(""); setNotice(""); }}>
              Create an account
            </button>
          )}
          {mode === "register" && (
            <button type="button" onClick={() => { setMode("login"); setError(""); setNotice(""); }}>
              Back to sign in
            </button>
          )}
          {mode === "verify" && (
            <>
              <button type="button" onClick={resendOtp} disabled={busy}>
                Resend code
              </button>
              <button type="button" onClick={() => { setMode("login"); setError(""); }}>
                Back to sign in
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
