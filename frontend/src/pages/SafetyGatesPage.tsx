import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Funnel, Search } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import { EmptyTableMessage, SectionHeader, StatePanel } from "../components/CommandPrimitives";
import { formatDate } from "../utils/formatters";

export default function SafetyGatesPage() {
  const [selectedDate, setSelectedDate] = useState("");
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [query, setQuery] = useState("");

  const summaryQuery = useQuery({
    queryKey: ["gatesSummaryPage", selectedDate],
    queryFn: () => api.fetchGatesSummary(selectedDate || undefined),
  });
  const universeQuery = useQuery({
    queryKey: ["gatesUniversePage", selectedDate],
    queryFn: () => api.fetchGatesUniverse(selectedDate || undefined),
  });

  useEffect(() => {
    if (!selectedDate && summaryQuery.data?.asOfDate) {
      setSelectedDate(summaryQuery.data.asOfDate);
    }
  }, [selectedDate, summaryQuery.data?.asOfDate]);

  useEffect(() => {
    if (!selectedSymbol && universeQuery.data?.rows?.[0]?.symbol) {
      setSelectedSymbol(universeQuery.data.rows[0].symbol);
    }
  }, [selectedSymbol, universeQuery.data?.rows]);

  const detailQuery = useQuery({
    queryKey: ["gatesDetailPage", selectedSymbol, selectedDate],
    queryFn: () => api.fetchGatesDetail(selectedSymbol, selectedDate || undefined),
    enabled: Boolean(selectedSymbol),
  });

  const rows = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const list = universeQuery.data?.rows ?? [];
    if (!normalized) {
      return list;
    }
    return list.filter((row) => row.symbol.toLowerCase().includes(normalized) || row.failureReasons.join(" ").toLowerCase().includes(normalized));
  }, [query, universeQuery.data?.rows]);

  return (
    <div className="space-y-6">
      <section className="panel p-6">
        <SectionHeader
          eyebrow="Phase 3"
          title="Safety gates"
          subtitle="Market health, sector strength, earnings, promoter, and breakout trigger validation for strong-buy names."
          actions={
            <>
              <input
                type="date"
                value={selectedDate}
                onChange={(event) => setSelectedDate(event.target.value)}
                className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-white outline-none"
              />
              <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2">
                <Search className="h-4 w-4 text-slate-400" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search symbol or failure reason"
                  className="w-52 bg-transparent text-sm text-white outline-none placeholder:text-slate-500"
                />
              </div>
            </>
          }
        />
        <div className="mt-6 grid gap-4 xl:grid-cols-[1fr_1fr]">
          <div className="subpanel rounded-[1.5rem] p-5">
            <div className="flex items-center gap-2">
              <Funnel className="h-4 w-4 text-ocean" />
              <p className="micro-label">Gate funnel</p>
            </div>
            <div className="mt-4 h-72">
              {!summaryQuery.data?.funnel.length ? (
                <StatePanel title="Gate funnel abhi available nahi hai." tone="empty" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={summaryQuery.data.funnel}>
                    <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                    <XAxis dataKey="stage" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                    <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} />
                    <Bar dataKey="count" radius={[10, 10, 0, 0]} fill="#13f7c7" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="subpanel rounded-[1.5rem] p-5">
            <p className="micro-label">Gate block breakdown</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4"><p className="micro-label">Eligible strong buys</p><p className="mt-2 text-2xl font-semibold text-white">{summaryQuery.data?.eligibleStrongBuy ?? 0}</p></div>
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4"><p className="micro-label">Approved BUY</p><p className="mt-2 text-2xl font-semibold text-mint">{summaryQuery.data?.buy ?? 0}</p></div>
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4"><p className="micro-label">Blocked by market</p><p className="mt-2 text-2xl font-semibold text-coral">{summaryQuery.data?.blockedByMarketHealth ?? 0}</p></div>
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4"><p className="micro-label">Blocked by entry</p><p className="mt-2 text-2xl font-semibold text-amber">{summaryQuery.data?.blockedByEntryTrigger ?? 0}</p></div>
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4"><p className="micro-label">Blocked by sector</p><p className="mt-2 text-2xl font-semibold text-white">{summaryQuery.data?.blockedBySectorStrength ?? 0}</p></div>
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4"><p className="micro-label">Blocked by promoter/earnings</p><p className="mt-2 text-2xl font-semibold text-white">{(summaryQuery.data?.blockedByPromoter ?? 0) + (summaryQuery.data?.blockedByEarningsProximity ?? 0)}</p></div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="panel p-6">
          <SectionHeader eyebrow="Universe Table" title="Per-symbol gate decisions" subtitle="Every BUY and SKIP from current Phase 3 gate evaluation." />
          <div className="mt-5 overflow-x-auto">
            {!rows.length ? (
              <EmptyTableMessage title="Gate decision rows abhi available nahi hain." />
            ) : (
              <table className="command-table w-full">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Decision</th>
                    <th>Phase 2</th>
                    <th>Failures</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.symbol} className={selectedSymbol === row.symbol ? "is-selected" : ""} onClick={() => setSelectedSymbol(row.symbol)}>
                      <td>
                        <p className="font-semibold text-white">{row.symbol}</p>
                        <p className="text-xs text-slate-500">{formatDate(row.asOfDate)}</p>
                      </td>
                      <td>
                        <span className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${row.decision === "BUY" ? "bg-mint/15 text-mint" : "bg-coral/15 text-coral"}`}>
                          {row.decision}
                        </span>
                      </td>
                      <td>{row.phase2Label}</td>
                      <td className="max-w-[18rem] text-xs text-slate-400">{row.failureReasons.join(", ") || "All gates passed"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div className="panel p-6">
          <SectionHeader eyebrow="Gate Detail" title={selectedSymbol || "Select a symbol"} subtitle="Detailed failure/debug payload from the live gate runner." />
          {!detailQuery.data ? (
            <div className="mt-5">
              <StatePanel title="Gate detail select karne ke baad yahan dikhega." tone="empty" />
            </div>
          ) : (
            <div className="mt-5 space-y-4">
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="glass-chip text-ocean">{detailQuery.data.phase2Label}</span>
                  <span className={`glass-chip ${detailQuery.data.decision === "BUY" ? "text-mint" : "text-coral"}`}>{detailQuery.data.decision}</span>
                </div>
                <p className="mt-4 text-sm text-slate-400">{detailQuery.data.failureReasons.join(", ") || "All gates passed successfully."}</p>
              </div>
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
                <pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-slate-300">
                  {JSON.stringify(detailQuery.data.debugPayload, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
