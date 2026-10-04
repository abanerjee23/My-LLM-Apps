"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowRight, ArrowUp, Check, ChevronRight, Copy, ExternalLink, FileText, Info, LoaderCircle, Menu, MessageSquare, Plus, Search, ShieldCheck, Square, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { consumeStream } from "@/lib/stream";
import { answerSources, policyDocuments, type Source } from "@/lib/sources";

type Message = { id: string; role: "user" | "assistant"; content: string; sources?: Source[]; error?: string; pending?: boolean; stopped?: boolean; recover?: boolean };
type Conversation = { id: string; title: string; updatedAt: string };

const starters = [
  { title: "Check a return", description: "Solstice Edition trainers", prompt: "Can I return my Solstice Edition shoes from order TF-88213?", icon: "return" },
  { title: "Find an exchange", description: "A fault with my running shoes", prompt: "The sole is separating on my Scree Trail shoes, order TF-88455. Can I exchange them?", icon: "exchange" },
  { title: "Understand the window", description: "How long do I have?", prompt: "How long do I have to return unworn trainers?", icon: "return" },
];
function BrandMark({ small = false }: { small?: boolean }) {
  return <span className={small ? "brand-mark small" : "brand-mark"} aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="m9 8-4 16h5l4-16zm8 0-4 16h5l4-16zm8 0-4 16h5l4-16z" fill="currentColor" /></svg></span>;
}

function RouteArtwork() {
  return <svg className="route-art" viewBox="0 0 150 110" fill="none" aria-hidden="true">
    <path d="M24 94V50C24 28 41 12 63 12h26c23 0 39 16 39 38v44" stroke="#D9E3E9" strokeWidth="1.5" />
    <path d="M34 94V50c0-16 12-28 29-28h26c16 0 29 12 29 28v44" stroke="#CAD7E0" strokeWidth="1.5" />
    <path d="M44 94V51c0-11 8-19 19-19h26c11 0 19 8 19 19v43" stroke="#BBCBD7" strokeWidth="1.5" />
    <path d="M54 94V51c0-5 4-9 9-9h26c5 0 9 4 9 9v43" stroke="#173253" strokeWidth="2" />
    <circle cx="98" cy="75" r="5" fill="#D94B3D" stroke="white" strokeWidth="3" />
    <path d="M15 94h124" stroke="#E0E7EC" />
  </svg>;
}

class RequestFailure extends Error {
  constructor(message: string, public retryable = false, public activeConversationId?: string, public active = false) { super(message); }
}

async function responseError(response: Response): Promise<RequestFailure> {
  try {
    const body = await response.json();
    const retryable = body.error?.retryable === true || [400, 403, 404, 413, 415, 422, 429].includes(response.status);
    if (typeof body.error?.message === "string") return new RequestFailure(body.error.message, retryable, typeof body.error.conversationId === "string" ? body.error.conversationId : undefined, body.error.active === true);
    if (typeof body.detail === "string") return new RequestFailure(body.detail, retryable);
  } catch { /* Fall back to a safe, actionable message. */ }
  return new RequestFailure("The assistant is temporarily unavailable. Reload the conversation before trying again.");
}

function Markdown({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => {
      const safe = href && (/^https?:\/\//i.test(href) || /^\/policies\//.test(href));
      return safe ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
    },
    img: ({ alt }) => <span>{alt}</span>,
  }}>{children}</ReactMarkdown>;
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [ready, setReady] = useState(false);
  const [bootstrapError, setBootstrapError] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [loadingList, setLoadingList] = useState(true);
  const [navigationError, setNavigationError] = useState<string | null>(null);
  const [inFlight, setInFlight] = useState(false);
  const [flightConversationId, setFlightConversationId] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [status, setStatus] = useState("Connecting to the assistant");
  const [elapsed, setElapsed] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [nearBottom, setNearBottom] = useState(true);
  const [announcement, setAnnouncement] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const sending = useRef(false);
  const loadingRequest = useRef(0);
  const navigationAbort = useRef<AbortController | null>(null);
  const creating = useRef(false);
  const drafts = useRef(new Map<string, string>());
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const listRequest = useRef(0);
  const initialConversationLoaded = useRef(false);
  const stopRecoveryPending = useRef(false);

  const refreshConversations = useCallback(async () => {
    const requestId = ++listRequest.current;
    setLoadingList(true);
    try {
      const response = await fetch("/api/conversations", { cache: "no-store", signal: AbortSignal.timeout(35000) });
      if (!response.ok) throw await responseError(response);
      const body = await response.json();
      if (listRequest.current !== requestId) return;
      setConversations(Array.isArray(body.conversations) ? body.conversations : []);
      const active = Array.isArray(body.conversations) ? body.conversations.find((item: { id: string; inFlight?: boolean }) => item.inFlight === true) : null;
      if (body.active === true || active) { setInFlight(true); setFlightConversationId(body.activeConversationId || active?.id || null); }
      if (body.active === false && !sending.current && !stopRecoveryPending.current) { setInFlight(false); setFlightConversationId(null); }
      setHistoryError(null);
    } catch {
      if (listRequest.current === requestId) setHistoryError("Conversation history couldn’t be loaded.");
    } finally { if (listRequest.current === requestId) setLoadingList(false); }
  }, []);

  const initialize = useCallback(async () => {
    try {
      const response = await fetch("/api/bootstrap", { cache: "no-store", signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error("Couldn’t connect.");
      setReady(true); setBootstrapError(false);
      void refreshConversations();
    } catch { setBootstrapError(true); }
  }, [refreshConversations]);
  useEffect(() => { void initialize(); return () => { abort.current?.abort(); navigationAbort.current?.abort(); if (copyTimer.current) clearTimeout(copyTimer.current); }; }, [initialize]);
  useEffect(() => {
    if (!ready || initialConversationLoaded.current) return;
    initialConversationLoaded.current = true;
    const id = new URLSearchParams(window.location.search).get("conversation");
    if (id && /^[A-Za-z0-9_-]+$/.test(id)) void openConversation(id);
  }, [ready]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const update = () => setMobile(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!mobile || !sidebarOpen) return;
    const panel = sidebar.current;
    const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled)') || []);
    focusable()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
      if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener("keydown", trap);
    return () => { document.removeEventListener("keydown", trap); menuButton.current?.focus(); };
  }, [mobile, sidebarOpen]);
  useEffect(() => {
    if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 156)}px`; }
  }, [draft]);
  useEffect(() => {
    if (nearBottom && scroll.current) scroll.current.scrollTop = messages.length ? scroll.current.scrollHeight : 0;
  }, [messages, status, nearBottom]);
  useEffect(() => {
    const area = scroll.current;
    if (!area) return;
    const observer = new ResizeObserver(() => { if (nearBottom && messages.length) area.scrollTop = area.scrollHeight; });
    observer.observe(area);
    return () => observer.disconnect();
  }, [nearBottom, messages.length]);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  useEffect(() => {
    const recoveryId = flightConversationId || conversationId;
    if (!inFlight || busy) return;
    const controller = new AbortController();
    let checking = false;
    const timer = window.setInterval(async () => {
      if (checking) return;
      checking = true;
      try {
        const route = recoveryId ? `/api/conversations/${encodeURIComponent(recoveryId)}` : "/api/conversations";
        const response = await fetch(route, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(35000)]) });
        if (!response.ok) return;
        const body = await response.json();
        if (controller.signal.aborted) return;
        if (!recoveryId && body.active === true && typeof body.activeConversationId === "string") { setFlightConversationId(body.activeConversationId); return; }
        if (controller.signal.aborted || body.active !== false) return;
        if (recoveryId && (recoveryId === conversationId || !conversationId)) {
          if (!conversationId) selectConversation(recoveryId);
          setMessages(Array.isArray(body.messages) ? body.messages : []);

        }
        stopRecoveryPending.current = false; setInFlight(false); setFlightConversationId(null); setAnnouncement(recoveryId ? "The previous request has finished. Conversation updated." : "The previous request has finished. Check conversation history for its answer.");
        void refreshConversations();
      } catch { /* The visible reload control remains available when recovery is offline. */ }
      finally { checking = false; }
    }, 5000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [inFlight, flightConversationId, conversationId, busy, refreshConversations]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        if (!helpOpen && !source && !sidebarOpen) { event.preventDefault(); input.current?.focus(); }
      }
      if (event.key === "Escape" && !helpOpen && !source) setSidebarOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [helpOpen, source, sidebarOpen]);

  function selectConversation(id: string) {
    setConversationId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("conversation", id);
    window.history.replaceState(null, "", url);
  }

  async function newConversation() {
    if (sending.current || creating.current || !ready || inFlight) return;
    creating.current = true;
    navigationAbort.current?.abort();
    const requestId = ++loadingRequest.current;
    const controller = new AbortController();
    navigationAbort.current = controller;
    setLoadingHistory(false); setCreatingConversation(true); setNavigationError(null);
    setAnnouncement("Creating a new conversation.");
    try {
      const response = await fetch("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(35000)]) });
      if (!response.ok) throw await responseError(response);
      const body = await response.json();
      if (typeof body.conversationId !== "string") throw new Error("The new conversation could not be opened. Please try again.");
      if (loadingRequest.current !== requestId) return;
      drafts.current.set(conversationId || "new", draft);
      selectConversation(body.conversationId);
      // Invalidate an older list fetch before adding the just-created session.
      listRequest.current += 1; setLoadingList(false); setHistoryError(null);
      setConversations((previous) => [{ id: body.conversationId, title: body.title || "New conversation", updatedAt: new Date().toISOString() }, ...previous.filter((item) => item.id !== body.conversationId)]);
      setMessages([]);  setDraft(""); setSearch(""); setSearchOpen(false); setSidebarOpen(false); setNearBottom(true); setInFlight(false); setFlightConversationId(null);
      setAnnouncement("New conversation created. Write a message to get started.");
      window.requestAnimationFrame(() => { sidebar.current?.querySelector(".history-list")?.scrollTo({ top: 0 }); input.current?.focus(); });
    } catch (error) {
      if (!controller.signal.aborted) {
        const text = error instanceof Error ? error.message : "The conversation couldn’t be created. Try again."; setNavigationError(text); setAnnouncement(text);
        if (error instanceof RequestFailure && error.active) { setInFlight(true); setFlightConversationId(error.activeConversationId || conversationId); }
      }
    } finally { creating.current = false; setCreatingConversation(false); }
  }

  async function openConversation(id: string) {
    if (sending.current || creating.current) return;
    drafts.current.set(conversationId || "new", draft);
    navigationAbort.current?.abort();
    const controller = new AbortController();
    navigationAbort.current = controller;
    const requestId = ++loadingRequest.current;
    setLoadingHistory(true); setSidebarOpen(false); setNavigationError(null);
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(35000)]) });
      if (!response.ok) throw await responseError(response);
      const body = await response.json();
      if (loadingRequest.current !== requestId) return;
      selectConversation(id); setMessages(Array.isArray(body.messages) ? body.messages : []); setDraft(drafts.current.get(id) || ""); setNearBottom(true); setInFlight(body.active === true);
      if (body.inFlight === true) setFlightConversationId(id);
      if (body.active === false) setFlightConversationId(null);

      if (typeof body.title === "string") setConversations((items) => items.map((item) => item.id === id ? { ...item, title: body.title } : item));
      setAnnouncement(body.inFlight ? "Your previous response is still finishing. Reload again shortly." : "Conversation loaded.");
      window.requestAnimationFrame(() => input.current?.focus());
    } catch (error) {
      if (!controller.signal.aborted && loadingRequest.current === requestId) setNavigationError(error instanceof Error ? error.message : "This conversation couldn’t be loaded.");
    } finally { if (loadingRequest.current === requestId) setLoadingHistory(false); }
  }

  async function send(value = draft) {
    const text = value.trim();
    if (!text || text.length > 6000 || sending.current || loadingHistory || creating.current || inFlight || !ready) return;
    sending.current = true;
    stopRecoveryPending.current = false;
    const userId = crypto.randomUUID();
    const replyId = crypto.randomUUID();
    let failed = false;
    let answered = false;
    let streamConversationId = conversationId;
    const controller = new AbortController();
    abort.current = controller;
    setMessages((previous) => [...previous, { id: userId, role: "user", content: text }, { id: replyId, role: "assistant", content: "", pending: true }]);
    setDraft(""); drafts.current.delete(conversationId || "new"); setNavigationError(null); setBusy(true); setElapsed(0); setStatus("Connecting to the assistant"); setNearBottom(true);
    setAnnouncement("Message sent. Waiting for the assistant.");
    const update = (change: (message: Message) => Message) => setMessages((previous) => previous.map((message) => message.id === replyId ? change(message) : message));
    try {
      const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, ...(conversationId ? { conversationId } : {}) }), signal: controller.signal });
      if (!response.ok) throw await responseError(response);
      await consumeStream(response, ({ type, data }) => {
        if (type === "session" && typeof data.conversationId === "string") {
          streamConversationId = data.conversationId; selectConversation(data.conversationId);
          const title = typeof data.title === "string" ? data.title : text.slice(0, 60);
          setConversations((items) => [{ id: data.conversationId as string, title, updatedAt: new Date().toISOString() }, ...items.filter((item) => item.id !== data.conversationId)]);
        }
        if (type === "status" && typeof data.label === "string") { setStatus(data.label); setAnnouncement(data.label); }
        if (type === "text" && typeof data.text === "string") {
          answered = true;
          const content = data.text as string;
          update((message) => ({ ...message, content: data.mode === "replace" ? content : message.content + content }));
        }
        if (type === "sources" && Array.isArray(data.sources)) {
          const checked = (data.sources as Source[]).filter((item) => item && typeof item.document === "string" && typeof item.title === "string" && policyDocuments.some((policy) => policy.document === item.document));
          update((message) => ({ ...message, sources: checked }));
        }
        if (type === "error") {
          failed = true;
          const error = typeof data.message === "string" ? data.message : "The response couldn’t be completed.";
          const retryable = data.retryable === true;
          update((message) => ({ ...message, error, pending: false, recover: !retryable }));
          if (retryable) setDraft((current) => current || text);
          setAnnouncement(error);
        }
        if (type === "done") update((message) => ({ ...message, pending: false }));
      });
      if (!failed && !answered) throw new Error("No answer came back. Reload this conversation to check its status before trying again.");
      if (!failed) setAnnouncement("The assistant’s answer is ready.");
    } catch (error) {
      failed = true;
      const stopped = controller.signal.aborted;
      const retryable = !stopped && error instanceof RequestFailure && error.retryable;
      const message = stopped ? "Display stopped. The assistant may still finish your request. Reload this conversation to check the answer before trying again." : error instanceof Error ? error.message : "The assistant couldn’t finish this response. Reload the conversation before trying again.";
      update((reply) => ({ ...reply, pending: false, error: message, stopped, recover: !retryable }));
      if (retryable) setDraft((current) => current || text);
      if (stopped) { stopRecoveryPending.current = true; setInFlight(true); setFlightConversationId(streamConversationId); }
      if (error instanceof RequestFailure && error.active) { setInFlight(true); setFlightConversationId(error.activeConversationId || streamConversationId); }
      setAnnouncement(message);
    } finally {
      update((message) => ({ ...message, pending: false }));
      setBusy(false); sending.current = false; abort.current = null;
      void refreshConversations();
      if (!failed) input.current?.focus();
    }
  }

  async function copyMessage(message: Message) {
    try { await navigator.clipboard.writeText(message.content); if (copyTimer.current) clearTimeout(copyTimer.current); setCopied(message.id); setAnnouncement("Answer copied to clipboard."); copyTimer.current = setTimeout(() => setCopied(null), 1800); }
    catch { setAnnouncement("Copy isn’t available in this browser. You can select the answer text instead."); }
  }

  const visibleConversations = conversations.filter((item) => (item.title || "Conversation").toLowerCase().includes(search.toLowerCase()));
  const currentTitle = conversations.find((item) => item.id === conversationId)?.title;
  const empty = messages.length === 0;
  const composer = <div className={`composer-area ${empty ? "welcome-composer" : ""}`}>
    {bootstrapError && <div className="connection-error" role="alert">The chat couldn’t connect. <button onClick={() => void initialize()}>Try again</button></div>}
    {navigationError && <div className="connection-error" role="alert">{navigationError}<button aria-label="Dismiss conversation error" onClick={() => setNavigationError(null)}><X size={16} /></button></div>}
    {inFlight && <div className="recovery-notice" role="status"><LoaderCircle className="spinner" size={17} /><span>Your previous request is still finishing. We’ll check for its answer.</span><button disabled={loadingHistory} onClick={() => { const id = flightConversationId || conversationId; if (id) void openConversation(id); }}>Reload</button></div>}
    <form className="composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <label className="sr-only" htmlFor="message-input">Message Tarnfield care</label>
      <textarea id="message-input" ref={input} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Ask about a return, exchange or charge…" rows={2} maxLength={6000} disabled={loadingHistory || creatingConversation} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
      <div className="composer-bottom"><span className="composer-hint">{draft.length > 5500 ? `${6000 - draft.length} characters left` : <><MessageSquare size={15} /><span>{busy ? "Checking your request" : !ready ? "Connecting to Tarnfield care…" : "Returns and exchanges, explained with policy sources."}</span></>}</span>{busy ? <Button type="button" className="stop-button" size="icon" variant="outline" onClick={() => abort.current?.abort()} aria-label="Stop displaying response"><Square size={15} fill="currentColor" /></Button> : <Button className="send-button" type="submit" size="icon" aria-label="Send message" disabled={!draft.trim() || loadingHistory || creatingConversation || inFlight || !ready}><ArrowUp size={22} /></Button>}</div>
    </form>
    <div className="composer-footer"><p>AI can make mistakes. Check the sources. Policy guidance only. No actions are taken.</p><span className="keyboard-hint"><kbd>↵</kbd> send · <kbd>Shift ↵</kbd> new line</span></div>
  </div>;

  return <div className="chat-shell">
    <a href="#message-input" className="skip-link">Skip to message</a>
    {sidebarOpen && <button className="sidebar-scrim" aria-label="Close conversation menu" onClick={() => setSidebarOpen(false)} />}
    <aside ref={sidebar} className={`sidebar ${sidebarOpen ? "is-open" : ""}`} aria-label="Conversation navigation" role={mobile && sidebarOpen ? "dialog" : undefined} aria-modal={mobile && sidebarOpen ? true : undefined} aria-hidden={mobile && !sidebarOpen ? true : undefined} inert={mobile && !sidebarOpen}>
      <div className="sidebar-brand"><a className="brand" href="/" onClick={(event) => { event.preventDefault(); void newConversation(); }} aria-label="Tarnfield Care home"><BrandMark /><span><strong>Tarnfield</strong><span>Running Co.</span></span></a><button className="plain-icon drawer-close" aria-label="Close conversation menu" onClick={() => setSidebarOpen(false)}><X size={22} /></button></div>
      <Button className="new-chat" onClick={() => void newConversation()} disabled={busy || creatingConversation || inFlight || !ready} title={busy || inFlight ? "Wait for the current request to finish, or stop and reload it." : undefined}>{creatingConversation ? <LoaderCircle className="spinner" size={19} /> : <Plus size={19} />}{creatingConversation ? "Creating conversation…" : "New conversation"}</Button>
      <div className="history-heading"><span>Your conversations</span><button className="plain-icon" aria-label={searchOpen ? "Close conversation search" : "Search conversations"} aria-expanded={searchOpen} onClick={() => { setSearchOpen(!searchOpen); setSearch(""); }}><Search size={18} /></button></div>
      {searchOpen && <div className="history-search-wrap"><input className="history-search" placeholder="Find a conversation" aria-label="Search conversations" value={search} onChange={(event) => setSearch(event.target.value)} autoFocus />{search && <button className="plain-icon" aria-label="Clear conversation search" onClick={() => setSearch("")}><X size={17} /></button>}</div>}
      <nav className="history-list" aria-label="Previous conversations">
        {loadingList && !conversations.length ? <div className="history-empty" role="status"><LoaderCircle className="spinner" size={19} /><p>Loading conversations…</p></div> : historyError ? <div className="history-empty"><p>{historyError}</p><button className="text-button" onClick={() => void refreshConversations()}>Try again</button></div> : visibleConversations.length ? visibleConversations.map((item) => <button key={item.id} title={item.title} aria-current={item.id === conversationId ? "page" : undefined} disabled={busy || creatingConversation} className={`conversation-link ${item.id === conversationId ? "active" : ""}`} onClick={() => void openConversation(item.id)}><MessageSquare size={17} /><span>{item.title || "Untitled conversation"}</span></button>) : <div className="history-empty"><MessageSquare size={22} /><p>{search ? "No conversations match this search." : "Your conversations will appear here."}</p></div>}
      </nav>
      <div className="sidebar-footer"><button className="demo-info" onClick={() => setHelpOpen(true)}><span className="demo-icon"><Info size={16} /></span><span><strong>Portfolio demo</strong><small>Sample orders. Real policy answers.</small></span><ChevronRight size={15} /></button><div className="sidebar-signoff"><BrandMark small /><span>Made for the miles ahead.</span></div></div>
    </aside>

    <main className={`main-panel ${empty ? "is-empty" : ""}`} inert={mobile && sidebarOpen}>
      <header className="chat-header">
        <div className="header-left"><button ref={menuButton} className="plain-icon mobile-menu" aria-label="Open conversation menu" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Menu size={22} /></button><div><span className="header-title" title={currentTitle || "Tarnfield care"}>{currentTitle || "Tarnfield care"}</span><span className="ai-label">AI assistant</span></div></div>
        <button className="policy-button" aria-label="Policies & help" onClick={() => setHelpOpen(true)}><ShieldCheck size={18} /><span>Policies & help</span><Info size={16} /></button>
      </header>

      <div className={`transcript-scroll ${empty ? "empty-scroll" : ""}`} ref={scroll} onScroll={(event) => { const el = event.currentTarget; setNearBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 110); }}>
        {loadingHistory ? <div className="loading-conversation" role="status"><LoaderCircle className="spinner" size={20} />Loading your conversation</div> : empty ? <section className="welcome" aria-labelledby="welcome-title">
          <div className="welcome-intro"><div><p className="welcome-eyebrow">TARNFIELD CARE</p><h1 id="welcome-title">Back to your run.</h1><p className="welcome-description">Help with returns and exchanges.<br />Ask a question. We’ll find your next step.</p></div><RouteArtwork /></div>
          {composer}
          <div className="starter-section"><div className="starter-heading">Try a demo order</div><div className="starters">{starters.map((item) => <button className="starter" key={item.title} onClick={() => void send(item.prompt)} disabled={busy || !ready || loadingHistory || creatingConversation || inFlight}><span className={`starter-symbol ${item.icon}`} aria-hidden="true">{item.icon === "return" ? <svg viewBox="0 0 24 24" fill="none"><path d="M7 8H4V5M4 8l4-4a7 7 0 1 1-2 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg> : item.icon === "exchange" ? <svg viewBox="0 0 24 24" fill="none"><path d="M4 8h16m-4-4 4 4-4 4M20 16H4m4-4-4 4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg> : <svg viewBox="0 0 24 24" fill="none"><rect x="3" y="5" width="18" height="14" rx="3" stroke="currentColor" strokeWidth="1.5" /><path d="M3 10h18M7 15h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>}</span><span className="starter-copy"><strong>{item.title}</strong><small>{item.description}</small></span><ArrowRight size={16} /></button>)}</div></div>
        </section> : <div className="transcript" aria-label={currentTitle || "Current conversation"}>
          <div className="conversation-day">This conversation</div>
          {messages.map((message) => <article className={`message ${message.role}`} key={message.id} aria-label={message.role === "assistant" ? "Tarnfield assistant" : "You"}>
            {message.role === "assistant" && <BrandMark small />}
            <div className="message-body">{message.role === "assistant" && <div className="message-author">Tarnfield<span>Care assistant</span></div>}
              {message.content && <div className="message-content"><Markdown>{message.content}</Markdown></div>}
              {message.pending && <div className="response-progress"><span className="pulse-dots" aria-hidden="true"><i /><i /><i /></span><span>{status}</span>{elapsed > 0 && <time aria-hidden="true">{elapsed}s</time>}</div>}
              {!message.pending && message.role === "assistant" && answerSources(message).length > 0 && <div className="sources"><span className="sources-label"><FileText size={12} />{answerSources(message)[0]?.kind === "referenced" ? "Referenced policy" : "Policy sources checked"}</span><div className="source-chips">{answerSources(message).map((item, index) => <button key={`${item.document}-${index}`} onClick={() => setSource(item)}><FileText size={13} />{item.title}<ChevronRight size={12} /></button>)}</div></div>}
              {message.error && <div className="message-error" role="alert"><Info size={18} /><div><span>{message.error}</span>{message.recover && <button className="recovery-button" disabled={loadingHistory || busy} onClick={() => { if (conversationId) void openConversation(conversationId); else { void refreshConversations(); if (mobile) setSidebarOpen(true); } }}>{conversationId ? "Reload conversation" : "Refresh conversation history"}<ArrowRight size={15} /></button>}</div></div>}
              {message.role === "assistant" && message.content && !message.pending && <div className="message-actions"><button className="plain-icon" aria-label={copied === message.id ? "Answer copied" : "Copy answer"} onClick={() => void copyMessage(message)}>{copied === message.id ? <Check size={14} /> : <Copy size={14} />}</button>{copied === message.id && <span>Copied</span>}</div>}
            </div>
          </article>)}
        </div>}
      </div>

      {!nearBottom && !empty && <button className="scroll-down" onClick={() => { setNearBottom(true); scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" }); }} aria-label="Go to latest message"><ArrowDown size={17} /></button>}

      {!empty && composer}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>
    </main>

    <Dialog open={helpOpen} onOpenChange={setHelpOpen}><DialogContent><div className="dialog-emblem"><ShieldCheck size={23} /></div><DialogTitle className="dialog-title">Helpful answers. Clear boundaries.</DialogTitle><DialogDescription className="dialog-description">Tarnfield care checks our policy documents to help with returns and exchanges.</DialogDescription><div className="help-points"><p><strong>Policy guidance only.</strong> The assistant explains returns and exchanges with policy evidence. It cannot issue refunds, arrange exchanges or file support requests.</p><p><strong>This is a portfolio demo.</strong> Tarnfield is a fictional retailer. Orders are sample data, and no real refund or replacement is issued.</p><p><strong>Continuity in this browser.</strong> Your conversations are linked to a browser cookie. This is not a verified customer account. Don’t share sensitive personal or payment information.</p></div><div className="policy-links"><span>Read the policy documents</span>{policyDocuments.map((item) => <a href={`/policies/${item.document}`} target="_blank" rel="noopener noreferrer" key={item.document}><FileText size={16} />{item.title}<ExternalLink size={14} /></a>)}</div></DialogContent></Dialog>
    <Dialog open={!!source} onOpenChange={(open) => { if (!open) setSource(null); }}><DialogContent><div className="dialog-emblem"><FileText size={23} /></div><DialogTitle className="dialog-title">{source?.title || "Policy source"}</DialogTitle><DialogDescription className="dialog-description">{source?.kind === "referenced" ? "Referenced in the assistant’s answer. Open the policy to verify the source and any exceptions." : "Policy evidence checked for this response."}</DialogDescription>{source?.excerpt ? <blockquote className="source-excerpt">{source.excerpt}</blockquote> : <p className="source-note">Read the original policy for the complete terms and exceptions.</p>}{source && policyDocuments.some((item) => item.document === source.document) && <Button variant="outline" asChild><a href={`/policies/${source.document}`} target="_blank" rel="noopener noreferrer"><FileText size={16} />Open policy PDF<ExternalLink size={14} /></a></Button>}</DialogContent></Dialog>
  </div>;
}
