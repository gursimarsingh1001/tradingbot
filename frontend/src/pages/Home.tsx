import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, BriefcaseBusiness, CandlestickChart, ChevronRight, Radar, RadioTower, ShieldCheck, Sparkles, TimerReset } from "lucide-react";
import { api, LivePayload } from "../api/client";
import { AllocationSplitPanel } from "../components/AllocationSplitPanel";
import { IndexCard } from "../components/IndexCard";
import { LivePaperTradesPanel } from "../components/LivePaperTradesPanel";
import { RecommendationCard } from "../components/RecommendationCard";
import { WatchlistDetailPanel } from "../components/WatchlistDetailPanel";
import { formatInr, formatPct, pnlToneClass } from "../utils/formatters";

type Props = {
  liveData: LivePayload | null;
};

type HomeView = "intraday" | "investment" | "tomorrow" | "live";

export default function Home({ liveData }: Props) {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<HomeView>("intraday");
  const indicesQuery = useQuery({ queryKey: ["indices"], queryFn: api.fetchIndices, refetchInterval: 10000 });
  const recommendationsQuery = useQuery({ queryKey: ["recommendations"], queryFn: api.fetchRecommendations, refetchInterval: 10000 });
  const watchlistQuery = useQuery({ queryKey: ["watchlistTomorrow"], queryFn: api.fetchTomorrowWatchlist, refetchInterval: 30000 });
  const paperTradesTodayQuery = useQuery({ queryKey: ["paperTradesTodayHome"], queryFn: api.fetchPaperTradesToday, refetchInterval: 5000 });
  const paperTradeHistoryQuery = useQuery({ queryKey: ["paperTradeHistoryHome"], queryFn: () => api.fetchPaperTradeHistory(30), refetchInterval: 15000 });
  const observationQuery = useQuery({ queryKey: ["paperTradeObservationHome"], queryFn: () => api.fetchPaperTradeObservation(30), refetchInterval: 15000 });
  const watchlistDetailQuery = useQuery({
    queryKey: ["watchlistDetail", selectedSymbol],
    queryFn: () => api.fetchWatchlistDetail(selectedSymbol ?? ""),
    enabled: Boolean(selectedSymbol),
  });

  const indices = liveData?.indices ?? indicesQuery.data ?? {};
  const recommendations = recommendationsQuery.data ?? [];
  const watchlist = watchlistQuery.data ?? [];
  const historicalTrades = paperTradeHistoryQuery.data?.trades ?? [];
  const livePaperTrades = liveData?.paperTrades ?? [];
  const paperTrades = useMemo(() => {
    const merged = historicalTrades.map((trade) => livePaperTrades.find((live) => live.tradeId === trade.tradeId) ?? trade);
    const known = new Set(merged.map((trade) => trade.tradeId));
    for (const live of livePaperTrades) {
      if (!known.has(live.tradeId)) {
        merged.push(live);
      }
    }
    if (merged.length) {
      return merged;
    }
    return paperTradesTodayQuery.data ?? [];
  }, [historicalTrades, livePaperTrades, paperTradesTodayQuery.data]);
  const livePriceMap = useMemo(() => {
    const map = new Map<string, { price: number | null; changePct: number | null }>();
    for (const item of liveData?.watchlistPrices ?? []) {
      if (!item.symbol) {
        continue;
      }
      map.set(item.symbol, { price: item.ltp, changePct: item.changePct });
    }
    for (const trade of paperTrades) {
      if (!trade.stockSymbol || trade.currentPrice === null || trade.currentPrice === undefined) {
        continue;
      }
      const existing = map.get(trade.stockSymbol);
      map.set(trade.stockSymbol, {
        price: trade.currentPrice,
        changePct: existing?.changePct ?? null,
      });
    }
    return map;
  }, [liveData?.watchlistPrices, paperTrades]);
  const intradayRecommendations = useMemo(
    () => recommendations.filter((item) => item.signalType === "INTRADAY"),
    [recommendations],
  );
  const investmentRecommendations = useMemo(
    () => recommendations.filter((item) => item.signalType === "INVESTMENT"),
    [recommendations],
  );
  const liveOpenTrades = useMemo(() => paperTrades.filter((trade) => trade.status === "OPEN"), [paperTrades]);
  const plannedTrades = useMemo(() => paperTrades.filter((trade) => trade.status === "PLANNED"), [paperTrades]);
  const openIntradayPnl = useMemo(
    () =>
      liveOpenTrades
        .filter((trade) => trade.signalType === "INTRADAY")
        .reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0),
    [liveOpenTrades],
  );
  const openInvestmentPnl = useMemo(
    () =>
      liveOpenTrades
        .filter((trade) => trade.signalType === "INVESTMENT")
        .reduce((sum, trade) => sum + (trade.pnlRupees ?? 0), 0),
    [liveOpenTrades],
  );

  const blocks: Array<{
    key: HomeView;
    title: string;
    subtitle: string;
    count: number;
    accent: string;
    icon: JSX.Element;
  }> = [
    {
      key: "intraday",
      title: "Intraday Recommendations",
      subtitle: "Live same-session trade ideas",
      count: intradayRecommendations.length,
      accent: "text-ocean",
      icon: <CandlestickChart className="h-5 w-5" />,
    },
    {
      key: "investment",
      title: "Investment Recommendations",
      subtitle: "Swing and positional setups",
      count: investmentRecommendations.length,
      accent: "text-mint",
      icon: <BriefcaseBusiness className="h-5 w-5" />,
    },
    {
      key: "tomorrow",
      title: "Tomorrow's Watchlist",
      subtitle: "Planned next-session opportunities",
      count: watchlist.length,
      accent: "text-amber",
      icon: <Activity className="h-5 w-5" />,
    },
    {
      key: "live",
      title: "Live Paper Trades",
      subtitle: "Open and planned executions",
      count: liveOpenTrades.length + plannedTrades.length,
      accent: "text-coral",
      icon: <RadioTower className="h-5 w-5" />,
    },
  ];

  const renderRecommendationGrid = (items: typeof recommendations) => {
    if (!items.length) {
      return (
        <div className="rounded-[1.75rem] border border-white/10 bg-white/5 px-5 py-6 text-sm text-slate-400">
          No recommendations in this bucket right now. The block will fill automatically when the bot finds a valid setup.
        </div>
      );
    }
    return (
        <div className="grid gap-4 xl:grid-cols-2">
          {items.map((item) => (
          <RecommendationCard
            key={`${item.stockSymbol}-${item.strategyName}-${item.signalType}`}
            item={item}
            livePrice={livePriceMap.get(item.stockSymbol)?.price ?? null}
            liveChangePct={livePriceMap.get(item.stockSymbol)?.changePct ?? null}
          />
        ))}
        </div>
      );
  };

  const renderTomorrowWatchlist = () => (
    <div className="panel p-6">
      <div className="flex items-center justify-between">
        <h2 className="section-title">Tomorrow's Watchlist</h2>
        <span className="text-sm text-slate-500">After-market ranked setups with planned paper trades</span>
      </div>
      <div className="mt-5 space-y-4">
        {watchlist.length ? (
          watchlist.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSelectedSymbol(item.symbol)}
              className="signal-shell w-full px-4 py-4 text-left transition hover:border-ocean/30 hover:shadow-[0_22px_42px_rgba(69,182,255,0.14)]"
            >
              <div className="edge-glow pointer-events-none" />
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="max-w-3xl">
                  <p className="font-display text-xl font-semibold text-ink">{item.symbol}</p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                    <span className="glass-chip text-ocean">{item.strategy ?? "Strategy pending"}</span>
                    {item.direction ? (
                      <span className={`rounded-full px-3 py-1 ${item.direction === "SELL" ? "bg-coral/10 text-coral" : "bg-mint/10 text-mint"}`}>
                        {item.direction}
                      </span>
                    ) : null}
                    <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">
                      {item.confidenceScore !== null && item.confidenceScore !== undefined ? `${item.confidenceScore.toFixed(0)} confidence` : "Watchlist setup"}
                    </span>
                    {item.financialDataSource ? (
                      <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">
                        {item.financialDataSource === "STRUCTURED_SNAPSHOT" ? "Structured financials" : "News-led financials"}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-2 text-sm leading-6 text-slate-600">{item.reason}</p>
                  {item.newsPerspective ? <p className="mt-3 text-sm leading-6 text-slate-500">{item.newsPerspective}</p> : null}
                  <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                    <span className="rounded-full bg-ocean/10 px-3 py-1 text-ocean">{item.planStatus ?? "WATCHLIST"}</span>
                    <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">
                      Best-strategy record {item.workedCount30d}/{item.recommendationCount30d}
                    </span>
                    <span className="rounded-full bg-mint/10 px-3 py-1 text-mint">
                      {formatPct(item.winRate30d * 100, 0)} win rate
                    </span>
                    {item.eventFlags?.map((flag) => (
                      <span key={flag} className="rounded-full bg-amber/10 px-3 py-1 text-amber">
                        {flag}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Watch Level</p>
                  <p className="mono-value mt-2 text-lg font-semibold text-ocean">{formatInr(item.watchPrice)}</p>
                  <p className="mt-3 text-xs text-slate-500">
                    {item.plannedTradeId ? "Paper trade plan created for next session" : "Paper trade plan pending"}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">Planned for {item.plannedForDate ?? "next trading day"}</p>
                  <p className="mt-3 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-ocean">
                    Click to inspect chart
                    <ChevronRight className="h-3.5 w-3.5" />
                  </p>
                </div>
              </div>
            </button>
          ))
        ) : (
          <div className="rounded-[1.75rem] border border-white/10 bg-white/5 px-5 py-6 text-sm text-slate-400">
            No upcoming tomorrow-watchlist batch is ready yet. During market hours, stale expired plans are hidden on purpose. The next batch will appear automatically after the after-market rebuild.
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      <section className="desk-banner panel-premium fx-fade-up px-6 py-6">
        <div className="relative z-[1] grid gap-6 xl:grid-cols-[1.25fr_0.95fr]">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="chrome-kicker">Market pulse</span>
              <span className="glass-chip text-mint">
                <ShieldCheck className="h-4 w-4" />
                Bot ready
              </span>
              <span className="glass-chip text-amber">
                <TimerReset className="h-4 w-4" />
                Active view {activeView}
              </span>
            </div>
            <h2 className="mt-5 max-w-3xl font-display text-4xl font-semibold tracking-[-0.05em] text-white">
              Institutional-style signal deck for live execution, next-session planning, and portfolio command.
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-300">
              Yahan se tum ek hi jagah par market pulse, live recommendations, tomorrow setups, aur paper execution flow sab dekh sakte ho.
            </p>
            <div className="mt-5 flex flex-wrap gap-3">
              <span className="glass-chip text-ocean">
                <Radar className="h-4 w-4" />
                {recommendations.length} live setups
              </span>
              <span className="glass-chip text-coral">
                <CandlestickChart className="h-4 w-4" />
                {intradayRecommendations.length} intraday ideas
              </span>
              <span className="glass-chip text-mint">
                <BriefcaseBusiness className="h-4 w-4" />
                {investmentRecommendations.length} carry trades
              </span>
              <span className="glass-chip text-amber">
                <Sparkles className="h-4 w-4" />
                {watchlist.length} next-session watch items
              </span>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Intraday book P&amp;L</p>
              <p className={`mono-value mt-3 font-display text-3xl font-semibold ${pnlToneClass(observationQuery.data?.intradayBookPnlRupees)}`}>
                {(observationQuery.data?.intradayBookPnlRupees ?? 0) >= 0 ? "+" : ""}
                {formatInr(observationQuery.data?.intradayBookPnlRupees)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Investment book P&amp;L</p>
              <p className={`mono-value mt-3 font-display text-3xl font-semibold ${pnlToneClass(observationQuery.data?.investmentBookPnlRupees)}`}>
                {(observationQuery.data?.investmentBookPnlRupees ?? 0) >= 0 ? "+" : ""}
                {formatInr(observationQuery.data?.investmentBookPnlRupees)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Open trades now</p>
              <p className="mt-3 font-display text-3xl font-semibold text-white">{liveOpenTrades.length}</p>
              <p className="mt-2 text-xs text-slate-400">Live execution book currently in market</p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="micro-label">Planned queue</p>
              <p className="mt-3 font-display text-3xl font-semibold text-white">{plannedTrades.length}</p>
              <p className="mt-2 text-xs text-slate-400">Trigger-ready plans waiting in the desk</p>
            </div>
          </div>
        </div>
      </section>

      <section className="fx-fade-up fx-delay-1">
        <div className="flex items-center justify-between">
          <h2 className="section-title">Indices</h2>
          <span className="glass-chip text-slate-200">
            <Radar className="h-4 w-4" />
            Live benchmark feed
          </span>
        </div>
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          {Object.entries(indices).map(([name, data]) => (
            <IndexCard key={name} name={name} data={data} />
          ))}
        </div>
      </section>

      <section className="fx-fade-up fx-delay-2 grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <AllocationSplitPanel observation={observationQuery.data} compact />
        <div className="panel p-6">
          <div className="flex flex-wrap items-center gap-2">
            <span className="chrome-kicker">Trading Desk</span>
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-300">
              Live execution pulse
            </span>
          </div>
          <h2 className="mt-2 font-display text-2xl font-semibold text-ink">Live execution snapshot</h2>
          <p className="mt-2 text-sm text-slate-500">
            Market live hai to yahin se pata chalega ki recommendations, plans aur open paper trades kis state me hain.
          </p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <div className="hero-stat px-5 py-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Intraday book P&amp;L</p>
              <p className={`mono-value mt-2 font-display text-3xl font-semibold ${pnlToneClass(observationQuery.data?.intradayBookPnlRupees)}`}>
                {(observationQuery.data?.intradayBookPnlRupees ?? 0) >= 0 ? "+" : ""}
                {formatInr(observationQuery.data?.intradayBookPnlRupees)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Investment book P&amp;L</p>
              <p className={`mono-value mt-2 font-display text-3xl font-semibold ${pnlToneClass(observationQuery.data?.investmentBookPnlRupees)}`}>
                {(observationQuery.data?.investmentBookPnlRupees ?? 0) >= 0 ? "+" : ""}
                {formatInr(observationQuery.data?.investmentBookPnlRupees)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Open intraday P&amp;L</p>
              <p className={`mono-value mt-2 font-display text-3xl font-semibold ${pnlToneClass(openIntradayPnl)}`}>
                {openIntradayPnl >= 0 ? "+" : ""}
                {formatInr(openIntradayPnl)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Open investment P&amp;L</p>
              <p className={`mono-value mt-2 font-display text-3xl font-semibold ${pnlToneClass(openInvestmentPnl)}`}>
                {openInvestmentPnl >= 0 ? "+" : ""}
                {formatInr(openInvestmentPnl)}
              </p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Live recommendations</p>
              <p className="mt-2 font-display text-3xl font-semibold text-white">{recommendations.length}</p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Open trades now</p>
              <p className="mt-2 font-display text-3xl font-semibold text-white">{liveOpenTrades.length}</p>
            </div>
            <div className="hero-stat px-5 py-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Planned queue</p>
              <p className="mt-2 font-display text-3xl font-semibold text-white">{plannedTrades.length}</p>
            </div>
          </div>
        </div>
      </section>

      <section className="fx-fade-up fx-delay-2">
        <div className="flex items-center justify-between">
          <h2 className="section-title">Decision Blocks</h2>
          <span className="glass-chip text-slate-200">Click a block to open its full detail view</span>
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-4">
          {blocks.map((block) => {
            const isActive = activeView === block.key;
            return (
              <button
                key={block.key}
                type="button"
                onClick={() => setActiveView(block.key)}
                className={`panel signal-tile p-5 text-left transition ${isActive ? "border-ocean/30 shadow-[0_22px_44px_rgba(69,182,255,0.24)]" : ""}`}
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className={`inline-flex items-center gap-2 rounded-full bg-white/5 px-3 py-2 text-xs font-semibold uppercase tracking-[0.18em] ${block.accent}`}>
                      {block.icon}
                      {block.title}
                    </div>
                    <p className="mt-4 font-display text-4xl font-semibold text-ink">{block.count}</p>
                    <p className="mt-2 text-sm text-slate-500">{block.subtitle}</p>
                    <p className="mt-4 micro-label">{isActive ? "Live focus" : "Available view"}</p>
                  </div>
                  <ChevronRight className={`h-5 w-5 text-slate-500 transition ${isActive ? "translate-x-1 text-ocean" : ""}`} />
                </div>
              </button>
            );
          })}
        </div>
      </section>

      <section className="fx-fade-up fx-delay-3 grid gap-4 xl:grid-cols-4">
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Open intraday trades</p>
          <p className="mt-3 font-display text-3xl font-semibold text-coral">
            {paperTrades.filter((trade) => trade.status === "OPEN" && trade.signalType === "INTRADAY").length}
          </p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Open investment trades</p>
          <p className="mt-3 font-display text-3xl font-semibold text-mint">
            {paperTrades.filter((trade) => trade.status === "OPEN" && trade.signalType === "INVESTMENT").length}
          </p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Planned intraday trades</p>
          <p className="mt-3 font-display text-3xl font-semibold text-ocean">
            {paperTrades.filter((trade) => trade.status === "PLANNED" && trade.signalType === "INTRADAY").length}
          </p>
        </div>
        <div className="hero-stat p-5">
          <p className="text-sm text-slate-500">Planned investment trades</p>
          <p className="mt-3 font-display text-3xl font-semibold text-amber">
            {paperTrades.filter((trade) => trade.status === "PLANNED" && trade.signalType === "INVESTMENT").length}
          </p>
        </div>
      </section>

      {activeView === "intraday" ? renderRecommendationGrid(intradayRecommendations) : null}
      {activeView === "investment" ? renderRecommendationGrid(investmentRecommendations) : null}
      {activeView === "tomorrow" ? renderTomorrowWatchlist() : null}
      {activeView === "live" ? (
        <LivePaperTradesPanel
          trades={paperTrades}
          onSelectStock={setSelectedSymbol}
          title="Live and Planned Execution Center"
          subtitle="This block combines open intraday, open investment, planned intraday, and planned investment paper trades."
          showPlanned
        />
      ) : null}

      <WatchlistDetailPanel
        detail={watchlistDetailQuery.data ?? null}
        isOpen={Boolean(selectedSymbol)}
        onClose={() => setSelectedSymbol(null)}
      />
    </div>
  );
}
