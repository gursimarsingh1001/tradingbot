import { ReactNode } from "react";

type SectionHeaderProps = {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
};

export function SectionHeader({ eyebrow, title, subtitle, actions }: SectionHeaderProps) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow ? <p className="micro-label">{eyebrow}</p> : null}
        <h2 className="mt-2 font-display text-2xl font-semibold tracking-[-0.04em] text-white">{title}</h2>
        {subtitle ? <p className="mt-2 text-sm text-slate-400">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

type StatePanelProps = {
  title: string;
  tone?: "loading" | "error" | "empty";
};

export function StatePanel({ title, tone = "loading" }: StatePanelProps) {
  return (
    <div
      className={`panel rounded-[1.5rem] p-5 text-sm ${
        tone === "error" ? "border-coral/20 text-coral" : tone === "empty" ? "text-slate-400" : "text-slate-300"
      }`}
    >
      {title}
    </div>
  );
}

type MetricTileProps = {
  label: string;
  value: ReactNode;
  helper?: ReactNode;
  accent?: string;
};

export function MetricTile({ label, value, helper, accent = "text-white" }: MetricTileProps) {
  return (
    <div className="hero-stat px-5 py-4">
      <p className="micro-label">{label}</p>
      <div className={`mt-3 font-display text-3xl font-semibold ${accent}`}>{value}</div>
      {helper ? <div className="mt-2 text-xs text-slate-400">{helper}</div> : null}
    </div>
  );
}

export function EmptyTableMessage({ title }: { title: string }) {
  return <div className="rounded-[1.25rem] border border-white/10 bg-white/5 px-4 py-5 text-sm text-slate-400">{title}</div>;
}
