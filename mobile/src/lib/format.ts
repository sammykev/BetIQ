// Kick-off times come in UTC; the app shows them in the phone's own time.

export function localTime(date: string, time?: string | null): string {
  if (!time) return "";
  const d = new Date(`${date}T${time}:00Z`);
  return isNaN(d.getTime()) ? time : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function dayLabel(iso: string, today: string): string {
  if (iso === today) return "Today";
  const d = new Date(`${iso}T12:00:00Z`), t = new Date(`${today}T12:00:00Z`);
  const diff = Math.round((d.getTime() - t.getTime()) / 86_400_000);
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

export const pct = (x?: number | null) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "—");
export const pctFine = (x?: number | null) =>
  typeof x === "number" ? `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%` : "—";
export const odds = (x?: number | null) => (typeof x === "number" ? x.toFixed(2) : "—");

export function ago(iso: string): string {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
