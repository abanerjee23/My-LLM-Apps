export type ChatEvent = {
  type: string;
  data: Record<string, unknown>;
};

/** SSE frames may span chunks, or several frames may share one chunk. */
export function parseFrame(frame: string): ChatEvent | null {
  let type = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  let parsed: unknown;
  try { parsed = JSON.parse(data.join("\n")); }
  catch { throw new Error("Invalid response from the assistant."); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Invalid response from the assistant.");
  return { type, data: parsed as Record<string, unknown> };
}

export async function consumeStream(response: Response, onEvent: (event: ChatEvent) => void) {
  if (!response.body) throw new Error("The assistant did not return a response.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let complete = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const event = parseFrame(frame);
        if (event) {
          onEvent(event);
          if (event.type === "done" || event.type === "error") {
            complete = true;
            // A terminal event completes delivery even if a proxy keeps the connection open.
            await reader.cancel();
            return;
          }
        }
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const event = parseFrame(buffer);
      if (event) { onEvent(event); complete ||= event.type === "done" || event.type === "error"; }
    }
    if (!complete) throw new Error("The connection ended before the answer finished. Ask for the latest status before repeating an action request.");
  } catch (error) {
    // releaseLock alone does not stop an unread fetch body after malformed data.
    await reader.cancel().catch(() => {});
    throw error;
  } finally { reader.releaseLock(); }
}
