"use client";

import clsx from "clsx";

// More of an account's record (backend tickets.summary): units over time,
// the model's chance for the legs against how often they won, and the
// return split by where the code came from and by its odds.

export interface RecordGroup { name: string; won: number; lost: number; units: number; roi: number }
export interface RecordDetailData {
  timeline?: { date: string; units: number }[];
  said?: { legs: number; model_said: number; came_in: number } | null;
  by_source?: RecordGroup[];
  by_odds?: RecordGroup[];
}

const signed = (n: number, digits = 1) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}`;
const pct = (x: number) => `${Math.round(x * 100)}%`;

function UnitsChart({ points }: { points: { date: string; units: number }[] }) {
  const W = 640, H = 160, P = { t: 10, r: 10, b: 20, l: 36 };
  const vals = [0, ...points.map(p => p.units)];
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (i: number) => P.l + (points.length <= 1 ? (W - P.l - P.r) / 2 : (i / (points.length - 1)) * (W - P.l - P.r));
  const y = (v: number) => P.t + (1 - (v - lo) / span) * (H - P.t - P.b);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.units).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const up = last.units >= 0;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img"
      aria-label={`Units over time: ${signed(last.units)} after ${points.length} settle days`}>
      <line x1={P.l} x2={W - P.r} y1={y(0)} y2={y(0)} className="stroke-n-600" strokeDasharray="3 3" />
      {[hi, 0, lo].filter((v, i, a) => a.indexOf(v) === i).map(v => (
        <text key={v} x={P.l - 6} y={y(v) + 3} textAnchor="end" className="fill-n-500 text-[10px] tnum">{signed(v, 0)}</text>
      ))}
      <path d={path} fill="none" strokeWidth="2.5" strokeLinejoin="round"
        className={up ? "stroke-accent" : "stroke-rose-500"} />
      {points.length <= 40 && points.map((p, i) => (
        <circle key={p.date} cx={x(i)} cy={y(p.units)} r="3" className={p.units >= 0 ? "fill-accent" : "fill-rose-500"}>
          <title>{`${p.date}: ${signed(p.units)} units`}</title>
        </circle>
      ))}
      <text x={P.l} y={H - 4} className="fill-n-500 text-[10px]">{points[0].date}</text>
      <text x={W - P.r} y={H - 4} textAnchor="end" className="fill-n-500 text-[10px]">{last.date}</text>
    </svg>
  );
}

function Groups({ title, rows }: { title: string; rows: RecordGroup[] }) {
  if (!rows.length) return null;
  return (
    <div className="card px-4 py-4 space-y-2">
      <p className="eyebrow">{title}</p>
      <table className="w-full text-xs">
        <thead><tr className="text-n-500 text-left">
          <th className="font-medium py-1">&nbsp;</th><th className="font-medium text-right">Won</th>
          <th className="font-medium text-right">Units</th><th className="font-medium text-right">Return</th>
        </tr></thead>
        <tbody>
          {rows.map(g => (
            <tr key={g.name} className="border-t border-n-800">
              <td className="py-1.5 text-n-200">{g.name}</td>
              <td className="text-right tnum text-n-400">{g.won}/{g.won + g.lost}</td>
              <td className={clsx("text-right tnum font-semibold", g.units >= 0 ? "text-accent" : "text-danger")}>{signed(g.units)}</td>
              <td className={clsx("text-right tnum", g.roi >= 0 ? "text-accent" : "text-danger")}>{g.roi > 0 ? "+" : ""}{Math.round(g.roi * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RecordDetail({ rec }: { rec: RecordDetailData }) {
  const points = rec.timeline ?? [];
  const said = rec.said;
  return (
    <>
      {points.length >= 2 && (
        <div className="card px-4 py-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="eyebrow">Units over time</p>
            <p className={clsx("text-xs font-semibold tnum", points[points.length - 1].units >= 0 ? "text-accent" : "text-danger")}>
              {signed(points[points.length - 1].units)} units</p>
          </div>
          <UnitsChart points={points} />
        </div>
      )}
      {said && said.legs >= 5 && (
        <div className="card px-4 py-4 space-y-1.5">
          <p className="eyebrow">Our chance vs what happened</p>
          <p className="text-sm text-n-300">
            Across your {said.legs} settled picks the model gave them <span className="font-bold text-n-0 tnum">{pct(said.model_said)}</span> on
            average, and <span className={clsx("font-bold tnum", said.came_in >= said.model_said - 0.03 ? "text-accent" : "text-warn")}>{pct(said.came_in)}</span> won.
          </p>
          <p className="text-[11px] text-n-500">
            {Math.abs(said.came_in - said.model_said) <= 0.03 ? "Close: the chances shown held up on your picks."
              : said.came_in > said.model_said ? "Your picks did better than the model expected."
              : said.legs < 40 ? "Short of what we said so far, over few picks: this moves a lot until there are more."
              : "Short of what we said: favour the markets that are working for you below."}
          </p>
        </div>
      )}
      <div className="grid sm:grid-cols-2 gap-3">
        <Groups title="By where the code came from" rows={rec.by_source ?? []} />
        <Groups title="By ticket odds" rows={rec.by_odds ?? []} />
      </div>
    </>
  );
}
