import { useState } from "react";
import { Bell, ChevronRight, Wifi, WifiOff } from "lucide-react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NotificationDrawer } from "./components/NotificationDrawer";
import { KillSwitchBanner } from "./components/KillSwitchBanner";
import { useWebSocket } from "./hooks/useWebSocket";
import { useNotifications } from "./hooks/useNotifications";
import { api } from "./api/client";
import Home from "./pages/Home";
import PaperTrading from "./pages/PaperTrading";
import BacktestResults from "./pages/BacktestResults";
import News from "./pages/News";
import LearningLog from "./pages/LearningLog";

const navItems = [
  { to: "/", label: "Home" },
  { to: "/paper-trading", label: "Paper Trading" },
  { to: "/backtest-results", label: "Backtest Results" },
  { to: "/news", label: "News" },
  { to: "/learning-log", label: "Learning Log" },
];

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { liveData, connectionState } = useWebSocket();
  const notifications = useNotifications(liveData);
  const queryClient = useQueryClient();
  const killSwitchQuery = useQuery({
    queryKey: ["killSwitch"],
    queryFn: api.fetchKillSwitchStatus,
    refetchInterval: 15000,
  });
  const restartMutation = useMutation({
    mutationFn: () => api.restartKillSwitch(true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["killSwitch"] });
    },
  });
  const effectiveKillSwitchStatus = liveData?.killSwitch ?? killSwitchQuery.data;
  const sessionTimestamp = liveData?.timestamp ? new Date(liveData.timestamp).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }) : "Waiting for live clock";
  const deskDate = new Date().toLocaleDateString("en-IN", {
    weekday: "short",
    day: "2-digit",
    month: "short",
  });

  return (
    <div className="relative min-h-screen overflow-hidden bg-transparent px-4 py-4 text-ink md:px-6">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="ambient-orb left-[-9rem] top-[-7rem] h-72 w-72 bg-mint/25" />
        <div className="ambient-orb right-[-7rem] top-[6rem] h-80 w-80 bg-ocean/25" />
        <div className="ambient-orb right-[30%] top-[40%] h-64 w-64 bg-violet/12" />
        <div className="ambient-orb bottom-[-8rem] left-[18%] h-96 w-96 bg-coral/10" />
        <div className="ambient-orb bottom-[10%] right-[12%] h-64 w-64 bg-amber/10" />
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent" />
      </div>

      <div className="relative mx-auto grid max-w-[1680px] gap-4 lg:grid-cols-[296px_minmax(0,1fr)]">
        <aside className="sidebar-shell shell-section fx-fade-up flex flex-col gap-6 rounded-[2rem] p-6 lg:sticky lg:top-4 lg:h-[calc(100vh-2rem)]">
          <div>
            <div className="chrome-kicker">
              <span className="status-beacon inline-flex h-2.5 w-2.5 rounded-full bg-mint" />
              Apex Control
            </div>
            <h1 className="mt-4 font-display text-3xl font-semibold tracking-[-0.04em] text-ink">Operator Console</h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              Premium command deck for live market data, paper execution, backtesting, news flow, and model learning.
            </p>
          </div>

          <div className="terminal-grid rounded-[1.75rem] border border-white/10 bg-white/5 p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Session status</p>
              <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-300">
                {deskDate}
              </span>
            </div>
            <div className="mt-3 grid gap-3">
              <div className="metric-card rounded-2xl px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Live stream</p>
                <div className="mt-2 flex items-center gap-3">
                  {connectionState === "open" ? <Wifi className="h-5 w-5 text-mint" /> : <WifiOff className="h-5 w-5 text-coral" />}
                  <p className="text-sm font-semibold text-white">{connectionState === "open" ? "Connected" : "Disconnected"}</p>
                </div>
              </div>
              <div className="metric-card rounded-2xl px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Last live tick</p>
                <p className="mt-2 text-sm font-semibold text-white">{sessionTimestamp}</p>
              </div>
            </div>
          </div>

          <nav className="space-y-2">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `pro-nav-link group flex items-center justify-between rounded-2xl border px-4 py-3 text-sm font-semibold transition ${
                    isActive
                      ? "border-ocean/40 bg-gradient-to-r from-ocean/90 to-[#7eb8ff]/55 text-white shadow-[0_16px_34px_rgba(90,166,255,0.24)]"
                      : "border-white/8 bg-slate-50 text-slate-700 hover:border-white/16 hover:bg-slate-100"
                  }`
                }
              >
                <span className="tracking-[0.01em]">{item.label}</span>
                <ChevronRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1" />
              </NavLink>
            ))}
          </nav>

          <div className="metric-card mt-auto rounded-[1.75rem] px-5 py-4 text-white">
            <p className="text-[11px] uppercase tracking-[0.2em] text-slate-400">Control layer</p>
            <p className="mt-2 font-display text-xl font-semibold text-white">
              {effectiveKillSwitchStatus?.active ? "Risk lock active" : "Execution unlocked"}
            </p>
            <p className="mt-2 text-sm text-slate-300">
              {effectiveKillSwitchStatus?.active
                ? effectiveKillSwitchStatus.reason ?? "Bot trading is temporarily locked."
                : "Kill switch is off and the paper execution engine is ready."}
            </p>
          </div>
        </aside>

        <main className="min-w-0">
          <div className="topbar-shell shell-section fx-fade-up fx-delay-1 mb-4 flex flex-wrap items-center justify-between gap-4 rounded-[2rem] px-6 py-5">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="chrome-kicker">Trading Workspace</span>
                <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-300">
                  {connectionState === "open" ? "Live market sync" : "Feed reconnecting"}
                </span>
              </div>
              <p className="mt-3 font-display text-[2rem] font-semibold tracking-[-0.04em] text-ink">Daily decision center</p>
              <p className="mt-2 max-w-3xl text-sm text-slate-500">
                High-clarity desk for live execution, tomorrow planning, research intelligence, and risk-aware paper trading.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <div className="hero-stat px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Unread</p>
                <p className="mt-1 text-sm font-semibold text-white">{notifications.unreadCount}</p>
              </div>
              <button
                onClick={() => setDrawerOpen(true)}
                className="relative inline-flex items-center gap-3 rounded-full border border-white/10 bg-gradient-to-r from-ocean via-[#56b8ff] to-[#8ed5ff] px-5 py-3 text-sm font-semibold text-white shadow-[0_18px_42px_rgba(69,182,255,0.30)]"
              >
                <Bell className="h-4 w-4" />
                Notifications
                {notifications.unreadCount > 0 ? (
                  <span className="rounded-full bg-coral px-2 py-0.5 text-xs">{notifications.unreadCount}</span>
                ) : null}
              </button>
            </div>
          </div>

          <div className="fx-fade-up fx-delay-2">
            <KillSwitchBanner status={effectiveKillSwitchStatus} onRestart={() => restartMutation.mutate()} />
          </div>

          <div className="fx-fade-up fx-delay-3">
            <Routes>
            <Route path="/" element={<Home liveData={liveData} />} />
            <Route path="/paper-trading" element={<PaperTrading liveData={liveData} />} />
            <Route path="/backtest-results" element={<BacktestResults />} />
            <Route path="/news" element={<News />} />
            <Route path="/learning-log" element={<LearningLog />} />
            </Routes>
          </div>
        </main>
      </div>

      <NotificationDrawer
        isOpen={drawerOpen}
        unreadCount={notifications.unreadCount}
        notifications={notifications.items}
        onClose={() => setDrawerOpen(false)}
        onMarkRead={notifications.markRead}
        onMarkAllRead={notifications.markAllRead}
      />
    </div>
  );
}
