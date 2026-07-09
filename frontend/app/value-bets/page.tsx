"use client";

import { useRouter } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { ValueBets } from "@/components/ValueBets";

export default function ValueBetsPage() {
  const router = useRouter();

  return (
    <AppShell>
      <div className="space-y-6 animate-fade-in">
        <div>
          <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-zinc-900 dark:text-white">
            Value Bets
          </h1>
          <p className="text-sm text-zinc-400 dark:text-zinc-500 mt-1">
            Where the model disagrees with the bookmakers — in your favour
          </p>
        </div>

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
