"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { currentSession, signOut } from "@/lib/auth";

// Sign-in and sign-out both navigate, so the control never needs a change notification.
const subscribeNever = () => () => {};

export function AccountControl() {
  const router = useRouter();
  const [isSigningOut, setIsSigningOut] = useState(false);
  // The session lives in localStorage, which the static HTML cannot know: reading it during render made the
  // server's "Sign in" disagree with the client's account (React #418 on every load). The server snapshot is
  // "nobody", and the client reads the store once hydrated.
  const user = useSyncExternalStore(subscribeNever, currentSession, () => null);

  async function handleSignOut() {
    setIsSigningOut(true);
    try {
      await signOut();
      router.refresh();
      router.push("/");
    } finally {
      setIsSigningOut(false);
    }
  }

  if (!user) {
    return (
      <Link
        href="/signin"
        className="block rounded-md bg-text px-3 py-1.5 text-xs font-medium text-bg hover:opacity-90"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <div className="min-w-0 text-right">
        <p className="truncate text-xs font-medium text-[var(--color-text)]">{user.email}</p>
      </div>
      <button
        onClick={handleSignOut}
        disabled={isSigningOut}
        className="rounded-md p-1.5 hover:bg-[var(--color-surface-raised)] disabled:opacity-50"
        aria-label="Sign out"
        title="Sign out"
      >
        <LogOut size={16} className="text-[var(--color-text-dim)]" />
      </button>
    </div>
  );
}
