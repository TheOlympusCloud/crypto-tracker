/** The wallet tracker's API, served next to the app by its own server (same origin, same password). */
export const API = "/api/tracker/";

/** GET without a body, POST with one; DELETE when asked. An error carries the server's message. */
export async function api<T>(path: string, body?: unknown, method?: "DELETE"): Promise<T> {
  const r = await fetch(API + path, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!r.ok) {
    const detail = ((await r.json().catch(() => ({}))) as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `${r.status} ${r.statusText}`);
  }
  return (await r.json()) as T;
}
