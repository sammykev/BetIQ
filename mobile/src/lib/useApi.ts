import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useSession } from "./session";

type Refresh<T> = number | null | ((data: T) => number | null);

/** Load `path` (null: don't) with the session token; reload on demand, and
 *  every `refresh` ms (or as often as `refresh(data)` says, e.g. only while live). */
export function useApi<T>(path: string | null, refresh?: Refresh<T>) {
  const { getToken, signedIn } = useSession();
  // Signing in or out changes what the backend answers
  const key = path ? `${signedIn ? "in" : "out"} ${path}` : null;
  const [got, setGot] = useState<{ key: string; data: T | null; error: Error | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (!path || !key) return;
    if (!quiet) setBusy(true);
    try {
      const data = await api<T>(path, getToken);
      if (alive.current) setGot({ key, data, error: null });
    } catch (e) {
      if (alive.current) setGot(g => ({ key, data: g?.key === key ? g.data : null, error: e as Error }));
    } finally {
      if (alive.current && !quiet) setBusy(false);
    }
  }, [path, key, getToken]);

  useEffect(() => { load(); }, [load]);

  const current = got && got.key === key ? got : null;
  const data = current?.data ?? null;
  const every = typeof refresh === "function" ? (data ? refresh(data) : null) : refresh ?? null;
  useEffect(() => {
    if (!every) return;
    const t = setInterval(() => load(true), every);
    return () => clearInterval(t);
  }, [every, load]);

  return { data, error: current?.error ?? null, loading: busy || (!!key && !current), reload: load };
}
