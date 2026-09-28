import { API_URL } from "./config";

// The BetIQ backend, as the website calls it. Signed-in calls send the Clerk
// session token (Authorization: Bearer); the backend checks it and the plan.

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail || `HTTP ${status}`);
  }
  /** 401 sign in, 402 a higher plan, 404 feature_off: the gates the website shows too. */
  get gate(): "sign_in" | "lite" | "premium" | "off" | null {
    if (this.status === 401) return "sign_in";
    if (this.status === 402) return this.detail === "lite_required" ? "lite" : "premium";
    if (this.status === 404 && this.detail === "feature_off") return "off";
    return null;
  }
}

export type TokenGetter = () => Promise<string | null>;

export async function api<T>(path: string, token?: TokenGetter | null, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(init.headers as Record<string, string>) };
  const t = token ? await token().catch(() => null) : null;
  if (t) headers.Authorization = `Bearer ${t}`;
  const r = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new ApiError(r.status, typeof body?.detail === "string" ? body.detail : "");
  }
  return r.json() as Promise<T>;
}
