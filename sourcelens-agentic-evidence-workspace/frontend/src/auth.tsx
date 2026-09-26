import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;
type GoogleCredentialResponse = { credential?: string };
type GoogleIdentity = { accounts: { id: {
  initialize: (config: { client_id: string; callback: (response: GoogleCredentialResponse) => void; auto_select?: boolean }) => void;
  renderButton: (element: HTMLElement, options: Record<string, string | number>) => void;
  disableAutoSelect: () => void;
} } };
declare global { interface Window { google?: GoogleIdentity } }

let googleScript: Promise<GoogleIdentity> | undefined;
let credentialHandler: ((response: GoogleCredentialResponse) => void) | undefined;
let googleInitialized = false;

function loadGoogleIdentity(): Promise<GoogleIdentity> {
  if (window.google) return Promise.resolve(window.google);
  if (!googleScript) {
    googleScript = new Promise((resolve, reject) => {
      const existing = document.querySelector<HTMLScriptElement>('script[src="https://accounts.google.com/gsi/client"]');
      const script = existing || document.createElement("script");
      const loaded = () => window.google ? resolve(window.google) : reject(new Error("Google sign-in did not load."));
      script.addEventListener("load", loaded, { once: true });
      script.addEventListener("error", () => reject(new Error("Google sign-in could not be reached.")), { once: true });
      if (!existing) {
        script.src = "https://accounts.google.com/gsi/client";
        script.async = true;
        document.head.appendChild(script);
      }
    });
  }
  return googleScript;
}

export type SessionUser = { user_id: string; email: string | null; displayName: string | null; photoURL: string | null };
export type AuthState = { user: SessionUser | null; signOut: () => Promise<void> };
export const AuthContext = createContext<AuthState>({ user: null, signOut: async () => {} });
export const useAuth = () => useContext(AuthContext);

function csrfToken(): string {
  return document.cookie.split("; ").find((item) => item.startsWith("sourcelens_csrf="))?.split("=").slice(1).join("=") || "";
}

async function serverUser(): Promise<SessionUser | null> {
  const response = await fetch("/api/me", { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("SourceLens could not verify your session.");
  const body = await response.json() as { user_id: string; email: string | null; display_name: string | null };
  return { user_id: body.user_id, email: body.email, displayName: body.display_name, photoURL: null };
}

async function exchange(idToken: string): Promise<SessionUser> {
  const response = await fetch("/api/auth/session", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ id_token: idToken }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail || "SourceLens could not create your session.");
  }
  const confirmed = await serverUser();
  if (!confirmed) throw new Error("Your browser did not retain the session. Allow cookies for SourceLens and try again.");
  return confirmed;
}

function authMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Google sign-in did not complete.";
}

function LensMark() {
  return <svg className="lens-mark" viewBox="0 0 32 32" aria-hidden="true"><circle cx="12" cy="16" r="8"/><circle cx="21" cy="16" r="8"/><path d="M16.5 9.4v13.2"/></svg>;
}

function SignInPage({ onCredential, error }: { onCredential: (credential: string) => Promise<void>; error: string }) {
  const button = useRef<HTMLDivElement>(null);
  const [loadError, setLoadError] = useState("");
  useEffect(() => {
    let active = true;
    credentialHandler = (response) => { if (active && response.credential) void onCredential(response.credential); };
    if (!GOOGLE_CLIENT_ID) {
      setLoadError("Google sign-in is not configured.");
      return () => { active = false; };
    }
    void loadGoogleIdentity().then((google) => {
      if (!active || !button.current) return;
      if (!googleInitialized) {
        google.accounts.id.initialize({ client_id: GOOGLE_CLIENT_ID, callback: (response) => credentialHandler?.(response), auto_select: false });
        googleInitialized = true;
      }
      button.current.replaceChildren();
      google.accounts.id.renderButton(button.current, { type: "standard", theme: "outline", size: "large", text: "continue_with", shape: "rectangular", logo_alignment: "left", width: 320 });
    }).catch((failure) => active && setLoadError(authMessage(failure)));
    return () => { active = false; };
  }, [onCredential]);
  return <main className="sign-in-page"><div className="sign-in-card"><div className="brand"><LensMark/>SourceLens</div><h1>Sign in to continue</h1><p className="muted">Your sources, investigations and notebook are private to your account.</p>{(error || loadError) && <p className="sign-in-error" role="alert">{error || loadError}</p>}<div className="google-sign-in" ref={button} aria-label="Continue with Google"/></div></main>;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void serverUser().then((existing) => { if (active) setUser(existing); }).catch((failure) => { if (active) setError(authMessage(failure)); }).finally(() => { if (active) setReady(true); });
    const expired = () => { setUser(null); setError("Your session has expired. Sign in again."); };
    window.addEventListener("sourcelens:unauthorized", expired);
    return () => { active = false; window.removeEventListener("sourcelens:unauthorized", expired); };
  }, []);

  const signOut = async () => {
    try {
      const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrfToken() } });
      if (!response.ok && response.status !== 401) throw new Error("Sign-out failed. Please try again.");
      window.google?.accounts.id.disableAutoSelect();
      setUser(null);
    } catch (failure) { window.alert(authMessage(failure)); }
  };
  const onCredential = async (credential: string) => {
    setError("");
    try { setUser(await exchange(credential)); }
    catch (failure) { setError(authMessage(failure)); }
  };

  if (!ready) return <main className="sign-in-page" aria-busy="true"><p className="muted">Checking your session…</p></main>;
  if (!user) return <SignInPage onCredential={onCredential} error={error}/>;
  return <AuthContext.Provider value={{ user, signOut }}>{children}</AuthContext.Provider>;
}
