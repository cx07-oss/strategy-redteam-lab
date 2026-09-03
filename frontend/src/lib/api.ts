export const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? null;
export const publicDemoMode = process.env.NEXT_PUBLIC_PRODUCT_MODE !== "connected";

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  if (!apiBase) throw new Error("API is not configured for this deployment.");
  const response = await fetch(`${apiBase}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { message?: string } | null;
    throw new Error(body?.message ?? `API request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
