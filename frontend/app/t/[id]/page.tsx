import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { SharedTicketCard } from "@/components/SharedTicketCard";
import { fetchSharedTicket } from "@/lib/share";

type Props = { params: { id: string } };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = params;
  const t = await fetchSharedTicket(id);
  if (!t) return { title: "Ticket not found · BetIQ" };
  const odds = t.total_odds ? ` · ${t.total_odds.toFixed(2)}x` : "";
  const title = `BetIQ ticket ${t.code}${odds}`;
  const description = `${t.legs.length} pick${t.legs.length === 1 ? "" : "s"}: ` +
    t.legs.slice(0, 3).map(l => `${l.home} v ${l.away}`).join(", ") + (t.legs.length > 3 ? "…" : "");
  const image = `/api/og/ticket?id=${encodeURIComponent(id)}`;
  return {
    title, description,
    openGraph: { title, description, images: [{ url: image, width: 1200, height: 630 }] },
    twitter: { card: "summary_large_image", title, description, images: [image] },
  };
}

export default async function SharedTicketPage({ params }: Props) {
  const { id } = params;
  const t = await fetchSharedTicket(id);
  if (!t) notFound();
  return (
    <AppShell>
      <div className="max-w-2xl mx-auto px-4 py-6">
        <SharedTicketCard ticket={t} />
      </div>
    </AppShell>
  );
}
