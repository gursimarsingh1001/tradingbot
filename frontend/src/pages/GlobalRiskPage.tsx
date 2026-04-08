import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { api } from "../api/client";
import { SectionHeader, StatePanel } from "../components/CommandPrimitives";
import { formatDate, formatNumber } from "../utils/formatters";
import { marketAwareInterval } from "../utils/refresh";

export default function GlobalRiskPage() {
  const latestQuery = useQuery({
    queryKey: ["riskLatestPage"],
    queryFn: api.fetchRiskLatest,
    refetchInterval: marketAwareInterval(60000, 300000),
  });
  const historyQuery = useQuery({
    queryKey: ["riskHistoryPage"],
    queryFn: () => api.fetchRiskHistory(30, "AFTER_MARKET"),
    refetchInterval: marketAwareInterval(60000, 300000),
  });

  const latest = latestQuery.data?.latest;
  const history = historyQuery.data?.rows ?? [];

  return (
    <div className="space-y-6">
      <section className="panel p-6">
        <SectionHeader
          eyebrow="Phase 5"
          title="Global crisis scanner"
          subtitle="Macro risk overlay that can block or resize official investment plans before cutover."
        />
        <div className="mt-6 grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
          <div className="desk-banner rounded-[1.8rem] px-6 py-6">
            <div className="flex flex-wrap items-center gap-2">
              <span className="glass-chip text-slate-200">Scan {latest?.scanType ?? "UNKNOWN"}</span>
              <span className="glass-chip text-ocean">{formatDate(latest?.asOfDate)}</span>
            </div>
            <p className="mt-5 micro-label">Current risk level</p>
            <p
              className={`mt-3 font-display text-6xl font-semibold ${
                latest?.riskLevel === "RED" ? "text-coral" : latest?.riskLevel === "YELLOW" ? "text-amber" : "text-mint"
              }`}
            >
              {latest?.riskLevel ?? "UNKNOWN"}
            </p>
            <p className="mt-4 text-sm text-slate-300">
              Position sizing multiplier: <span className="font-semibold text-white">{formatNumber(latest?.positionSizeMultiplier, 2)}x</span>
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              {(latest?.activeSignals ?? []).length ? (
                latest?.activeSignals.map((signal) => (
                  <span key={signal} className="glass-chip text-amber">
                    <AlertTriangle className="h-4 w-4" />
                    {signal}
                  </span>
                ))
              ) : (
                <span className="glass-chip text-mint">
                  <ShieldCheck className="h-4 w-4" />
                  No active crisis flags
                </span>
              )}
            </div>
          </div>

          <div className="panel rounded-[1.8rem] p-5">
            <p className="micro-label">30-day risk timeline</p>
            <div className="mt-4 h-72">
              {!history.length ? (
                <StatePanel title="Risk history abhi available nahi hai." tone="empty" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={history}>
                    <defs>
                      <linearGradient id="riskFill" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.36} />
                        <stop offset="100%" stopColor="#f59e0b" stopOpacity={0.04} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                    <XAxis dataKey="asOfDate" tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={(value) => formatDate(value)} tickLine={false} axisLine={false} />
                    <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} />
                    <Area dataKey="positionSizeMultiplier" type="monotone" stroke="#f59e0b" strokeWidth={3} fill="url(#riskFill)" />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {(latest?.signals ?? []).map((signal) => (
          <div key={signal.name} className="panel p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="micro-label">{signal.label}</p>
                <p className="mt-3 mono-value text-3xl font-semibold text-white">
                  {signal.value === null || signal.value === undefined ? "-" : formatNumber(signal.value)}
                </p>
              </div>
              <span
                className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${
                  signal.severity === "BLOCK"
                    ? "bg-coral/15 text-coral"
                    : signal.severity === "CAUTION"
                      ? "bg-amber/15 text-amber"
                      : "bg-mint/15 text-mint"
                }`}
              >
                {signal.severity}
              </span>
            </div>
            <p className="mt-4 text-sm leading-6 text-slate-400">{signal.message}</p>
            <p className="mt-4 text-xs uppercase tracking-[0.16em] text-slate-500">
              Threshold {signal.threshold === null || signal.threshold === undefined ? "-" : formatNumber(signal.threshold)}
            </p>
          </div>
        ))}
      </section>
    </div>
  );
}
