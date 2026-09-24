"use client";

import { useMemo, useRef, useState } from "react";

// Small SVG charts for the admin panel. Colours come from --chart-1/--chart-2
// (globals.css, validated for colour-blind separation in both themes); the
// second series is also dashed and every chart has a legend, so identity
// never rests on colour alone. Hover shows exact values.

export interface Series { name: string; values: number[]; color?: "1" | "2"; format?: (n: number) => string }

const W = 640;
const PAD = { top: 12, right: 12, bottom: 22, left: 40 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

const short = (n: number) =>
  n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(1)}K` : `${Math.round(n)}`;

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="flex flex-wrap gap-3 text-[11px] text-n-400">
      {series.map((s, i) => (
        <span key={s.name} className="inline-flex items-center gap-1.5">
          <svg width="18" height="6" aria-hidden="true">
            <line x1="0" y1="3" x2="18" y2="3" stroke={`rgb(var(--chart-${s.color ?? i + 1}))`} strokeWidth="2"
              strokeDasharray={i === 1 ? "4 3" : undefined} />
          </svg>
          {s.name}
        </span>
      ))}
    </div>
  );
}

/** Lines over dates, one y-axis. Hover: crosshair + all series' values. */
export function LineChart({ labels, series, height = 180 }: { labels: string[]; series: Series[]; height?: number }) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const H = height;
  const max = niceMax(Math.max(1, ...series.flatMap(s => s.values)));
  const x = (i: number) => PAD.left + (labels.length <= 1 ? 0 : (i / (labels.length - 1)) * (W - PAD.left - PAD.right));
  const y = (v: number) => PAD.top + (1 - v / max) * (H - PAD.top - PAD.bottom);
  const ticks = [0, max / 2, max];
  const every = Math.max(1, Math.ceil(labels.length / 6));

  const move = (e: React.PointerEvent) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !labels.length) return;
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - PAD.left) / (W - PAD.left - PAD.right)) * (labels.length - 1));
    setHover(Math.min(labels.length - 1, Math.max(0, i)));
  };

  return (
    <div className="space-y-2">
      <Legend series={series} />
      <div className="relative">
        <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto touch-none" role="img"
          aria-label={series.map(s => s.name).join(" and ") + " by day"}
          onPointerMove={move} onPointerLeave={() => setHover(null)}>
          {ticks.map(t => (
            <g key={t}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="rgb(var(--n-800))" strokeWidth="1" />
              <text x={PAD.left - 6} y={y(t) + 3} textAnchor="end" className="fill-n-500 text-[10px] tnum">{short(t)}</text>
            </g>
          ))}
          {labels.map((l, i) => i % every === 0 && (
            <text key={l} x={x(i)} y={H - 6} textAnchor="middle" className="fill-n-500 text-[10px]">{l.slice(5)}</text>
          ))}
          {series.map((s, si) => (
            <polyline key={s.name} fill="none" stroke={`rgb(var(--chart-${s.color ?? si + 1}))`} strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" strokeDasharray={si === 1 ? "5 4" : undefined}
              points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
          ))}
          {hover !== null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} stroke="rgb(var(--n-600))" strokeWidth="1" />
              {series.map((s, si) => (
                <circle key={s.name} cx={x(hover)} cy={y(s.values[hover] ?? 0)} r="4"
                  fill={`rgb(var(--chart-${s.color ?? si + 1}))`} stroke="rgb(var(--surface))" strokeWidth="2" />
              ))}
            </g>
          )}
        </svg>
        {hover !== null && (
          <div className="pointer-events-none absolute top-0 rounded-lg border border-n-800 bg-surface-raised px-2.5 py-1.5 text-[11px] shadow-card"
            style={{ left: `${(x(hover) / W) * 100}%`, transform: `translateX(${hover > labels.length / 2 ? "-105%" : "5%"})` }}>
            <p className="text-n-400">{labels[hover]}</p>
            {series.map(s => (
              <p key={s.name} className="text-n-0 tnum"><span className="text-n-400">{s.name}</span> {(s.format ?? ((n: number) => n.toLocaleString()))(s.values[hover] ?? 0)}</p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Columns, one series. Hover a column for its value. */
export function ColumnChart({ labels, values, height = 140, format = (n: number) => n.toLocaleString(), tick = 1, name }: {
  labels: string[]; values: number[]; height?: number; format?: (n: number) => string; tick?: number; name: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const H = height;
  const max = niceMax(Math.max(1, ...values));
  const slot = (W - PAD.left - PAD.right) / Math.max(1, values.length);
  const bw = Math.max(2, slot - 2);  // 2px gap between columns
  const y = (v: number) => PAD.top + (1 - v / max) * (H - PAD.top - PAD.bottom);
  const base = H - PAD.bottom;
  const bars = useMemo(() => values.map((v, i) => ({ v, x: PAD.left + i * slot + 1, top: y(v) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [values, slot, max]);

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={name}
        onPointerLeave={() => setHover(null)}>
        {[0, max / 2, max].map(t => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="rgb(var(--n-800))" strokeWidth="1" />
            <text x={PAD.left - 6} y={y(t) + 3} textAnchor="end" className="fill-n-500 text-[10px] tnum">{short(t)}</text>
          </g>
        ))}
        {bars.map((b, i) => {
          const h = Math.max(0, base - b.top);
          const r = Math.min(4, bw / 2, h);
          return (
            <g key={i} onPointerEnter={() => setHover(i)}>
              {/* hit target: the whole column slot */}
              <rect x={b.x - 1} y={PAD.top} width={slot} height={base - PAD.top} fill="transparent" />
              {h > 0 && (
                <path fill="rgb(var(--chart-1))" opacity={hover === null || hover === i ? 1 : 0.55}
                  d={`M${b.x},${base} V${b.top + r} Q${b.x},${b.top} ${b.x + r},${b.top} H${b.x + bw - r} Q${b.x + bw},${b.top} ${b.x + bw},${b.top + r} V${base} Z`} />
              )}
              {i % tick === 0 && (
                <text x={b.x + bw / 2} y={H - 6} textAnchor="middle" className="fill-n-500 text-[10px]">{labels[i]}</text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-0 rounded-lg border border-n-800 bg-surface-raised px-2.5 py-1.5 text-[11px] shadow-card"
          style={{ left: `${((bars[hover].x + bw / 2) / W) * 100}%`, transform: `translateX(${hover > values.length / 2 ? "-105%" : "5%"})` }}>
          <p className="text-n-400">{labels[hover]}</p>
          <p className="text-n-0 tnum font-semibold">{format(values[hover])}</p>
        </div>
      )}
    </div>
  );
}
