"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { dropPast, removeSelection, toggleSelection, type SlipSelection } from "./slip";

// The bet slip, shared by every page and kept in this browser between visits.

const STORAGE_KEY = "betiq:slip:v1";

interface BetSlipState {
  items: SlipSelection[];
  toggle: (s: SlipSelection) => void;
  addMany: (s: SlipSelection[]) => void;
  remove: (matchKey: string) => void;
  clear: () => void;
  open: boolean;
  setOpen: (open: boolean) => void;
}

const BetSlipContext = createContext<BetSlipState | null>(null);

const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export function BetSlipProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<SlipSelection[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);

  // Read after mount so server and first client render agree
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      if (Array.isArray(saved)) setItems(dropPast(saved, today()));
    } catch { /* storage blocked or corrupt — start empty */ }
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!loaded) return;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(items)); } catch { /* ignore */ }
  }, [items, loaded]);

  const toggle = useCallback((s: SlipSelection) => setItems(prev => toggleSelection(prev, s)), []);
  const addMany = useCallback((list: SlipSelection[]) => setItems(prev =>
    list.reduce((acc, s) => (acc.some(x => x.home === s.home && x.away === s.away && x.date === s.date && x.market === s.market && x.code === s.code)
      ? acc : toggleSelection(acc, s)), prev)), []);
  const remove = useCallback((key: string) => setItems(prev => removeSelection(prev, key)), []);
  const clear = useCallback(() => setItems([]), []);

  const value = useMemo(() => ({ items, toggle, addMany, remove, clear, open, setOpen }),
    [items, toggle, addMany, remove, clear, open]);
  return <BetSlipContext.Provider value={value}>{children}</BetSlipContext.Provider>;
}

export function useBetSlip(): BetSlipState {
  const ctx = useContext(BetSlipContext);
  if (!ctx) throw new Error("useBetSlip must be used inside BetSlipProvider");
  return ctx;
}
