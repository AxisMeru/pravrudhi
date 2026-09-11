// Authentication module for Supabase email/password sign-in.
// Session is stored in localStorage and persisted across page reloads.

interface User {
  id: string;
  email: string;
}

interface Session {
  accessToken: string;
  user: User;
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
export function setSession(token: string, user: User): void {
  cachedSession = { accessToken: token, user };
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
    };

    // If access_token is present, confirmation is not required
    if (data.access_token) {
      setSession(data.access_token, data.user);
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

    const data = (await response.json()) as { access_token: string; user: User };
    setSession(data.access_token, data.user);
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
