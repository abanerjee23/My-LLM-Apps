import { useState, type FormEvent } from "react";
import type { DataSource, Evidence, Investigation } from "./types";
import { Icon } from "./Icon";

type Props = {
  current: Investigation | null;
  loading: boolean;
  sources: DataSource[];
  busy: boolean;
  onSend: (message: string, sourceId: string) => Promise<void>;
  onEvidence: (evidence: Evidence) => void;
  onSources: () => void;
};

export function Chat({current, loading, sources, busy, onSend, onEvidence, onSources}: Props) {
  const [message, setMessage] = useState("");
  const [sourceId, setSourceId] = useState("");
  const connected = sources.filter(source => source.status === "connected");
  const selected = connected.find(source => source.source_id === sourceId)?.source_id || connected[0]?.source_id || "";
  const working = busy || current?.status === "running";
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!message.trim() || working) return;
    try {
      await onSend(message.trim(), selected);
      setMessage("");
    } catch { /* The workspace shows the failure and keeps the draft for retry. */ }
  }
  return <main className={`chat-main ${current || loading ? "has-conversation" : "new-conversation"}`}>
    <div className="chat-transcript">
      {loading ? <p role="status">Loading your conversation…</p> : current ? <>
        <div className="user-message">{current.brief.split("\nFollow-up direction: ").map((text, index) => <p key={index}>{text}</p>)}</div>
        <article className="assistant-message">
          <div className="assistant-identity"><span className="assistant-symbol">S</span><strong>SourceLens</strong><span>{current.status === "running" ? "Investigating" : "Analysis"}</span></div>
          {current.status === "running" && <div className="thinking" role="status"><span className="status-dot"/>{current.events.at(-1)?.title || "Reading your question…"}<p>You can follow each step below while the analysis runs.</p></div>}
          {current.status === "failed" && <p role="alert">{current.events.filter(event => event.event_type === "error").at(-1)?.detail || "The investigation could not finish. Try again."}</p>}
          {current.executive_summary && <p className="chat-answer">{current.executive_summary}</p>}
          {current.findings.map(finding => <section className="chat-finding" key={finding.finding_id}>
            <h2>{finding.title}</h2><p>{finding.observation}</p><p className="interpretation">{finding.interpretation}</p>
            <div className="citation-list">{finding.evidence_ids.map((id, index) => {
              const evidence = current.evidence.find(item => item.evidence_id === id);
              const artifact = current.artifacts.find(item => item.artifact_id === id);
              return <button key={id} onClick={() => onEvidence(evidence || {evidence_id:id, source_id:id, kind:"query", title:artifact?.title || id, excerpt:JSON.stringify(artifact?.data || {}, null, 2),metadata:{}})}><span>{index + 1}</span>{evidence?.title || artifact?.title || id}</button>;
            })}</div>
          </section>)}
          {current.limitations.length > 0 && <details className="chat-details"><summary>Limitations and uncertainty</summary><ul>{current.limitations.map(item => <li key={item}>{item}</li>)}</ul></details>}
          {current.events.length > 0 && <details className="chat-details"><summary>View investigation activity · {current.events.length} steps</summary><ol>{current.events.map(event => <li key={event.sequence}><strong>{event.title}</strong><p>{event.detail}</p></li>)}</ol></details>}
        </article>
      </> : <div className="chat-welcome"><div className="lens-emblem" aria-hidden="true"><svg viewBox="0 0 80 64"><circle cx="29" cy="32" r="23"/><circle cx="51" cy="32" r="23"/><path d="M40 12v40"/></svg></div><h1>Find the story in your data.</h1><p>Start with a question. Follow the evidence.</p></div>}
    </div>
    <div className="chat-compose-area">
      <form className="chat-composer" onSubmit={send}>
        <label className="sr-only" htmlFor="chat-question">{current ? "Follow-up direction" : "Investigation brief"}</label>
        <textarea id="chat-question" value={message} onChange={event => setMessage(event.target.value)} placeholder={current ? "Ask a follow-up or add context…" : "Ask anything about your sources…"} rows={3} onKeyDown={event => {if(event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing){event.preventDefault();event.currentTarget.form?.requestSubmit();}}}/>
        <div className="chat-composer-bottom">{!current && connected.length > 0 ? <select aria-label="Investigation source" value={selected} onChange={event => setSourceId(event.target.value)}>{connected.map(source => <option key={source.source_id} value={source.source_id}>{source.name}</option>)}</select> : <button type="button" className="source-link" onClick={onSources}>{current ? "Manage sources" : "Connect a source"}</button>}<button className="send-message" aria-label={current ? "Send follow-up" : "Start investigation"} disabled={working || message.trim().length < (current ? 3 : 8) || (!current && !selected)} type="submit">↑</button></div>
      </form>
      <p className="composer-hint">SourceLens can make mistakes. Check the evidence before making a decision.</p>
      {!current && !loading && <><div className="prompt-heading">A few places to start</div><div className="starter-prompts"><button onClick={() => {setMessage("Why did revenue and customer sentiment decline for Nova X300 in the latest quarter?");document.getElementById("chat-question")?.focus();}}><Icon name="trend"/><strong>Understand what changed</strong><span>Investigate revenue and customer sentiment</span><Icon name="arrow" className="prompt-arrow"/></button><button onClick={() => {setMessage("What are the strongest customer feedback themes for Nova X300?");document.getElementById("chat-question")?.focus();}}><Icon name="feedback"/><strong>Hear what customers are saying</strong><span>Find the themes behind the feedback</span><Icon name="arrow" className="prompt-arrow"/></button></div><div className="connected-strip"><Icon name="sources"/><span>{connected.length ? `${connected.length} ${connected.length === 1 ? "source" : "sources"} connected` : "Add a source to begin"}</span><button onClick={onSources}>{connected.length ? "Manage sources" : "Connect a source"}<Icon name="arrow"/></button></div></>}
    </div>
  </main>;
}
