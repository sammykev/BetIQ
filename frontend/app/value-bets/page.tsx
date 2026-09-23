"use client";

import { useRouter } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { ValueBets } from "@/components/ValueBets";
import { PageHeader } from "@/components/shell/PageHeader";

export default function ValueBetsPage() {
  const router = useRouter();

  return (
    <AppShell>
      <div className="space-y-6 animate-fade-in">
        <PageHeader
          eyebrow="Model vs bookmakers"
          title="Value bets"
          description="Where the model disagrees with the bookmakers — in your favour."
        />

        <ValueBets
          onMatchClick={(home, away) => {
            const q = new URLSearchParams({ home, away });
            router.push(`/match?${q}`);
          }}
        />
      </div>
    </AppShell>
  );
}
