import type { DataSource, Investigation, NotebookEntry } from "./types";

function csrfToken(): string {
  return document.cookie.split("; ").find((item) => item.startsWith("sourcelens_csrf="))?.split("=").slice(1).join("=") || "";
}

async function request<T>(url: string, options: RequestInit = {}, json = true): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const mutating = !["GET", "HEAD", "OPTIONS"].includes(method);
  const headers = {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(mutating ? { "X-CSRF-Token": csrfToken() } : {}),
    ...options.headers,
  };
  const response = await fetch(url, { ...options, credentials: "same-origin", headers });
  if (response.status === 401) {
    window.dispatchEvent(new Event("sourcelens:unauthorized"));
    throw new Error("Your session has expired. Sign in again.");
  }
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText);
  return response.json();
}

const json = <T,>(url: string, options?: RequestInit) => request<T>(url, options);
const upload = <T,>(url: string, file: File) => { const body = new FormData(); body.append("file", file); return request<T>(url, { method: "POST", body }, false); };

export const api = {
  create: (brief: string, source_id?: string) => json<Investigation>("/api/investigations", { method: "POST", body: JSON.stringify({ brief, source_id }) }),
  list: () => json<Investigation[]>("/api/investigations"),
  source: (id: string) => json<{source: DataSource; preview: Record<string, unknown>[]}>(`/api/sources/${id}`),
  requirements: () => json<{service_account: string}>("/api/sources/requirements"),
  verifySource: (body: object) => json<{tables: string[]; service_account: string}>("/api/sources/connections/verify", {method: "POST", body: JSON.stringify(body)}),
  previewSource: (file: File) => upload<{source: DataSource; preview: Record<string, unknown>[]}>("/api/sources/files/preview", file),
  get: (id: string) => json<Investigation>(`/api/investigations/${id}`),
  refine: (id: string, direction: string) => json<Investigation>(`/api/investigations/${id}/refine`, { method: "POST", body: JSON.stringify({ direction }) }),
  notebook: () => json<NotebookEntry[]>("/api/notebook"),
  review: (id: string, body: object) => json<NotebookEntry>(`/api/investigations/${id}/review`, { method: "POST", body: JSON.stringify(body) }),
  sources: () => json<DataSource[]>("/api/sources"),
  connectSource: (body: object) => json<DataSource>("/api/sources/connections", { method: "POST", body: JSON.stringify(body) }),
  uploadSource: (file: File) => upload<{ source: DataSource; preview: Array<Record<string, unknown>> }>("/api/sources/files", file),
};
