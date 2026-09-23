// Kick-off formatting shared by the prediction cards and the day-grouped list.

const pad = (n: number) => String(n).padStart(2, "0");

/** Local calendar date as "YYYY-MM-DD" (not UTC — users are mostly UTC+1). */
export function localDateStr(d = new Date()): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Convert a UTC "HH:MM" match time to the user's local timezone. */
export function localTime(date: string, utcTime: string): string {
  try {
    const dt = new Date(`${date}T${utcTime}:00Z`);
    if (isNaN(dt.getTime())) return utcTime;
    return dt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return utcTime;
  }
}

/** "Today", "Tomorrow", or e.g. "Sat 26 Sep". */
export function dayLabel(date: string): string {
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (date === localDateStr(today)) return "Today";
  if (date === localDateStr(tomorrow)) return "Tomorrow";
  try {
    return new Date(`${date}T12:00:00`).toLocaleDateString(undefined, {
      weekday: "short", day: "numeric", month: "short",
    });
  } catch {
    return date;
  }
}

/** "Today · 16:30" — or just the day when the time is unknown. */
export function kickoff(date: string, time?: string): string {
  const day = dayLabel(date);
  return time && time !== "TBD" ? `${day} · ${localTime(date, time)}` : day;
}
