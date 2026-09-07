"use client";

// A bell for the record `notifications.py` keeps: what happened while nobody was watching a page. Polls the
// feed, shows an unread count, and lists recent entries newest first with a link to where each happened.
//
// A native desktop notification for a run finishing is offered once, remembered either way, and never asked
// again -- the browser's own permission prompt is itself a one-shot dialog, so the ask here is only the small
// inline banner deciding whether to trigger that prompt at all.

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell } from "lucide-react";
import { markRead, notifications, type NotificationItem } from "@/lib/notifications";

const ASKED_KEY = "pravrudhi.notifications.asked";
const OPTIN_KEY = "pravrudhi.notifications.optin";
const POLL_MS = 15_000;

function notificationApiAvailable(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [showAsk, setShowAsk] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const seenIds = useRef<Set<string> | null>(null);

  const poll = useCallback(() => {
    notifications(30)
      .then((snap) => {
        // The first poll after mount only primes `seenIds`; without that guard, reloading a page with an
        // established backlog would fire a desktop notification for every run that ever finished.
        if (seenIds.current) {
          for (const item of snap.notifications) {
            if (
              !seenIds.current.has(item.id) &&
              item.kind === "run_finished" &&
              localStorage.getItem(OPTIN_KEY) === "granted" &&
              notificationApiAvailable() &&
              Notification.permission === "granted"
            ) {
              try {
                new Notification(item.title, { body: item.detail || undefined });
              } catch {
                /* the browser can still refuse at fire time; nothing to do about it here */
              }
            }
          }
        }
        seenIds.current = new Set(snap.notifications.map((item) => item.id));
        setItems(snap.notifications);
        setUnread(snap.unread);
      })
      .catch(() => {
        /* no engine reachable yet -- the bell just stays quiet */
      });
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  function toggleOpen() {
    const next = !open;
    setOpen(next);
    if (next) {
      const alreadyAsked = typeof window !== "undefined" && localStorage.getItem(ASKED_KEY);
      setShowAsk(!alreadyAsked && notificationApiAvailable() && Notification.permission === "default");
    }
  }

  function enableDesktopAlerts() {
    localStorage.setItem(ASKED_KEY, "1");
    setShowAsk(false);
    if (!notificationApiAvailable()) return;
    Notification.requestPermission()
      .then((permission) => {
        localStorage.setItem(OPTIN_KEY, permission === "granted" ? "granted" : "denied");
      })
      .catch(() => {
        localStorage.setItem(OPTIN_KEY, "denied");
      });
  }

  function declineDesktopAlerts() {
    localStorage.setItem(ASKED_KEY, "1");
    localStorage.setItem(OPTIN_KEY, "denied");
    setShowAsk(false);
  }

  function markAllRead() {
    markRead([])
      .then((snap) => {
        setItems(snap.notifications);
        setUnread(snap.unread);
      })
      .catch(() => {
        /* best-effort; the next poll reconciles */
      });
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={toggleOpen}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        className="relative flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
      >
        <Bell size={16} />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--color-accent)] px-1 text-[10px] font-medium leading-none text-[#06110c]">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute left-0 z-20 mt-2 w-80 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] shadow-lg">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
            <span className="text-xs font-medium text-[var(--color-text)]">Notifications</span>
            <button
              type="button"
              onClick={markAllRead}
              disabled={unread === 0}
              className="text-xs text-[var(--color-text-dim)] hover:text-[var(--color-text)] disabled:cursor-default disabled:opacity-40"
            >
              Mark all read
            </button>
          </div>
          {showAsk && (
            <div className="flex items-start justify-between gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-raised)] px-3 py-2">
              <span className="text-xs text-[var(--color-text-dim)]">Alert this browser when a night finishes?</span>
              <div className="flex shrink-0 gap-2">
                <button type="button" onClick={enableDesktopAlerts} className="text-xs font-medium text-[var(--color-accent)]">
                  Enable
                </button>
                <button type="button" onClick={declineDesktopAlerts} className="text-xs text-[var(--color-text-dim)]">
                  No thanks
                </button>
              </div>
            </div>
          )}
          <div className="max-h-96 overflow-y-auto">
            {items.length === 0 && (
              <p className="px-3 py-6 text-center text-xs text-[var(--color-text-dim)]">Nothing yet.</p>
            )}
            {items.map((item) => (
              <Link
                key={item.id}
                href={item.ref || "#"}
                onClick={() => setOpen(false)}
                className="block border-b border-[var(--color-border)] px-3 py-2 text-xs last:border-b-0 hover:bg-[var(--color-surface-raised)]"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className={item.read ? "text-[var(--color-text-dim)]" : "font-medium text-[var(--color-text)]"}>
                    {item.title}
                  </span>
                  {!item.read && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent)]" />}
                </div>
                {item.detail && <p className="mt-0.5 line-clamp-2 text-[var(--color-text-dim)]">{item.detail}</p>}
                <p className="mt-0.5 text-[10px] text-[var(--color-text-dim)]">{relativeTime(item.at)}</p>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
