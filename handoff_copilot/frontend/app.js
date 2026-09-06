const patientIdInput = document.getElementById("patient-id");
const noteForm = document.getElementById("note-form");
const noteStatus = document.getElementById("note-status");
const handoffBtn = document.getElementById("handoff-btn");
const eventLog = document.getElementById("event-log");
const sbarPanel = document.getElementById("sbar-panel");
const flagsPanel = document.getElementById("flags-panel");
const askForm = document.getElementById("ask-form");
const askAnswer = document.getElementById("ask-answer");

let currentSource = null;

function logLine(text) {
  eventLog.textContent += text + "\n";
  eventLog.scrollTop = eventLog.scrollHeight;
}

noteForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const patientId = patientIdInput.value.trim();
  if (!patientId) {
    noteStatus.textContent = "Enter a patient ID first.";
    return;
  }
  const body = {
    patient_id: patientId,
    author: document.getElementById("note-author").value.trim(),
    note_type: document.getElementById("note-type").value,
    text: document.getElementById("note-text").value.trim(),
  };
  noteStatus.textContent = "Saving...";
  try {
    const res = await fetch("/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    noteStatus.textContent = "Saved.";
    document.getElementById("note-text").value = "";
  } catch (err) {
    noteStatus.textContent = "Failed: " + err.message;
  }
});

handoffBtn.addEventListener("click", () => {
  const patientId = patientIdInput.value.trim();
  if (!patientId) return;

  if (currentSource) currentSource.close();
  eventLog.textContent = "";
  sbarPanel.innerHTML = "";
  flagsPanel.innerHTML = "";
  handoffBtn.disabled = true;

  const source = new EventSource(`/handoff/${encodeURIComponent(patientId)}`);
  currentSource = source;

  const severityRank = { critical: 0, high: 1, medium: 2, low: 3 };

  ["started", "progress", "completed", "flag"].forEach((type) => {
    source.addEventListener(type, (ev) => {
      const data = JSON.parse(ev.data);
      logLine(`[${data.step}] ${type}: ${data.detail}`);
    });
  });

  source.addEventListener("result", (ev) => {
    const data = JSON.parse(ev.data);
    renderResult(data, severityRank);
    source.close();
    handoffBtn.disabled = false;
  });

  // Covers both our own "event: error" messages (step failures, JSON body)
  // and the browser's native EventSource error (connection dropped, no data).
  source.addEventListener("error", (ev) => {
    if (ev.data) logLine("[error] " + ev.data);
    else logLine("[error] connection to server lost");
    handoffBtn.disabled = false;
  });
});

function renderResult(data, severityRank) {
  const s = data.summary;
  sbarPanel.innerHTML = `
    <div class="sbar-section"><h3>Situation</h3><p>${escapeHtml(s.situation)}</p></div>
    <div class="sbar-section"><h3>Background</h3><p>${escapeHtml(s.background)}</p></div>
    <div class="sbar-section"><h3>Assessment</h3><p>${escapeHtml(s.assessment)}</p></div>
    <div class="sbar-section"><h3>Recommendation</h3><p>${escapeHtml(s.recommendation)}</p></div>
    <div class="sbar-section">
      <h3>Sources</h3>
      ${s.source_note_ids.map((id) => `<span class="citation-pill">${escapeHtml(id.slice(0, 8))}</span>`).join("")}
    </div>
    ${!data.guardrail.passed ? `<div class="guardrail-fail">Guardrail did not fully pass: ${escapeHtml(data.guardrail.reasons.join("; "))}</div>` : ""}
  `;

  const flags = [...data.red_flags].sort((a, b) => severityRank[a.severity] - severityRank[b.severity]);
  flagsPanel.innerHTML =
    (data.red_flag_service_degraded ? `<div class="degraded-banner">Red-flag service was unavailable during this check — review meds/allergies/vitals manually.</div>` : "") +
    (flags.length
      ? flags.map((f) => `<div class="flag ${f.severity}"><strong>${f.severity.toUpperCase()}</strong> — ${escapeHtml(f.description)}<br><span class="muted">${escapeHtml(f.evidence)}</span></div>`).join("")
      : `<p class="muted">No red flags detected.</p>`);
}

askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const patientId = patientIdInput.value.trim();
  const question = document.getElementById("ask-question").value.trim();
  if (!patientId || !question) return;
  askAnswer.textContent = "Thinking...";
  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patient_id: patientId, question }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const citations = data.citations.map((id) => `<span class="citation-pill">${escapeHtml(id.slice(0, 8))}</span>`).join("");
    askAnswer.innerHTML = `<p>${escapeHtml(data.answer)}</p>${citations}` +
      (!data.guardrail.passed ? `<div class="guardrail-fail">Guardrail flagged: ${escapeHtml(data.guardrail.reasons.join("; "))}</div>` : "");
  } catch (err) {
    askAnswer.textContent = "Failed: " + err.message;
  }
});

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
