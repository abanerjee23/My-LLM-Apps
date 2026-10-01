import { test } from "node:test";
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { GET, POST } from "../app/api/[...path]/route";

const publicOrigin = "https://support.example.test";
const gatewayOrigin = "https://gateway.internal.example.test";
type FetchCall = { input: Parameters<typeof fetch>[0]; init: Parameters<typeof fetch>[1] };

async function withGateway(
  run: (calls: FetchCall[]) => Promise<void>,
  upstream: typeof fetch = async () => Response.json({ accepted: true }),
) {
  const previousFetch = globalThis.fetch;
  const previousOrigin = process.env.CHAT_PUBLIC_ORIGIN;
  const previousGateway = process.env.CHAT_GATEWAY_URL;
  const calls: FetchCall[] = [];
  process.env.CHAT_PUBLIC_ORIGIN = publicOrigin;
  process.env.CHAT_GATEWAY_URL = gatewayOrigin;
  globalThis.fetch = async (input, init) => {
    calls.push({ input, init });
    return upstream(input, init);
  };
  try { await run(calls); }
  finally {
    globalThis.fetch = previousFetch;
    if (previousOrigin === undefined) delete process.env.CHAT_PUBLIC_ORIGIN;
    else process.env.CHAT_PUBLIC_ORIGIN = previousOrigin;
    if (previousGateway === undefined) delete process.env.CHAT_GATEWAY_URL;
    else process.env.CHAT_GATEWAY_URL = previousGateway;
  }
}

const context = (...path: string[]) => ({ params: Promise.resolve({ path }) });
const jsonHeaders = { Origin: publicOrigin, "Content-Type": "application/json" };

test("rejects a sibling-origin history GET before forwarding browser cookies", async () => {
  await withGateway(async (calls) => {
    const request = new NextRequest(`${publicOrigin}/api/conversations`, {
      headers: {
        Origin: "https://other.example.test",
        "Sec-Fetch-Site": "same-site",
        Cookie: "__Host-tarnfield_visitor=private-visitor",
      },
    });
    assert.equal((await GET(request, context("conversations"))).status, 403);
    assert.equal(calls.length, 0);
  });
});

test("rejects a state-changing POST with no Origin", async () => {
  await withGateway(async (calls) => {
    const request = new NextRequest(`${publicOrigin}/api/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: '{"message":"hello"}',
    });
    assert.equal((await POST(request, context("chat"))).status, 403);
    assert.equal(calls.length, 0);
  });
});

test("rejects cross-site requests even when their Origin claims to match", async () => {
  await withGateway(async (calls) => {
    const request = new NextRequest(`${publicOrigin}/api/bootstrap`, {
      headers: { Origin: publicOrigin, "Sec-Fetch-Site": "cross-site" },
    });
    assert.equal((await GET(request, context("bootstrap"))).status, 403);
    assert.equal(calls.length, 0);
  });
});

test("forwards an allowed browser cookie and message without browser authorization", async () => {
  await withGateway(async (calls) => {
    const body = JSON.stringify({ message: "Check my support request", conversationId: "session-1" });
    const cookie = "__Host-tarnfield_visitor=owned-visitor";
    const request = new NextRequest(`${publicOrigin}/api/chat`, {
      method: "POST", body,
      headers: { ...jsonHeaders, Cookie: cookie, Authorization: "Bearer browser-secret" },
    });
    assert.equal((await POST(request, context("chat"))).status, 200);
    assert.equal(calls.length, 1);
    assert.equal(String(calls[0].input), `${gatewayOrigin}/api/chat`);
    assert.equal(calls[0].init?.body, body);
    assert.equal(calls[0].init?.method, "POST");
    assert.equal(calls[0].init?.cache, "no-store");
    const forwarded = new Headers(calls[0].init?.headers);
    assert.equal(forwarded.get("Cookie"), cookie);
    assert.equal(forwarded.get("Authorization"), null);
    assert.equal(forwarded.get("Origin"), publicOrigin);
    assert.equal(forwarded.get("Accept"), "text/event-stream");
  });
});

test("supports same-origin GET without an Origin header and preserves safe response headers", async () => {
  const cookie = "__Host-tarnfield_visitor=new-visitor; Path=/; HttpOnly; Secure; SameSite=Strict";
  await withGateway(async (calls) => {
    const response = await GET(new NextRequest(`${publicOrigin}/api/bootstrap`), context("bootstrap"));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ready: true });
    assert.equal(calls.length, 1);
    assert.equal(response.headers.get("Set-Cookie"), cookie);
    assert.equal(response.headers.get("Cache-Control"), "no-store, no-transform");
    assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff");
    assert.equal(response.headers.get("X-Accel-Buffering"), "no");
  }, async () => Response.json({ ready: true }, { headers: { "Set-Cookie": cookie } }));
});

test("unknown and traversal-like API paths cannot reach the configured gateway", async () => {
  await withGateway(async (calls) => {
    for (const path of [["tools", "query"], ["conversations", "../secret"], ["conversations", "session?private=true"]]) {
      const response = await GET(new NextRequest(`${publicOrigin}/api/unknown`), context(...path));
      assert.equal(response.status, 404);
    }
    assert.equal(calls.length, 0);
  });
});

test("enforces actual request bytes, cancels oversized input and never calls the gateway", async () => {
  await withGateway(async (calls) => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array(40_001)); },
      cancel() { cancelled = true; },
    });
    const request = new NextRequest(`${publicOrigin}/api/chat`, {
      method: "POST", headers: { ...jsonHeaders, "Content-Length": "1" }, body,
    });
    assert.equal((await POST(request, context("chat"))).status, 413);
    assert.equal(cancelled, true);
    assert.equal(calls.length, 0);
  });
});

test("invalid UTF-8 returns a controlled error without modifying and forwarding the message", async () => {
  await withGateway(async (calls) => {
    const request = new NextRequest(`${publicOrigin}/api/chat`, {
      method: "POST", headers: jsonHeaders, body: Uint8Array.of(0xc3, 0x28),
    });
    const response = await POST(request, context("chat"));
    assert.equal(response.status, 400);
    assert.match((await response.json()).detail, /UTF-8/);
    assert.equal(calls.length, 0);
  });
});

test("request-body read failures return a sanitized error without contacting the gateway", async () => {
  await withGateway(async (calls) => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.error(new Error("private body read failure")); },
    });
    const request = new NextRequest(`${publicOrigin}/api/chat`, { method: "POST", headers: jsonHeaders, body });
    const response = await POST(request, context("chat"));
    assert.equal(response.status, 400);
    assert.doesNotMatch(await response.text(), /private body read failure/);
    assert.equal(calls.length, 0);
  });
});

test("forwards valid 6,000-character multibyte and JSON-escaped messages intact", async () => {
  await withGateway(async (calls) => {
    for (const message of ["汉".repeat(6_000), '"'.repeat(6_000)]) {
      const body = JSON.stringify({ message, conversationId: "session-1" });
      assert.ok(new TextEncoder().encode(body).length > 12_000);
      assert.ok(new TextEncoder().encode(body).length < 40_000);
      const request = new NextRequest(`${publicOrigin}/api/chat`, { method: "POST", headers: jsonHeaders, body });
      assert.equal((await POST(request, context("chat"))).status, 200);
      assert.equal(calls.at(-1)?.init?.body, body);
    }
    assert.equal(calls.length, 2);
  });
});

test("upstream failures are non-retryable and do not expose credential details", async () => {
  await withGateway(async (calls) => {
    const request = new NextRequest(`${publicOrigin}/api/chat`, {
      method: "POST", headers: jsonHeaders, body: '{"message":"File a request"}',
    });
    const response = await POST(request, context("chat"));
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    const result = await response.json();
    assert.equal(result.error.retryable, false);
    assert.match(result.error.message, /Reload your conversation/);
    assert.doesNotMatch(JSON.stringify(result), /Bearer private-cloud-token/);
    assert.equal(calls.length, 1);
  }, async () => { throw new Error("Bearer private-cloud-token"); });
});

test("a failed session creation offers a safe retry without claiming an action was dispatched", async () => {
  await withGateway(async () => {
    const request = new NextRequest(`${publicOrigin}/api/conversations`, {
      method: "POST", headers: jsonHeaders, body: "{}",
    });
    const response = await POST(request, context("conversations"));
    assert.equal(response.status, 503);
    const result = await response.json();
    assert.equal(result.error.retryable, true);
    assert.doesNotMatch(result.error.message, /action request/);
  }, async () => { throw new Error("private internal connection failure"); });
});
