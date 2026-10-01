import { test } from "node:test";
import assert from "node:assert/strict";
import { consumeStream, parseFrame } from "./stream";

test("parses named SSE events and skips keepalive comments", () => {
  assert.deepEqual(parseFrame('event: text\ndata: {"text":"hello","mode":"append"}'), { type: "text", data: { text: "hello", mode: "append" } });
  assert.equal(parseFrame(": keepalive"), null);
});

test("preserves split unicode, split frames, CRLF, and multiple events per chunk", async () => {
  const bytes = new TextEncoder().encode('event: text\r\ndata: {"text":"café 🏃"}\r\n\r\nevent: done\ndata: {}\n\n');
  const stream = new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3)); controller.close(); } });
  const received: unknown[] = [];
  await consumeStream(new Response(stream), (event) => received.push(event));
  assert.deepEqual(received, [{ type: "text", data: { text: "café 🏃" } }, { type: "done", data: {} }]);
});

test("detects a truncated stream instead of showing an incomplete answer as success", async () => {
  await assert.rejects(consumeStream(new Response('event: text\ndata: {"text":"partial"}\n\n'), () => {}), /before the answer finished/);
});

test("rejects empty and keepalive-only streams without a terminal event", async () => {
  for (const content of ["", ": keepalive\n\n", 'event: session\ndata: {"conversationId":"session-1"}\n\n']) {
    await assert.rejects(consumeStream(new Response(content), () => {}), /before the answer finished/);
  }
});

test("does not expose malformed JSON or accept non-object event data", () => {
  for (const data of ['{"private":"sensitive value",', 'null', '[]', '"private value"', '42']) {
    assert.throws(() => parseFrame(`event: text\ndata: ${data}`), {
      message: "Invalid response from the assistant.",
    });
  }
});

test("cancels malformed streams rather than leaving an unread response open", async () => {
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new TextEncoder().encode('event: text\ndata: {"private":"sensitive value",\n\n')); },
    cancel() { cancelled = true; },
  });
  await assert.rejects(consumeStream(new Response(stream), () => {}), {
    message: "Invalid response from the assistant.",
  });
  assert.equal(cancelled, true);
  assert.equal(stream.locked, false);
});

test("preserves UTF-8 and CRLF when every byte is its own chunk", async () => {
  const input = 'event: text\r\ndata: {"text":"跑步 café 🏃🏽‍♀️","mode":"replace"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n';
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const byte of new TextEncoder().encode(input)) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    },
  });
  const received: unknown[] = [];
  await consumeStream(new Response(stream), (event) => received.push(event));
  assert.deepEqual(received, [
    { type: "text", data: { text: "跑步 café 🏃🏽‍♀️", mode: "replace" } },
    { type: "done", data: {} },
  ]);
});

test("terminal errors preserve retryable metadata and need no done event", async () => {
  for (const retryable of [false, true]) {
    const payload = { message: "Check the existing request before sending again.", retryable };
    const received: unknown[] = [];
    await consumeStream(new Response(`event: error\ndata: ${JSON.stringify(payload)}\n\n`), (event) => received.push(event));
    assert.deepEqual(received, [{ type: "error", data: payload }]);
  }
});

test("terminal events finish an open connection and ignore trailing events", async () => {
  for (const type of ["done", "error"]) {
    let cancelled = false;
    let streamController: ReadableStreamDefaultController<Uint8Array>;
    const payload = type === "error" ? { message: "Request may already exist.", retryable: false } : {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
        controller.enqueue(new TextEncoder().encode(`event: ${type}\ndata: ${JSON.stringify(payload)}\n\nevent: text\ndata: {"text":"late content"}\n\n`));
      },
      cancel() { cancelled = true; },
    });
    const received: unknown[] = [];
    // A terminal frame defines completion even if an intermediary leaves the socket open.
    const timeout = setTimeout(() => streamController.error(new Error("Terminal frame did not finish the response.")), 500);
    try {
      await consumeStream(new Response(stream), (event) => received.push(event));
    } finally { clearTimeout(timeout); }
    assert.deepEqual(received, [{ type, data: payload }]);
    assert.equal(cancelled, true);
    assert.equal(stream.locked, false);
  }
});

test("accepts a final terminal frame without its trailing blank separator", async () => {
  const received: unknown[] = [];
  await consumeStream(new Response('event: text\ndata: {"text":"Complete answer"}\n\nevent: done\ndata: {}'), (event) => received.push(event));
  assert.deepEqual(received, [
    { type: "text", data: { text: "Complete answer" } },
    { type: "done", data: {} },
  ]);
});
