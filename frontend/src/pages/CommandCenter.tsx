import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, ArrowUpRight, BriefcaseBusiness, RadioTower, ShieldCheck, Sparkles, WifiOff } from "lucide-react";
import { api, LivePayload } from "../api/client";
import { SectionHeader, StatePanel, MetricTile, EmptyTableMessage } from "../components/CommandPrimitives";
import { formatDate, formatDateTime, formatInr, formatNumber, formatPct, pnlToneClass } from "../utils/formatters";
import { marketAwareInterval } from "../utils/refresh";

type Props = {
  liveData: LivePayload | null;
  connectionState: "connecting" | "open" | "closed";
};

const liveBenchmarkOrder = ["NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY", "GIFTNIFTY", "MCX_CRUDE", "BRENT_CRUDE", "USDINR"];

export default function CommandCenter({ liveData, connectionState }: Props) {
  const scoringSummaryQuery = useQuery({
    queryKey: ["scoringSummary"],
    queryFn: () => api.fetchScoringSummary(),
  });
  const gatesSummaryQuery = useQuery({
    queryKey: ["gatesSummary"],
    queryFn: () => api.fetchGatesSummary(),
  });
  const cutoverQuery = useQuery({
    queryKey: ["cutoverLatest"],
    queryFn: api.fetchCutoverLatest,
    refetchInterval: marketAwareInterval(30000, 300000),
  });
  const riskQuery = useQuery({
    queryKey: ["riskLatest"],
    queryFn: api.fetchRiskLatest,
    refetchInterval: marketAwareInterval(60000, 300000),
  });
  const marketQuery = useQuery({
    queryKey: ["marketIndicesCommand"],
    queryFn: api.fetchMarketIndices,
    refetchInterval: marketAwareInterval(60000, 300000),
  });
  const liveIndicesQuery = useQuery({
    queryKey: ["liveIndicesCommand"],
    queryFn: api.fetchIndices,
    refetchInterval: marketAwareInterval(5000, 30000),
  });
  const portfolioSummaryQuery = useQuery({
    queryKey: ["portfolioSummaryCommand"],
    queryFn: () => api.fetchPortfolioSummary(180),
    refetchInterval: marketAwareInterval(30000, 300000),
  });
  const portfolioPerformanceQuery = useQuery({
    queryKey: ["portfolioPerformanceCommand"],
    queryFn: () => api.fetchPortfolioPerformance(180),
    refetchInterval: marketAwareInterval(30000, 300000),
  });
  const tradesOpenQuery = useQuery({
    queryKey: ["tradesOpenCommand"],
    queryFn: api.fetchTradesOpen,
    refetchInterval: marketAwareInterval(30000, 300000),
  });

  const scoringSummary = scoringSummaryQuery.data;
  const gatesSummary = gatesSummaryQuery.data;
  const cutover = cutoverQuery.data;
  const latestRisk = riskQuery.data?.latest;
  const market = marketQuery.data;
  const liveIndices = liveData?.indices ?? liveIndicesQuery.data ?? {};
  const portfolio = portfolioSummaryQuery.data?.observation;
  const equityCurve = portfolioPerformanceQuery.data?.equityCurve ?? [];
  const plannedSignals = cutover?.signals ?? [];
  const openPositions = tradesOpenQuery.data?.open ?? [];
  const riskSignals = latestRisk?.signals ?? [];
  const liveBenchmarks = liveBenchmarkOrder
    .map((key) => ({
      key,
      ...(liveIndices[key] ?? {
        value: 0,
        change: 0,
        changePct: 0,
        label: key,
        source: null,
        updatedAt: null,
        status: "SYNCING",
        isDelayed: false,
      }),
    }))
    .filter((item) => item.value > 0 || item.status === "SYNCING");

  return (
    <div className="space-y-6">
      <section className="desk-banner panel-premium px-6 py-6">
        <div className="grid gap-6 xl:grid-cols-[1.35fr_0.95fr]">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="chrome-kicker">Phase 1-6 Command Center</span>
              <span className="glass-chip text-mint">
                <ShieldCheck className="h-4 w-4" />
                Read-only
              </span>
              <span className="glass-chip text-amber">
                <AlertTriangle className="h-4 w-4" />
                Risk {latestRisk?.riskLevel ?? "UNKNOWN"}
              </span>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <span className="glass-chip text-ocean">
                <Sparkles className="h-4 w-4" />
                {scoringSummary?.counts.strongBuy ?? 0} strong buys
              </span>
              <span className="glass-chip text-mint">
                <ArrowUpRight className="h-4 w-4" />
                {gatesSummary?.buy ?? 0} gate-approved
              </span>
              <span className="glass-chip text-coral">
                <BriefcaseBusiness className="h-4 w-4" />
                {cutover?.plannedCount ?? 0} planned trades
              </span>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <MetricTile
              label="Strong Buy"
              value={scoringSummary?.counts.strongBuy ?? 0}
              helper={`${scoringSummary?.universeSize ?? 0} symbols scored`}
            />
            <MetricTile
              label="Gate Approved"
              value={gatesSummary?.buy ?? 0}
              helper={`${gatesSummary?.eligibleStrongBuy ?? 0} strong buys evaluated`}
              accent="text-mint"
            />
            <MetricTile
              label="Official Plans"
              value={cutover?.plannedCount ?? 0}
              helper={cutover?.latestPlanDate ? `Next session ${formatDate(cutover.latestPlanDate)}` : "No active plan batch"}
              accent="text-ocean"
            />
            <MetricTile
              label="Global Risk"
              value={latestRisk?.riskLevel ?? "UNKNOWN"}
              helper={`Size x${formatNumber(latestRisk?.positionSizeMultiplier, 2)}`}
              accent={latestRisk?.riskLevel === "RED" ? "text-coral" : latestRisk?.riskLevel === "YELLOW" ? "text-amber" : "text-mint"}
            />
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-4">
        <MetricTile
          label="Phase 2 Watchlist"
          value={scoringSummary?.counts.watchlist ?? 0}
          helper={`Avg Lynch ${formatNumber(scoringSummary?.averages.lynchValue)}`}
          accent="text-amber"
        />
        <MetricTile
          label="Open Positions"
          value={openPositions.length}
          helper={`${tradesOpenQuery.data?.plannedCount ?? 0} planned orders queued`}
        />
        <MetricTile
          label="Portfolio Value"
          value={<span className={pnlToneClass(portfolio?.totalPnlRupees)}>{formatInr(portfolio?.portfolioValue)}</span>}
          helper={`30D P&L ${formatInr(portfolio?.totalPnlRupees)}`}
        />
        <MetricTile
          label="Fill Rate"
          value={formatPct((scoringSummary?.averages.fillRate ?? 0) * 100, 0)}
          helper="Phase 6 reconciled data completeness"
          accent="text-violet"
        />
      </section>

      <section className="panel p-6">
        <SectionHeader
          eyebrow="Live Benchmarks"
          title="1-second live indices and macro tape"
          subtitle="Domestic indices stream through the live feed. GIFT Nifty and crude use the best available source and show source status directly."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <span className={`glass-chip ${connectionState === "open" ? "text-mint" : "text-coral"}`}>
                {connectionState === "open" ? <RadioTower className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
                {connectionState === "open" ? "1s feed active" : "Feed reconnecting"}
              </span>
              <span className="glass-chip text-slate-200">
                Last tick {liveData?.timestamp ? formatDateTime(liveData.timestamp) : "waiting"}
              </span>
            </div>
          }
        />
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {liveBenchmarks.map((item) => {
            const positive = item.change >= 0;
            const tone = positive ? "text-mint" : "text-coral";
            const borderTone = positive ? "border-mint/15" : "border-coral/15";
            return (
              <div key={item.key} className={`subpanel rounded-[1.25rem] p-4 ${borderTone}`}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="micro-label">{item.label ?? item.key}</p>
                    <p className="mt-3 mono-value text-3xl font-semibold text-white">{formatNumber(item.value)}</p>
                  </div>
                  <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${item.isDelayed ? "bg-amber/15 text-amber" : "bg-ocean/15 text-ocean"}`}>
                    {item.isDelayed ? "Delayed" : "Live"}
                  </span>
                </div>
                <div className="mt-4 flex items-end justify-between gap-3">
                  <div>
                    <p className={`mono-value text-lg font-semibold ${tone}`}>
                      {positive ? "+" : ""}
                      {formatNumber(item.change)}
                    </p>
                    <p className={`mt-1 text-xs ${tone}`}>
                      {positive ? "+" : ""}
                      {formatPct((item.changePct ?? 0) * 100)}
                    </p>
                  </div>
                  <div className="text-right text-xs text-slate-400">
                    <p>{item.source ?? "UNKNOWN"}</p>
                    <p className="mt-1">{item.updatedAt ? formatDateTime(item.updatedAt) : item.status ?? "SYNCING"}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.35fr_0.95fr]">
        <div className="panel p-6">
          <SectionHeader
            eyebrow="Equity Curve"
            title="Paper portfolio performance"
            subtitle="180-day running equity from executed paper trades."
          />
          <div className="mt-5 h-80">
            {!equityCurve.length ? (
              <StatePanel title="Equity curve abhi available nahi hai." tone="empty" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityCurve}>
                  <defs>
                    <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="#5aa6ff" stopOpacity={0.38} />
                      <stop offset="100%" stopColor="#5aa6ff" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} width={90} />
                  <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} />
                  <Area dataKey="value" type="monotone" stroke="#5aa6ff" strokeWidth={3} fill="url(#equityFill)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="panel p-6">
          <SectionHeader eyebrow="Crisis Scanner" title="Latest global risk snapshot" subtitle="Phase 5 macro overlay before official investment planning." />
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            {latestRisk ? (
              riskSignals.map((signal) => (
                <div key={signal.name} className="subpanel rounded-[1.25rem] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-white">{signal.label}</p>
                    <span
                      className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${
                        signal.severity === "BLOCK"
                          ? "bg-coral/15 text-coral"
                          : signal.severity === "CAUTION"
                            ? "bg-amber/15 text-amber"
                            : "bg-mint/15 text-mint"
                      }`}
                    >
                      {signal.severity}
                    </span>
                  </div>
                  <p className="mt-3 mono-value text-2xl font-semibold text-white">
                    {signal.value === null || signal.value === undefined ? "-" : formatNumber(signal.value)}
                  </p>
                  <p className="mt-2 text-xs leading-6 text-slate-400">{signal.message}</p>
                </div>
              ))
            ) : (
              <StatePanel title="Global risk snapshot abhi load nahi hua." tone="empty" />
            )}
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        <div className="panel p-6">
          <SectionHeader
            eyebrow="Phase 4"
            title="Today’s official BUY signals"
            subtitle="Only Phase 2 STRONG_BUY + Phase 3 BUY candidates that survived cutover."
          />
          <div className="mt-5 space-y-3">
            {!plannedSignals.length ? (
              <EmptyTableMessage title="Aaj ke official cutover se koi planned BUY signal nahi bana." />
            ) : (
              plannedSignals.slice(0, 8).map((trade) => (
                <div key={trade.tradeId} className="subpanel rounded-[1.35rem] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-display text-xl font-semibold text-white">{trade.stockSymbol}</p>
                      <p className="mt-1 text-sm text-slate-400">{trade.strategyName}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <span className="glass-chip text-ocean">{trade.planStatus ?? trade.status}</span>
                        <span className="glass-chip text-mint">{formatInr(trade.entryPrice)}</span>
                        <span className="glass-chip text-amber">{trade.maxHoldingDays ?? 0} day window</span>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="micro-label">Planned for</p>
                      <p className="mt-2 text-sm font-semibold text-white">{formatDate(trade.plannedForDate)}</p>
                      <p className="mt-2 text-xs text-slate-400">Capital {formatInr(trade.capitalBlocked)}</p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel p-6">
          <SectionHeader
            eyebrow="Open Book"
            title="Live open positions"
            subtitle="Current paper positions carried by the execution engine."
          />
          <div className="mt-5 space-y-3">
            {!openPositions.length ? (
              <EmptyTableMessage title="Abhi koi live open position nahi hai." />
            ) : (
              openPositions.slice(0, 8).map((trade) => (
                <div key={trade.tradeId} className="subpanel rounded-[1.35rem] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-display text-xl font-semibold text-white">{trade.stockSymbol}</p>
                      <p className="mt-1 text-sm text-slate-400">{trade.strategyName}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <span className="glass-chip text-ocean">{trade.signalType}</span>
                        <span className="glass-chip text-slate-200">{trade.direction ?? "BUY"}</span>
                        <span className={`glass-chip ${pnlToneClass(trade.pnlRupees)}`}>{formatInr(trade.pnlRupees)}</span>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="micro-label">Current</p>
                      <p className="mt-2 mono-value text-lg font-semibold text-white">{formatInr(trade.currentPrice)}</p>
                      <p className="mt-2 text-xs text-slate-400">{formatPct(trade.pnlPct)}</p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="panel p-6">
          <SectionHeader eyebrow="Official Market Context" title="Market indices strip" subtitle="Phase 1 market context used downstream by gates and crisis logic." />
          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {(market?.indices ?? []).map((item) => (
              <div key={item.key} className="subpanel rounded-[1.25rem] p-4">
                <p className="micro-label">{item.label}</p>
                <p className="mt-3 mono-value text-3xl font-semibold text-white">{formatNumber(item.value)}</p>
                <p className="mt-2 text-xs text-slate-400">
                  {item.reference !== null ? `Ref ${formatNumber(item.reference)} | Delta ${formatPct(item.deltaPctToReference)}` : item.status}
                </p>
              </div>
            ))}
          </div>
        </div>

        <div className="panel p-6">
          <SectionHeader eyebrow="Sector Breadth" title="Leaders vs SMA50" subtitle="Official sector context from the latest market snapshot." />
          <div className="mt-5 h-72">
            {!market?.sectorOverview.leaders.length ? (
              <StatePanel title="Sector context abhi available nahi hai." tone="empty" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={market.sectorOverview.leaders}>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                  <XAxis dataKey="sector" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} />
                  <Line dataKey="deltaPctToSma50" type="monotone" stroke="#13f7c7" strokeWidth={3} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="mt-4 flex items-center justify-between text-sm text-slate-400">
            <span>Sectors above SMA50</span>
            <span className="font-semibold text-white">
              {market?.sectorOverview.aboveSma50 ?? 0}/{market?.sectorOverview.total ?? 0}
            </span>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <div className="panel p-6 xl:col-span-2">
          <SectionHeader eyebrow="Distribution" title="Phase 2 label spread" subtitle="Snapshot of current universe mix across STRONG BUY, WATCHLIST, and NO ACTION." />
          <div className="mt-5 h-64">
            {!scoringSummary?.distribution.length ? (
              <StatePanel title="Scoring distribution abhi available nahi hai." tone="empty" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={scoringSummary.distribution}>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ background: "#08101d", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 16 }} />
                  <Area dataKey="count" type="monotone" stroke="#f59e0b" fill="rgba(245,158,11,0.16)" strokeWidth={3} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="panel p-6">
          <SectionHeader eyebrow="Snapshot" title="Desk pulse" subtitle="Quick audit values from the latest batch." />
          <div className="mt-5 space-y-4">
            <div className="subpanel rounded-[1.2rem] p-4">
              <p className="micro-label">Score date</p>
              <p className="mt-2 text-lg font-semibold text-white">{formatDate(scoringSummary?.asOfDate)}</p>
            </div>
            <div className="subpanel rounded-[1.2rem] p-4">
              <p className="micro-label">Plan date</p>
              <p className="mt-2 text-lg font-semibold text-white">{formatDate(cutover?.latestPlanDate)}</p>
            </div>
            <div className="subpanel rounded-[1.2rem] p-4">
              <p className="micro-label">Open risk signals</p>
              <p className="mt-2 text-lg font-semibold text-white">{latestRisk?.activeSignals.length ?? 0}</p>
              <p className="mt-2 text-xs text-slate-400">{(latestRisk?.activeSignals ?? []).join(", ") || "No active crisis flags"}</p>
            </div>
            <div className="subpanel rounded-[1.2rem] p-4">
              <p className="micro-label">Closed trades</p>
              <p className="mt-2 text-lg font-semibold text-white">{portfolioPerformanceQuery.data?.closedTradeCount ?? 0}</p>
              <p className="mt-2 text-xs text-slate-400">Last 180-day executed sample</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
