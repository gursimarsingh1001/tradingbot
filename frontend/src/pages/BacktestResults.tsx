import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";

function formatMetric(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toFixed(decimals);
}

export default function BacktestResults() {
  const summaryQuery = useQuery({ queryKey: ["backtestSummary"], queryFn: api.fetchBacktestSummary, refetchInterval: 10000 });
  const progressQuery = useQuery({ queryKey: ["backtestProgress"], queryFn: api.fetchBacktestProgress, refetchInterval: 5000 });
  const runMutation = useMutation({ mutationFn: () => api.runFullBacktest(0) });
  const [selectedSymbol, setSelectedSymbol] = useState<string>("");
  const stockRows = (summaryQuery.data?.stocks ?? []).filter((row) => row.bestStrategy && row.compositeScore !== null);
  const progressMessage = progressQuery.data?.message ?? "Idle";
  const progressMatch = progressMessage.match(/(?:Backtested|Backfilled|Skipped) (\d+) of (\d+)/i);
  const completed = progressMatch ? Number(progressMatch[1]) : stockRows.length;
  const total = progressMatch ? Number(progressMatch[2]) : stockRows.length;
  const remaining = Math.max(total - completed, 0);
  const isRunning = Boolean(progressQuery.data?.active);

  useEffect(() => {
    const preferredSymbol =
      stockRows[0]?.symbol;
    if (!selectedSymbol && preferredSymbol) {
      setSelectedSymbol(preferredSymbol);
    }
  }, [selectedSymbol, stockRows]);

  const detailQuery = useQuery({
    queryKey: ["backtestStock", selectedSymbol],
    queryFn: () => api.fetchBacktestStock(selectedSymbol),
    enabled: Boolean(selectedSymbol),
    refetchInterval: 10000,
  });

  return (
    <div className="space-y-6">
      <section className="grid gap-4 xl:grid-cols-3">
        <div className="panel p-5">
          <p className="text-sm text-slate-500">Backtest status</p>
          <p className={`mt-3 font-display text-3xl font-semibold ${isRunning ? "text-ocean" : "text-ink"}`}>
            {isRunning ? "Running" : "Ready"}
          </p>
          <p className="mt-2 text-sm text-slate-500">{progressMessage}</p>
        </div>
        <div className="panel p-5">
          <p className="text-sm text-slate-500">Stocks processed</p>
          <p className="mt-3 font-display text-3xl font-semibold text-ocean">{completed} / {total}</p>
          <p className="mt-2 text-sm text-slate-500">{remaining} remaining</p>
        </div>
        <div className="panel p-5">
          <p className="text-sm text-slate-500">Current global best strategy</p>
          <p className="mt-3 font-display text-3xl font-semibold text-mint">{summaryQuery.data?.globalBestStrategy ?? "Pending"}</p>
          <p className="mt-2 text-sm text-slate-500">
            Wins on {summaryQuery.data?.globalBestStrategyStockCount ?? 0} stocks, median Sharpe {formatMetric(summaryQuery.data?.medianSharpeRatio)}
          </p>
        </div>
      </section>

      {(summaryQuery.isError || detailQuery.isError) && (
        <section className="rounded-3xl border border-coral/30 bg-coral/10 px-5 py-4 text-sm text-coral">
          {summaryQuery.isError && <p>Backtest summary is temporarily unavailable while the dashboard refreshes live results.</p>}
          {detailQuery.isError && <p className={summaryQuery.isError ? "mt-2" : ""}>The selected stock detail is still being prepared. Pick another stock or wait a moment and retry.</p>}
        </section>
      )}

      <section className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="section-title">Full Backtest</h2>
            <p className="mt-1 text-sm text-slate-500">Run monthly or during setup. Progress updates as the full loaded NSE universe works through the backtest engine.</p>
          </div>
          <button
            onClick={() => runMutation.mutate()}
            disabled={isRunning || runMutation.isPending}
            className="rounded-full bg-ocean px-5 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {isRunning ? "Backtest Running" : "Run Full NSE Backtest"}
          </button>
        </div>
        <div className="mt-5 h-4 rounded-full bg-slate-100">
          <div className="h-4 rounded-full bg-mint" style={{ width: `${progressQuery.data?.progress ?? 0}%` }} />
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500">
          <span>{progressMessage}</span>
          <span>{progressQuery.data?.progress ?? 0}% complete</span>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="panel p-6">
          <h2 className="section-title">Best Strategy by Stock</h2>
          <div className="mt-4 max-h-[480px] overflow-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-[0.18em] text-slate-500">
                <tr>
                  {["Symbol", "Best Strategy", "Composite", "Sharpe", "Win Rate"].map((header) => (
                    <th key={header} className="px-3 py-3">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stockRows.map((row) => (
                  <tr
                    key={row.symbol}
                    onClick={() => setSelectedSymbol(row.symbol)}
                    className={`cursor-pointer border-t border-slate-100 ${selectedSymbol === row.symbol ? "bg-ocean/5" : "hover:bg-slate-50"}`}
                  >
                    <td className="px-3 py-3 font-semibold text-ink">{row.symbol}</td>
                    <td className="px-3 py-3">{row.bestStrategy}</td>
                    <td className="px-3 py-3">{formatMetric(row.compositeScore)}</td>
                    <td className="px-3 py-3">{formatMetric(row.sharpeRatio)}</td>
                    <td className="px-3 py-3">{formatMetric(row.winRate !== null && row.winRate !== undefined ? row.winRate * 100 : null, 1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel p-6">
          <h2 className="section-title">Strategy Comparison</h2>
          <div className="mt-4 h-[440px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={summaryQuery.data?.strategyComparison ?? []}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="strategyName" angle={-25} textAnchor="end" height={120} interval={0} tick={{ fontSize: 11, fill: "#475569" }} />
                <YAxis tick={{ fill: "#475569" }} />
                <Tooltip />
                <Bar dataKey="avgSharpeRatio" fill="#134074" radius={[10, 10, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="panel p-6">
          <h2 className="section-title">Selected Stock Drilldown</h2>
          <p className="mt-1 text-sm text-slate-500">{selectedSymbol || "Pick a stock from the table"}</p>
          <div className="mt-4 space-y-3">
            {detailQuery.data?.strategies.map((strategy) => (
              <div key={strategy.strategyName} className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-4">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="font-semibold text-ink">{strategy.strategyName}</p>
                    <p className="text-sm text-slate-500">{strategy.trades} trades</p>
                  </div>
                  <div className="text-right text-sm">
                    <p className="font-semibold text-mint">{formatMetric(strategy.totalReturn)}%</p>
                    <p className="text-slate-500">Win {formatMetric(strategy.winRate * 100, 0)}%</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="panel p-6">
          <h2 className="section-title">Walk-Forward Equity Curve</h2>
          <div className="mt-4 h-[320px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={detailQuery.data?.walkForwardCurve ?? []}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: "#475569", fontSize: 12 }} />
                <YAxis tick={{ fill: "#475569", fontSize: 12 }} />
                <Tooltip />
                <Line dataKey="equity" stroke="#2a9d8f" strokeWidth={3} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </section>
    </div>
  );
}
