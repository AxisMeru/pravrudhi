"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { signIn as supabaseSignIn, signUp as supabaseSignUp, currentSession } from "@/lib/auth";

export default function SignInPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSignUp, setIsSignUp] = useState(false);
  const [confirmationMessage, setConfirmationMessage] = useState<string | null>(null);

  useEffect(() => {
    // If already signed in, redirect to home
    if (currentSession()) {
      router.push("/");
    }
  }, [router]);

  const isConfigured = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setConfirmationMessage(null);
    setIsLoading(true);

    if (isSignUp) {
      const result = await supabaseSignUp(email, password);
      if (result.ok) {
        router.push("/");
      } else if ("needsConfirmation" in result) {
        setConfirmationMessage("Check your email for a confirmation link, then sign in here.");
        setEmail("");
        setPassword("");
        setIsSignUp(false);
      } else {
        setError((result as { error?: string }).error || "Sign up failed");
      }
    } else {
      const result = await supabaseSignIn(email, password);
      if (result.ok) {
        router.push("/");
      } else {
        setError(result.error || "Sign in failed");
      }
    }

    setIsLoading(false);
  }

  if (!isConfigured) {
    return (
      <div className="flex h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="mx-auto w-full max-w-sm space-y-6 px-4">
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold text-[var(--color-text)]">Sign in</h1>
            <p className="text-sm text-[var(--color-text-dim)]">
              Sign-in is not configured for this installation.
            </p>
          </div>
          <div className="space-y-4">
            <Link
              href="/"
              className="block rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-center text-sm font-medium text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
            >
              Back to home
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen items-center justify-center bg-[var(--color-background)]">
      <div className="mx-auto w-full max-w-sm space-y-6 px-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold text-[var(--color-text)]">
            {isSignUp ? "Create an account" : "Sign in"}
          </h1>
          <p className="text-sm text-[var(--color-text-dim)]">
            {isSignUp
              ? "Enter your email and password to create an account."
              : "Enter your email and password to continue."}
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {confirmationMessage && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">
            {confirmationMessage}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-[var(--color-text)]">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-dim)] focus:border-[var(--color-text)] focus:outline-none"
              placeholder="you@example.com"
              disabled={isLoading}
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-[var(--color-text)]">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-dim)] focus:border-[var(--color-text)] focus:outline-none"
              placeholder="••••••••"
              disabled={isLoading}
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full rounded-md bg-[var(--color-text)] px-4 py-2 text-sm font-medium text-[var(--color-background)] hover:opacity-90 disabled:opacity-50"
          >
            {isLoading ? (isSignUp ? "Creating account..." : "Signing in...") : isSignUp ? "Create account" : "Sign in"}
          </button>
        </form>

        <div className="space-y-3">
          <div className="text-center text-sm text-[var(--color-text-dim)]">
            {isSignUp ? "Already have an account? " : "Don't have an account? "}
            <button
              type="button"
              onClick={() => {
                setIsSignUp(!isSignUp);
                setError(null);
                setConfirmationMessage(null);
              }}
              className="font-medium text-[var(--color-text)] hover:underline"
            >
              {isSignUp ? "Sign in" : "Create an account"}
            </button>
          </div>

          <div className="text-center text-sm text-[var(--color-text-dim)]">
            <Link href="/" className="hover:text-[var(--color-text)]">
              Back to home
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
