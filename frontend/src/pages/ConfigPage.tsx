import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ShieldAlert } from "lucide-react";
import { api } from "../api/client";
import { SectionHeader, StatePanel } from "../components/CommandPrimitives";
import { formatDateTime, formatPct } from "../utils/formatters";
import { marketAwareInterval } from "../utils/refresh";

export default function ConfigPage() {
  const statusQuery = useQuery({
    queryKey: ["systemStatusPage"],
    queryFn: api.fetchSystemStatus,
    refetchInterval: marketAwareInterval(60000, 300000),
  });

  const status = statusQuery.data;
  const featureFlags = Object.entries(status?.featureFlags ?? {});
  const nextJobs = status?.scheduler.nextJobs ?? [];
  const phases = status?.phases ?? {};
  const phase6 = (phases["phase6"] as Record<string, unknown> | undefined) ?? {};

  if (!status) {
    return <StatePanel title="System status load ho raha hai." />;
  }

  return (
    <div className="space-y-6">
      <section className="panel p-6">
        <SectionHeader
          eyebrow="Config & Health"
          title="System status and feature flags"
          subtitle="Backend health, scheduler cadence, kill switch state, and Phase 1-6 runtime visibility."
        />
        <div className="mt-6 grid gap-4 xl:grid-cols-3">
          <div className="subpanel rounded-[1.5rem] p-5">
            <p className="micro-label">Backend</p>
            <div className="mt-4 flex items-center gap-3">
              <CheckCircle2 className="h-5 w-5 text-mint" />
              <p className="text-lg font-semibold text-white">{status.backend.status}</p>
            </div>
            <p className="mt-3 text-xs text-slate-400">{formatDateTime(status.backend.currentTime)}</p>
          </div>

          <div className="subpanel rounded-[1.5rem] p-5">
            <p className="micro-label">Kill switch</p>
            <div className="mt-4 flex items-center gap-3">
              <ShieldAlert className={`h-5 w-5 ${status.killSwitch.active ? "text-coral" : "text-mint"}`} />
              <p className="text-lg font-semibold text-white">{status.killSwitch.active ? "Active" : "Inactive"}</p>
            </div>
            <p className="mt-3 text-xs text-slate-400">{status.killSwitch.reason ?? "No active override reason."}</p>
          </div>

          <div className="subpanel rounded-[1.5rem] p-5">
            <p className="micro-label">Phase 6 average fill rate</p>
            <p className="mt-4 text-3xl font-semibold text-white">
              {formatPct(Number((phase6["averageFillRate"] as number | undefined) ?? 0) * 100, 0)}
            </p>
            <p className="mt-3 text-xs text-slate-400">
              {(phase6["symbolsBelow80FillRate"] as number | undefined) ?? 0} symbols below 80% completeness
            </p>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="panel p-6">
          <SectionHeader eyebrow="Feature Flags" title="Runtime switches" subtitle="Read-only view of current backend feature toggles." />
          <div className="mt-5 grid gap-3">
            {featureFlags.map(([key, value]) => (
              <div key={key} className="subpanel rounded-[1.2rem] px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-slate-300">{key}</span>
                  <span className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${value ? "bg-mint/15 text-mint" : "bg-slate-500/15 text-slate-300"}`}>
                    {String(value)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="panel p-6">
          <SectionHeader eyebrow="Scheduler" title="Health and next jobs" subtitle="Embedded schedulers and upcoming runtime jobs." />
          <div className="mt-5 grid gap-3">
            <div className="subpanel rounded-[1.2rem] p-4">
              <p className="micro-label">Market scheduler</p>
              <p className="mt-2 text-lg font-semibold text-white">{status.scheduler.health.market.status ?? "unknown"}</p>
              <p className="mt-2 text-xs text-slate-400">Last event {formatDateTime(status.scheduler.health.market.last_event_at as string | null)}</p>
            </div>
            <div className="subpanel rounded-[1.2rem] p-4">
              <p className="micro-label">After-market scheduler</p>
              <p className="mt-2 text-lg font-semibold text-white">{status.scheduler.health.afterMarket.status ?? "unknown"}</p>
              <p className="mt-2 text-xs text-slate-400">Last event {formatDateTime(status.scheduler.health.afterMarket.last_event_at as string | null)}</p>
            </div>
            <div className="rounded-[1.2rem] border border-white/10 bg-white/5 p-4">
              <p className="micro-label">Next jobs</p>
              <div className="mt-4 space-y-3">
                {nextJobs.length ? (
                  nextJobs.map((job) => (
                    <div key={`${job.scheduler}-${job.id}`} className="flex items-center justify-between gap-3 text-sm">
                      <div>
                        <p className="font-semibold text-white">{job.name}</p>
                        <p className="text-xs text-slate-500">{job.scheduler}</p>
                      </div>
                      <p className="text-slate-300">{formatDateTime(job.nextRunAt)}</p>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-slate-400">No scheduler jobs exposed yet.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="panel p-6">
        <SectionHeader eyebrow="Phase State" title="Phase progression" subtitle="Last-known state for official data, scoring, gates, cutover, risk, and reconciliation." />
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(phases).map(([phaseName, payload]) => (
            <div key={phaseName} className="subpanel rounded-[1.35rem] p-4">
              <p className="micro-label">{phaseName.toUpperCase()}</p>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-slate-300">
                {JSON.stringify(payload, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
