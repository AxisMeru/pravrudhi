"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { currentSession, signOut } from "@/lib/auth";

export function AccountControl() {
  const router = useRouter();
  const [isSigningOut, setIsSigningOut] = useState(false);
  const user = currentSession();

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
        className="block rounded-md bg-[var(--color-text)] px-3 py-1.5 text-xs font-medium text-[var(--color-bg)] hover:opacity-90"
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
