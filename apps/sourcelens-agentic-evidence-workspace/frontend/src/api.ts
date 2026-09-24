import type { DataSource, Investigation, NotebookEntry } from "./types";

async function json<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
  return response.json();
}

export const api = {
  create: (brief: string, source_id?: string) => json<Investigation>("/api/investigations", { method: "POST", body: JSON.stringify({ brief, source_id }) }),
  list: () => json<Investigation[]>("/api/investigations"),
  source: (id: string) => json<{source: DataSource; preview: Record<string, unknown>[]}>(`/api/sources/${id}`),
  requirements: () => json<{service_account: string}>("/api/sources/requirements"),
  verifySource: (body: object) => json<{tables: string[]; service_account: string}>("/api/sources/connections/verify", {method: "POST", body: JSON.stringify(body)}),
  previewSource: async (file: File) => {
    const body = new FormData(); body.append("file", file);
    const response = await fetch("/api/sources/files/preview", {method: "POST", body});
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    return response.json() as Promise<{source: DataSource; preview: Record<string, unknown>[]} >;
  },
  get: (id: string) => json<Investigation>(`/api/investigations/${id}`),
  refine: (id: string, direction: string) => json<Investigation>(`/api/investigations/${id}/refine`, { method: "POST", body: JSON.stringify({ direction }) }),
  notebook: () => json<NotebookEntry[]>("/api/notebook"),
  review: (id: string, body: object) => json<NotebookEntry>(`/api/investigations/${id}/review`, { method: "POST", body: JSON.stringify(body) }),
  sources: () => json<DataSource[]>("/api/sources"),
  connectSource: (body: object) => json<DataSource>("/api/sources/connections", { method: "POST", body: JSON.stringify(body) }),
  uploadSource: async (file: File) => {
    const body = new FormData(); body.append("file", file);
    const response = await fetch("/api/sources/files", { method: "POST", body });
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    return response.json() as Promise<{ source: DataSource; preview: Array<Record<string, unknown>> }>;
  },
};
