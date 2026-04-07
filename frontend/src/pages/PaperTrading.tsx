import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CandlestickChart, Radar, ShieldCheck, TimerReset } from "lucide-react";
import { api, LivePayload, PaperTrade, StockPaperTradeDetail } from "../api/client";
import { AllocationSplitPanel } from "../components/AllocationSplitPanel";
import { EquityCurveChart } from "../components/EquityCurveChart";
import { LivePaperTradesPanel } from "../components/LivePaperTradesPanel";
import { PaperTradeStockPanel } from "../components/PaperTradeStockPanel";
import { PaperTradeTable } from "../components/PaperTradeTable";
import { formatInr, pnlToneClass } from "../utils/formatters";

type Props = {
  liveData?: LivePayload | null;
};

type LivePriceMap = Map<string, { price: number | null; changePct: number | null }>;

type TradeView = "ALL" | "OPEN" | "INTRADAY" | "INVESTMENT" | "PLANNED" | "MISSED" | "CLOSED_TODAY";

function toDayKey(value: string | null | undefined): string | null {
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

function isTodayTrade(trade: PaperTrade): boolean {
  const todayKey = new Date().toISOString().slice(0, 10);
  return [trade.exitDate, trade.entryDate, trade.plannedForDate].some((value) => toDayKey(value) === todayKey);
}

function mergeLiveTrades(trades: PaperTrade[], liveTrades: PaperTrade[] | undefined, livePriceMap?: LivePriceMap): PaperTrade[] {
  if (!liveTrades?.length && !livePriceMap?.size) {
    return trades;
  }
  const liveMap = new Map((liveTrades ?? []).map((trade) => [trade.tradeId, trade]));
  const mergedTrades = trades.map((trade) => {
    const live = liveMap.get(trade.tradeId);
    const merged = live ? { ...trade, ...live } : { ...trade };
    if ((merged.currentPrice === null || merged.currentPrice === undefined) && merged.stockSymbol && livePriceMap?.has(merged.stockSymbol)) {
      merged.currentPrice = livePriceMap.get(merged.stockSymbol)?.price ?? merged.currentPrice;
    }
    return merged;
  });
  const knownTradeIds = new Set(mergedTrades.map((trade) => trade.tradeId));
  for (const live of liveTrades ?? []) {
    if (!knownTradeIds.has(live.tradeId)) {
      const extra = { ...live };
      if ((extra.currentPrice === null || extra.currentPrice === undefined) && extra.stockSymbol && livePriceMap?.has(extra.stockSymbol)) {
        extra.currentPrice = livePriceMap.get(extra.stockSymbol)?.price ?? extra.currentPrice;
      }
      mergedTrades.push(extra);
    }
  }
  return mergedTrades;
}

function mergeStockDetail(
  detail: StockPaperTradeDetail | null | undefined,
  liveTrades: PaperTrade[] | undefined,
  livePriceMap?: LivePriceMap,
): StockPaperTradeDetail | null {
  if (!detail) {
    return null;
  }
  const trades = mergeLiveTrades(detail.trades, liveTrades, livePriceMap).filter((trade) => trade.stockSymbol === detail.stockSymbol);
  const wins = trades.filter((trade) => trade.status === "WIN").length;
  const losses = trades.filter((trade) => trade.status === "LOSS").length;
  const openTrades = trades.filter((trade) => trade.status === "OPEN").length;
  const totalPnlRupees = trades.reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0);
  const pnlCount = trades.filter((trade) => trade.pnlPct !== null && trade.pnlPct !== undefined).length;
  const avgPnlPct =
    pnlCount > 0 ? trades.reduce((sum, trade) => sum + (trade.pnlPct ?? 0), 0) / pnlCount : detail.avgPnlPct;
  return {
    ...detail,
    wins,
    losses,
    openTrades,
    totalPnlRupees,
    avgPnlPct,
    trades,
  };
}

export default function PaperTrading({ liveData }: Props) {
  const [days, setDays] = useState(30);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [tradeView, setTradeView] = useState<TradeView>("ALL");
  const todayQuery = useQuery({ queryKey: ["paperTradesToday"], queryFn: api.fetchPaperTradesToday, refetchInterval: 5000 });
  const historyQuery = useQuery({ queryKey: ["paperTradesHistory", days], queryFn: () => api.fetchPaperTradeHistory(days) });
  const effectivenessQuery = useQuery({ queryKey: ["paperTradeEffectiveness", days], queryFn: () => api.fetchPaperTradeEffectiveness(days) });
  const observationQuery = useQuery({ queryKey: ["paperTradeObservation", days], queryFn: () => api.fetchPaperTradeObservation(days) });
  const stockDetailQuery = useQuery({
    queryKey: ["paperTradeStockDetail", selectedSymbol, days],
    queryFn: () => api.fetchPaperTradeStockDetail(selectedSymbol ?? "", Math.max(days, 90)),
    enabled: Boolean(selectedSymbol),
  });

  const liveTrades = liveData?.paperTrades;
  const livePriceMap = useMemo(() => {
    const map = new Map<string, { price: number | null; changePct: number | null }>();
    for (const item of liveData?.watchlistPrices ?? []) {
      if (!item.symbol) {
        continue;
      }
      map.set(item.symbol, { price: item.ltp, changePct: item.changePct });
    }
    return map;
  }, [liveData?.watchlistPrices]);
  const trades = useMemo(() => mergeLiveTrades(todayQuery.data ?? [], liveTrades, livePriceMap), [todayQuery.data, liveTrades, livePriceMap]);
  const historyTrades = useMemo(
    () => mergeLiveTrades(historyQuery.data?.trades ?? [], liveTrades, livePriceMap),
    [historyQuery.data?.trades, liveTrades, livePriceMap],
  );
  const liveTradeFeed = useMemo(() => (historyTrades.length ? historyTrades : liveTrades?.length ? liveTrades : trades), [historyTrades, liveTrades, trades]);
  const stockDetail = useMemo(
    () => mergeStockDetail(stockDetailQuery.data, liveTrades, livePriceMap),
    [stockDetailQuery.data, liveTrades, livePriceMap],
  );
  const openIntraday = useMemo(() => liveTradeFeed.filter((trade) => trade.status === "OPEN" && trade.signalType === "INTRADAY"), [liveTradeFeed]);
  const openInvestment = useMemo(() => liveTradeFeed.filter((trade) => trade.status === "OPEN" && trade.signalType === "INVESTMENT"), [liveTradeFeed]);
  const plannedIntraday = useMemo(() => historyTrades.filter((trade) => trade.status === "PLANNED" && trade.signalType === "INTRADAY"), [historyTrades]);
  const plannedInvestment = useMemo(() => historyTrades.filter((trade) => trade.status === "PLANNED" && trade.signalType === "INVESTMENT"), [historyTrades]);
  const missedToday = useMemo(
    () => historyTrades.filter((trade) => trade.status === "MISSED" && isTodayTrade(trade)),
    [historyTrades],
  );
  const closedToday = useMemo(
    () => historyTrades.filter((trade) => isClosedStatus(trade.status) && isTodayTrade(trade)),
    [historyTrades],
  );
  const filteredHistoryTrades = useMemo(() => {
    switch (tradeView) {
      case "OPEN":
        return historyTrades.filter((trade) => trade.status === "OPEN");
      case "PLANNED":
        return historyTrades.filter((trade) => trade.status === "PLANNED");
      case "MISSED":
        return historyTrades.filter((trade) => trade.status === "MISSED");
      case "CLOSED_TODAY":
        return historyTrades.filter((trade) => isClosedStatus(trade.status) && isTodayTrade(trade));
      case "INTRADAY":
        return historyTrades.filter((trade) => trade.signalType === "INTRADAY");
      case "INVESTMENT":
        return historyTrades.filter((trade) => trade.signalType === "INVESTMENT");
      default:
        return historyTrades;
    }
  }, [historyTrades, tradeView]);
  const intradayPnl = trades
    .filter((trade) => trade.signalType === "INTRADAY")
    .reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0);
  const investmentPnl = trades
    .filter((trade) => trade.signalType === "INVESTMENT")
    .reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0);
  const winRate = trades.length ? trades.filter((trade) => trade.status === "WIN").length / trades.length : 0;
  const portfolioValue =
    observationQuery.data?.portfolioValue ??
    1_000_000 + historyTrades.reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0);

  return (
    <div className="space-y-6">
      <section className="desk-banner panel-premium fx-fade-up px-6 py-6">
        <div className="relative z-[1] grid gap-6 xl:grid-cols-[1.2fr_1fr]">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="chrome-kicker">Paper mission control</span>
              <span className="glass-chip text-ocean">
                <CandlestickChart className="h-4 w-4" />
                {tradeView} view
              </span>
              <span className="glass-chip text-mint">
                <ShieldCheck className="h-4 w-4" />
                {observationQuery.data?.openTrades ?? 0} live positions
              </span>
            </div>
            <h2 className="mt-5 max-w-3xl font-display text-4xl font-semibold tracking-[-0.05em] text-white">
              Capital books, execution quality, and live paper-trade behavior in one professional desk.
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-300">
              Intraday aur investment ko alag books ke saath track karo, live execution dekho, aur quickly samjho ki bot real market conditions me kaisa behave kar raha hai.
            </p>
            <div className="mt-5 flex flex-wrap gap-3">
              <span className="glass-chip text-amber">
                <TimerReset className="h-4 w-4" />
                {observationQuery.data?.plannedTrades ?? 0} planned
              </span>
              <span className="glass-chip text-ocean">
                <Radar className="h-4 w-4" />
                {historyTrades.length} trades in range
              </span>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Intraday P&amp;L</p>
              <p className={`mono-value mt-3 font-display text-3xl font-semibold ${pnlToneClass(intradayPnl)}`}>
                {intradayPnl >= 0 ? "+" : ""}
                {formatInr(intradayPnl)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Investment P&amp;L</p>
              <p className={`mono-value mt-3 font-display text-3xl font-semibold ${pnlToneClass(investmentPnl)}`}>
                {investmentPnl >= 0 ? "+" : ""}
                {formatInr(investmentPnl)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Win rate</p>
              <p className="mono-value mt-3 font-display text-3xl font-semibold text-white">{(winRate * 100).toFixed(1)}%</p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Portfolio value</p>
              <p className="mono-value mt-3 font-display text-3xl font-semibold text-ocean">{formatInr(portfolioValue)}</p>
            </div>
          </div>
        </div>
      </section>

      <section className="fx-fade-up fx-delay-1 grid gap-4 xl:grid-cols-5">
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Paper trades in view</p>
          <p className="mt-3 font-display text-3xl font-semibold text-ink">{trades.length}</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Intraday P&amp;L</p>
          <p className={`mono-value mt-3 font-display text-3xl font-semibold ${pnlToneClass(intradayPnl)}`}>
            {intradayPnl >= 0 ? "+" : ""}
            {formatInr(intradayPnl)}
          </p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Investment P&amp;L</p>
          <p className={`mono-value mt-3 font-display text-3xl font-semibold ${pnlToneClass(investmentPnl)}`}>
            {investmentPnl >= 0 ? "+" : ""}
            {formatInr(investmentPnl)}
          </p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Win rate</p>
          <p className="mono-value mt-3 font-display text-3xl font-semibold text-ink">{(winRate * 100).toFixed(1)}%</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Portfolio value</p>
          <p className="mono-value mt-3 font-display text-3xl font-semibold text-ocean">{formatInr(portfolioValue)}</p>
        </div>
      </section>

      <section className="fx-fade-up fx-delay-2">
        <AllocationSplitPanel observation={observationQuery.data} />
      </section>

      <section className="fx-fade-up fx-delay-2 grid gap-4 xl:grid-cols-4">
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Executed / planned</p>
          <p className="mt-3 font-display text-3xl font-semibold text-ink">
            {observationQuery.data ? `${observationQuery.data.executedTrades} / ${observationQuery.data.plannedTrades}` : "-"}
          </p>
          <p className="mt-2 text-sm text-slate-500">
            {observationQuery.data ? `${observationQuery.data.openTrades} open live positions` : "Executed trades vs next-session plans"}
          </p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Profit factor / streak</p>
          <p className="mono-value mt-3 font-display text-3xl font-semibold text-ocean">
            {observationQuery.data?.profitFactor !== null && observationQuery.data?.profitFactor !== undefined
              ? observationQuery.data.profitFactor.toFixed(2)
              : "-"}
          </p>
          <p className="mt-2 text-sm text-slate-500">
            {observationQuery.data?.currentStreakType
              ? `${observationQuery.data.currentStreakType} streak ${observationQuery.data.currentStreakCount}`
              : "No closed trade streak yet"}
          </p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Average win / loss</p>
          <p className="mono-value mt-3 font-display text-2xl font-semibold text-ink">
            {observationQuery.data?.avgWinPct !== null && observationQuery.data?.avgWinPct !== undefined
              ? `${observationQuery.data.avgWinPct.toFixed(2)}%`
              : "-"}
            <span className="mx-2 text-slate-400">/</span>
            {observationQuery.data?.avgLossPct !== null && observationQuery.data?.avgLossPct !== undefined
              ? `${observationQuery.data.avgLossPct.toFixed(2)}%`
              : "-"}
          </p>
          <p className="mt-2 text-sm text-slate-500">Helps compare winner size vs loser size</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Best live strategy</p>
          <p className="mt-3 font-display text-2xl font-semibold text-ink">{observationQuery.data?.bestStrategy ?? "-"}</p>
          <p className="mt-2 text-sm text-slate-500">
            {observationQuery.data?.bestStrategyWinRate !== null && observationQuery.data?.bestStrategyWinRate !== undefined
              ? `${(observationQuery.data.bestStrategyWinRate * 100).toFixed(1)}% win rate`
              : "Waiting for enough closed live trades"}
          </p>
        </div>
      </section>

      <section className="fx-fade-up fx-delay-3 grid gap-4 xl:grid-cols-6">
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Open intraday</p>
          <p className="mt-3 font-display text-3xl font-semibold text-coral">{openIntraday.length}</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Open investment</p>
          <p className="mt-3 font-display text-3xl font-semibold text-mint">{openInvestment.length}</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Planned intraday</p>
          <p className="mt-3 font-display text-3xl font-semibold text-ocean">{plannedIntraday.length}</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Planned investment</p>
          <p className="mt-3 font-display text-3xl font-semibold text-amber">{plannedInvestment.length}</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Missed today</p>
          <p className="mt-3 font-display text-3xl font-semibold text-slate-700">{missedToday.length}</p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Closed today</p>
          <p className="mt-3 font-display text-3xl font-semibold text-ink">{closedToday.length}</p>
        </div>
      </section>

      <section className="fx-fade-up fx-delay-3">
        <LivePaperTradesPanel
          trades={liveTradeFeed}
          onSelectStock={setSelectedSymbol}
          title="Live and Planned Paper Trades"
          subtitle="Intraday and investment executions are organized here together, with open, planned, missed, and closed buckets."
          showPlanned
        />
      </section>

      <section className="fx-fade-up fx-delay-4">
        <EquityCurveChart data={historyQuery.data?.equityCurve ?? []} />
      </section>

      <section className="fx-fade-up fx-delay-4 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="section-title">Paper Trades</h2>
          <label className="flex items-center gap-3 text-sm text-slate-500">
            Range
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-ink"
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </label>
        </div>
        <div className="flex items-center justify-between gap-3 text-sm text-slate-500">
          <span>Open paper trades now stream live price and live P&amp;L updates while the market is open.</span>
          <span>{liveData?.timestamp ? `Last live tick ${new Date(liveData.timestamp).toLocaleTimeString()}` : "Waiting for live feed"}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {[
            { key: "ALL", label: `All (${historyTrades.length})` },
            { key: "OPEN", label: `Open (${historyTrades.filter((trade) => trade.status === "OPEN").length})` },
            { key: "PLANNED", label: `Planned (${historyTrades.filter((trade) => trade.status === "PLANNED").length})` },
            { key: "MISSED", label: `Missed (${historyTrades.filter((trade) => trade.status === "MISSED").length})` },
            { key: "CLOSED_TODAY", label: `Closed Today (${closedToday.length})` },
            { key: "INTRADAY", label: `Intraday (${historyTrades.filter((trade) => trade.signalType === "INTRADAY").length})` },
            { key: "INVESTMENT", label: `Investment (${historyTrades.filter((trade) => trade.signalType === "INVESTMENT").length})` },
          ].map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setTradeView(item.key as TradeView)}
              className={`rounded-full px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] transition ${
                tradeView === item.key
                  ? "bg-gradient-to-r from-ocean to-[#78beff] text-white shadow-[0_16px_34px_rgba(69,182,255,0.28)]"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <PaperTradeTable trades={filteredHistoryTrades} onSelectStock={setSelectedSymbol} />
      </section>

      <section className="panel fx-fade-up fx-delay-4 p-6">
        <div className="flex items-center justify-between">
          <h2 className="section-title">Day-Wise Recommendation Record</h2>
          <span className="text-sm text-slate-500">Only executed paper trades are shown here, not backtests or future plans</span>
        </div>
        {effectivenessQuery.data?.length ? (
          <div className="mt-5 overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-[0.18em] text-slate-500">
                <tr>
                  {["Date", "Stock", "Total", "Worked", "Failed", "Open", "Win Rate", "Avg P&L"].map((header) => (
                    <th key={header} className="px-4 py-4 font-medium">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white/70">
                {effectivenessQuery.data?.map((row) => (
                  <tr key={`${row.tradeDate}-${row.stockSymbol}`}>
                    <td className="px-4 py-4 text-slate-600">{row.tradeDate}</td>
                    <td className="px-4 py-4 font-semibold text-ink">
                      <button type="button" onClick={() => setSelectedSymbol(row.stockSymbol)} className="transition hover:text-ocean">
                        {row.stockSymbol}
                      </button>
                    </td>
                    <td className="px-4 py-4">{row.totalRecommendations}</td>
                    <td className="px-4 py-4 text-mint">{row.workedRecommendations}</td>
                    <td className="px-4 py-4 text-coral">{row.failedRecommendations}</td>
                    <td className="px-4 py-4 text-slate-600">{row.openRecommendations}</td>
                    <td className="mono-value px-4 py-4 font-semibold text-ocean">{(row.winRate * 100).toFixed(1)}%</td>
                    <td className={`mono-value px-4 py-4 font-semibold ${pnlToneClass(row.avgPnlPct)}`}>
                      {row.avgPnlPct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="mt-5 rounded-3xl border border-slate-200 bg-white/70 px-5 py-6 text-sm text-slate-500">
            No executed paper trades yet. Planned trades for the next trading session will appear above, and this section will fill in only after real paper trades open and close during market hours.
          </div>
        )}
      </section>

      <PaperTradeStockPanel
        detail={stockDetail}
        isOpen={Boolean(selectedSymbol)}
        symbol={selectedSymbol}
        isLoading={stockDetailQuery.isLoading}
        onClose={() => setSelectedSymbol(null)}
      />
    </div>
  );
}
