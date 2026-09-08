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
} from "lucide-react";
import type { ComponentType } from "react";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { edition, PRODUCT, STUDIO, type Edition } from "@/lib/edition";
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
  const [whoami, setWhoami] = useState<Edition>(PRODUCT);
  useEffect(() => {
    let off = false;
    edition().then((e) => !off && setWhoami(e)).catch(() => {});
    return () => { off = true; };
  }, []);
  const [pendingInbox, setPendingInbox] = useState(0);
  const [openRequests, setOpenRequests] = useState(0);

  useEffect(() => {
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
  }, []);

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="border-b border-[var(--color-border)] px-5 py-5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-lg font-semibold tracking-tight text-[var(--color-text)]">{whoami.edition}</div>
          <NotificationBell />
        </div>
        <p className="mt-1 text-xs leading-snug text-[var(--color-text-dim)]">
          {whoami.tagline}
        </p>
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
  );
}
