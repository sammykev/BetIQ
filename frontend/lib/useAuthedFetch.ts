"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useRef } from "react";

/**
 * fetch() that sends the signed-in user's Clerk session token as
 * `Authorization: Bearer …`. The backend's user endpoints derive the user
 * from this token (see backend/auth.py) instead of trusting a uid in the
 * request. Signed out, it behaves like plain fetch.
 *
 * The returned function is stable across renders (safe in effect deps):
 * `getToken` isn't guaranteed to be, so it's read through a ref.
 */
const TOKEN_WAIT_MS = 3000;

export function useAuthedFetch() {
  const { getToken } = useAuth();
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  return useCallback(async (url: string, init: RequestInit = {}) => {
    let token: string | null = null;
    try {
      // If Clerk can't load (blocked script, bad network) getToken never
      // settles: don't hold every request hostage to it
      token = await Promise.race([
        getTokenRef.current(),
        new Promise<null>(resolve => setTimeout(() => resolve(null), TOKEN_WAIT_MS)),
      ]);
    } catch {
      // No session / Clerk not loaded — send without a token
    }
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(url, { ...init, headers });
  }, []);
}
