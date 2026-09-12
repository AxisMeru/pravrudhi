// Authentication module for Supabase email/password sign-in.
// Session is stored in localStorage and persisted across page reloads.

interface User {
  id: string;
  email: string;
}

interface Session {
  accessToken: string;
  user: User;
  refreshToken?: string;
  expiresAt?: number; // unix seconds; Supabase access tokens live one hour
}

interface Grant {
  refresh_token?: string;
  expires_in?: number;
}

const STORAGE_KEY = "pravrudhi-auth-session";

// Load session from localStorage on module load
function loadSession(): Session | null {
  try {
    const stored = typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    if (!stored) return null;
    return JSON.parse(stored) as Session;
  } catch {
    return null;
  }
}

let cachedSession: Session | null = loadSession();

/**
 * Get the current access token, or null if not signed in.
 * This is the token to attach to Authorization: Bearer headers.
 */
export function accessToken(): string | null {
  return cachedSession?.accessToken ?? null;
}

/**
 * Get the current session, or null if not signed in.
 */
export function currentSession(): { id: string; email: string } | null {
  return cachedSession?.user ?? null;
}

/**
 * Set session manually (used by tests and initialization).
 */
export function setSession(token: string, user: User, grant: Grant = {}): void {
  cachedSession = {
    accessToken: token,
    user,
    refreshToken: grant.refresh_token ?? cachedSession?.refreshToken,
    expiresAt: grant.expires_in ? Math.floor(Date.now() / 1000) + grant.expires_in : cachedSession?.expiresAt,
  };
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(cachedSession));
    }
  } catch {
    // Ignore localStorage write errors
  }
}

/**
 * Clear the current session.
 */
export function clearSession(): void {
  cachedSession = null;
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Ignore localStorage delete errors
  }
}

/**
 * Sign up with email and password.
 * Returns { ok: true, data: user } if a session is provided (no confirmation required),
 * or { ok: false, needsConfirmation: true } if confirmation is required,
 * or { ok: false, error: message } on failure.
 */
export async function signUp(
  email: string,
  password: string,
): Promise<
  | { ok: true; data: User }
  | { ok: false; needsConfirmation: true }
  | { ok: false; error: string }
> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    return { ok: false, error: "Supabase not configured" };
  }

  try {
    const response = await fetch(`${url}/auth/v1/signup`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        apikey: anonKey,
      },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const data = (await response.json()) as { error_description?: string };
      const message = data.error_description || "Sign up failed";
      return { ok: false, error: message };
    }

    const data = (await response.json()) as {
      access_token?: string;
      user: User;
    } & Grant;

    // If access_token is present, confirmation is not required
    if (data.access_token) {
      setSession(data.access_token, data.user, data);
      return { ok: true, data: data.user };
    }

    // No access token means confirmation is required
    return { ok: false, needsConfirmation: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Unknown error" };
  }
}

/**
 * Sign in with email and password.
 * Returns { ok: true, data: user } on success, or { ok: false, error: message } on failure.
 */
export async function signIn(
  email: string,
  password: string,
): Promise<{ ok: true; data: User } | { ok: false; error: string }> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    return { ok: false, error: "Supabase not configured" };
  }

  try {
    const response = await fetch(`${url}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        apikey: anonKey,
      },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const data = (await response.json()) as { error_description?: string };
      const message = data.error_description || "Sign in failed";
      return { ok: false, error: message };
    }

    const data = (await response.json()) as { access_token: string; user: User } & Grant;
    setSession(data.access_token, data.user, data);
    return { ok: true, data: data.user };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Unknown error" };
  }
}

/**
 * Sign out and clear the session.
 */
export async function signOut(): Promise<void> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const token = accessToken();

  if (url && token) {
    try {
      await fetch(`${url}/auth/v1/logout`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${token}`,
        },
      });
    } catch {
      // Ignore logout errors, clear session anyway
    }
  }

  clearSession();
}

/**
 * Verify the session is still valid by fetching the current user.
 */
export async function verifySession(): Promise<boolean> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const token = accessToken();

  if (!url || !token) {
    return false;
  }

  try {
    const response = await fetch(`${url}/auth/v1/user`, {
      method: "GET",
      headers: {
        authorization: `Bearer ${token}`,
        apikey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
      },
    });

    if (!response.ok) {
      clearSession();
      return false;
    }

    const user = (await response.json()) as User;
    if (cachedSession) {
      cachedSession.user = user;
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Send a magic link: Supabase emails a one-time sign-in link that lands back on this origin's /signin with the
 * session in the URL fragment (implicit flow). `create_user` makes a first sign-in an account too, so the
 * operator's admin identity exists the moment they approve the first link. Supabase only honours redirect_to
 * when the origin is in the project's allowed Redirect URLs; otherwise it uses the Site URL.
 */
export async function sendMagicLink(email: string): Promise<{ ok: true } | { ok: false; error: string }> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return { ok: false, error: "Supabase not configured" };
  const redirect = typeof window !== "undefined" ? `${window.location.origin}/signin` : "";
  try {
    const response = await fetch(`${url}/auth/v1/otp${redirect ? `?redirect_to=${encodeURIComponent(redirect)}` : ""}`, {
      method: "POST",
      headers: { "content-type": "application/json", apikey: anonKey },
      body: JSON.stringify({ email, create_user: true }),
    });
    if (!response.ok) {
      const data = (await response.json()) as { error_description?: string; msg?: string };
      return { ok: false, error: data.error_description || data.msg || "Could not send the link" };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Unknown error" };
  }
}

/**
 * Finish a magic-link arrival. Supabase returns the browser to /signin with `#access_token=…&refresh_token=…`
 * (or `#error_description=…`). Nothing to do when the fragment carries neither; the fragment is removed from
 * the address bar once read so a reload does not replay it.
 */
export async function completeMagicLink(): Promise<{ ok: true; data: User } | { ok: false; error: string } | null> {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const token = params.get("access_token");
  const failure = params.get("error_description");
  if (!token) return failure ? { ok: false, error: failure.replace(/\+/g, " ") } : null;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return { ok: false, error: "Supabase not configured" };
  try {
    const response = await fetch(`${url}/auth/v1/user`, { headers: { authorization: `Bearer ${token}`, apikey: anonKey } });
    if (!response.ok) return { ok: false, error: "That link has expired; request a new one" };
    const user = (await response.json()) as User;
    setSession(token, user, {
      refresh_token: params.get("refresh_token") ?? undefined,
      expires_in: Number(params.get("expires_in")) || undefined,
    });
    window.history.replaceState(null, "", window.location.pathname);
    return { ok: true, data: user };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Unknown error" };
  }
}

/**
 * Whether the stored access token is past, or within a minute of, its expiry. Unknown expiry counts as fresh;
 * the engine's 401 is the judge then.
 */
export function sessionStale(): boolean {
  const at = cachedSession?.expiresAt;
  return at !== undefined && Math.floor(Date.now() / 1000) >= at - 60;
}

/**
 * Exchange the refresh token for a new access token. Supabase access tokens live one hour; before this the
 * session simply died after an hour and every engine call answered 401 while the account control still showed
 * the address (the operator's first hour on the hosted door, 2026-09-12). A failed refresh clears the session so
 * the page shows one truth: signed out.
 */
export async function refreshSession(): Promise<boolean> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const refresh = cachedSession?.refreshToken;
  if (!url || !anonKey || !refresh) return false;
  try {
    const response = await fetch(`${url}/auth/v1/token?grant_type=refresh_token`, {
      method: "POST",
      headers: { "content-type": "application/json", apikey: anonKey },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!response.ok) {
      clearSession();
      return false;
    }
    const data = (await response.json()) as { access_token: string; user: User } & Grant;
    setSession(data.access_token, data.user, data);
    return true;
  } catch {
    return false;
  }
}
