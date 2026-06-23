"use client";

import { useUser, useClerk, SignInButton } from "@clerk/nextjs";
import { Crown, LogOut, User, LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { useState, useRef, useEffect } from "react";
import clsx from "clsx";

interface Props {
  onUpgrade: () => void;
}

export function UserMenu({ onUpgrade }: Props) {
  const { user, isLoaded } = useUser();
  const { signOut } = useClerk();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const isPremium =
    (user?.publicMetadata as { subscription?: string })?.subscription === "premium";

  // Check expiry
  const expires = (user?.publicMetadata as { subscription_expires?: string })
    ?.subscription_expires;
  const isExpired = expires ? new Date(expires) < new Date() : false;
  const active = isPremium && !isExpired;

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  if (!isLoaded) return <div className="w-8 h-8 rounded-full bg-slate-700 animate-pulse" />;

  if (!user) {
    return (
      <SignInButton mode="modal">
        <button className="flex items-center gap-1.5 px-3 py-1.5 bg-green-500 hover:bg-green-400 text-black text-xs font-bold rounded-lg transition-all">
          <User size={12} /> Sign in
        </button>
      </SignInButton>
    );
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-800 transition-all"
      >
        {user.imageUrl ? (
          <img src={user.imageUrl} alt="" className="w-7 h-7 rounded-full object-cover" />
        ) : (
          <div className="w-7 h-7 rounded-full bg-green-500 flex items-center justify-center text-black text-xs font-bold">
            {user.firstName?.[0] ?? "U"}
          </div>
        )}
        {active && <Crown size={12} className="text-yellow-400" />}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-56 bg-slate-900 border border-slate-700 rounded-xl shadow-2xl z-50 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-700">
            <p className="text-sm font-semibold text-white truncate">
              {user.fullName || user.emailAddresses[0]?.emailAddress}
            </p>
            <p className="text-xs text-slate-400 truncate">
              {user.emailAddresses[0]?.emailAddress}
            </p>
            <div className="mt-2">
              {active ? (
                <span className="inline-flex items-center gap-1 text-[10px] bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 px-2 py-0.5 rounded-full font-bold">
                  <Crown size={9} /> Premium
                  {expires && (
                    <span className="text-yellow-600 font-normal ml-1">
                      · expires {new Date(expires).toLocaleDateString()}
                    </span>
                  )}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[10px] bg-slate-700 text-slate-400 px-2 py-0.5 rounded-full">
                  Free plan
                </span>
              )}
            </div>
          </div>

          <Link href="/dashboard" onClick={() => setOpen(false)}
            className="w-full flex items-center gap-2 px-4 py-3 text-sm text-slate-300 hover:bg-slate-800 transition-colors border-b border-slate-700">
            <LayoutDashboard size={14} /> My Dashboard
          </Link>

          {!active && (
            <button
              onClick={() => { setOpen(false); onUpgrade(); }}
              className="w-full flex items-center gap-2 px-4 py-3 text-sm text-yellow-400 hover:bg-yellow-500/10 transition-colors border-b border-slate-700 font-semibold"
            >
              <Crown size={14} /> Upgrade to Premium
            </button>
          )}

          <button
            onClick={() => signOut()}
            className="w-full flex items-center gap-2 px-4 py-3 text-sm text-slate-400 hover:bg-slate-800 transition-colors"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
