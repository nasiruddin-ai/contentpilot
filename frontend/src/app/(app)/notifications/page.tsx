"use client";

import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { useNotifications } from "@/lib/hooks";
import type { Notification } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";

export default function NotificationsPage() {
  const queryClient = useQueryClient();
  const { data: items = [], isLoading } = useNotifications();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["notifications"] });

  const markAll = useMutation({
    mutationFn: () => api.post<{ updated: number }>("/api/v1/notifications/read-all"),
    onSuccess: invalidate,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const markOne = useMutation({
    mutationFn: (id: string) => api.post<Notification>(`/api/v1/notifications/${id}/read`),
    onSuccess: invalidate,
  });
  const unread = items.filter((n) => !n.read_at).length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Notifications"
        description="Publishing results, autopilot decisions and anything that needs attention."
        actions={
          <Button variant="outline" onClick={() => markAll.mutate()} disabled={!unread || markAll.isPending}>
            Mark all as read
          </Button>
        }
      />
      {isLoading ? null : items.length ? (
        <div className="flex flex-col divide-y rounded-xl border bg-card">
          {items.map((n) => (
            <div key={n.id} className="flex flex-col gap-0.5 p-4">
              <div className="flex items-center gap-2">
                {!n.read_at ? <span className="size-2 rounded-full bg-primary" /> : null}
                {n.post_id ? (
                  <Link href={`/content/${n.post_id}`} onClick={() => !n.read_at && markOne.mutate(n.id)} className={`text-sm hover:underline ${n.read_at ? "" : "font-medium"}`}>
                    {n.title}
                  </Link>
                ) : (
                  <span className={`text-sm ${n.read_at ? "" : "font-medium"}`}>{n.title}</span>
                )}
                <span className="ml-auto text-xs text-muted-foreground">{formatRelative(n.created_at)}</span>
              </div>
              <p className="text-sm text-muted-foreground">{n.message}</p>
              <div className="flex gap-3 text-xs">
                {n.link ? (
                  <a href={n.link} target="_blank" rel="noreferrer noopener" className="underline">
                    Open on the platform
                  </a>
                ) : null}
                {!n.read_at ? (
                  <button type="button" className="text-muted-foreground underline" onClick={() => markOne.mutate(n.id)}>
                    Mark as read
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="No notifications" description="You will hear here when posts publish, fail or wait for review." />
      )}
    </div>
  );
}
