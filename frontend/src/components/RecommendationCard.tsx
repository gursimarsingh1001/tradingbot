import { BriefcaseBusiness, Radar, ShieldCheck, Sparkles, Target, Zap } from "lucide-react";
import { Recommendation } from "../api/client";
import { formatInr, pnlToneClass } from "../utils/formatters";
import { RegimeBadge } from "./RegimeBadge";

type Props = {
  item: Recommendation;
  livePrice?: number | null;
  liveChangePct?: number | null;
};

export function RecommendationCard({ item, livePrice = null, liveChangePct = null }: Props) {
  const confidenceColor =
    item.confidenceScore >= 90 ? "bg-mint" : item.confidenceScore >= 70 ? "bg-ocean" : item.confidenceScore >= 55 ? "bg-amber" : "bg-slate-300";
  const directionTone = item.direction === "SELL" ? "bg-coral/10 text-coral" : "bg-mint/10 text-mint";
  const confidenceLabel =
    item.confidenceScore >= 90 ? "Prime" : item.confidenceScore >= 75 ? "High Conviction" : item.confidenceScore >= 60 ? "Actionable" : "Watch Only";
  const explanationSections = item.explanationSections ?? {};
  const sectionOrder: Array<{ key: string; label: string }> = [
    { key: "technical", label: "Technical" },
    { key: "news", label: "News" },
    { key: "sector", label: "Sector" },
    { key: "fundamentals", label: "Financials" },
    { key: "risk", label: "Risk" },
  ];

  return (
    <div className="panel panel-premium shell-section signal-shell p-6">
      <div className="edge-glow pointer-events-none" />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-[240px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="chrome-kicker">{item.signalType}</span>
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
              {item.paperTradeStatus}
            </span>
            {item.patternName ? <span className="glass-chip text-amber">{item.patternName}</span> : null}
          </div>
          <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h3 className="font-display text-3xl font-semibold text-ink">{item.stockSymbol}</h3>
              <p className="mt-1 text-sm text-slate-600">{item.strategyName}</p>
            </div>
            <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-300">
              <Sparkles className="h-4 w-4 text-amber" />
              {confidenceLabel}
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
            {item.direction ? <span className={`rounded-full px-3 py-1 ${directionTone}`}>{item.direction}</span> : null}
            {item.productType ? <span className="rounded-full bg-ocean/10 px-3 py-1 text-ocean">{item.productType}</span> : null}
            {item.leverageMultiplier ? (
              <span className="rounded-full bg-mint/10 px-3 py-1 text-mint">{item.leverageMultiplier.toFixed(2)}x leverage</span>
            ) : null}
            {item.sector ? <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">{item.sector}</span> : null}
            {item.sectorScore !== null && item.sectorScore !== undefined ? (
              <span className="rounded-full bg-amber/10 px-3 py-1 text-amber">Sector {item.sectorScore.toFixed(2)}</span>
            ) : null}
            {item.fundamentalQualityScore !== null && item.fundamentalQualityScore !== undefined ? (
              <span className="rounded-full bg-mint/10 px-3 py-1 text-mint">Financials {item.fundamentalQualityScore.toFixed(2)}</span>
            ) : null}
            {item.financialDataSource ? (
              <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">
                {item.financialDataSource === "STRUCTURED_SNAPSHOT" ? "Structured financials" : "News-led financials"}
              </span>
            ) : null}
            {item.daysToEarnings !== null && item.daysToEarnings !== undefined ? (
              <span className="rounded-full bg-coral/10 px-3 py-1 text-coral">
                {item.daysToEarnings <= 0 ? "Results due" : `${item.daysToEarnings}d to results`}
              </span>
            ) : null}
            {item.maxHoldingDays ? (
              <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">{item.maxHoldingDays} day hold</span>
            ) : item.signalType === "INTRADAY" ? (
              <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">Same-day square-off</span>
            ) : null}
          </div>
        </div>
        <div className="space-y-3 text-right">
          <RegimeBadge regime={item.regimeAtEntry} />
          <div className="hero-stat min-w-[180px] px-4 py-3 text-left">
            <p className="micro-label">Selection engine</p>
            <p className="mt-2 text-sm font-semibold text-white">
              {item.fundamentalHasSnapshot ? "Structured conviction" : "Signal-led conviction"}
            </p>
            <p className="mt-1 text-xs text-slate-400">
              {item.fundamentalConfidence !== null && item.fundamentalConfidence !== undefined
                ? `${item.fundamentalConfidence.toFixed(2)} financial confidence`
                : "Dynamic scoring blend"}
            </p>
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        <div className="subpanel rounded-2xl px-4 py-4">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-slate-400">
            <Radar className="h-4 w-4" />
            Live price
          </div>
          <p className="mono-value mt-2 font-display text-2xl font-semibold text-white">{formatInr(livePrice)}</p>
          {liveChangePct !== null && liveChangePct !== undefined ? (
            <p className={`mono-value mt-1 text-xs font-semibold ${pnlToneClass(liveChangePct)}`}>
              {liveChangePct >= 0 ? "+" : ""}
              {liveChangePct.toFixed(2)}%
            </p>
          ) : (
            <p className="mt-1 text-xs text-slate-400">Waiting for live quote</p>
          )}
        </div>
        <div className="subpanel rounded-2xl px-4 py-4">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-slate-400">
            <BriefcaseBusiness className="h-4 w-4" />
            Capital blocked
          </div>
          <p className="mono-value mt-2 font-display text-2xl font-semibold text-white">{formatInr(item.capitalBlocked)}</p>
          <p className="mt-1 text-xs text-slate-400">Effective paper capital reserved</p>
        </div>
        <div className="subpanel rounded-2xl px-4 py-4">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-slate-400">
            <Target className="h-4 w-4" />
            Remaining shares
          </div>
          <p className="mt-2 font-display text-2xl font-semibold text-white">
            {item.remainingShares !== null && item.remainingShares !== undefined ? item.remainingShares : "Pending"}
          </p>
          <p className="mt-1 text-xs text-slate-400">Residual quantity available</p>
        </div>
      </div>

      <div className="mt-5 rounded-[1.5rem] border border-white/10 bg-white/5 px-4 py-4">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-ocean" />
            <span className="text-slate-500">Confidence</span>
          </div>
          <span className="mono-value font-semibold text-ink">{item.confidenceScore.toFixed(0)} / 100</span>
        </div>
        <div className="confidence-shell mt-3">
          <div className="h-3 rounded-full bg-slate-100">
            <div className={`h-3 rounded-full ${confidenceColor}`} style={{ width: `${item.confidenceScore}%` }} />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <span className="glass-chip text-ocean">{confidenceLabel}</span>
          {item.daysToEarnings !== null && item.daysToEarnings !== undefined ? (
            <span className="glass-chip text-amber">
              {item.daysToEarnings <= 0 ? "Results due now" : `${item.daysToEarnings}d earnings runway`}
            </span>
          ) : null}
          {item.sector ? <span className="glass-chip text-slate-200">{item.sector} tape</span> : null}
        </div>
      </div>

      <div className="mt-5 grid gap-4 text-sm text-slate-700 md:grid-cols-2">
        <div className="hero-stat px-4 py-4">
          <p className="text-slate-500">Entry zone</p>
          <p className="mono-value font-semibold">
            {formatInr(item.entryZoneLow)} to {formatInr(item.entryZoneHigh)}
          </p>
        </div>
        <div className="hero-stat px-4 py-4">
          <p className="text-slate-500">Stop loss</p>
          <p className="mono-value font-semibold">{formatInr(item.stopLoss)}</p>
        </div>
        <div className="hero-stat px-4 py-4">
          <p className="text-slate-500">Targets</p>
          <p className="mono-value font-semibold">
            {formatInr(item.target1)} / {formatInr(item.target2)} / {formatInr(item.target3)}
          </p>
        </div>
        <div className="hero-stat px-4 py-4">
          <p className="text-slate-500">Paper trade</p>
          <p className={`mono-value font-semibold ${pnlToneClass(item.pnlRupees)}`}>
            {item.pnlRupees !== null ? `${formatInr(item.pnlRupees)} (${item.pnlPct?.toFixed(2)}%)` : "Waiting"}
          </p>
        </div>
      </div>

      {(item.recommendationReason || item.basisPoints?.length) && (
        <div className="mt-5 rounded-2xl border border-slate-100 bg-slate-50 p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-slate-500">
            <ShieldCheck className="h-4 w-4 text-mint" />
            Why This Was Recommended
          </div>
          {item.recommendationReason && <p className="mt-3 text-sm leading-6 text-slate-700">{item.recommendationReason}</p>}
          {sectionOrder.some((section) => (explanationSections[section.key] ?? []).length) ? (
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {sectionOrder.map((section) => {
                const points = explanationSections[section.key] ?? [];
                if (!points.length) {
                  return null;
                }
                return (
                  <div key={section.key} className="rounded-2xl border border-white/60 bg-white/80 p-3">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">{section.label}</p>
                    <div className="mt-2 space-y-1 text-sm text-slate-600">
                      {points.slice(0, 3).map((point) => (
                        <p key={point}>- {point}</p>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : item.basisPoints?.length ? (
            <div className="mt-3 space-y-2 text-sm text-slate-600">
              {item.basisPoints.slice(0, 4).map((point) => (
                <p key={point}>- {point}</p>
              ))}
            </div>
          ) : null}
          {item.eventFlags?.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {item.eventFlags.slice(0, 3).map((flag) => (
                <span key={flag} className="rounded-full bg-coral/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-coral">
                  {flag}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
