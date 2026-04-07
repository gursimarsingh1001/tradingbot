import { PieChart, Wallet } from "lucide-react";
import { PaperTradeObservation } from "../api/client";
import { formatInr, formatPct, pnlToneClass } from "../utils/formatters";

type Props = {
  observation?: PaperTradeObservation | null;
  compact?: boolean;
};

type BucketCardProps = {
  title: string;
  accentTextClass: string;
  accentBarClass: string;
  bookPnl: number;
  baseBudget: number;
  budget: number;
  openBlocked: number;
  plannedBlocked: number;
  available: number;
};

function usagePct(used: number, budget: number): number {
  if (!budget) {
    return 0;
  }
  return Math.max(0, Math.min((used / budget) * 100, 100));
}

function BucketCard({
  title,
  accentTextClass,
  accentBarClass,
  bookPnl,
  baseBudget,
  budget,
  openBlocked,
  plannedBlocked,
  available,
}: BucketCardProps) {
  const openPct = usagePct(openBlocked, budget);
  const totalReservedPct = usagePct(openBlocked + plannedBlocked, budget);

  return (
    <div className="signal-shell p-5">
      <div className="edge-glow pointer-events-none" />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className={`text-xs font-semibold uppercase tracking-[0.18em] ${accentTextClass}`}>{title}</p>
          <p className="mono-value mt-3 whitespace-nowrap font-display text-[clamp(2rem,2.2vw,2.65rem)] font-semibold leading-none tracking-[-0.05em] text-white">
            {formatInr(budget)}
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-400">Base {formatInr(baseBudget)} with book P&amp;L adjustment</p>
        </div>
        <div className="glass-chip">
          <span className="mono-value">{formatPct(totalReservedPct, 0)}</span> reserved
        </div>
      </div>

      <div className="mt-5 space-y-3">
        <div>
          <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.16em] text-slate-400">
            <span>Open capital in market</span>
            <span className="mono-value">{formatPct(openPct, 0)}</span>
          </div>
          <div className="h-2 rounded-full bg-white/10">
            <div className={`h-2 rounded-full ${accentBarClass}`} style={{ width: `${openPct}%` }} />
          </div>
        </div>
        <div>
          <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.16em] text-slate-400">
            <span>Open + planned reserved</span>
            <span className="mono-value">{formatPct(totalReservedPct, 0)}</span>
          </div>
          <div className="h-2 rounded-full bg-white/10">
            <div className="h-2 rounded-full bg-white/40" style={{ width: `${totalReservedPct}%` }} />
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <div className="min-w-0 rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Book P&amp;L</p>
          <p className={`mono-value mt-2 break-words text-[0.95rem] font-semibold leading-5 ${pnlToneClass(bookPnl)}`}>
            {bookPnl >= 0 ? "+" : ""}
            {formatInr(bookPnl)}
          </p>
        </div>
        <div className="min-w-0 rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Open blocked</p>
          <p className="mono-value mt-2 break-words text-[0.95rem] font-semibold leading-5 text-white">{formatInr(openBlocked)}</p>
        </div>
        <div className="min-w-0 rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Planned reserved</p>
          <p className="mono-value mt-2 break-words text-[0.95rem] font-semibold leading-5 text-white">{formatInr(plannedBlocked)}</p>
        </div>
        <div className="min-w-0 rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
          <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Available now</p>
          <p className="mono-value mt-2 break-words text-[0.95rem] font-semibold leading-5 text-white">{formatInr(available)}</p>
        </div>
      </div>
    </div>
  );
}

export function AllocationSplitPanel({ observation, compact = false }: Props) {
  if (!observation) {
    return (
      <div className="panel p-6">
        <div className="flex items-center gap-3">
          <div className="rounded-full border border-white/10 bg-white/5 p-3 text-slate-200">
            <Wallet className="h-5 w-5" />
          </div>
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Capital Split</p>
            <h2 className="mt-2 font-display text-2xl font-semibold text-ink">50 / 50 paper-trade allocation</h2>
          </div>
        </div>
        <div className="mt-5 rounded-[1.5rem] border border-white/10 bg-white/5 px-5 py-6 text-sm text-slate-400">
          Allocation summary will appear here once the paper-trade observation feed loads.
        </div>
      </div>
    );
  }

  return (
    <div className="panel p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="chrome-kicker">Capital split</div>
          <h2 className="mt-4 font-display text-2xl font-semibold text-ink">50 / 50 paper-trade allocation</h2>
          <p className="mt-2 text-sm text-slate-500">
            Intraday aur investment dono ko ab fixed dedicated capital pool milta hai.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="hero-stat px-5 py-4 text-white">
            <p className="micro-label">Total paper capital</p>
            <p className="mono-value mt-2 font-display text-3xl font-semibold text-white">{formatInr(observation.portfolioValue)}</p>
          </div>
          <div className="hero-stat px-5 py-4">
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-slate-400">
              <PieChart className="h-4 w-4" />
              Live reservation
            </div>
            <p className="mono-value mt-2 font-display text-3xl font-semibold text-white">
              {formatInr(observation.intradayOpenCapitalBlocked + observation.investmentOpenCapitalBlocked)}
            </p>
            <p className="mt-2 text-xs text-slate-400">
              Open capital currently deployed across both books
            </p>
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-3 lg:grid-cols-4">
        <div className="hero-stat px-4 py-4">
          <p className="micro-label">Executed trades</p>
          <p className="mt-2 font-display text-3xl font-semibold text-white">{observation.executedTrades}</p>
        </div>
        <div className="hero-stat px-4 py-4">
          <p className="micro-label">Open trades</p>
          <p className="mt-2 font-display text-3xl font-semibold text-white">{observation.openTrades}</p>
        </div>
        <div className="hero-stat px-4 py-4">
          <p className="micro-label">Planned queue</p>
          <p className="mt-2 font-display text-3xl font-semibold text-white">{observation.plannedTrades}</p>
        </div>
        <div className="hero-stat px-4 py-4">
          <p className="micro-label">Total live P&amp;L</p>
          <p className={`mono-value mt-2 font-display text-3xl font-semibold ${pnlToneClass(observation.totalPnlRupees)}`}>
            {observation.totalPnlRupees >= 0 ? "+" : ""}
            {formatInr(observation.totalPnlRupees)}
          </p>
        </div>
      </div>

      <div className={`mt-5 grid gap-4 ${compact ? "xl:grid-cols-2" : "xl:grid-cols-2"}`}>
        <BucketCard
          title="Intraday Book"
          accentTextClass="text-coral"
          accentBarClass="bg-coral"
          bookPnl={observation.intradayBookPnlRupees}
          baseBudget={observation.intradayBaseBudget}
          budget={observation.intradayBudget}
          openBlocked={observation.intradayOpenCapitalBlocked}
          plannedBlocked={observation.intradayPlannedCapitalBlocked}
          available={observation.intradayAvailableCapital}
        />
        <BucketCard
          title="Investment Book"
          accentTextClass="text-mint"
          accentBarClass="bg-mint"
          bookPnl={observation.investmentBookPnlRupees}
          baseBudget={observation.investmentBaseBudget}
          budget={observation.investmentBudget}
          openBlocked={observation.investmentOpenCapitalBlocked}
          plannedBlocked={observation.investmentPlannedCapitalBlocked}
          available={observation.investmentAvailableCapital}
        />
      </div>
    </div>
  );
}
