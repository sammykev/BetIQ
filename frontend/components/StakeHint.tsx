"use client";

import { useEffect, useState } from "react";
import { Wallet } from "lucide-react";
import { MAX_SHARE, naira, onBankroll, readBankroll, saveBankroll, stakeAmount, stakeShare } from "@/lib/stake";

// The suggested stake for a slip: quarter Kelly from the slip's chance and
// odds, capped at 3% of the bankroll the visitor sets (kept in this browser).

export function StakeHint({ chance, odds }: { chance?: number; odds?: number }) {
  const [bankroll, setBankroll] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    setBankroll(readBankroll());
    return onBankroll(setBankroll);
  }, []);

  if (typeof chance !== "number" || typeof odds !== "number") return null;
  const share = stakeShare(chance, odds);
  const save = () => {
    const v = Number(draft.replace(/[^\d.]/g, ""));
    saveBankroll(v > 0 ? v : null);
    setEditing(false);
  };

  if (editing || !bankroll) {
    return (
      <form onSubmit={(e) => { e.preventDefault(); save(); }}
        className="flex flex-wrap items-center gap-2 text-xs text-n-400">
        <Wallet size={13} className="text-accent shrink-0" />
        <label htmlFor="bankroll" className="shrink-0">Your betting bankroll (₦), for a suggested stake:</label>
        <input id="bankroll" inputMode="numeric" value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. 20000" className="w-28 rounded-lg bg-surface-sunken px-2 py-1 text-n-0 tnum [box-shadow:var(--ring-control)]" />
        <button type="submit" className="chip chip-idle">Save</button>
        {bankroll && <button type="button" onClick={() => setEditing(false)} className="text-n-500 underline">Cancel</button>}
      </form>
    );
  }

  return (
    <p className="text-xs text-n-400 flex flex-wrap items-center gap-x-1.5 gap-y-1">
      <Wallet size={13} className="text-accent shrink-0" />
      {share > 0 ? (
        <span>Suggested stake: <span className="font-bold text-n-0 tnum">{naira(stakeAmount(bankroll, chance, odds))}</span>{" "}
          ({(share * 100).toFixed(1)}% of {naira(bankroll)}: a quarter of the Kelly stake, at most {MAX_SHARE * 100}%)</span>
      ) : (
        <span>No stake suggested: at {odds.toFixed(2)}x the odds don&apos;t pay enough for a {(chance * 100).toFixed(1)}% chance.</span>
      )}
      <button type="button" onClick={() => { setDraft(String(bankroll)); setEditing(true); }}
        className="text-n-500 underline">Change bankroll</button>
    </p>
  );
}
