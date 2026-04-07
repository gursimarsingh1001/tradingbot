import { PaperTrade } from "../api/client";
import { formatInr, formatPct } from "../utils/formatters";

type Props = {
  trades: PaperTrade[];
  onSelectStock?: (symbol: string) => void;
};

function targetLabel(hit: boolean | undefined): string {
  return hit ? "Hit" : "Pending";
}

function statusTone(status: string): string {
  switch (status) {
    case "OPEN":
      return "bg-ocean/10 text-ocean";
    case "PLANNED":
      return "bg-amber/10 text-amber";
    case "WIN":
      return "bg-mint/10 text-mint";
    case "LOSS":
      return "bg-coral/10 text-coral";
    case "MISSED":
      return "bg-slate-200 text-slate-700";
    default:
      return "bg-slate-100 text-slate-700";
  }
}

export function PaperTradeTable({ trades, onSelectStock }: Props) {
  return (
    <div className="panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-100 text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-[0.18em] text-slate-500">
            <tr>
              {["Stock", "Type / Side", "Strategy", "Entry", "Market / Exit", "Current Stop", "Targets", "P&L", "Status", "Exit"].map(
                (header) => (
                  <th key={header} className="px-4 py-4 font-medium">
                    {header}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white/70">
            {trades.map((trade) => {
              const isPlanned = trade.status === "PLANNED";
              const displayPrice = trade.currentPrice ?? trade.exitPrice ?? trade.entryPrice ?? 0;

              return (
                <tr key={trade.tradeId} className={isPlanned ? "bg-amber/5" : ""}>
                  <td className="px-4 py-4 font-semibold text-ink">
                    {trade.stockSymbol ? (
                      <div className="flex flex-col gap-1">
                        <button
                          type="button"
                          onClick={() => onSelectStock?.(trade.stockSymbol as string)}
                          className="w-fit transition hover:text-ocean"
                        >
                          {trade.stockSymbol}
                        </button>
                        {trade.plannedForDate ? <span className="text-xs text-slate-500">For {trade.plannedForDate}</span> : null}
                      </div>
                    ) : (
                      "-"
                    )}
                  </td>
                  <td className="px-4 py-4">
                    <div className="flex flex-col gap-1">
                      <span>{trade.signalType ?? "-"}</span>
                      {trade.direction ? (
                        <span
                          className={`inline-flex w-fit rounded-full px-2.5 py-1 text-xs font-semibold uppercase tracking-[0.14em] ${
                            trade.direction === "SELL" ? "bg-coral/10 text-coral" : "bg-mint/10 text-mint"
                          }`}
                        >
                          {trade.direction}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">Direction pending</span>
                      )}
                      <span className="text-xs text-slate-500">
                        {trade.holdingHorizonLabel ??
                          (trade.signalType === "INVESTMENT" ? "Carry forward" : "Same-day square-off")}
                      </span>
                      {trade.signalType === "INVESTMENT" && trade.daysHeld !== null && trade.daysHeld !== undefined ? (
                        <span className="text-xs text-slate-500">
                          Held {trade.daysHeld}d
                          {trade.daysRemaining !== null && trade.daysRemaining !== undefined ? ` | Left ${trade.daysRemaining}d` : ""}
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-4 py-4">
                    <div className="flex flex-col gap-1">
                      <span>{trade.strategyName ?? "-"}</span>
                      <span className="text-xs text-slate-500">
                        {trade.productType ?? "CASH"}
                        {trade.leverageMultiplier ? ` | ${trade.leverageMultiplier.toFixed(2)}x` : ""}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-4">{formatInr(trade.entryPrice)}</td>
                  <td className="px-4 py-4">
                    <div className="flex flex-col gap-1">
                      <span>{formatInr(displayPrice)}</span>
                      {trade.status === "OPEN" ? <span className="text-xs text-ocean">Live market price</span> : null}
                      {isPlanned ? <span className="text-xs text-amber">Trigger pending</span> : null}
                    </div>
                  </td>
                  <td className="px-4 py-4">{formatInr(trade.stopLoss)}</td>
                  <td className="px-4 py-4">
                    <div className="flex flex-col gap-1 text-xs text-slate-600">
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
                  <td className={`px-4 py-4 font-semibold ${(trade.pnlRupees ?? 0) >= 0 ? "text-mint" : "text-coral"}`}>
                    {formatInr(trade.pnlRupees)} / {formatPct(trade.pnlPct, 2)}
                  </td>
                  <td className="px-4 py-4">
                    <div className="flex flex-col gap-2">
                      <span className={`inline-flex w-fit rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] ${statusTone(trade.status)}`}>
                        {trade.status}
                      </span>
                      {trade.planStatus ? <span className="text-xs text-slate-500">{trade.planStatus}</span> : null}
                    </div>
                  </td>
                  <td className="px-4 py-4">
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
                      {trade.exitReason ?? (isPlanned ? "PENDING" : "OPEN")}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
