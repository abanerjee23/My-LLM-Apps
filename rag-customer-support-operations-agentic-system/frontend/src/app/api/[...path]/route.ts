import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  const valid = route === "chat" || route === "health" || route === "bootstrap" || route === "conversations" || /^conversations\/[A-Za-z0-9_-]+$/.test(route);
  if (!valid) return Response.json({ detail: "Not found." }, { status: 404 });
  const publicOrigin = process.env.CHAT_PUBLIC_ORIGIN || "http://127.0.0.1:3010";
  const origin = request.headers.get("origin");
  if (request.headers.get("sec-fetch-site") === "cross-site") return Response.json({ detail: "Open this chat in its original tab." }, { status: 403 });
  if ((origin && origin !== publicOrigin) || (request.method !== "GET" && !origin)) {
    return Response.json({ detail: "Open this chat in its original tab and try again." }, { status: 403 });
  }
  const headers = new Headers({ "Accept": route === "chat" ? "text/event-stream" : "application/json", "Origin": publicOrigin });
  if (request.headers.get("cookie")) headers.set("Cookie", request.headers.get("cookie")!);
  let body: string | undefined;
  if (request.method === "POST") {
    if (!request.headers.get("content-type")?.startsWith("application/json")) return Response.json({ detail: "Use a JSON request." }, { status: 415 });
    const chunks: Uint8Array[] = [];
    let bytes = 0;
    const reader = request.body?.getReader();
    if (reader) {
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (bytes > 40000) {
            await reader.cancel();
            return Response.json({ detail: "Your message is too long. Please keep it under 6,000 characters." }, { status: 413 });
          }
          chunks.push(value);
        }
      } catch {
        await reader.cancel().catch(() => undefined);
        return Response.json({ detail: "Your message couldn’t be read. Please try again." }, { status: 400 });
      } finally { reader.releaseLock(); }
    }
    const joined = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.length; }
    try { body = new TextDecoder("utf-8", { fatal: true }).decode(joined); }
    catch { return Response.json({ detail: "Your message must use valid UTF-8 text." }, { status: 400 }); }
    headers.set("Content-Type", "application/json");
  }
  try {
    const url = new URL(`/api/${route}`, process.env.CHAT_GATEWAY_URL || "http://127.0.0.1:8081");
    const upstream = await fetch(url, { method: request.method, headers, body, cache: "no-store", signal: AbortSignal.any([request.signal, AbortSignal.timeout(150000)]) });
    const outgoing = new Headers({ "Content-Type": upstream.headers.get("content-type") || "application/json", "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff" });
    const cookie = upstream.headers.get("set-cookie");
    if (cookie) outgoing.set("Set-Cookie", cookie);
    return new Response(upstream.body, { status: upstream.status, headers: outgoing });
  } catch {
    if (route !== "chat") return Response.json({ error: { message: "The chat service is temporarily unavailable. Please try again shortly.", retryable: true } }, { status: 503, headers: { "Cache-Control": "no-store" } });
    return Response.json({ error: { message: "The connection to the assistant was interrupted. Reload your conversation before repeating an action request.", retryable: false } }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}

export const GET = proxy;
export const POST = proxy;
