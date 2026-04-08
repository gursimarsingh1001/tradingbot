import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell, ChevronLeft, ChevronRight, Gauge, LayoutDashboard, Settings2, ShieldCheck, ShieldAlert, Sparkles, TrendingUp, Wifi, WifiOff } from "lucide-react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { NotificationDrawer } from "./components/NotificationDrawer";
import { useWebSocket } from "./hooks/useWebSocket";
import { useNotifications } from "./hooks/useNotifications";
import { api } from "./api/client";
import CommandCenter from "./pages/CommandCenter";
import ScoreBoardPage from "./pages/ScoreBoardPage";
import SafetyGatesPage from "./pages/SafetyGatesPage";
import TradesPage from "./pages/TradesPage";
import GlobalRiskPage from "./pages/GlobalRiskPage";
import ConfigPage from "./pages/ConfigPage";
import { formatDateTime, formatNumber } from "./utils/formatters";
import { marketAwareInterval } from "./utils/refresh";

const navItems = [
  { to: "/", label: "Home", icon: LayoutDashboard },
  { to: "/scores", label: "Score Board", icon: Sparkles },
  { to: "/gates", label: "Safety Gates", icon: ShieldCheck },
  { to: "/trades", label: "Trades", icon: TrendingUp },
  { to: "/risk", label: "Global Risk", icon: Gauge },
  { to: "/config", label: "Config", icon: Settings2 },
];

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const { liveData, connectionState } = useWebSocket();
  const notifications = useNotifications(liveData);
  const riskQuery = useQuery({
    queryKey: ["statusBarRisk"],
    queryFn: api.fetchRiskLatest,
    refetchInterval: marketAwareInterval(60000, 300000),
  });
  const systemStatusQuery = useQuery({
    queryKey: ["statusBarSystem"],
    queryFn: api.fetchSystemStatus,
    refetchInterval: marketAwareInterval(60000, 300000),
  });
  const cutoverQuery = useQuery({
    queryKey: ["statusBarCutover"],
    queryFn: api.fetchCutoverLatest,
    refetchInterval: marketAwareInterval(30000, 300000),
  });

  const sessionTimestamp = liveData?.timestamp ? formatDateTime(liveData.timestamp) : "Waiting for live heartbeat";
  const riskLevel = riskQuery.data?.latest?.riskLevel ?? "UNKNOWN";
  const killSwitch = systemStatusQuery.data?.killSwitch;
  const unreadLabel = notifications.unreadCount > 0 ? `${notifications.unreadCount} unread` : "No unread";
  const shellPadding = collapsed ? "lg:pl-[7.5rem]" : "lg:pl-[20.5rem]";
  const riskTone =
    riskLevel === "RED" ? "bg-coral/15 text-coral" : riskLevel === "YELLOW" ? "bg-amber/15 text-amber" : "bg-mint/15 text-mint";

  const currentTime = useMemo(
    () =>
      new Date().toLocaleString("en-IN", {
        weekday: "short",
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }),
    [],
  );

  useEffect(() => {
    const previous = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    return () => {
      window.history.scrollRestoration = previous;
    };
  }, []);

  useLayoutEffect(() => {
    const resetScroll = () => {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    };

    resetScroll();
    requestAnimationFrame(resetScroll);
    window.setTimeout(resetScroll, 60);
  }, [location.pathname]);

  return (
    <div className="relative min-h-screen overflow-x-hidden overflow-y-auto bg-transparent text-ink">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="ambient-orb left-[-10rem] top-[-7rem] h-80 w-80 bg-mint/20" />
        <div className="ambient-orb right-[-8rem] top-[6rem] h-96 w-96 bg-ocean/18" />
        <div className="ambient-orb bottom-[-10rem] left-[16%] h-[28rem] w-[28rem] bg-violet/10" />
      </div>

      <aside
        className={`sidebar-shell shell-section fixed inset-y-4 left-4 z-30 hidden overflow-hidden rounded-[2rem] border border-white/10 p-4 transition-all duration-300 lg:flex lg:flex-col ${
          collapsed ? "w-[5.5rem]" : "w-[18rem]"
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          {!collapsed ? (
            <div>
              <div className="chrome-kicker">
                <span className="status-beacon inline-flex h-2.5 w-2.5 rounded-full bg-mint" />
                Investment OS
              </div>
              <h1 className="mt-4 font-display text-2xl font-semibold tracking-[-0.04em] text-white">Command Center</h1>
            </div>
          ) : (
            <div className="mx-auto rounded-full border border-white/10 bg-white/5 p-3">
              <LayoutDashboard className="h-5 w-5 text-white" />
            </div>
          )}
          <button
            onClick={() => setCollapsed((value) => !value)}
            className="rounded-full border border-white/10 bg-white/5 p-2 text-slate-300 transition hover:bg-white/10"
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        </div>

        {!collapsed ? (
          <div className="mt-5 rounded-[1.5rem] border border-white/10 bg-white/5 p-4">
            <p className="micro-label">Desk state</p>
            <div className="mt-3 space-y-3">
              <div className="metric-card rounded-2xl px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Feed</p>
                <div className="mt-2 flex items-center gap-3">
                  {connectionState === "open" ? <Wifi className="h-5 w-5 text-mint" /> : <WifiOff className="h-5 w-5 text-coral" />}
                  <span className="text-sm font-semibold text-white">{connectionState === "open" ? "Connected" : "Reconnecting"}</span>
                </div>
              </div>
              <div className="metric-card rounded-2xl px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Cutover queue</p>
                <p className="mt-2 text-lg font-semibold text-white">{cutoverQuery.data?.plannedCount ?? 0} planned</p>
              </div>
            </div>
          </div>
        ) : null}

        <nav className="mt-6 flex-1 space-y-2">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `pro-nav-link group flex items-center gap-3 rounded-2xl border px-4 py-3 text-sm font-semibold transition ${
                    isActive
                      ? "border-ocean/40 bg-gradient-to-r from-ocean/90 to-[#7eb8ff]/55 text-white shadow-[0_16px_34px_rgba(90,166,255,0.24)]"
                      : "border-white/8 bg-white/5 text-slate-300 hover:border-white/16 hover:bg-white/10"
                  } ${collapsed ? "justify-center px-0" : ""}`
                }
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed ? <span>{item.label}</span> : null}
              </NavLink>
            );
          })}
        </nav>

        {!collapsed ? (
          <div className="metric-card mt-auto rounded-[1.5rem] px-4 py-4">
            <p className="micro-label">Kill switch</p>
            <div className="mt-3 flex items-center gap-3">
              {killSwitch?.active ? <ShieldAlert className="h-5 w-5 text-coral" /> : <ShieldCheck className="h-5 w-5 text-mint" />}
              <span className="text-sm font-semibold text-white">{killSwitch?.active ? "Active" : "Inactive"}</span>
            </div>
            <p className="mt-2 text-xs text-slate-400">{killSwitch?.reason ?? "No active override."}</p>
          </div>
        ) : null}
      </aside>

      <div className={`relative px-4 pb-28 pt-4 transition-all duration-300 md:px-6 ${shellPadding}`}>
        <div className="mx-auto max-w-[1680px]">
          <header className="relative topbar-shell shell-section mb-4 flex flex-wrap items-center justify-between gap-4 rounded-[2rem] px-6 py-5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="chrome-kicker">Investment Command Center</span>
              <span className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${riskTone}`}>
                Risk {riskLevel}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <div className="hero-stat px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Time</p>
                <p className="mt-1 text-sm font-semibold text-white">{currentTime}</p>
              </div>
              <button
                onClick={() => setDrawerOpen(true)}
                className="inline-flex items-center gap-3 rounded-full border border-white/10 bg-gradient-to-r from-ocean via-[#56b8ff] to-[#8ed5ff] px-5 py-3 text-sm font-semibold text-white shadow-[0_18px_42px_rgba(69,182,255,0.30)]"
              >
                <Bell className="h-4 w-4" />
                Notifications
                <span className="rounded-full bg-coral px-2 py-0.5 text-xs">{notifications.unreadCount}</span>
              </button>
            </div>
          </header>

          <main className="fx-fade-up">
            <Routes>
              <Route path="/" element={<CommandCenter liveData={liveData} connectionState={connectionState} />} />
              <Route path="/scores" element={<ScoreBoardPage />} />
              <Route path="/gates" element={<SafetyGatesPage />} />
              <Route path="/trades" element={<TradesPage />} />
              <Route path="/risk" element={<GlobalRiskPage />} />
              <Route path="/config" element={<ConfigPage />} />
              <Route path="/paper-trading" element={<Navigate to="/trades" replace />} />
              <Route path="/backtest-results" element={<Navigate to="/scores" replace />} />
              <Route path="/news" element={<Navigate to="/risk" replace />} />
              <Route path="/learning-log" element={<Navigate to="/config" replace />} />
            </Routes>
          </main>
        </div>
      </div>

      <div className={`bottom-status-shell fixed inset-x-4 bottom-4 z-20 rounded-[1.5rem] px-5 py-3 ${shellPadding}`}>
        <div className="mx-auto flex max-w-[1680px] flex-wrap items-center justify-between gap-3 text-sm text-slate-300">
          <div className="flex flex-wrap items-center gap-3">
            <span className="glass-chip text-slate-200">{connectionState === "open" ? "Live heartbeat" : "Feed reconnecting"}</span>
            <span>Last tick: {sessionTimestamp}</span>
            <span>{unreadLabel}</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${riskTone}`}>
              Global risk {riskLevel}
            </span>
            <span>Multiplier x{formatNumber(riskQuery.data?.latest?.positionSizeMultiplier, 2)}</span>
            <span>{cutoverQuery.data?.plannedCount ?? 0} official plans</span>
          </div>
        </div>
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
