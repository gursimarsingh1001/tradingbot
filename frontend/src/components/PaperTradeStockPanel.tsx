import { X } from "lucide-react";
import { PaperTrade, StockPaperTradeDetail } from "../api/client";
import { formatDateTime, formatInr, formatPct } from "../utils/formatters";

type Props = {
  detail: StockPaperTradeDetail | null;
  isOpen: boolean;
  symbol: string | null;
  isLoading?: boolean;
  onClose: () => void;
};

function tradeStatusLabel(trade: PaperTrade): string {
  return trade.status || "OPEN";
}

function targetLabel(hit: boolean | undefined): string {
  return hit ? "Hit" : "Pending";
}

export function PaperTradeStockPanel({ detail, isOpen, symbol, isLoading = false, onClose }: Props) {
  if (!isOpen) {
    return null;
  }

  const highlightedTrade = detail?.trades.find((trade) => trade.status === "OPEN") ?? detail?.trades[0] ?? null;
  const livePrice = highlightedTrade?.currentPrice ?? highlightedTrade?.exitPrice ?? null;
  const direction = highlightedTrade?.direction ?? null;

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-end bg-slate-950/65 backdrop-blur-sm">
      <div className="h-full w-full max-w-5xl overflow-y-auto border-l border-white/10 bg-[#09111f] p-6 text-white shadow-[0_24px_60px_rgba(2,6,23,0.55)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-400">Paper Trade History</p>
            <h3 className="mt-2 font-display text-3xl font-semibold">{detail?.stockSymbol ?? symbol ?? "-"}</h3>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              {detail
                ? `Showing the last ${detail.days} days of executed paper trades, strategies used, win rate, and P&L.`
                : "Loading paper-trade history for this stock."}
            </p>
          </div>
          <button
            onClick={onClose}
            className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/5 text-slate-200 transition hover:bg-white/10"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {!detail ? (
          <div className="mt-6 rounded-[2rem] border border-white/10 bg-white/5 p-6 text-sm text-slate-300">
            {isLoading ? "Loading paper-trade detail..." : "No paper-trade detail found for this stock in the selected range."}
          </div>
        ) : (
          <>
            <div className="mt-6 grid gap-4 md:grid-cols-7">
              <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Total Trades</p>
                <p className="mt-3 text-2xl font-semibold">{detail.totalTrades}</p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Win Rate</p>
                <p className="mt-3 text-2xl font-semibold">{formatPct(detail.winRate * 100, 1)}</p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Total P&amp;L</p>
                <p className={`mt-3 text-2xl font-semibold ${detail.totalPnlRupees >= 0 ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
                  {formatInr(detail.totalPnlRupees)}
                </p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Average P&amp;L</p>
                <p className={`mt-3 text-2xl font-semibold ${detail.avgPnlPct >= 0 ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
                  {formatPct(detail.avgPnlPct, 2)}
                </p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Best Strategy</p>
                <p className="mt-3 text-xl font-semibold">{detail.bestStrategy ?? "-"}</p>
                <p className="mt-2 text-sm text-slate-300">
                  Wins {detail.wins} | Losses {detail.losses} | Open {detail.openTrades}
                </p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Live Price / Side</p>
                <p className="mt-3 text-2xl font-semibold">{formatInr(livePrice)}</p>
                <p className="mt-2 text-sm text-slate-300">
                  {direction ? (
                    <span className={direction === "SELL" ? "text-[#ff8b5e]" : "text-[#4be1c3]"}>{direction}</span>
                  ) : (
                    "Direction pending"
                  )}
                  {" | "}
                  {highlightedTrade ? tradeStatusLabel(highlightedTrade) : "No trade"}
                </p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Holding Plan</p>
                <p className="mt-3 text-xl font-semibold">
                  {highlightedTrade?.holdingHorizonLabel ??
                    (highlightedTrade?.signalType === "INVESTMENT" ? "Carry forward" : "Same-day square-off")}
                </p>
                <p className="mt-2 text-sm text-slate-300">
                  {highlightedTrade?.signalType === "INVESTMENT"
                    ? highlightedTrade?.daysHeld !== null && highlightedTrade?.daysHeld !== undefined
                      ? `Held ${highlightedTrade.daysHeld}d${highlightedTrade.daysRemaining !== null && highlightedTrade.daysRemaining !== undefined ? ` | Left ${highlightedTrade.daysRemaining}d` : ""}`
                      : "Investment trades carry forward across days"
                    : "Intraday trades square off the same day"}
                </p>
              </div>
            </div>

            <section className="mt-6 rounded-[2rem] border border-white/10 bg-white/5 p-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-white">Strategy Usage</p>
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Which strategies this stock used and how they performed</p>
                </div>
              </div>
              <div className="mt-4 overflow-x-auto">
                <table className="min-w-full divide-y divide-white/10 text-sm">
                  <thead className="text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                    <tr>
                      {["Strategy", "Trades", "Wins", "Losses", "Open", "Win Rate", "Total P&L", "Avg P&L", "Last Used"].map((header) => (
                        <th key={header} className="px-3 py-3 font-medium">
                          {header}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {detail.strategies.map((row) => (
                      <tr key={row.strategyName}>
                        <td className="px-3 py-3 font-semibold text-white">{row.strategyName}</td>
                        <td className="px-3 py-3 text-slate-300">{row.trades}</td>
                        <td className="px-3 py-3 text-[#4be1c3]">{row.wins}</td>
                        <td className="px-3 py-3 text-[#ff8b5e]">{row.losses}</td>
                        <td className="px-3 py-3 text-slate-300">{row.openTrades}</td>
                        <td className="px-3 py-3 text-[#5aa6ff]">{formatPct(row.winRate * 100, 1)}</td>
                        <td className={`px-3 py-3 font-semibold ${row.totalPnlRupees >= 0 ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
                          {formatInr(row.totalPnlRupees)}
                        </td>
                        <td className={`px-3 py-3 font-semibold ${row.avgPnlPct >= 0 ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
                          {formatPct(row.avgPnlPct, 2)}
                        </td>
                        <td className="px-3 py-3 text-slate-300">{row.lastUsedOn ?? "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="mt-6 rounded-[2rem] border border-white/10 bg-white/5 p-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-white">Day-Wise Summary</p>
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-400">All past trade days for this stock</p>
                </div>
              </div>
              <div className="mt-4 overflow-x-auto">
                <table className="min-w-full divide-y divide-white/10 text-sm">
                  <thead className="text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                    <tr>
                      {["Date", "Trades", "Wins", "Losses", "Open", "Total P&L", "Avg P&L"].map((header) => (
                        <th key={header} className="px-3 py-3 font-medium">
                          {header}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {detail.dailySummary.map((row) => (
                      <tr key={row.tradeDate}>
                        <td className="px-3 py-3 text-slate-300">{row.tradeDate}</td>
                        <td className="px-3 py-3 text-slate-300">{row.trades}</td>
                        <td className="px-3 py-3 text-[#4be1c3]">{row.wins}</td>
                        <td className="px-3 py-3 text-[#ff8b5e]">{row.losses}</td>
                        <td className="px-3 py-3 text-slate-300">{row.openTrades}</td>
                        <td className={`px-3 py-3 font-semibold ${row.totalPnlRupees >= 0 ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
                          {formatInr(row.totalPnlRupees)}
                        </td>
                        <td className={`px-3 py-3 font-semibold ${row.avgPnlPct >= 0 ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
                          {formatPct(row.avgPnlPct, 2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="mt-6 rounded-[2rem] border border-white/10 bg-white/5 p-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-white">Trade List</p>
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Every executed paper trade for this stock</p>
                </div>
              </div>
              <div className="mt-4 overflow-x-auto">
                <table className="min-w-full divide-y divide-white/10 text-sm">
                  <thead className="text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                    <tr>
                      {["Date", "Strategy", "Type", "Side", "Hold", "Entry", "Exit / Current", "Current Stop", "Targets", "P&L", "Status", "Exit Reason"].map((header) => (
                        <th key={header} className="px-3 py-3 font-medium">
                          {header}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {detail.trades.map((trade) => (
                      <tr key={trade.tradeId}>
                        <td className="px-3 py-3 text-slate-300">{trade.entryDate ?? "-"}</td>
                        <td className="px-3 py-3 font-semibold text-white">{trade.strategyName ?? "-"}</td>
                        <td className="px-3 py-3 text-slate-300">{trade.signalType ?? "-"}</td>
                        <td className="px-3 py-3">
                          {trade.direction ? (
                            <span
                              className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold uppercase tracking-[0.14em] ${
                                trade.direction === "SELL" ? "bg-[#ff8b5e]/10 text-[#ff8b5e]" : "bg-[#4be1c3]/10 text-[#4be1c3]"
                              }`}
                            >
                              {trade.direction}
                            </span>
                          ) : (
                            <span className="text-slate-300">-</span>
                          )}
                        </td>
                        <td className="px-3 py-3 text-slate-300">
                          <div className="flex flex-col gap-1 text-xs">
                            <span>{trade.holdingHorizonLabel ?? (trade.signalType === "INVESTMENT" ? "Carry forward" : "Same-day square-off")}</span>
                            {trade.signalType === "INVESTMENT" && trade.daysHeld !== null && trade.daysHeld !== undefined ? (
                              <span>
                                Held {trade.daysHeld}d
                                {trade.daysRemaining !== null && trade.daysRemaining !== undefined ? ` | Left ${trade.daysRemaining}d` : ""}
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-slate-300">{formatInr(trade.entryPrice)}</td>
                        <td className="px-3 py-3 text-slate-300">
                          <div className="flex flex-col gap-1">
                            <span>{formatInr(trade.exitPrice ?? trade.currentPrice)}</span>
                            {trade.status === "OPEN" ? <span className="text-xs text-[#5aa6ff]">Live market price</span> : null}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-slate-300">{formatInr(trade.stopLoss)}</td>
                        <td className="px-3 py-3 text-slate-300">
                          <div className="flex flex-col gap-1 text-xs">
                            <span>
                              T1 {formatInr(trade.target1)} � {targetLabel(trade.targetsHit?.T1)}
                            </span>
                            <span>
                              T2 {formatInr(trade.target2)} � {targetLabel(trade.targetsHit?.T2)}
                            </span>
                            <span>
                              T3 {formatInr(trade.target3)} � {targetLabel(trade.targetsHit?.T3)}
                            </span>
                          </div>
                        </td>
                        <td className={`px-3 py-3 font-semibold ${(trade.pnlRupees ?? 0) >= 0 ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
                          {formatInr(trade.pnlRupees)} / {formatPct(trade.pnlPct, 2)}
                        </td>
                        <td className="px-3 py-3 text-slate-300">{tradeStatusLabel(trade)}</td>
                        <td className="px-3 py-3 text-slate-300">{trade.exitReason ?? formatDateTime(trade.exitDate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
