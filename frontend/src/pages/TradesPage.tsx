import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, PaperTrade } from "../api/client";
import { EmptyTableMessage, SectionHeader, StatePanel } from "../components/CommandPrimitives";
import { formatDate, formatInr, formatPct, pnlToneClass } from "../utils/formatters";
import { marketAwareInterval } from "../utils/refresh";

type TabKey = "planned" | "open" | "closed" | "performance";

function TradeTable({ rows }: { rows: PaperTrade[] }) {
  if (!rows.length) {
    return <EmptyTableMessage title="Is tab ke liye koi trades available nahi hain." />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="command-table w-full">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Strategy</th>
            <th>Status</th>
            <th>Entry</th>
            <th>P&L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((trade) => (
            <tr key={trade.tradeId}>
              <td>
                <p className="font-semibold text-white">{trade.stockSymbol}</p>
                <p className="text-xs text-slate-500">{formatDate(trade.plannedForDate ?? trade.entryDate)}</p>
              </td>
              <td>{trade.strategyName}</td>
              <td>{trade.planStatus ?? trade.status}</td>
              <td>{formatInr(trade.entryPrice)}</td>
              <td className={pnlToneClass(trade.pnlRupees)}>{formatInr(trade.pnlRupees)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function TradesPage() {
  const [activeTab, setActiveTab] = useState<TabKey>("planned");
  const cutoverQuery = useQuery({
    queryKey: ["cutoverLatestTradesPage"],
    queryFn: api.fetchCutoverLatest,
    refetchInterval: marketAwareInterval(30000, 300000),
  });
  const openQuery = useQuery({
    queryKey: ["tradesOpenPage"],
    queryFn: api.fetchTradesOpen,
    refetchInterval: marketAwareInterval(30000, 300000),
  });
  const closedQuery = useQuery({
    queryKey: ["tradesClosedPage"],
    queryFn: () => api.fetchTradesClosed(180),
    refetchInterval: marketAwareInterval(30000, 300000),
  });
  const performanceQuery = useQuery({
    queryKey: ["portfolioPerformanceTradesPage"],
    queryFn: () => api.fetchPortfolioPerformance(180),
    refetchInterval: marketAwareInterval(30000, 300000),
  });

  const plannedRows = cutoverQuery.data?.signals ?? [];
  const openRows = openQuery.data?.open ?? [];
  const closedRows = closedQuery.data?.trades ?? [];
  const monthlyReturns = performanceQuery.data?.monthlyReturns ?? [];
  const tradeDistribution = performanceQuery.data?.tradeReturnDistribution ?? [];

  const histogram = useMemo(() => {
    return tradeDistribution.map((trade, index) => ({
      bucket: `${index + 1}`,
      pnlPct: trade.pnlPct ?? 0,
    }));
  }, [tradeDistribution]);

  return (
    <div className="space-y-6">
      <section className="panel p-6">
        <SectionHeader
          eyebrow="Trades"
          title="Planned, open, closed, and performance"
          subtitle="Phase 4 official planned trades plus full paper-trading portfolio performance."
        />
        <div className="mt-5 flex flex-wrap gap-2">
          {([
            ["planned", `Planned (${plannedRows.length})`],
            ["open", `Open (${openRows.length})`],
            ["closed", `Closed (${closedRows.length})`],
            ["performance", "Performance"],
          ] as Array<[TabKey, string]>).map(([tab, label]) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                activeTab === tab ? "bg-ocean text-white shadow-[0_14px_30px_rgba(90,166,255,0.22)]" : "border border-white/10 bg-white/5 text-slate-300"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      {activeTab === "planned" ? (
        <section className="panel p-6">
          <SectionHeader eyebrow="Phase 4 Cutover" title="Official planned investment trades" subtitle="Read from current official cutover metadata-backed paper trade plans." />
          <div className="mt-5">
            <TradeTable rows={plannedRows} />
          </div>
        </section>
      ) : null}

      {activeTab === "open" ? (
        <section className="panel p-6">
          <SectionHeader eyebrow="Open Book" title="Currently open paper trades" subtitle="Live positions still carried by the execution engine." />
          <div className="mt-5">
            <TradeTable rows={openRows} />
          </div>
        </section>
      ) : null}

      {activeTab === "closed" ? (
        <section className="panel p-6">
          <SectionHeader eyebrow="Closed History" title="Closed trade ledger" subtitle="Executed paper trades across the last 180 days." />
          <div className="mt-5">
            <TradeTable rows={closedRows} />
          </div>
        </section>
      ) : null}

      {activeTab === "performance" ? (
        <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
          <div className="panel p-6">
            <SectionHeader eyebrow="Equity Curve" title="Portfolio trajectory" subtitle="Cumulative equity value from the paper-trading book." />
            <div className="mt-5 h-80">
              {!performanceQuery.data?.equityCurve.length ? (
                <StatePanel title="Performance curve abhi available nahi hai." tone="empty" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={performanceQuery.data.equityCurve}>
                    <defs>
                      <linearGradient id="tradeEquityFill" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stopColor="#13f7c7" stopOpacity={0.28} />
                        <stop offset="100%" stopColor="#13f7c7" stopOpacity={0.04} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                    <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                    <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} />
                    <Area dataKey="value" type="monotone" stroke="#13f7c7" fill="url(#tradeEquityFill)" strokeWidth={3} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="space-y-4">
            <div className="panel p-6">
              <SectionHeader eyebrow="Monthly Returns" title="Return heatmap table" subtitle="Month-wise closed trade aggregation." />
              <div className="mt-5 grid gap-3">
                {!monthlyReturns.length ? (
                  <EmptyTableMessage title="Monthly return data abhi available nahi hai." />
                ) : (
                  monthlyReturns.map((month) => (
                    <div key={month.month} className="subpanel rounded-[1.2rem] px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-semibold text-white">{month.month}</span>
                        <span className={pnlToneClass(month.pnlRupees)}>{formatInr(month.pnlRupees)}</span>
                      </div>
                      <p className="mt-2 text-xs text-slate-400">{formatPct(month.pnlPct)} across {month.trades} trades</p>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="panel p-6">
              <SectionHeader eyebrow="Distribution" title="Trade return spread" subtitle="Closed trade percentage-return distribution." />
              <div className="mt-5 h-64">
                {!histogram.length ? (
                  <StatePanel title="Trade return distribution abhi empty hai." tone="empty" />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={histogram}>
                      <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                      <XAxis dataKey="bucket" hide />
                      <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                      <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} formatter={(value: number) => formatPct(value)} />
                      <Bar dataKey="pnlPct" radius={[8, 8, 0, 0]} fill="#8b5cf6" />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
