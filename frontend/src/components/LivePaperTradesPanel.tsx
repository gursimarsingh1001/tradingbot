import { ArrowUpRight, BriefcaseBusiness, CalendarClock, Clock3, Radar, RadioTower, Wallet, XCircle } from "lucide-react";
import { PaperTrade } from "../api/client";
import { formatInr, formatPct, pnlToneClass } from "../utils/formatters";

type Props = {
  trades: PaperTrade[];
  onSelectStock?: (symbol: string) => void;
  title?: string;
  subtitle?: string;
  showPlanned?: boolean;
};

function tradeDateKey(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toISOString().slice(0, 10);
  }
  return value.slice(0, 10);
}

function isClosedStatus(status: string): boolean {
  return status === "WIN" || status === "LOSS";
}

function isToday(value: string | null | undefined): boolean {
  return tradeDateKey(value) === new Date().toISOString().slice(0, 10);
}

function TradeCard({ trade, onSelectStock }: { trade: PaperTrade; onSelectStock?: (symbol: string) => void }) {
  const isPlanned = trade.status === "PLANNED";
  const pnlPositive = (trade.pnlRupees ?? 0) >= 0;

  return (
    <div key={trade.tradeId} className="signal-shell px-5 py-4">
      <div className="edge-glow pointer-events-none" />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="glass-chip text-slate-200">{trade.signalType}</span>
            {trade.direction ? (
              <span
                className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${
                  trade.direction === "SELL" ? "bg-coral/10 text-coral" : "bg-mint/10 text-mint"
                }`}
              >
                {trade.direction}
              </span>
            ) : null}
            {trade.productType ? (
              <span className="rounded-full bg-ocean/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-ocean">
                {trade.productType}
              </span>
            ) : null}
            {trade.planStatus ? (
              <span className="rounded-full bg-amber/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-amber">
                {trade.planStatus}
              </span>
            ) : null}
          </div>
          {trade.stockSymbol ? (
            <button
              type="button"
              onClick={() => onSelectStock?.(trade.stockSymbol as string)}
              className="mt-2 font-display text-3xl font-semibold text-white transition hover:text-ocean"
            >
              {trade.stockSymbol}
            </button>
          ) : (
            <p className="mt-2 font-display text-3xl font-semibold text-white">-</p>
          )}
          <p className="mt-1 text-sm text-slate-300">{trade.strategyName ?? "-"}</p>
          <div className="mt-4 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
            <span className="rounded-full bg-white/5 px-3 py-1">Qty {trade.remainingShares ?? trade.initialShares ?? 0}</span>
            <span className="mono-value rounded-full bg-white/5 px-3 py-1">Avg {formatInr(trade.entryPrice)}</span>
            <span className="mono-value rounded-full bg-white/5 px-3 py-1">Mkt {formatInr(trade.currentPrice ?? trade.exitPrice ?? trade.entryPrice)}</span>
            {trade.plannedForDate ? <span className="rounded-full bg-white/5 px-3 py-1">Planned {trade.plannedForDate}</span> : null}
            <span className="rounded-full bg-white/5 px-3 py-1">
              {trade.holdingHorizonLabel ?? (trade.signalType === "INVESTMENT" ? "Carry forward" : "Same-day square-off")}
            </span>
            {trade.signalType === "INVESTMENT" && trade.daysHeld !== null && trade.daysHeld !== undefined ? (
              <span className="rounded-full bg-white/5 px-3 py-1">
                Held {trade.daysHeld}d
                {trade.daysRemaining !== null && trade.daysRemaining !== undefined ? ` | Left ${trade.daysRemaining}d` : ""}
              </span>
            ) : null}
          </div>
        </div>

        <div className="text-right">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-200">
            {isPlanned ? <Clock3 className="h-3.5 w-3.5" /> : <RadioTower className="h-3.5 w-3.5" />}
            {isPlanned ? "Planned" : trade.status}
          </div>
          <p className={`mono-value mt-3 font-display text-3xl font-semibold ${pnlToneClass(trade.pnlRupees)}`}>
            {pnlPositive ? "+" : ""}
            {formatInr(trade.pnlRupees)}
          </p>
          <p className={`mono-value mt-1 text-sm font-semibold ${pnlToneClass(trade.pnlPct)}`}>
            {trade.pnlPct !== null && trade.pnlPct !== undefined
              ? `${trade.pnlPct >= 0 ? "+" : ""}${formatPct(trade.pnlPct, 2)}`
              : isPlanned
                ? "Awaiting trigger"
                : "-"}
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-400">
            <Wallet className="h-3.5 w-3.5" />
            Capital blocked
          </div>
          <p className="mono-value mt-2 text-sm font-semibold text-white">{formatInr(trade.capitalBlocked)}</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-400">
            <ArrowUpRight className="h-3.5 w-3.5" />
            Current Stop
          </div>
          <p className="mono-value mt-2 text-sm font-semibold text-white">{formatInr(trade.stopLoss)}</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Targets</p>
          <p className="mono-value mt-2 text-sm font-semibold text-white">
            {formatInr(trade.target1)} / {formatInr(trade.target2)} / {formatInr(trade.target3)}
          </p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Target hits</p>
          <p className="mt-2 text-sm font-semibold text-white">
            {trade.targetsHit?.T1 ? "T1 " : ""}
            {trade.targetsHit?.T2 ? "T2 " : ""}
            {trade.targetsHit?.T3 ? "T3" : ""}
            {!trade.targetsHit?.T1 && !trade.targetsHit?.T2 && !trade.targetsHit?.T3 ? "None yet" : ""}
          </p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Holding Plan</p>
          <p className="mt-2 text-sm font-semibold text-white">
            {trade.holdingHorizonLabel ?? (trade.signalType === "INVESTMENT" ? "Carry forward" : "Same-day square-off")}
          </p>
          {trade.signalType === "INVESTMENT" && trade.daysHeld !== null && trade.daysHeld !== undefined ? (
            <p className="mt-1 text-xs text-slate-300">
              Held {trade.daysHeld} day{trade.daysHeld === 1 ? "" : "s"}
              {trade.daysRemaining !== null && trade.daysRemaining !== undefined
                ? ` | ${trade.daysRemaining} day${trade.daysRemaining === 1 ? "" : "s"} left`
                : ""}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  subtitle,
  trades,
  icon,
  emptyMessage,
  onSelectStock,
}: {
  title: string;
  subtitle: string;
  trades: PaperTrade[];
  icon: JSX.Element;
  emptyMessage: string;
  onSelectStock?: (symbol: string) => void;
}) {
  return (
    <div className="rounded-[1.75rem] border border-white/10 bg-white/5 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="rounded-full border border-white/10 bg-white/5 p-2 text-slate-200">{icon}</div>
          <div>
            <p className="text-sm font-semibold text-white">{title}</p>
            <p className="text-xs uppercase tracking-[0.16em] text-slate-400">{subtitle}</p>
          </div>
        </div>
        <p className="font-display text-2xl font-semibold text-white">{trades.length}</p>
      </div>
      <div className="mt-4 space-y-3">
        {trades.length ? (
          trades.map((trade) => <TradeCard key={trade.tradeId} trade={trade} onSelectStock={onSelectStock} />)
        ) : (
          <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-5 text-sm text-slate-400">{emptyMessage}</div>
        )}
      </div>
    </div>
  );
}

export function LivePaperTradesPanel({
  trades,
  onSelectStock,
  title = "Live Paper Trades",
  subtitle = "Open positions with live price and live P&L movement.",
  showPlanned = true,
}: Props) {
  const openTrades = trades.filter((trade) => trade.status === "OPEN");
  const plannedTrades = showPlanned ? trades.filter((trade) => trade.status === "PLANNED") : [];
  const openIntraday = openTrades.filter((trade) => trade.signalType === "INTRADAY");
  const openInvestment = openTrades.filter((trade) => trade.signalType === "INVESTMENT");
  const plannedIntraday = plannedTrades.filter((trade) => trade.signalType === "INTRADAY");
  const plannedInvestment = plannedTrades.filter((trade) => trade.signalType === "INVESTMENT");
  const missedIntradayToday = trades.filter(
    (trade) => trade.signalType === "INTRADAY" && trade.status === "MISSED" && isToday(trade.plannedForDate ?? trade.exitDate ?? trade.entryDate),
  );
  const closedToday = trades.filter((trade) => isClosedStatus(trade.status) && isToday(trade.exitDate ?? trade.entryDate));
  const openIntradayPnl = openIntraday.reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0);
  const openInvestmentPnl = openInvestment.reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0);

  return (
    <div className="panel p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="chrome-kicker">Execution center</div>
          <h2 className="mt-2 font-display text-2xl font-semibold text-ink">{title}</h2>
          <p className="mt-2 text-sm text-slate-500">{subtitle}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-7">
          <div className="hero-stat px-5 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Intraday P&amp;L</p>
            <p className={`mono-value mt-2 font-display text-3xl font-semibold ${pnlToneClass(openIntradayPnl)}`}>
              {openIntradayPnl >= 0 ? "+" : ""}
              {formatInr(openIntradayPnl)}
            </p>
          </div>
          <div className="hero-stat px-5 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Investment P&amp;L</p>
            <p className={`mono-value mt-2 font-display text-3xl font-semibold ${pnlToneClass(openInvestmentPnl)}`}>
              {openInvestmentPnl >= 0 ? "+" : ""}
              {formatInr(openInvestmentPnl)}
            </p>
          </div>
          <div className="hero-stat px-5 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Open intraday</p>
            <p className="mt-2 font-display text-3xl font-semibold text-white">{openIntraday.length}</p>
          </div>
          <div className="hero-stat px-5 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Open investment</p>
            <p className="mt-2 font-display text-3xl font-semibold text-white">{openInvestment.length}</p>
          </div>
          <div className="hero-stat px-5 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Planned total</p>
            <p className="mt-2 font-display text-3xl font-semibold text-white">{plannedTrades.length}</p>
          </div>
          <div className="hero-stat px-5 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Missed today</p>
            <p className="mt-2 font-display text-3xl font-semibold text-white">{missedIntradayToday.length}</p>
          </div>
          <div className="hero-stat px-5 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Closed today</p>
            <p className="mt-2 font-display text-3xl font-semibold text-white">{closedToday.length}</p>
          </div>
        </div>
      </div>

      <div className="mt-5 flex items-center gap-2 rounded-[1.5rem] border border-white/10 bg-white/5 px-4 py-3 text-sm text-slate-300">
        <Radar className="h-4 w-4 text-ocean" />
        Open positions stream live mark-to-market, while planned positions stay visible till trigger, miss, or carry-forward conversion.
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <Section
          title="Open Intraday"
          subtitle="Same-session live positions"
          trades={openIntraday}
          icon={<RadioTower className="h-4 w-4" />}
          emptyMessage="No live intraday positions right now."
          onSelectStock={onSelectStock}
        />
        <Section
          title="Open Investment"
          subtitle="Swing and positional live positions"
          trades={openInvestment}
          icon={<BriefcaseBusiness className="h-4 w-4" />}
          emptyMessage="No live investment positions right now."
          onSelectStock={onSelectStock}
        />
        {showPlanned ? (
          <Section
            title="Planned Intraday"
            subtitle="Waiting for same-session trigger"
            trades={plannedIntraday}
            icon={<Clock3 className="h-4 w-4" />}
            emptyMessage="No intraday plans are waiting for a trigger right now."
            onSelectStock={onSelectStock}
          />
        ) : null}
        {showPlanned ? (
          <Section
            title="Planned Investment"
            subtitle="Waiting for positional trigger"
            trades={plannedInvestment}
            icon={<CalendarClock className="h-4 w-4" />}
            emptyMessage="No investment plans are waiting for a trigger right now."
            onSelectStock={onSelectStock}
          />
        ) : null}
        <Section
          title="Missed Intraday Today"
          subtitle="Plans that never triggered in time"
          trades={missedIntradayToday}
          icon={<XCircle className="h-4 w-4" />}
          emptyMessage="No intraday plans have been missed today."
          onSelectStock={onSelectStock}
        />
        <Section
          title="Closed Today"
          subtitle="Win and loss outcomes from today"
          trades={closedToday}
          icon={<BriefcaseBusiness className="h-4 w-4" />}
          emptyMessage="No trades have closed today yet."
          onSelectStock={onSelectStock}
        />
      </div>
    </div>
  );
}
