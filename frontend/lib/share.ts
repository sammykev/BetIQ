import { API, type Ticket, type TicketLeg } from "@/lib/matchday";

/** A ticket shared by link (/t/<id>): what anyone with the link can see. */
export type SharedTicket = Pick<Ticket, "code" | "created_at" | "total_odds" | "status" | "settled_at"> & {
  legs: (TicketLeg & { league?: string })[];
};

/** The shared ticket, or null when the link is unknown or the ticket was removed. */
export async function fetchSharedTicket(id: string): Promise<SharedTicket | null> {
  try {
    const r = await fetch(`${API}/api/share/${encodeURIComponent(id)}`, { next: { revalidate: 60 } });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

export const shareUrl = (id: string) =>
  `${typeof window !== "undefined" ? window.location.origin : "https://predict-withbetiq.vercel.app"}/t/${id}`;
