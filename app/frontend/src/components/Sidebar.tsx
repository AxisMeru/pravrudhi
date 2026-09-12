"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Sparkles,
  History,
  Package,
  Server,
  Settings,
  Download,
  Target,
  MessageSquare,
  LineChart,
  Bot,
  Brain,
  FileDiff,
  Activity,
  Scale,
  Radio,
  Inbox as InboxIcon,
  Library,
  Layers,
  ListChecks,
  Compass,
  Flame,
  Rocket,
  Cpu,
  Monitor,
  GitCompare,
  GitBranch,
  Menu,
  X,
} from "lucide-react";
import type { ComponentType } from "react";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { BUILT_AS, edition, STUDIO, type Edition } from "@/lib/edition";
import { AccountControl } from "@/components/AccountControl";
import { isStudioOnlyHref } from "@/lib/palette";
import { inbox } from "@/lib/inbox";
import { requests } from "@/lib/requests";

interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<{ size?: number; className?: string }>;
}

const NAV: NavItem[] = [
  { href: "/start", label: "Start", icon: Rocket },
  { href: "/", label: "Improve", icon: Sparkles },
  { href: "/tour", label: "Tour", icon: Compass },
  { href: "/appetite", label: "Appetite", icon: Flame },
  { href: "/objectives", label: "Objectives", icon: Target },
  { href: "/progress", label: "Progress", icon: LineChart },
  { href: "/inbox", label: "Inbox", icon: InboxIcon },
  { href: "/requests", label: "Requests", icon: ListChecks },
  { href: "/candidates", label: "Candidates", icon: Layers },
  { href: "/swarm", label: "Swarm", icon: Bot },
  { href: "/diffs", label: "Diffs", icon: FileDiff },
  { href: "/memory", label: "Memory", icon: Brain },
  { href: "/heartbeat", label: "Heartbeat", icon: Activity },
  { href: "/trace", label: "Agent trace", icon: Radio },
  { href: "/catalogue", label: "Catalogue", icon: Library },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/nyaya", label: "Nyaya", icon: Scale },
  { href: "/search", label: "Search", icon: GitBranch },
  { href: "/runs", label: "Runs", icon: History },
  { href: "/models", label: "Models", icon: Package },
  { href: "/machines", label: "Machines", icon: Server },
  { href: "/desktop", label: "Desktop", icon: Monitor },
  { href: "/parity", label: "Parity", icon: GitCompare },
  { href: "/system", label: "System", icon: Cpu },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/install", label: "Install", icon: Download },
];

export function Sidebar() {
  const pathname = usePathname();
  // The interface names itself from who is asking, so an operator sees Studio and a user sees the product.
  const [whoami, setWhoami] = useState<Edition>(BUILT_AS);
  useEffect(() => {
    let off = false;
    edition().then((e) => !off && setWhoami(e)).catch(() => {});
    return () => { off = true; };
  }, []);
  const [pendingInbox, setPendingInbox] = useState(0);
  const [openRequests, setOpenRequests] = useState(0);

  const [navOpen, setNavOpen] = useState(false);

  // A drawer that survives a route change hides the page the reader just asked for. Closing it here, during
  // render, rather than in an effect after the fact: React's own pattern for resetting state when a prop
  // changes (https://react.dev/reference/react/useState#storing-information-from-previous-renders) — comparing
  // against the previous pathname and calling setState directly in the render body avoids the extra commit an
  // effect would cause.
  const [prevPathname, setPrevPathname] = useState(pathname);
  if (pathname !== prevPathname) {
    setPrevPathname(pathname);
    setNavOpen(false);
  }

  useEffect(() => {
    // The inbox and requests badges are Studio's; a product install answers 404 to both routes, and asking on
    // every page put two console errors on every product page (found by the product's own e2e, 2026-09-11).
    if (whoami.edition !== STUDIO) return;
    let cancelled = false;
    inbox()
      .then((items) => {
        if (!cancelled) setPendingInbox(items.filter((i) => !i.signed).length);
      })
      .catch(() => {
        /* no engine reachable yet — the badge just stays at zero */
      });
    requests()
      .then((snapshot) => {
        if (!cancelled) setOpenRequests(snapshot?.open ?? 0);
      })
      .catch(() => {
        /* no engine reachable yet — the badge just stays at zero */
      });
    return () => {
      cancelled = true;
    };
  }, [whoami.edition]);

  return (
    <>
      {/* Below `md` the sidebar is a drawer. It used to be a fixed 240px column at every width, which on a
          390px phone took 62% of the screen permanently and left 150px for the content — no overflow, so
          nothing caught it, just an app that could not be read on the device the operator actually carries. */}
      <button
        type="button"
        onClick={() => setNavOpen((open) => !open)}
        aria-label={navOpen ? "Close navigation" : "Open navigation"}
        aria-expanded={navOpen}
        aria-controls="primary-navigation"
        className="fixed left-3 top-3 z-[60] rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-[var(--color-text)] shadow-lg md:hidden"
      >
        {navOpen ? <X size={18} aria-hidden /> : <Menu size={18} aria-hidden />}
      </button>
      {navOpen ? (
        <div
          onClick={() => setNavOpen(false)}
          aria-hidden
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
        />
      ) : null}
      <aside
        id="primary-navigation"
        className={`fixed inset-y-0 left-0 z-50 flex h-full w-60 shrink-0 transform flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-transform duration-200 md:static md:translate-x-0 ${
          navOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
      <div className="border-b border-[var(--color-border)] px-5 py-5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-lg font-semibold tracking-tight text-[var(--color-text)]">{whoami.edition}</div>
          <NotificationBell />
        </div>
        <p className="mt-1 text-xs leading-snug text-[var(--color-text-dim)]">
          {whoami.tagline}
        </p>
        {/* Only the hosted door has an account: a local or desktop Studio is the operator's by construction. */}
        {process.env.NEXT_PUBLIC_SUPABASE_URL ? (
          <div className="mt-3">
            <AccountControl />
          </div>
        ) : null}
      </div>
      <nav className="flex flex-1 flex-col gap-1 p-3">
        {/* A product install does not serve the engine's self-improvement surfaces at all, so offering them
            here would be links that answer 404. The rule lives in lib/palette.ts, which the command palette
            asks too. */}
        {NAV.filter(({ href }) => whoami.edition === STUDIO || !isStudioOnlyHref(href))
          .map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                active
                  ? "bg-[var(--color-surface-raised)] text-[var(--color-text)]"
                  : "text-[var(--color-text-dim)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
              }`}
            >
              <Icon size={16} />
              <span className="flex-1">{label}</span>
              {href === "/inbox" && pendingInbox > 0 && (
                <span className="rounded-full bg-[var(--color-accent)] px-1.5 py-0.5 text-[10px] font-medium text-[#06110c]">
                  {pendingInbox}
                </span>
              )}
              {href === "/requests" && openRequests > 0 && (
                <span className="rounded-full bg-[var(--color-accent)] px-1.5 py-0.5 text-[10px] font-medium text-[#06110c]">
                  {openRequests}
                </span>
              )}
            </Link>
          );
        })}
      </nav>
      </aside>
    </>
  );
}
