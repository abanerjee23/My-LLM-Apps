const state = { actions: [], status: "", selected: null };
const $ = (selector) => document.querySelector(selector);
const esc = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

function humanize(value = "") { return value.replaceAll("_", " ").replace(/^./, c => c.toUpperCase()); }
function age(iso) {
  const hours = Math.max(0, (Date.now() - new Date(iso)) / 36e5);
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}
function showError(message = "") { const box = $("#error"); box.hidden = !message; box.textContent = message; }
function toast(message) { const el = $("#toast"); el.textContent = message; el.classList.add("show"); setTimeout(() => el.classList.remove("show"), 2600); }

async function api(path, options = {}) {
  const response = await fetch(path, { headers: {"Content-Type":"application/json"}, ...options });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Request failed (${response.status})`); }
  return response.json();
}

function renderQueue() {
  const term = $("#search").value.trim().toLowerCase();
  const kind = $("#kind-filter").value;
  const actions = state.actions.filter(item => (!state.status || item.status === state.status) && (!kind || item.kind === kind) && (!term || [item.reference, item.order_id, item.customer_id, item.summary].join(" ").toLowerCase().includes(term)));
  $("#queue-body").innerHTML = actions.map(item => {
    const isOverdue = ["pending","in_review","reopened","needs_information"].includes(item.status) && (Date.now() - new Date(item.created_at)) > 864e5;
    return `<tr data-reference="${esc(item.reference)}" tabindex="0"><td><span class="ref">${esc(item.reference)}</span><span class="subtle">${esc(item.order_id || "No order")}</span></td><td class="summary">${esc(item.summary)}<span class="subtle">${esc(item.customer_id || "Customer not identified")}</span></td><td>${esc(item.assigned_queue)}</td><td>${esc(item.assignee || "Unassigned")}</td><td class="${isOverdue ? "overdue" : ""}">${age(item.created_at)}${isOverdue ? " overdue" : ""}</td><td><span class="pill ${esc(item.status)}">${humanize(item.status)}</span></td></tr>`;
  }).join("");
  $("#empty").hidden = actions.length > 0;
  document.querySelectorAll("tbody tr").forEach(row => {
    const open = () => openDetail(row.dataset.reference);
    row.addEventListener("click", open);
    row.addEventListener("keydown", event => { if (event.key === "Enter") open(); });
  });
}

function actionButtons(item) {
  if (["pending","reopened"].includes(item.status)) return `<button class="action" data-action="assign">Assign to me</button><button class="action primary" data-action="approve">Approve</button><button class="action danger" data-action="reject">Reject</button><button class="action" data-action="request_information">Request information</button>`;
  if (item.status === "in_review") return `<button class="action primary" data-action="approve">Approve</button><button class="action danger" data-action="reject">Reject</button><button class="action" data-action="request_information">Request information</button>`;
  if (item.status === "approved") return `<button class="action primary" data-action="complete">Mark complete</button><button class="action danger" data-action="fail">Mark failed</button>`;
  if (["rejected","needs_information","failed"].includes(item.status)) return `<button class="action" data-action="reopen">Reopen</button>`;
  return `<span class="subtle">This request is closed.</span>`;
}

async function openDetail(reference) {
  try {
    const item = await api(`/ops/api/actions/${encodeURIComponent(reference)}`);
    state.selected = item;
    $("#detail-content").innerHTML = `<header class="detail-head"><span class="pill ${esc(item.status)}">${humanize(item.status)}</span><h2>${esc(item.reference)}</h2><p>${esc(item.summary)}</p></header><section class="detail-section facts"><div><span>Area</span>${esc(item.assigned_queue)}</div><div><span>Order</span>${esc(item.order_id || "—")}</div><div><span>Customer</span>${esc(item.customer_id || "—")}</div><div><span>Owner</span>${esc(item.assignee || "Unassigned")}</div></section><section class="detail-section"><h3>Customer request</h3><pre>${esc(JSON.stringify(item.customer_request, null, 2))}</pre></section><section class="detail-section"><h3>Policy evidence</h3><p>${esc(item.policy_evidence || "No policy evidence was attached; reviewer confirmation is required.")}</p></section><section class="detail-section"><h3>Order facts</h3><pre>${esc(JSON.stringify(item.order_facts, null, 2))}</pre></section><section class="detail-section"><h3>Handoff timeline</h3><ol class="audit">${item.audit.map(event => `<li><b>${humanize(event.event)}</b>${esc(event.note || "")}<span>${esc(event.actor)} · ${new Date(event.at).toLocaleString()}</span></li>`).join("")}</ol></section><form class="review-form"><label for="review-note">Decision note</label><textarea id="review-note" placeholder="Record the reason so the customer and next reviewer can understand the decision."></textarea><div class="action-row">${actionButtons(item)}</div></form>`;
    if (!$("#detail-dialog").open) {
      $("#detail-dialog").showModal();
    }
    document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", event => { event.preventDefault(); submitAction(button.dataset.action); }));
  } catch (error) { showError(error.message); }
}

async function submitAction(action) {
  const item = state.selected;
  const note = $("#review-note").value.trim();
  if (action !== "assign" && note.length < 2) { toast("Add a short decision note first."); return; }
  try {
    const path = action === "assign" ? "assign" : "decision";
    const body = action === "assign" ? { assignee: "me", note, expected_version: item.version } : { decision: action, note, expected_version: item.version };
    const updated = await api(`/ops/api/actions/${encodeURIComponent(item.reference)}/${path}`, { method: "POST", body: JSON.stringify(body) });
    state.selected = updated;
    toast(action === "assign" ? "Request assigned." : `Request moved to ${humanize(updated.status).toLowerCase()}.`);
    await load();
    await openDetail(updated.reference);
  } catch (error) { toast(error.message); }
}

async function load() {
  try {
    showError();
    const [queue, metrics] = await Promise.all([api("/ops/api/actions"), api("/ops/api/metrics")]);
    state.actions = queue.actions;
    $("#metric-open").textContent = metrics.open;
    $("#metric-overdue").textContent = metrics.overdue;
    $("#metric-decision").textContent = metrics.median_first_decision_hours == null ? "—" : `${metrics.median_first_decision_hours}h`;
    $("#metric-approval").textContent = metrics.approval_rate == null ? "—" : `${Math.round(metrics.approval_rate * 100)}%`;
    $("#count-all").textContent = metrics.total;
    ["pending","in_review","approved","completed"].forEach(status => $(`#count-${status}`).textContent = metrics.by_status[status] || 0);
    $("#last-updated").textContent = `Updated ${new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}`;
    renderQueue();
  } catch (error) { showError(`${error.message} Refresh the page or check reviewer access.`); }
}

document.querySelectorAll(".lane-step").forEach(button => button.addEventListener("click", () => { document.querySelectorAll(".lane-step").forEach(item => item.classList.remove("active")); button.classList.add("active"); state.status = button.dataset.status; renderQueue(); }));
$("#search").addEventListener("input", renderQueue);
$("#kind-filter").addEventListener("change", renderQueue);
$("#refresh").addEventListener("click", load);
$("#close-detail").addEventListener("click", () => $("#detail-dialog").close());
$("#detail-dialog").addEventListener("close", () => { state.selected = null; });
load();
setInterval(load, 15000);
