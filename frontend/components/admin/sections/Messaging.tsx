"use client";

import { useEffect, useState } from "react";
import { Bot, Mail, Megaphone, MessageSquare } from "lucide-react";
import { API, Btn, Card, Pill, Stat, inputClass, num, useAdmin } from "../ui";

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
