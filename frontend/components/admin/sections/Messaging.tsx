"use client";

import { useEffect, useState } from "react";
import { Bot, ExternalLink, Mail, Megaphone, MessageSquare, Send } from "lucide-react";
import { API, Btn, Card, Pill, Stat, Toggle, ago, inputClass, num, useAdmin } from "../ui";

/** The Telegram message is HTML: shown here as the channel will read it. */
const plain = (html: string) => html.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
  .replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

const CHANNEL_SETUP: Record<string, string> = {
  x: "An X developer app with Read and write permission (and API credits)",
  telegram: "A bot from @BotFather, added to your channel as an administrator",
};

/** Posting the day's 10 / 15 / 20 odds slips to one channel each morning
 * (backend x_poster.py / telegram_poster.py). */
function DailyPost({ ch, name }: { ch: "x" | "telegram"; name: string }) {
  const { get, flash, adminFetch } = useAdmin();
  const [x, setX] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = () => get(`/api/admin/post/${ch}`).then(d => setX(d ?? { error: true }));
  useEffect(() => { load(); }, [get]); // eslint-disable-line react-hooks/exhaustive-deps

  const call = async (method: string, path: string, body: object, key: string) => {
    setBusy(key);
    try {
      const r = await adminFetch(`${API}${path}`, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) throw new Error(d?.detail || "Didn't work");
      return d;
    } catch (e: any) {
      flash("err", e instanceof TypeError ? "Couldn't reach the server" : e?.message || "Didn't work");
      return null;
    } finally { setBusy(null); }
  };
  const toggle = async () => {
    const d = await call("PUT", `/api/admin/post/${ch}`, { enabled: !x.enabled }, "toggle");
    if (d) { setX(d); flash("ok", d.enabled ? `The daily odds will be posted on ${name} each morning` : `Posting on ${name} is off`); }
  };
  const postNow = async () => {
    const again = x?.today?.status === "posted";
    if (again && !confirm("Today's slips are already posted. Post them again?")) return;
    const d = await call("POST", `/api/admin/post/${ch}/now`, { again }, "post");
    if (d) { flash(d.status === "posted" ? "ok" : "err", d.status === "posted" ? `Posted on ${name}` : d.error || "Not posted"); load(); }
  };

  if (!x) return <p className="text-xs text-n-400">Loading…</p>;
  if (x.error) return <p className="text-xs text-n-400">Couldn&apos;t load the {name} settings.</p>;
  const t = x.today ?? {};
  const lagos = (utc: string) => { const [h, m] = utc.split(":").map(Number); return `${String((h + 1) % 24).padStart(2, "0")}:${String(m).padStart(2, "0")}`; };
  return (
    <div className="space-y-3">
      {!x.configured && (
        <p className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-n-200">
          {CHANNEL_SETUP[ch]}. Add to the server&apos;s .env: <span className="font-mono">{x.missing.join(", ")}</span>. Then run{" "}
          <span className="font-mono">sudo docker compose up -d</span>.
        </p>
      )}
      <div className="grid gap-2 grid-cols-1 sm:grid-cols-2">
        <Toggle on={!!x.enabled} busy={busy === "toggle"} onChange={toggle} label={x.enabled ? "Posting each morning" : "Posting off"}
          hint={`Once the slips have their codes (07:05), by ${lagos(x.post_by_utc)} at the latest (Lagos)`} />
        <div className="flex items-center justify-between gap-3 rounded-xl bg-surface-sunken px-3 py-2.5">
          <div className="min-w-0 text-sm">
            <span className="block font-semibold text-n-0">Today</span>
            <span className="block text-[11px] text-n-400 truncate">
              {t.status === "posted" ? `Posted ${ago(t.at)}` : t.status === "failed" ? `Failed ${t.tries}×: ${t.error}`
                : t.status === "skipped" ? t.error : "Not posted yet"}
            </span>
          </div>
          <span className="flex gap-1.5 shrink-0">
            {t.url && <a href={t.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-n-800 px-2.5 py-1.5 text-xs text-n-300 hover:text-n-0"><ExternalLink size={12} /> View</a>}
            <Btn onClick={postNow} busy={busy === "post"} disabled={!x.configured || !x.preview}><Send size={12} /> Post now</Btn>
          </span>
        </div>
      </div>
      {x.preview ? (
        <div>
          <p className="eyebrow mb-1.5">Today&apos;s post</p>
          <pre className="whitespace-pre-wrap rounded-xl border border-n-800 bg-surface-sunken p-3 text-xs text-n-200 font-sans max-h-80 overflow-y-auto">{plain(x.preview)}</pre>
        </div>
      ) : <p className="text-xs text-n-400">Today&apos;s slips aren&apos;t ready yet (or none has a booking code), so there&apos;s nothing to post.</p>}
      <p className="text-[11px] text-n-500">
        {ch === "x"
          ? "One post a day: each slip's total odds and SportyBet booking code, with a link to the picks on the site. X charges for API use (credits in the X developer console)."
          : "One message a day: every slip with its booking code and all its picks (times in Lagos time), and a link to the site. Telegram's bot API is free."}
      </p>
    </div>
  );
}

export function MessagingSection() {
  const { get, post, flash, adminFetch } = useAdmin();
  const [banner, setBanner] = useState("");
  const [live, setLive] = useState("");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [stats, setStats] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/admin/banner`).then(r => r.json()).then(d => { setLive(d.banner || ""); setBanner(d.banner || ""); }).catch(() => {});
    get("/api/admin/stats").then(setStats);
  }, [get]);

  const publish = async (text: string) => {
    setBusy("banner");
    try { const d = await post("/api/admin/banner", { text }); setLive(d.banner || ""); setBanner(d.banner || "");
      flash("ok", d.banner ? "Banner is live" : "Banner removed"); }
    catch { flash("err", "Couldn't update the banner"); }
    setBusy(null);
  };

  const blast = async () => {
    if (!confirm(`Email every active Premium member now?\n\n${subject}`)) return;
    setBusy("blast");
    try {
      const r = await adminFetch("/api/admin/blast", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, message }) });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      flash("ok", `Sent to ${d.sent} of ${d.total_premium ?? d.sent} Premium members`);
      setSubject(""); setMessage("");
    } catch { flash("err", "Email didn't send (check RESEND_API_KEY on Vercel)"); }
    setBusy(null);
  };

  return (
    <div className="space-y-4">
      <Card title="Daily odds on Telegram" icon={<Send size={15} />} subtitle="Post the 10, 15 and 20 odds slips, with every pick, to your Telegram channel every morning">
        <DailyPost ch="telegram" name="Telegram" />
      </Card>

      <Card title="Daily odds on X" icon={<Send size={15} />} subtitle="Post the 10, 15 and 20 odds slips on X every morning">
        <DailyPost ch="x" name="X" />
      </Card>

      <Card title="Site banner" icon={<Megaphone size={15} />} subtitle="A bar across the top of every page"
        action={live ? <Pill tone="ok">Live</Pill> : <Pill>Off</Pill>}>
        {live && <p className="rounded-lg bg-info/10 border border-info/25 px-3 py-2 text-sm text-info">📢 {live}</p>}
        <div className="flex gap-2">
          <input value={banner} maxLength={300} onChange={e => setBanner(e.target.value)}
            placeholder='e.g. "🔥 Weekend acca is live — check your picks!"' className={inputClass} />
          <Btn variant="primary" busy={busy === "banner"} disabled={!banner.trim() || banner === live} onClick={() => publish(banner.trim())}>Publish</Btn>
          {live && <Btn busy={busy === "banner"} onClick={() => publish("")}>Remove</Btn>}
        </div>
        <p className="text-[11px] text-n-500">{banner.length}/300</p>
      </Card>

      <Card title="Email Premium members" icon={<Mail size={15} />} subtitle="Resend free tier: 100 emails a day">
        <input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject" className={inputClass} />
        <textarea value={message} onChange={e => setMessage(e.target.value)} rows={4} placeholder="Message" className={`${inputClass} resize-y`} />
        <Btn variant="primary" onClick={blast} busy={busy === "blast"} disabled={!subject.trim() || !message.trim()}>
          <Mail size={12} /> Send to Premium members
        </Btn>
      </Card>

      <Card title="AI assistant" icon={<Bot size={15} />}>
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Explanations today" value={num(stats?.ai?.explain_today)} />
          <Stat label="Explanations total" value={num(stats?.ai?.explain_total)} />
          <Stat label="Recent chat questions" value={num(stats?.ai?.recent_queries?.length)} />
        </div>
        {(stats?.ai?.recent_queries ?? []).length > 0 && (
          <ul className="space-y-1">
            {(stats.ai.recent_queries as string[]).slice(0, 12).map((q, i) => (
              <li key={i} className="flex items-start gap-2 text-xs"><MessageSquare size={11} className="text-n-500 mt-0.5 shrink-0" />
                <span className="text-n-300 break-words">{q}</span></li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
