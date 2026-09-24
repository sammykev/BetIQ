import { clerkClient } from "@clerk/nextjs/server";
import { Resend } from "resend";
import { NextRequest, NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/serverAdmin";

const resend = new Resend(process.env.RESEND_API_KEY || "");

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

export async function POST(req: NextRequest) {
  try {
    if (!(await isAdminRequest(req)))
      return NextResponse.json({ error: "forbidden" }, { status: 403 });
    const body = await req.json();

    const { subject, message } = body;
    if (!subject || !message)
      return NextResponse.json({ error: "missing_subject_or_message" }, { status: 400 });

    const client = await clerkClient();
    const { data: users } = await client.users.getUserList({ limit: 500 });
    const now = new Date();
    const premiumEmails = users
      .filter(u => {
        const meta = u.publicMetadata as { subscription?: string; subscription_expires?: string };
        return meta?.subscription === "premium" && new Date(meta?.subscription_expires ?? 0) > now;
      })
      .flatMap(u => u.emailAddresses.map(e => e.emailAddress))
      .filter(Boolean);

    if (!premiumEmails.length)
      return NextResponse.json({ sent: 0, message: "No active premium users found." });

    const html = `
      <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px;">
          <span style="font-size:28px;font-weight:900;color:#22c55e;">BetIQ</span>
        </div>
        <p style="font-size:16px;line-height:1.6;color:#1e293b;">${escapeHtml(String(message)).replace(/\n/g, "<br/>")}</p>
        <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;"/>
        <p style="font-size:12px;color:#94a3b8;">
          You're receiving this as a BetIQ Premium member.
          <a href="https://predict-withbetiq.vercel.app" style="color:#22c55e;">Visit BetIQ</a>
        </p>
      </div>`;

    let sent = 0;
    // Resend free tier: 100/day — batch in groups of 50
    for (const email of premiumEmails.slice(0, 100)) {
      try {
        await resend.emails.send({
          from: "BetIQ <noreply@predict-withbetiq.vercel.app>",
          to: email,
          subject,
          html,
        });
        sent++;
      } catch { /* skip failed sends */ }
    }

    return NextResponse.json({ sent, total_premium: premiumEmails.length });
  } catch (err) {
    console.error("[admin/blast]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
