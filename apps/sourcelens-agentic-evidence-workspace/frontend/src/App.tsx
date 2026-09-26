import { useEffect, useState } from "react";
import { api } from "./api";
import { Chat } from "./Chat";
import { Icon } from "./Icon";
import { useAuth } from "./auth";
import type { Artifact, DataSource, Evidence, Investigation, NotebookEntry } from "./types";



function Money({ value }: { value: number }) {
  return <>{new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value)}</>;
}

function ComparisonChart({ artifact }: { artifact?: Artifact }) {
  const series = artifact?.data.series || [];
  const max = Math.max(1, ...series.map((row) => Number(row.net_revenue || 0)));
  return <section className="analysis-block chart-block"><div className="block-title"><strong>Commercial signal</strong><span>Net revenue</span></div><div className="bars">
    {series.map((row) => <div className="bar-column" key={String(row.period)}><strong><Money value={Number(row.net_revenue)} /></strong><div className="bar-track"><div className="bar-fill" style={{ height: `${Math.max(12, Number(row.net_revenue) / max * 100)}%` }} /></div><span>{row.period === "current" ? "Current quarter" : "Previous quarter"}</span><small>{Number(row.units).toLocaleString()} units</small></div>)}
  </div></section>;
}

function ThemeChart({ artifact }: { artifact?: Artifact }) {
  const current = (artifact?.data.series || []).filter((row) => row.period === "current").slice(0, 5);
  const max = Math.max(1, ...current.map((row) => Number(row.review_count || 0)));
  return <section className="analysis-block"><div className="block-title"><strong>Customer themes</strong><span>Current quarter</span></div><div className="theme-list">{current.map((row) => <div className="theme" key={String(row.theme)}><div><span>{String(row.theme).replaceAll("_", " ")}</span><small>{row.review_count} comments · {row.avg_rating} ★</small></div><div className="theme-track"><div style={{ width: `${Number(row.review_count) / max * 100}%` }} /></div></div>)}</div></section>;
}

function SourceProfile({ artifact }: { artifact: Artifact }) {
  const columns = Array.isArray(artifact.data.columns) ? artifact.data.columns : [];
  const numeric = Object.entries(artifact.data.numeric || {}).slice(0, 4) as [string, { minimum: number; maximum: number; average: number }][];
  const categories = Object.entries(artifact.data.categories || {}).slice(0, 3) as [string, { value: string; count: number }[]][];
  return <section className="analysis-block source-profile"><div className="block-title"><strong>Source profile</strong><span>{Number(artifact.data.record_count || 0).toLocaleString()} records</span></div><div className="profile-fields">{columns.slice(0, 12).map((column: string) => <span key={column}>{column.replaceAll("_", " ")}</span>)}</div>{numeric.map(([field, values]) => <div className="profile-line" key={field}><strong>{field.replaceAll("_", " ")}</strong><span>{values.minimum.toFixed(1)} → {values.maximum.toFixed(1)} · average {values.average.toFixed(1)}</span></div>)}{categories.map(([field, values]) => <div className="profile-line" key={field}><strong>{field.replaceAll("_", " ")}</strong><span>{values.slice(0, 3).map((item) => `${item.value} (${item.count})`).join(" · ")}</span></div>)}</section>;
}

function EvidenceDrawer({ evidence, onClose }: { evidence?: Evidence; onClose: () => void }) {
  if (!evidence) return null;
  return <div className="overlay" onClick={onClose}><aside className="drawer" onClick={(event) => event.stopPropagation()}><button className="icon-button" onClick={onClose}>×</button><p className="section-label">Source evidence</p><h2>{evidence.title}</h2><blockquote>“{evidence.excerpt}”</blockquote><dl>{Object.entries(evidence.metadata).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl><p className="source-id">Evidence ID: {evidence.evidence_id}</p></aside></div>;
}

function RatingPicker({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return <label className="rating-picker"><span>{label}</span><span className="rating-buttons">{[1, 2, 3, 4, 5].map((rating) => <button type="button" className={rating === value ? "selected" : ""} onClick={() => onChange(rating)} key={rating}>{rating}</button>)}</span></label>;
}

function NotebookDrawer({ entry, onClose }: { entry?: NotebookEntry; onClose: () => void }) {
  if (!entry) return null;
  const snapshot = entry.brief_snapshot;
  return <div className="overlay" onClick={onClose}><aside className="drawer notebook-drawer" onClick={(event) => event.stopPropagation()}><button className="icon-button" onClick={onClose}>×</button><p className="section-label">Notebook · revision {entry.revision}</p><h2>{entry.title}</h2><p className="saved-date">{new Date(entry.saved_at).toLocaleString()} · {entry.decision.replaceAll("_", " ")}</p><h3>Reviewed summary</h3><p className="drawer-summary">{entry.summary}</p><div className="saved-ratings"><span>Source accuracy <strong>{entry.accuracy_rating || "—"}/5</strong></span><span>Useful and complete <strong>{entry.usefulness_rating || "—"}/5</strong></span></div><h3>Full analysis</h3>{snapshot.findings.map((finding) => <article className="saved-finding" key={finding.finding_id}><strong>{finding.title}</strong><p>{finding.observation}</p><p>{finding.interpretation}</p></article>)}<h3>Limitations</h3><ul>{snapshot.limitations.map((item) => <li key={item}>{item}</li>)}</ul></aside></div>;
}


function LensMark() {
  return <svg className="lens-mark" viewBox="0 0 32 32" aria-hidden="true"><circle cx="12" cy="16" r="8"/><circle cx="21" cy="16" r="8"/><path d="M16.5 9.4v13.2"/></svg>;
}

function UserMenu() {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  if (!user) return null;
  const name = user.displayName || user.email || "Signed in";
  return <div className="user-menu"><button className="user-button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>{user.photoURL ? <img className="avatar" src={user.photoURL} alt="" referrerPolicy="no-referrer"/> : <span className="avatar">{name[0].toUpperCase()}</span>}<span className="user-name">{name}</span></button>{open && <div className="user-dropdown" role="menu"><p><strong>{user.displayName}</strong><span>{user.email}</span></p><button role="menuitem" className="text-button" onClick={() => signOut()}>Sign out</button></div>}</div>;
}

const tabs = [{name: "Home", path: "/"}, {name: "Investigations", path: "/investigations"}, {name: "Sources", path: "/sources"}, {name: "Notebook", path: "/notebook"}];
const date = (value: string) => new Date(value).toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"});

export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [runs, setRuns] = useState<Investigation[]>([]);
  const [notebook, setNotebook] = useState<NotebookEntry[]>([]);
  const [current, setCurrent] = useState<Investigation | null>(null);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [evidence, setEvidence] = useState<Evidence>(); const [entry, setEntry] = useState<NotebookEntry>();
  const [direction, setDirection] = useState("");
  const [decision, setDecision] = useState<"accepted" | "edited_accepted" | "rejected" | null>(null);
  const [edited, setEdited] = useState(""); const [accuracy, setAccuracy] = useState(0); const [usefulness, setUsefulness] = useState(0);
  const [sourceMode, setSourceMode] = useState<"file" | "bigquery" | null>(null);
  const [file, setFile] = useState<File>(); const [preview, setPreview] = useState<{source: DataSource; preview: Record<string, unknown>[]} | null>(null);
  const [connection, setConnection] = useState({name: "", project: "", dataset: "", location: "US"});
  const [verified, setVerified] = useState<{tables: string[]; service_account: string} | null>(null);
  const [requirements, setRequirements] = useState<{service_account: string} | null>(null);
  const [copied, setCopied] = useState(false);
  const [sourceDetail, setSourceDetail] = useState<{source: DataSource; preview: Record<string, unknown>[]} | null>(null);
  function navigate(to: string) { window.history.pushState({}, "", to); setPath(to); setCurrent(null); setError(""); window.scrollTo(0, 0); }
  async function refresh() { try { const [s, r, n] = await Promise.all([api.sources(), api.list(), api.notebook()]); setSources(s); setRuns(r); setNotebook(n); } catch (e) { setError(String(e)); } }
  useEffect(() => { refresh(); const onPop = () => setPath(window.location.pathname); window.addEventListener("popstate", onPop); return () => window.removeEventListener("popstate", onPop); }, []);
  const runId = path.startsWith("/investigations/") ? path.split("/")[2] : null;
  useEffect(() => { if (!runId) return; let active = true; const load = async () => { try { const result = await api.get(runId); if (active) { setCurrent(result); if (result.status !== "running") clearInterval(timer); } } catch (e) { if (active) setError(String(e)); } }; load(); const timer = window.setInterval(load, 1500); return () => { active = false; clearInterval(timer); }; }, [runId, current?.status]);
  useEffect(() => { if (path === "/sources") api.requirements().then(setRequirements).catch(e => setError(String(e))); }, [path]);
  async function act(work: () => Promise<void>) { setBusy(true); setError(""); try { await work(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); } }
  const activeTab = runId ? "/investigations" : path;
  function runList(items: Investigation[]) { return items.length ? <div className="document-list">{items.map(run => <button className="document-row" key={run.investigation_id} onClick={() => navigate(`/investigations/${run.investigation_id}`)}><div><strong>{run.title}</strong><p>{run.brief.split("\n")[0]}</p></div><span className="row-meta">{date(run.updated_at)}<small>{run.status === "ready" ? "Ready for review" : run.status}</small></span><span className="chevron">›</span></button>)}</div> : <div className="empty-state"><h3>Your next question starts here</h3><p>Investigations bring the analysis, evidence and your decisions together.</p><button className="secondary" onClick={() => navigate("/")}>Start an investigation</button></div>; }
  function recordTable(records: Record<string, unknown>[]) { const cols = [...new Set(records.flatMap(r => Object.keys(r)))].slice(0, 8); return records.length ? <div className="table-scroll"><table><thead><tr>{cols.map(c => <th key={c}>{c}</th>)}</tr></thead><tbody>{records.slice(0, 8).map((r, i) => <tr key={i}>{cols.map(c => <td key={c}>{String(r[c] ?? "—").slice(0, 180)}</td>)}</tr>)}</tbody></table></div> : <p className="muted">No row preview is available for this source.</p>; }
  async function sendChat(message: string, source: string) {
    setBusy(true); setError("");
    try {
      if (current && runId) setCurrent(await api.refine(current.investigation_id, message));
      else { const result = await api.create(message, source); setRuns([result, ...runs]); navigate(`/investigations/${result.investigation_id}`); }
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); throw error; }
    finally { setBusy(false); }
  }
  return <div className="site chat-workspace">
    <aside className="workspace-sidebar">
      <a className="brand" href="/" onClick={e => { e.preventDefault(); navigate("/"); }}><LensMark/>SourceLens</a>
      <button className="new-chat" onClick={() => { setCurrent(null); navigate("/"); }}><span>＋</span>New conversation</button>
      <nav aria-label="Main navigation">{tabs.filter(tab => tab.path !== "/").map(tab => <a key={tab.path} href={tab.path} aria-current={activeTab === tab.path ? "page" : undefined} onClick={e => {e.preventDefault(); navigate(tab.path);}}><Icon name={tab.path.slice(1)}/>{tab.name}</a>)}</nav>
      <div className="sidebar-history"><p>Recent conversations</p>{runs.map(run => <button key={run.investigation_id} className={runId === run.investigation_id ? "selected" : ""} onClick={() => navigate(`/investigations/${run.investigation_id}`)} title={run.brief.split("\n")[0]}><span>{run.brief.split("\n")[0]}</span></button>)}</div>
      <div className="sidebar-profile"><UserMenu/></div>
    </aside>
    <header className="workspace-topbar"><span>{runId ? current?.title || "Conversation" : path === "/" ? "New conversation" : tabs.find(tab => tab.path === path)?.name}</span></header>
  {error && <div className="error-banner" role="alert">{error}<button aria-label="Dismiss error" onClick={() => setError("")}>×</button></div>}
  {(path === "/" || runId) && <Chat key={runId || "new"} current={runId ? current : null} loading={Boolean(runId && !current)} sources={sources} busy={busy} onSend={sendChat} onEvidence={setEvidence} onSources={() => navigate("/sources")}/>}

  {path === "/investigations" && <main className="content-page"><div className="page-heading"><div><h1>Investigations</h1><p>Every question, with the work behind the answer.</p></div><button className="primary" onClick={() => navigate("/")}>New investigation</button></div>{runList(runs)}</main>}
  {path === "/sources" && <main className="content-page"><div className="page-heading"><div><h1>Sources</h1><p>The evidence your investigations begin with.</p></div></div><div className="source-actions"><button className={sourceMode === "file" ? "secondary selected" : "secondary"} onClick={() => {setSourceMode(sourceMode === "file" ? null : "file");setPreview(null);}}>Upload a file</button><button className={sourceMode === "bigquery" ? "secondary selected" : "secondary"} onClick={() => setSourceMode(sourceMode === "bigquery" ? null : "bigquery")}>Connect BigQuery</button></div>
    {sourceMode === "file" && <section className="setup-panel"><h2>Upload a file</h2><p>CSV, XLSX, PDF, DOCX or DOC, up to 25 MB. Text-based documents are supported; scanned pages need OCR before uploading.</p>{!preview ? <><label className="file-drop"><strong>{file?.name || "Choose a file to preview"}</strong><input type="file" accept=".csv,.xlsx,.pdf,.docx,.doc" onChange={e => setFile(e.target.files?.[0])}/></label><button className="primary" disabled={!file || busy} onClick={() => act(async () => { if(file) setPreview(await api.previewSource(file)); })}>{busy ? "Reading file…" : "Preview file"}</button></> : <><h3>{preview.source.name}</h3><p>{preview.source.record_count.toLocaleString()} extracted records. Review the content before connecting.</p>{recordTable(preview.preview)}<button className="primary" disabled={busy} onClick={() => act(async () => {if(file) { await api.uploadSource(file); await refresh(); setPreview(null); setFile(undefined); setSourceMode(null); }})}>Confirm source</button><button className="text-button" onClick={() => setPreview(null)}>Choose another file</button></>}</section>}
    {sourceMode === "bigquery" && <section className="setup-panel bq-setup"><div className="setup-intro"><span className="source-logo">BQ</span><div><h2>Connect Google BigQuery</h2><p>SourceLens connects with read-only access. You will grant access to the SourceLens service account; no key or credential file is uploaded.</p></div></div><ol className="connection-steps">
      <li><span className="step-number">1</span><div className="step-content"><h3>Identify your dataset</h3><p>Enter the values shown in BigQuery Explorer and the dataset’s <strong>Details</strong> tab.</p><div className="form-grid">{([
        ['name','Connection name','Customer research','A name you will recognise in SourceLens'],
        ['project','Google Cloud project ID','my-company-project','The project that contains the dataset'],
        ['dataset','BigQuery dataset ID','customer_feedback','The dataset name, without the project prefix'],
        ['location','Dataset location','US','Exactly as shown in Details, such as US, EU or europe-west2'],
      ] as const).map(([key,label,placeholder,help]) => <label className="field" key={key}>{label}<input value={connection[key]} placeholder={placeholder} onChange={e => {setConnection({...connection,[key]:e.target.value});setVerified(null);}}/><small>{help}</small></label>)}</div></div></li>
      <li><span className="step-number">2</span><div className="step-content"><h3>Grant SourceLens read access</h3><p>Copy this service account and use it as the principal in both permission steps below.</p><div className="copy-line service-account"><code>{requirements?.service_account || "Loading service account…"}</code><button className="secondary" disabled={!requirements} onClick={() => act(async () => {await navigator.clipboard.writeText(requirements!.service_account);setCopied(true);window.setTimeout(()=>setCopied(false),1800);})}>{copied ? "Copied" : "Copy"}</button></div><div className="permission-grid"><article><span>On the dataset</span><strong>BigQuery Data Viewer</strong><p>BigQuery Explorer → dataset menu → Sharing → Permissions → Add principal.</p></article><article><span>On the project</span><strong>BigQuery Job User</strong><p>IAM &amp; Admin → IAM → Grant access. Use the same service account.</p></article></div><div className="cloud-links"><a className="secondary" href={`https://console.cloud.google.com/bigquery?project=${encodeURIComponent(connection.project)}`} target="_blank" rel="noreferrer">Open BigQuery ↗</a><a className="secondary" href={`https://console.cloud.google.com/iam-admin/iam?project=${encodeURIComponent(connection.project)}`} target="_blank" rel="noreferrer">Open project IAM ↗</a></div></div></li>
      <li className={verified ? "step-complete" : ""}><span className="step-number">{verified ? "✓" : "3"}</span><div className="step-content"><h3>Verify and connect</h3><p>SourceLens will check the permissions and list the tables it can read. It will not modify the dataset.</p>{verified ? <><div className="verification" role="status"><strong>Connection verified</strong><p>{verified.tables.length} readable {verified.tables.length === 1 ? "table" : "tables"} found.</p><div className="field-chips">{verified.tables.map(t => <span key={t}>{t}</span>)}</div></div><button className="primary" disabled={busy} onClick={() => act(async () => {await api.connectSource({...connection,kind:"bigquery"});await refresh();setSourceMode(null);setVerified(null);})}>{busy ? "Connecting…" : "Connect this dataset"}</button></> : <button className="primary" disabled={busy || Object.values(connection).some(v => !v.trim())} onClick={() => act(async () => setVerified(await api.verifySource({...connection,kind:"bigquery"})))}>{busy ? "Checking access…" : "Verify access"}</button>}</div></li>
    </ol></section>}
    <div className="section-heading source-heading"><h2>Your sources</h2><span>{sources.length} sources</span></div><div className="document-list">{sources.map(s => <button className="document-row" key={s.source_id} onClick={() => act(async () => setSourceDetail(await api.source(s.source_id)))}><div><strong>{s.name}</strong><p>{s.kind.replace("file.","").toUpperCase()} · {s.record_count.toLocaleString()} records</p></div><span className="connection-status">{s.status === "connected" ? "● Connected" : s.status}</span><span className="chevron">›</span></button>)}</div><p className="source-footnote">Original files are preserved with a content hash and source history.</p>
  </main>}
  {path === "/notebook" && <main className="content-page"><div className="page-heading"><div><h1>Notebook</h1><p>Your reviewed briefs, ready to return to.</p></div><span className="muted">{notebook.length} saved</span></div>{notebook.length ? <div className="notebook-grid">{notebook.map(n => <button className="notebook-tile" key={n.entry_id} onClick={() => setEntry(n)}><span className="decision">{n.decision.replaceAll("_", " ")}</span><h3>{n.title}</h3><p>{n.summary}</p><footer>{date(n.saved_at)}<span>Accuracy {n.accuracy_rating || "—"}/5</span></footer></button>)}</div> : <div className="empty-state"><h3>A home for your conclusions</h3><p>Accept, edit or reject an investigation to save the full brief here with your ratings.</p><button className="secondary" onClick={() => navigate("/investigations")}>Browse investigations</button></div>}</main>}
  {runId && <details className="full-report"><summary>Open full report, charts and notebook review</summary><main className="investigation-page"><button className="text-button back-link" onClick={() => {refresh();navigate("/investigations");}}>‹ All investigations</button>{current ? <><div className="page-heading"><div><h1>{current.title}</h1><p>{date(current.created_at)} · {current.status === "ready" ? "Ready for review" : current.status}</p></div></div><div className="investigation-layout"><article className="brief-document"><p className="original-question">{current.brief}</p>{current.status === "running" && <p className="loading">Reviewing sources and preparing your brief…</p>}{current.status === "failed" && <p role="alert">This investigation could not finish. Open Activity for details.</p>}{current.status === "ready" && <><h2>Summary</h2><p className="executive-summary">{current.executive_summary}</p>{current.artifacts.some(a => a.artifact_id === "source-profile") ? <SourceProfile artifact={current.artifacts.find(a => a.artifact_id === "source-profile")!}/> : <div className="chart-grid"><ComparisonChart artifact={current.artifacts.find(a=>a.artifact_id === "chart-revenue")}/><ThemeChart artifact={current.artifacts.find(a=>a.artifact_id === "chart-themes")}/></div>}<h2>What the evidence shows</h2>{current.findings.map(f => <section className="finding" key={f.finding_id}><h3>{f.title}</h3><p>{f.observation}</p><p className="muted">{f.interpretation}</p><div className="evidence-links">{f.evidence_ids.map(id => {const ev=current.evidence.find(e=>e.evidence_id===id);const artifact=current.artifacts.find(a=>a.artifact_id===id);return <button key={id} onClick={() => setEvidence(ev || {evidence_id:id,source_id:id,kind:"query",title:artifact?.title || id,excerpt:JSON.stringify(artifact?.data || {},null,2),metadata:{}})}>{ev ? ev.title : artifact?.title || id} ↗</button>;})}</div><p className="next"><strong>Next step:</strong> {f.next_step}</p></section>)}<h2>What remains uncertain</h2><ul className="limitations">{current.limitations.map(l=><li key={l}>{l}</li>)}</ul><div className="review-bar"><div><strong>Your judgment</strong><p>Save this brief and its evidence to your notebook.</p></div><div>{([['rejected','Reject'],['edited_accepted','Edit and accept'],['accepted','Accept']] as const).map(([value,label])=><button className={value === "accepted" ? "primary":"secondary"} key={value} onClick={()=>{setDecision(value);setEdited(current.executive_summary);}}>{label}</button>)}</div></div>{decision && <section className="review-panel"><h3>Review before saving</h3>{decision === "edited_accepted" && <textarea aria-label="Edited summary" value={edited} onChange={e=>setEdited(e.target.value)}/>}<RatingPicker label="Source accuracy" value={accuracy} onChange={setAccuracy}/><RatingPicker label="Relevant, complete and useful" value={usefulness} onChange={setUsefulness}/><button className="primary" disabled={busy || !accuracy || !usefulness} onClick={()=>act(async()=>{await api.review(current.investigation_id,{decision,edited_summary:decision === "edited_accepted"?edited:undefined,accuracy_rating:accuracy,usefulness_rating:usefulness});await refresh();setDecision(null);navigate("/notebook");})}>Save to notebook</button><button className="text-button" onClick={()=>setDecision(null)}>Cancel</button></section>}</>}</article><aside className="conversation-panel"><h2>Conversation</h2><div className="conversation-note"><LensMark/><p>{current.status === "ready" ? current.findings[0]?.next_step || "What would you like to explore next?" : "I’ll examine the available evidence and record the work here."}</p></div><textarea aria-label="Follow-up direction" placeholder="Add context or guide the next pass…" value={direction} onChange={e=>setDirection(e.target.value)}/><button className="secondary" disabled={busy || current.status !== "ready" || direction.trim().length < 3} onClick={()=>act(async()=>{setCurrent(await api.refine(current.investigation_id,direction));setDirection("");})}>Update investigation</button><details className="activity"><summary>Activity <span>{current.events.length} steps</span></summary>{current.events.map(e=><details className="activity-step" key={e.sequence}><summary>{e.title}</summary><p>{e.detail}</p>{typeof e.payload.role === "string" && <small>{e.payload.role}</small>}{typeof e.payload.sql === "string" && <pre>{e.payload.sql}</pre>}</details>)}</details></aside></div></> : <p>Loading investigation…</p>}</main></details>}
  {!tabs.some(t=>t.path===path) && !runId && <main className="content-page"><h1>Page not found</h1><button onClick={()=>navigate("/")}>Return home</button></main>}
  <EvidenceDrawer evidence={evidence} onClose={()=>setEvidence(undefined)}/><NotebookDrawer entry={entry} onClose={()=>setEntry(undefined)}/>{sourceDetail && <div className="overlay" onClick={()=>setSourceDetail(null)}><aside className="drawer" onClick={e=>e.stopPropagation()}><button className="icon-button" aria-label="Close source" onClick={()=>setSourceDetail(null)}>×</button><h2>{sourceDetail.source.name}</h2><p>{sourceDetail.source.record_count.toLocaleString()} records</p>{recordTable(sourceDetail.preview)}<h3>Source details</h3><dl>{Object.entries(sourceDetail.source.metadata).filter(([key])=>key!=="raw_path").map(([key,value])=><div key={key}><dt>{key.replaceAll("_"," ")}</dt><dd>{String(value)}</dd></div>)}</dl></aside></div>}
  </div>;
}
