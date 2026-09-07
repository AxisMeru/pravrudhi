// Typed fetch client for the engine's notification feed: the record of what happened while nobody was watching
// a page -- a run ending, a job's verdict, a criterion being met. A new file rather than additions to api.ts, so
// pages built in parallel never contend for that one.

import { ApiError, apiBase, IS_DEMO, localToken } from "./api";

export interface NotificationItem {
  id: string;
  at: string;
  kind: string;
  title: string;
  detail: string;
  ref: string;
  read: boolean;
}

export interface NotificationsSnapshot {
  notifications: NotificationItem[];
  unread: number;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const token = await localToken();
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

export async function notifications(n = 30): Promise<NotificationsSnapshot> {
  if (IS_DEMO) return { notifications: [], unread: 0 };
  return getJSON<NotificationsSnapshot>(`/api/notifications?n=${n}`);
}

// An empty `ids` list marks every unread notification read.
export async function markRead(ids: string[] = []): Promise<NotificationsSnapshot> {
  if (IS_DEMO) throw new ApiError(501, "/api/notifications/read");
  return postJSON<NotificationsSnapshot>("/api/notifications/read", { ids });
}
