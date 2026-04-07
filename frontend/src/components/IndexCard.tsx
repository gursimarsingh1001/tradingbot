import type { CSSProperties } from "react";
import { ArrowDownRight, ArrowUpRight, Dot, GaugeCircle } from "lucide-react";
import { IndexValue } from "../api/client";

type Props = {
  name: string;
  data: IndexValue;
};

export function IndexCard({ name, data }: Props) {
  const isVix = name.toUpperCase().includes("VIX");
  const hasLiveValue = Number.isFinite(data.value) && data.value > 0;
  const positive = data.change >= 0;
  const tonePositive = isVix ? !positive : positive;
  const movePct = Math.abs((data.changePct ?? 0) * 100);
  const intensityPct = Math.max(8, Math.min(movePct * 18, 100));
  const statusLabel = !hasLiveValue ? "Syncing" : movePct >= 1.2 ? "Strong Move" : movePct >= 0.45 ? "Active" : "Quiet";
  const directionLabel = !hasLiveValue
    ? "Syncing"
    : isVix
      ? positive
        ? "VIX Up"
        : "VIX Down"
      : positive
        ? "Up Day"
        : "Down Day";

  return (
    <div className="panel panel-premium shell-section relative overflow-hidden p-6">
      <div
        className={`pointer-events-none absolute inset-0 opacity-70 ${
          tonePositive
            ? "bg-[radial-gradient(circle_at_top_right,rgba(0,255,178,0.18),transparent_34%)]"
            : "bg-[radial-gradient(circle_at_top_right,rgba(255,46,91,0.18),transparent_34%)]"
        }`}
      />
      <div className="edge-glow pointer-events-none" />
      <div className="orbital-ring pointer-events-none -right-10 -top-10 h-28 w-28" />
      <div className="orbital-ring pointer-events-none bottom-6 right-12 h-14 w-14 opacity-30" />
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="data-pill inline-flex bg-slate-900 text-white">{name}</p>
          <p className="mt-3 micro-label">Live index feed</p>
        </div>
        <span className={`glass-chip ${tonePositive ? "text-mint" : "text-coral"}`}>
          <Dot className="h-4 w-4" />
          {hasLiveValue ? statusLabel : "Syncing"}
        </span>
      </div>
      <div className="mt-5 space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex items-center gap-3">
            {hasLiveValue ? <span className="status-beacon inline-flex h-2.5 w-2.5 rounded-full bg-mint" /> : null}
            <h3 className="mono-value font-display text-4xl font-semibold tracking-tight text-ink">
              {hasLiveValue ? data.value.toFixed(2) : "--"}
            </h3>
          </div>
          {hasLiveValue ? (
            <div className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] ${tonePositive ? "border-mint/25 bg-mint/10 text-mint" : "border-coral/25 bg-coral/10 text-coral"}`}>
              {positive ? <ArrowUpRight className="h-4 w-4" /> : <ArrowDownRight className="h-4 w-4" />}
              {directionLabel}
            </div>
          ) : null}
        </div>
        {hasLiveValue ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="subpanel rounded-2xl px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Point change</p>
              <p className={`mono-value mt-2 text-lg font-semibold ${tonePositive ? "pnl-up" : "pnl-down"}`}>
                {positive ? "+" : ""}
                {data.change.toFixed(2)}
              </p>
            </div>
            <div className="subpanel rounded-2xl px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Session move</p>
              <p className={`mono-value mt-2 text-lg font-semibold ${tonePositive ? "pnl-up" : "pnl-down"}`}>
                {positive ? "+" : ""}
                {(data.changePct * 100).toFixed(2)}%
              </p>
            </div>
            <div className="subpanel rounded-2xl px-4 py-3 sm:col-span-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Move strength</p>
                  <p className="mt-2 text-sm font-semibold text-white">{statusLabel} move</p>
                </div>
                <div className="inline-flex items-center gap-3 text-slate-300">
                  <div className="radial-gauge ml-auto" style={{ "--gauge-pct": intensityPct } as CSSProperties} />
                  <div className="text-right">
                    <div className="inline-flex items-center gap-2">
                      <GaugeCircle className="h-4 w-4" />
                      <span className="mono-value text-xs font-semibold uppercase tracking-[0.16em]">{intensityPct.toFixed(0)} / 100</span>
                    </div>
                    <p className="mt-1 text-[11px] uppercase tracking-[0.16em] text-slate-400">Strength</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-2xl border border-slate-200/70 bg-white/70 px-4 py-3 text-sm text-slate-500">
            Awaiting live quote refresh.
          </div>
        )}
      </div>
    </div>
  );
}
