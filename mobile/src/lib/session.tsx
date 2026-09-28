import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { Platform } from "react-native";
import { ClerkProvider, useAuth, useUser } from "@clerk/expo";
import { useHostedAuth } from "@clerk/expo/hosted-auth";
import { tokenCache } from "@clerk/expo/token-cache";
import { CLERK_PUBLISHABLE_KEY } from "./config";
import { api, type TokenGetter } from "./api";

// Who's signed in, their plan, and sign in / out: one context for the app,
// backed by Clerk (the website's sign-in) when a publishable key is set.
// Without one the app still works signed out (predictions are public).

export type Tier = "free" | "lite" | "premium";

export interface Session {
  /** Sign-in is set up (a Clerk key is configured). */
  enabled: boolean;
  loaded: boolean;
  signedIn: boolean;
  userId: string | null;
  name: string | null;
  email: string | null;
  tier: Tier;
  /** When a paid plan or trial ends (ISO), or null. */
  expires: string | null;
  trial: boolean;
  getToken: TokenGetter | null;
  signIn: (mode?: "sign-in" | "sign-up") => Promise<void>;
  signOut: () => Promise<void>;
}

const signedOut: Session = {
  enabled: false, loaded: true, signedIn: false, userId: null, name: null, email: null,
  tier: "free", expires: null, trial: false, getToken: null,
  signIn: async () => {}, signOut: async () => {},
};

const SessionContext = createContext<Session>(signedOut);
export const useSession = () => useContext(SessionContext);

/** The plan from Clerk's public metadata, as the website reads it (lib/subscription.ts tierOf). */
export function tierOf(meta: Record<string, unknown> | undefined, now = Date.now()): Tier {
  const tier = meta?.subscription;
  if (tier !== "lite" && tier !== "premium") return "free";
  const expires = Date.parse(typeof meta?.subscription_expires === "string" ? meta.subscription_expires : "");
  return expires > now ? tier : "free";
}

function ClerkSession({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, userId, getToken, signOut } = useAuth();
  const { user } = useUser();
  const { startHostedAuth } = useHostedAuth();
  const meta = useMemo(() => (user?.publicMetadata ?? {}) as Record<string, unknown>, [user?.publicMetadata]);

  const token = useCallback<TokenGetter>(() => getToken(), [getToken]);
  const signIn = useCallback(async (mode: "sign-in" | "sign-up" = "sign-in") => {
    await startHostedAuth({ mode });
  }, [startHostedAuth]);
  const out = useCallback(async () => { await signOut(); }, [signOut]);

  // A new account's free trial, as the website starts it (components/Trial.tsx):
  // the backend decides (one per email address); once per app run
  const asked = useRef<string | null>(null);
  useEffect(() => {
    if (!isSignedIn || !user || meta.trial_used === true || asked.current === user.id) return;
    const young = user.createdAt && Date.now() - new Date(user.createdAt).getTime() < 31 * 86_400_000;
    if (!young) return;
    asked.current = user.id;
    api<{ started: boolean }>("/api/trial/start", token, { method: "POST" })
      .then(d => (d.started ? user.reload() : undefined))
      .catch(() => {});
  }, [isSignedIn, user, meta.trial_used, token]);

  const value = useMemo<Session>(() => ({
    enabled: true, loaded: isLoaded, signedIn: !!isSignedIn, userId: userId ?? null,
    name: user?.fullName || user?.firstName || null,
    email: user?.primaryEmailAddress?.emailAddress ?? null,
    tier: tierOf(meta),
    expires: typeof meta.subscription_expires === "string" && tierOf(meta) !== "free" ? meta.subscription_expires : null,
    trial: meta.trial === true && tierOf(meta) !== "free",
    getToken: isSignedIn ? token : null,
    signIn, signOut: out,
  }), [isLoaded, isSignedIn, userId, user, meta, token, signIn, out]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  // Hosted sign-in is native only; the web preview runs signed out
  if (!CLERK_PUBLISHABLE_KEY || Platform.OS === "web") {
    return <SessionContext.Provider value={signedOut}>{children}</SessionContext.Provider>;
  }
  return (
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} tokenCache={tokenCache}>
      <ClerkSession>{children}</ClerkSession>
    </ClerkProvider>
  );
}
