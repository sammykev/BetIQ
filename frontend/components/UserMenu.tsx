"use client";

import { useUser, useClerk, SignInButton } from "@clerk/nextjs";
import { Crown, LogOut, User, LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { useState, useRef, useEffect } from "react";

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

  if (!isLoaded)
    return <div className="w-8 h-8 rounded-full bg-zinc-200 dark:bg-zinc-800 animate-pulse" />;

  if (!user) {
    return (
      <SignInButton mode="modal">
        <button className="btn-primary !px-4 !py-2 !text-xs">
          <User size={13} /> Sign in
        </button>
      </SignInButton>
    );
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 p-1 rounded-full hover:ring-2 hover:ring-zinc-200 dark:hover:ring-zinc-700 transition-all"
      >
        {user.imageUrl ? (
          <img src={user.imageUrl} alt="" className="w-8 h-8 rounded-full object-cover" />
        ) : (
          <div className="w-8 h-8 rounded-full bg-brand-600 flex items-center justify-center text-white text-xs font-bold">
            {user.firstName?.[0] ?? "U"}
          </div>
        )}
        {active && <Crown size={12} className="text-amber-500" />}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-60 card !rounded-xl shadow-pop z-50 overflow-hidden animate-scale-in origin-top-right">
          <div className="px-4 py-3 border-b border-zinc-100 dark:border-zinc-800">
            <p className="text-sm font-semibold text-zinc-900 dark:text-white truncate">
              {user.fullName || user.emailAddresses[0]?.emailAddress}
            </p>
            <p className="text-xs text-zinc-500 truncate">
              {user.emailAddresses[0]?.emailAddress}
            </p>
            <div className="mt-2">
              {active ? (
                <span className="inline-flex items-center gap-1 text-[10px] bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-500/30 px-2 py-0.5 rounded-full font-bold">
                  <Crown size={9} /> Premium
                  {expires && (
                    <span className="text-amber-500/70 font-normal ml-1">
                      · expires {new Date(expires).toLocaleDateString()}
                    </span>
                  )}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[10px] bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 px-2 py-0.5 rounded-full font-medium">
                  Free plan
                </span>
              )}
            </div>
          </div>

          <Link href="/dashboard" onClick={() => setOpen(false)}
            className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 transition-colors border-b border-zinc-100 dark:border-zinc-800">
            <LayoutDashboard size={15} /> My Dashboard
          </Link>

          {!active && (
            <button
              onClick={() => { setOpen(false); onUpgrade(); }}
              className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-500/10 transition-colors border-b border-zinc-100 dark:border-zinc-800 font-semibold"
            >
              <Crown size={15} /> Upgrade to Premium
            </button>
          )}

          <button
            onClick={() => signOut()}
            className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-zinc-500 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 transition-colors"
          >
            <LogOut size={15} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
