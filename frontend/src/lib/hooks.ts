"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { Notification, User } from "@/lib/types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/v1/users/me"), staleTime: 5 * 60_000 });
}

export function useLogout() {
  const router = useRouter();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/api/v1/auth/logout"),
    onSettled: () => {
      queryClient.clear();
      router.replace("/login");
    },
  });
}

export function useUnreadCount() {
  return useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => api.get<{ unread: number }>("/api/v1/notifications/unread-count"),
    refetchInterval: 60_000,
  });
}

export function useNotifications(unreadOnly = false) {
  return useQuery({
    queryKey: ["notifications", "list", unreadOnly],
    queryFn: () => api.get<Notification[]>("/api/v1/notifications", { unread_only: unreadOnly, limit: 50 }),
  });
}

/** Poll a background job until it leaves queued/running. */
export function pollingInterval<T extends { status: string }>(data: T | undefined, everyMs = 3000): number | false {
  if (!data) return everyMs;
  return data.status === "queued" || data.status === "running" ? everyMs : false;
}
