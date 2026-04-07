import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, LivePayload, NotificationItem } from "../api/client";

export function useNotifications(liveData: LivePayload | null) {
  const queryClient = useQueryClient();
  const notificationsQuery = useQuery({
    queryKey: ["notifications"],
    queryFn: api.fetchNotifications,
  });

  const markOne = useMutation({
    mutationFn: api.markNotificationRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const markAll = useMutation({
    mutationFn: api.markAllNotificationsRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const merged = new Map<string, NotificationItem>();
  for (const item of notificationsQuery.data ?? []) {
    merged.set(item.id, item);
  }
  for (const item of liveData?.notifications ?? []) {
    merged.set(item.id, item);
  }

  const items = Array.from(merged.values()).sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
  const unreadCount = items.filter((item) => !item.isRead).length;

  return {
    items,
    unreadCount,
    isLoading: notificationsQuery.isLoading,
    markRead: (id: string) => markOne.mutate(id),
    markAllRead: () => markAll.mutate(),
  };
}
