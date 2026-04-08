import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Search } from "lucide-react";
import { api, ScoringRow } from "../api/client";
import { EmptyTableMessage, SectionHeader, StatePanel } from "../components/CommandPrimitives";
import { formatCompactNumber, formatDate, formatNumber, formatPct } from "../utils/formatters";

type SortKey = "symbol" | "label" | "lynchValue" | "piotroskiFScore" | "minerviniRsPercentile" | "fillRate";

export default function ScoreBoardPage() {
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [selectedSymbol, setSelectedSymbol] = useState<string>("");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("symbol");

  const summaryQuery = useQuery({
    queryKey: ["scoringSummaryPage", selectedDate],
    queryFn: () => api.fetchScoringSummary(selectedDate || undefined),
  });
  const universeQuery = useQuery({
    queryKey: ["scoringUniversePage", selectedDate],
    queryFn: () => api.fetchScoringUniverse(selectedDate || undefined),
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
    queryKey: ["scoringDetailPage", selectedSymbol, selectedDate],
    queryFn: () => api.fetchScoringDetail(selectedSymbol, selectedDate || undefined),
    enabled: Boolean(selectedSymbol),
  });

  const filteredRows = useMemo(() => {
    const rows = universeQuery.data?.rows ?? [];
    const normalized = query.trim().toLowerCase();
    const visible = !normalized
      ? rows
      : rows.filter((row) => `${row.symbol} ${row.companyName ?? ""} ${row.sector ?? ""}`.toLowerCase().includes(normalized));
    return [...visible].sort((left, right) => {
      if (sortKey === "symbol" || sortKey === "label") {
        return String(left[sortKey]).localeCompare(String(right[sortKey]));
      }
      return Number(right[sortKey] ?? -9999) - Number(left[sortKey] ?? -9999);
    });
  }, [query, sortKey, universeQuery.data?.rows]);

  const detail = detailQuery.data;

  return (
    <div className="space-y-6">
      <section className="panel p-6">
        <SectionHeader
          eyebrow="Phase 2"
          title="Investment score board"
          subtitle="Lynch PEG, Piotroski F-Score, and Minervini template distribution across the official universe."
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
                  placeholder="Search symbol or sector"
                  className="w-48 bg-transparent text-sm text-white outline-none placeholder:text-slate-500"
                />
              </div>
            </>
          }
        />
        <div className="mt-6 grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
          <div className="subpanel rounded-[1.5rem] p-5">
            <p className="micro-label">Distribution</p>
            <div className="mt-4 h-72">
              {!summaryQuery.data?.distribution.length ? (
                <StatePanel title="Score distribution abhi available nahi hai." tone="empty" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={summaryQuery.data.distribution}>
                    <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                    <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                    <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} />
                    <Bar dataKey="count" radius={[10, 10, 0, 0]} fill="#5aa6ff" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
                <p className="micro-label">Universe</p>
                <p className="mt-2 text-2xl font-semibold text-white">{summaryQuery.data?.universeSize ?? 0}</p>
              </div>
              <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
                <p className="micro-label">Avg fill rate</p>
                <p className="mt-2 text-2xl font-semibold text-white">{formatPct((summaryQuery.data?.averages.fillRate ?? 0) * 100, 0)}</p>
              </div>
            </div>
          </div>

          <div className="subpanel rounded-[1.5rem] p-5">
            <p className="micro-label">Selected symbol detail</p>
            {!detail ? (
              <div className="mt-4">
                <StatePanel title="Score detail select karne ke baad yahan dikhega." tone="empty" />
              </div>
            ) : (
              <div className="mt-4 space-y-4">
                <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-display text-2xl font-semibold text-white">{detail.symbol}</p>
                      <p className="mt-1 text-sm text-slate-400">{(detail.snapshot["companyName"] as string | null) ?? "-"}</p>
                    </div>
                    <div className="text-right">
                      <span className="glass-chip text-ocean">{detail.label}</span>
                      <p className="mt-2 text-xs text-slate-400">{formatDate(detail.asOfDate)}</p>
                    </div>
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    <div>
                      <p className="micro-label">Lynch PEG</p>
                      <p className="mt-2 text-xl font-semibold text-white">{formatNumber(detail.lynchValue)}</p>
                    </div>
                    <div>
                      <p className="micro-label">Piotroski</p>
                      <p className="mt-2 text-xl font-semibold text-white">{detail.piotroskiFScore}/9</p>
                    </div>
                    <div>
                      <p className="micro-label">RS percentile</p>
                      <p className="mt-2 text-xl font-semibold text-white">{formatNumber(detail.minerviniRsPercentile)}</p>
                    </div>
                  </div>
                </div>

                <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
                  <p className="micro-label">Snapshot metrics</p>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div><span className="text-slate-400">PE</span><p className="mt-1 text-white">{formatNumber(detail.peRatio)}</p></div>
                    <div><span className="text-slate-400">PB</span><p className="mt-1 text-white">{formatNumber(detail.pbRatio)}</p></div>
                    <div><span className="text-slate-400">Market cap</span><p className="mt-1 text-white">{formatCompactNumber(detail.marketCap)}</p></div>
                    <div><span className="text-slate-400">Fill rate</span><p className="mt-1 text-white">{formatPct((detail.fillRate ?? 0) * 100, 0)}</p></div>
                  </div>
                </div>

                <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
                  <p className="micro-label">Data sources</p>
                  <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-slate-300">
                    {JSON.stringify(detail.snapshot["dataSources"] ?? {}, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="panel p-6">
        <SectionHeader eyebrow="Universe Table" title="Sortable score universe" subtitle="Search and inspect current Phase 2 score outputs." />
        <div className="mt-5 overflow-x-auto">
          {!filteredRows.length ? (
            <EmptyTableMessage title="Selected date ke liye koi scoring rows nahi mile." />
          ) : (
            <table className="command-table w-full">
              <thead>
                <tr>
                  <th>
                    <button onClick={() => setSortKey("symbol")} className="command-sort">Symbol</button>
                  </th>
                  <th>Label</th>
                  <th>
                    <button onClick={() => setSortKey("lynchValue")} className="command-sort">Lynch</button>
                  </th>
                  <th>
                    <button onClick={() => setSortKey("piotroskiFScore")} className="command-sort">Piotroski</button>
                  </th>
                  <th>
                    <button onClick={() => setSortKey("minerviniRsPercentile")} className="command-sort">RS %ile</button>
                  </th>
                  <th>
                    <button onClick={() => setSortKey("fillRate")} className="command-sort">Fill Rate</button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row: ScoringRow) => (
                  <tr
                    key={row.symbol}
                    className={selectedSymbol === row.symbol ? "is-selected" : ""}
                    onClick={() => setSelectedSymbol(row.symbol)}
                  >
                    <td>
                      <div>
                        <p className="font-semibold text-white">{row.symbol}</p>
                        <p className="text-xs text-slate-500">{row.sector ?? "Sector unavailable"}</p>
                      </div>
                    </td>
                    <td>{row.label}</td>
                    <td>{formatNumber(row.lynchValue)}</td>
                    <td>{row.piotroskiFScore}</td>
                    <td>{formatNumber(row.minerviniRsPercentile)}</td>
                    <td>{formatPct((row.fillRate ?? 0) * 100, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
