import { Bell, CheckCheck, X } from "lucide-react";
import { NotificationItem } from "../api/client";

type Props = {
  isOpen: boolean;
  unreadCount: number;
  notifications: NotificationItem[];
  onClose: () => void;
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
};

export function NotificationDrawer({
  isOpen,
  unreadCount,
  notifications,
  onClose,
  onMarkRead,
  onMarkAllRead,
}: Props) {
  return (
    <>
      {isOpen ? <div className="fixed inset-0 z-30 bg-[#020611]/70 backdrop-blur-sm" onClick={onClose} /> : null}
      <aside
        className={`fixed right-0 top-0 z-40 h-full w-full max-w-md transform border-l border-white/10 bg-[linear-gradient(180deg,rgba(8,14,26,0.96),rgba(6,11,20,0.94))] shadow-2xl backdrop-blur-xl transition-transform duration-300 ${
          isOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
          <div className="flex items-center gap-3">
            <div className="relative">
              <Bell className="h-5 w-5 text-ocean" />
              {unreadCount > 0 ? (
                <span className="absolute -right-2 -top-2 rounded-full bg-coral px-1.5 text-[10px] font-bold text-white">
                  {unreadCount}
                </span>
              ) : null}
            </div>
            <div>
              <h3 className="font-display text-lg font-semibold text-ink">Notifications</h3>
              <p className="text-sm text-slate-500">Live events and recent system messages</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-full p-2 text-slate-500 transition hover:bg-slate-100">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex items-center justify-between px-6 py-4">
          <p className="text-sm text-slate-500">{unreadCount} unread</p>
          <button onClick={onMarkAllRead} className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-ocean px-4 py-2 text-sm font-semibold text-white shadow-[0_12px_24px_rgba(90,166,255,0.2)]">
            <CheckCheck className="h-4 w-4" />
            Mark all as read
          </button>
        </div>

        <div className="space-y-3 overflow-y-auto px-6 pb-6">
          {notifications.map((item) => (
            <button
              key={item.id}
              onClick={() => onMarkRead(item.id)}
              className={`w-full rounded-[1.4rem] border px-4 py-4 text-left transition ${
                item.isRead
                  ? "border-slate-100 bg-slate-50"
                  : "border-ocean/20 bg-[linear-gradient(180deg,rgba(90,166,255,0.12),rgba(90,166,255,0.04))] shadow-[0_0_0_1px_rgba(90,166,255,0.06)]"
              }`}
            >
              <div className="flex items-center justify-between gap-4">
                <p className="font-semibold text-ink">{item.title}</p>
                <span className={`h-2.5 w-2.5 rounded-full ${item.color === "red" ? "bg-coral" : item.color === "green" ? "bg-mint" : item.color === "orange" ? "bg-amber" : "bg-ocean"}`} />
              </div>
              <p className="mt-2 text-sm text-slate-600">{item.body}</p>
              <p className="mt-3 text-xs uppercase tracking-[0.18em] text-slate-400">{item.createdAt ?? "Live"}</p>
            </button>
          ))}
        </div>
      </aside>
    </>
  );
}
