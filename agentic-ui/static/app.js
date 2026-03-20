'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let personas      = [];
let conversations = {};   // { [persona]: [conv, ...] }
let activeConvId  = null;
let activePersona = null;
let streaming     = false;
let costMode      = "off";   // "off" | "nzd" | "tokens"
let errorMode     = "off";   // "off" | "debug" | "errors"
let messageStyle  = "bubble";  // "bubble" | "inline"

let abortController  = null;
let timeoutId        = null;
let timeoutWarningEl = null;

let pendingFiles  = [];   // [{ id, filename, status, content?, file_id?, url?, error? }]
let fileIdCounter = 0;

// ── DOM ───────────────────────────────────────────────────────────────────────
const personaList     = document.getElementById("persona-list");
const emptyState      = document.getElementById("empty-state");
const chatView        = document.getElementById("chat-view");
const chatPersona     = document.getElementById("chat-persona-name");  // span inside #btn-agent-picker
const messagesEl      = document.getElementById("messages");
const inputEl         = document.getElementById("input");
const btnSend          = document.getElementById("btn-send");
const btnNewConv       = document.getElementById("btn-new-conv");
const modelSelect      = document.getElementById("model-select");
const costToggle       = document.getElementById("cost-toggle");
const styleToggle      = document.getElementById("style-toggle");
const errorModeSelect  = document.getElementById("error-mode-select");
const providerBadge    = document.getElementById("provider-badge");
const btnMenu          = document.getElementById("btn-menu");
const btnSettings      = document.getElementById("btn-settings");
const settingsMenu     = document.getElementById("settings-menu");
const sidebarEl        = document.getElementById("sidebar");
const sidebarOverlay   = document.getElementById("sidebar-overlay");
const btnAgentPicker   = document.getElementById("btn-agent-picker");
const agentPicker      = document.getElementById("agent-picker");
const inputArea        = document.getElementById("input-area");
const fileChips        = document.getElementById("file-chips");
const fileInput        = document.getElementById("file-input");
const btnAttach        = document.getElementById("btn-attach");

// ── Boot ──────────────────────────────────────────────────────────────────────
async function init() {
  try {
    // costMode starts "off" — meta spans hidden by default
    const personaResp = await api("GET", "/api/personas");
    console.log('[Init] Personas response:', personaResp);
    personas = personaResp.personas;
    const defaultPersona = personaResp.default;
    console.log('[Init] Personas loaded:', personas.length, 'Default:', defaultPersona);

    const allConvs = await api("GET", "/api/conversations");
    console.log('[Init] Conversations loaded:', allConvs.length);
    allConvs.forEach(c => {
      if (!conversations[c.persona]) conversations[c.persona] = [];
      conversations[c.persona].push(c);
    });

    renderSidebar();
    renderEmptyPersonaList();

    // Initialize settings checkboxes
    costToggle.checked = costMode !== "off";
    styleToggle.checked = messageStyle === "inline";

    if (defaultPersona) {
      console.log('[Init] Starting default conversation:', defaultPersona);
      await startConversation(defaultPersona);
    } else {
      console.log('[Init] No default persona, showing empty state');
      showEmpty();
    }
  } catch (err) {
    console.error('[Init] Error:', err);
    showEmpty();
  }
}

function renderEmptyPersonaList() {
  const list = document.getElementById("empty-persona-list");
  if (personas.length === 0) {
    list.innerHTML = `<p style="color:red; padding:20px; font-family:monospace;">No personas loaded. Check console for errors.</p>`;
    return;
  }
  list.innerHTML = personas.map(p => `
    <div class="empty-persona-row" data-persona="${p.id}">
      <span class="persona-icon">${p.icon}</span>
      <span class="persona-name">${escHtml(p.display)}</span>
    </div>`).join("");
  list.querySelectorAll(".empty-persona-row").forEach(el =>
    el.addEventListener("click", () => startConversation(el.dataset.persona))
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function renderSidebar() {
  personaList.innerHTML = personas.map(p => {
    const convs = conversations[p.id] || [];
    const isActive = p.id === activePersona;
    const convItems = convs.slice(0, 5).map(c => `
      <div class="conv-item ${c.id === activeConvId ? "active" : ""}"
           data-conv="${c.id}" data-persona="${p.id}">
        ${escHtml(c.title)}
      </div>`).join("");

    return `
      <div class="persona-section" data-persona="${p.id}">
        <div class="persona-header ${isActive ? "active" : ""}" data-persona="${p.id}">
          <span class="persona-icon">${p.icon}</span>
          <span class="persona-name">${p.display}</span>
          <span class="dot ${isActive ? "active" : ""}"></span>
        </div>
        ${convs.length ? `<div class="conv-list">${convItems}</div>` : ""}
      </div>`;
  }).join("");

  personaList.querySelectorAll(".persona-header").forEach(el =>
    el.addEventListener("click", () => startConversation(el.dataset.persona))
  );
  personaList.querySelectorAll(".conv-item").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      openConversation(el.dataset.conv, el.dataset.persona);
    })
  );
}


// ── Views ─────────────────────────────────────────────────────────────────────
function showEmpty() {
  emptyState.style.display = "flex";
  chatView.style.display = "none";
}

function showChat(persona) {
  emptyState.style.display = "none";
  chatView.style.display = "flex";
  const p = personas.find(x => x.id === persona);
  chatPersona.textContent = p ? `${p.icon}  ${p.display}` : persona;
  btnSend.disabled = false;
}

// ── Mobile drawer ─────────────────────────────────────────────────────────────
function openDrawer() {
  sidebarEl.classList.add("open");
  sidebarOverlay.classList.add("visible");
}

function closeDrawer() {
  sidebarEl.classList.remove("open");
  sidebarOverlay.classList.remove("visible");
}

btnMenu.addEventListener("click", openDrawer);
sidebarOverlay.addEventListener("click", closeDrawer);

// ── Agent picker dropdown ─────────────────────────────────────────────────────
function renderAgentPicker() {
  agentPicker.innerHTML = personas.map(p => `
    <div class="picker-row ${p.id === activePersona ? "active" : ""}" data-persona="${p.id}">
      <span class="persona-icon">${p.icon}</span>
      <span class="persona-name">${escHtml(p.display)}</span>
    </div>`).join("");
  agentPicker.querySelectorAll(".picker-row").forEach(el =>
    el.addEventListener("click", () => {
      closeAgentPicker();
      startConversation(el.dataset.persona);
    })
  );
}

function openAgentPicker() {
  renderAgentPicker();
  agentPicker.classList.add("open");
  btnAgentPicker.classList.add("open");
}

function closeAgentPicker() {
  agentPicker.classList.remove("open");
  btnAgentPicker.classList.remove("open");
}

btnAgentPicker.addEventListener("click", () => {
  agentPicker.classList.contains("open") ? closeAgentPicker() : openAgentPicker();
});

document.addEventListener("click", e => {
  if (!e.target.closest("#btn-agent-picker") && !e.target.closest("#agent-picker"))
    closeAgentPicker();
});

// ── Click persona — resume most recent, or start fresh if none ───────────────
async function startConversation(persona) {
  closeDrawer();
  activePersona = persona;
  streaming = false; // cancel any in-progress stream on persona switch

  const existing = (conversations[persona] || [])[0];
  if (existing) {
    await openConversation(existing.id, persona);
    return;
  }

  await newConversation(persona);
}

// ── Start a brand new conversation (called by "New" button) ───────────────────
async function newConversation(persona) {
  streaming = false;
  activePersona = persona;
  activeConvId = null;

  const conv = await api("POST", "/api/conversations", { persona });
  activeConvId = conv.id;
  if (!conversations[persona]) conversations[persona] = [];
  conversations[persona].unshift(conv);

  showChat(persona);
  messagesEl.innerHTML = "";
  renderSidebar();

  // Show loading indicator while init runs
  const loader = document.createElement("div");
  loader.className = "msg-loading";
  loader.innerHTML = `<span>Loading system context</span><span class="loading-dots"><span></span><span></span><span></span></span>`;
  messagesEl.appendChild(loader);

  await sendMessage(`init --persona ${persona}`);
  loader.remove();
}

// ── Open existing conversation ────────────────────────────────────────────────
async function openConversation(convId, persona) {
  if (streaming) return;
  closeDrawer();
  activeConvId = convId;
  activePersona = persona;

  const [conv, msgs] = await Promise.all([
    api("GET", `/api/conversations/${convId}`),
    api("GET", `/api/conversations/${convId}/messages`),
  ]);

  showChat(persona);
  renderMessages(msgs);
  renderSidebar();
}

// ── Render messages ───────────────────────────────────────────────────────────
function renderMessages(msgs) {
  messagesEl.innerHTML = msgs.map(m => {
    if (m.role === "user") return userBubble(m.content);
    if (m.role === "assistant") return assistantBubble(m.content, m.tool_calls, m.cost_usd, m.input_tokens, m.output_tokens);
    if (m.role === "system") return systemLine(m.content);
    return "";
  }).join("");
  scrollBottom();
}

function userBubble(text) {
  return `<div class="msg user"><div class="msg-bubble">${fmt(text)}</div></div>`;
}

function assistantBubble(text, toolCalls, costUsd, inputTokens, outputTokens) {
  const tools = (toolCalls || []).map(tc => toolBlock(tc)).join("");
  const metaHtml = costUsd ? `<span class="msg-meta"
    data-cost="${costUsd}"
    data-input-tokens="${inputTokens || 0}"
    data-output-tokens="${outputTokens || 0}"
    style="${costMode === 'off' ? 'display:none' : ''}"
    >${escHtml(formatCost(costUsd, inputTokens || 0, outputTokens || 0))}</span>` : "";
  return `
    <div class="msg assistant">
      ${tools}
      ${text ? `<div class="msg-bubble">${fmt(text)}</div>` : ""}
      ${metaHtml}
    </div>`;
}

function toolBlock(tc) {
  const preview = toolPreview(tc.name, tc.input);
  const detail = JSON.stringify(tc.input, null, 2);
  return `
    <details class="tool-block">
      <summary class="tool-summary">
        <span class="tool-icon">⚡</span>
        <span class="tool-name">${escHtml(tc.name)}</span>
        <span class="tool-preview">${escHtml(preview)}</span>
      </summary>
      <div class="tool-detail">${escHtml(detail)}</div>
    </details>`;
}

function toolPreview(name, input) {
  if (input.command) return input.command.slice(0, 80);
  if (input.file_path) return input.file_path;
  if (input.pattern) return input.pattern;
  if (input.query) return input.query;
  if (input.url) return input.url;
  const keys = Object.keys(input);
  if (keys.length) return `${keys[0]}: ${String(input[keys[0]]).slice(0, 60)}`;
  return "";
}

function systemLine(text) {
  return `<div class="msg system"><div class="msg-bubble">${escHtml(text)}</div></div>`;
}

function fmt(text) {
  return escHtml(text)
    .replace(/```([\s\S]*?)```/g, (_, c) => `<pre><code>${c.trim()}</code></pre>`)
    .replace(/`([^`\n]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ── Error display ─────────────────────────────────────────────────────────────
function errorDisplay(detail) {
  if (errorMode === "errors") {
    return `<span style="color:var(--danger)">${escHtml(detail)}</span>`;
  } else if (errorMode === "debug") {
    const brief = detail.split(/\s+/).slice(0, 5).join(" ");
    return `<span class="msg-error-debug">${escHtml(brief)}</span>`;
  } else {
    return `<span class="msg-error-simple">Thinking…</span>`;
  }
}

function updateErrorBubbles() {
  messagesEl.querySelectorAll("[data-error-detail]").forEach(el => {
    el.innerHTML = errorDisplay(el.dataset.errorDetail);
  });
}

// ── Timeout warning ───────────────────────────────────────────────────────────
function showTimeoutWarning() {
  if (timeoutWarningEl) return;
  timeoutWarningEl = document.createElement("div");
  timeoutWarningEl.className = "msg-timeout-warning";
  timeoutWarningEl.innerHTML = `
    <span>Still working — taking longer than usual.</span>
    <button class="btn-cancel-stream">Cancel</button>
  `;
  timeoutWarningEl.querySelector(".btn-cancel-stream").addEventListener("click", cancelStream);
  messagesEl.appendChild(timeoutWarningEl);
  scrollBottom();
}

function cancelStream() {
  if (abortController) abortController.abort();
}

// ── Send message ──────────────────────────────────────────────────────────────
async function sendMessage(content) {
  const hasFiles = pendingFiles.some(f => f.status === "inline" || f.status === "drive");
  if ((!content && !hasFiles) || streaming || !activeConvId) return;

  // Client-side slash commands
  if (content.trim() === "/model") {
    inputEl.value = "";
    autoResize();
    const label = modelSelect.options[modelSelect.selectedIndex].text;
    const el = document.createElement("div");
    el.innerHTML = systemLine(`Current model: ${label}`);
    messagesEl.appendChild(el.firstElementChild);
    scrollBottom();
    return;
  }

  if (content.trim().startsWith("/rename ")) {
    const newTitle = content.trim().slice(8).trim();
    inputEl.value = "";
    autoResize();
    if (!newTitle || !activeConvId) return;
    await api("PATCH", `/api/conversations/${activeConvId}`, { title: newTitle });
    const convs = conversations[activePersona] || [];
    const conv = convs.find(c => c.id === activeConvId);
    if (conv) conv.title = newTitle;
    renderSidebar();
    const el = document.createElement("div");
    el.innerHTML = systemLine(`Conversation renamed to "${newTitle}"`);
    messagesEl.appendChild(el.firstElementChild);
    scrollBottom();
    return;
  }

  if (content.trim().startsWith("/delete ")) {
    const targetTitle = content.trim().slice(8).trim();
    inputEl.value = "";
    autoResize();
    if (!targetTitle) return;
    const convs = conversations[activePersona] || [];
    const match = convs.find(c => c.title === targetTitle);
    if (!match) {
      const el = document.createElement("div");
      el.innerHTML = systemLine(`No conversation found with title "${targetTitle}"`);
      messagesEl.appendChild(el.firstElementChild);
      scrollBottom();
      return;
    }
    await api("DELETE", `/api/conversations/${match.id}`);
    conversations[activePersona] = convs.filter(c => c.id !== match.id);
    const el = document.createElement("div");
    el.innerHTML = systemLine(`Deleted "${targetTitle}"`);
    if (match.id === activeConvId) {
      activeConvId = null;
      messagesEl.innerHTML = "";
      renderSidebar();
      await startConversation(activePersona);
    } else {
      renderSidebar();
      messagesEl.appendChild(el.firstElementChild);
      scrollBottom();
    }
    return;
  }

  streaming = true;
  btnSend.disabled = true;
  btnSend.textContent = "…";

  // Prepend any attached files to the content (only ready ones)
  const readyFiles = pendingFiles.filter(f => f.status === "inline" || f.status === "drive");
  const prefix = buildFilePrefix();
  const fullContent = content === "init" ? content : prefix + content;

  // User bubble (skip for auto-init)
  if (!content.startsWith("init")) {
    // Show only the user's text in the bubble (not the raw file dump)
    const chipSummary = readyFiles.length
      ? `<div class="msg-file-summary">${readyFiles.map(f =>
          `<span class="msg-file-chip ${f.status}">${f.status === "drive" ? "☁️" : "📄"} ${escHtml(f.filename)}</span>`
        ).join("")}</div>` : "";
    const displayText = content || (readyFiles.length === 1
      ? `${escHtml(readyFiles[0].filename)} uploaded`
      : `${readyFiles.length} files uploaded`);
    const bubble = document.createElement("div");
    bubble.className = "msg user";
    bubble.innerHTML = `<div class="msg-bubble">${chipSummary}${fmt(displayText)}</div>`;
    messagesEl.appendChild(bubble);
    scrollBottom();
  }

  // Clear pending files
  pendingFiles = [];
  renderChips();

  inputEl.value = "";
  autoResize();

  // Assistant bubble (accumulates during stream)
  const assistantDiv = document.createElement("div");
  assistantDiv.className = "msg assistant";
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble cursor";
  assistantDiv.appendChild(bubble);
  messagesEl.appendChild(assistantDiv);
  scrollBottom();

  let textBuf = "";
  const toolCallsAccum = [];

  abortController = new AbortController();
  timeoutId = setTimeout(showTimeoutWarning, 120000);

  try {
    const selectedModel = modelSelect.value;
    const OPENAI_MODELS = new Set(["gpt-4o","gpt-4o-mini","gpt-4.1","gpt-4.1-mini","o4-mini","o3-mini"]);
    const provider = OPENAI_MODELS.has(selectedModel) ? "openai" : "claude";

    const resp = await fetch(`/api/conversations/${activeConvId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: fullContent, model: selectedModel, provider }),
      signal: abortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        try {
          const obj = JSON.parse(raw);

          if (obj.text !== undefined) {
            textBuf += obj.text;
            bubble.innerHTML = fmt(textBuf);
            bubble.classList.add("cursor");
            scrollBottom();

          } else if (obj.name !== undefined) {
            // tool use — insert before bubble
            const tb = document.createElement("div");
            tb.innerHTML = toolBlock({ name: obj.name, input: obj.input });
            assistantDiv.insertBefore(tb.firstElementChild, bubble);
            toolCallsAccum.push({ name: obj.name, input: obj.input });
            scrollBottom();

          } else if (obj.cost_usd !== undefined) {
            bubble.classList.remove("cursor");
            if (obj.cost_usd) {
              assistantDiv.appendChild(makeMeta(obj.cost_usd, obj.input_tokens, obj.output_tokens));
            }
            if (obj.title) {
              const convs = conversations[activePersona] || [];
              const conv = convs.find(c => c.id === activeConvId);
              if (conv) conv.title = obj.title;
              renderSidebar();
            }

          } else if (obj.detail) {
            bubble.classList.remove("cursor");
            bubble.innerHTML = errorDisplay(obj.detail);
            bubble.dataset.errorDetail = obj.detail;
          }
        } catch (_) {}
      }
    }

  } catch (err) {
    bubble.classList.remove("cursor");
    if (err.name === "AbortError") {
      bubble.innerHTML = `<span class="msg-error-simple">Cancelled.</span>`;
    } else {
      bubble.innerHTML = errorDisplay(err.message);
      bubble.dataset.errorDetail = err.message;
    }
  } finally {
    clearTimeout(timeoutId);
    timeoutId = null;
    if (timeoutWarningEl) { timeoutWarningEl.remove(); timeoutWarningEl = null; }
    abortController = null;
    bubble.classList.remove("cursor");
    streaming = false;
    btnSend.disabled = false;
    btnSend.textContent = "Send";
  }
}

function makeBubble(role, htmlContent) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="msg-bubble">${htmlContent}</div>`;
  return div;
}

// ── Cost display ──────────────────────────────────────────────────────────────
const COST_MODES = ["off", "nzd", "tokens"];
const NZD_RATE   = 1.8;

function formatCost(costUsd, inputTokens, outputTokens) {
  if (costMode === "nzd")    return `NZ$${(costUsd * NZD_RATE).toFixed(4)}`;
  if (costMode === "tokens") return `↑${inputTokens} ↓${outputTokens}`;
  return "";
}

function makeMeta(costUsd, inputTokens, outputTokens) {
  const el = document.createElement("span");
  el.className = "msg-meta";
  el.dataset.cost = costUsd;
  el.dataset.inputTokens  = inputTokens  || 0;
  el.dataset.outputTokens = outputTokens || 0;
  el.style.display = costMode === "off" ? "none" : "";
  el.textContent = formatCost(costUsd, inputTokens || 0, outputTokens || 0);
  return el;
}

function updateCostDisplay() {
  messagesEl.querySelectorAll(".msg-meta[data-cost]").forEach(el => {
    el.style.display = costMode === "off" ? "none" : "";
    if (costMode !== "off")
      el.textContent = formatCost(
        parseFloat(el.dataset.cost),
        parseInt(el.dataset.inputTokens  || 0),
        parseInt(el.dataset.outputTokens || 0),
      );
  });
}

// ── Settings menu toggle ──────────────────────────────────────────────────────
btnSettings.addEventListener("click", (e) => {
  e.stopPropagation();
  const isOpen = settingsMenu.style.display !== "none";
  settingsMenu.style.display = isOpen ? "none" : "flex";
});

// Close settings menu on outside click
document.addEventListener("click", (e) => {
  if (!btnSettings.contains(e.target) && !settingsMenu.contains(e.target)) {
    settingsMenu.style.display = "none";
  }
});

// ── Settings handlers ──────────────────────────────────────────────────────────
costToggle.addEventListener("change", () => {
  costMode = costToggle.checked ? "nzd" : "off";
  updateCostDisplay();
});

styleToggle.addEventListener("change", () => {
  messageStyle = styleToggle.checked ? "inline" : "bubble";
  messagesEl.classList.toggle("inline", messageStyle === "inline");
});

errorModeSelect.addEventListener("change", () => {
  errorMode = errorModeSelect.value;
  updateErrorBubbles();
});

modelSelect.addEventListener("change", () => {
  const OPENAI_MODELS = new Set(["gpt-4o","gpt-4o-mini","gpt-4.1","gpt-4.1-mini","o4-mini","o3-mini"]);
  const provider = OPENAI_MODELS.has(modelSelect.value) ? "OpenAI" : "Claude";
  if (providerBadge) providerBadge.textContent = provider;

  if (!activeConvId) return;
  const opt = modelSelect.options[modelSelect.selectedIndex];
  const group = opt.closest("optgroup")?.label || "";
  const label = group ? `${group} / ${opt.text}` : opt.text;
  const el = document.createElement("div");
  el.innerHTML = systemLine(`Model switched to ${label}`);
  messagesEl.appendChild(el.firstElementChild);
  scrollBottom();
});

// ── New conversation button ───────────────────────────────────────────────────
btnNewConv.addEventListener("click", () => {
  if (activePersona) newConversation(activePersona);
});

// ── Input handling ────────────────────────────────────────────────────────────
function autoResize() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + "px";
}

inputEl.addEventListener("input", autoResize);
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const msg = inputEl.value.trim();
    const hasFiles = pendingFiles.some(f => f.status === "inline" || f.status === "drive");
    if ((msg || hasFiles) && activeConvId) sendMessage(msg);
  }
});
btnSend.addEventListener("click", () => {
  const msg = inputEl.value.trim();
  const hasFiles = pendingFiles.some(f => f.status === "inline" || f.status === "drive");
  if ((msg || hasFiles) && activeConvId) sendMessage(msg);
});

// ── PWA Install (Ctrl+I) ───────────────────────────────────────────────────
let deferredPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
});
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'i') {
    e.preventDefault();
    if (deferredPrompt) {
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(({ outcome }) => {
        console.log(`PWA install: ${outcome}`);
        deferredPrompt = null;
      });
    } else {
      console.log('PWA already installed or install prompt not available');
    }
  }
});

// ── iOS Install Banner ────────────────────────────────────────────────────────
(function () {
  const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isStandalone = window.navigator.standalone === true;
  const dismissed = localStorage.getItem('ios-install-dismissed');
  if (!isIos || isStandalone || dismissed) return;

  const banner = document.createElement('div');
  banner.id = 'ios-install-banner';
  banner.innerHTML = `
    <span class="ios-banner-icon">⬆</span>
    <span class="ios-banner-text">
      <strong>Add to Home Screen</strong>
      Tap Share then "Add to Home Screen"
    </span>
    <button class="ios-banner-dismiss" aria-label="Dismiss">✕</button>
  `;
  document.body.appendChild(banner);

  banner.querySelector('.ios-banner-dismiss').addEventListener('click', () => {
    localStorage.setItem('ios-install-dismissed', '1');
    banner.remove();
  });
})();

// ── Helpers ───────────────────────────────────────────────────────────────────
async function api(method, path, body = null) {
  const opts = { method, headers: {} };
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  return res.json();
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ── File attachments ──────────────────────────────────────────────────────────

function renderChips() {
  fileChips.innerHTML = pendingFiles.map(f => {
    const icon = f.status === "loading" ? "⏳"
               : f.status === "inline"  ? "📄"
               : f.status === "drive"   ? "☁️"
               : "❌";
    const label = f.status === "drive"
      ? `<a href="${escHtml(f.url)}" target="_blank" class="file-chip-name">${escHtml(f.filename)}</a>`
      : `<span class="file-chip-name">${escHtml(f.filename)}</span>`;
    return `<div class="file-chip ${f.status}" data-id="${f.id}">
      <span>${icon}</span>${label}
      <button class="file-chip-remove" data-id="${f.id}" title="Remove">×</button>
    </div>`;
  }).join("");
  fileChips.querySelectorAll(".file-chip-remove").forEach(btn =>
    btn.addEventListener("click", () => {
      pendingFiles = pendingFiles.filter(f => f.id !== +btn.dataset.id);
      renderChips();
    })
  );
}

async function uploadFile(file) {
  const id = ++fileIdCounter;
  pendingFiles.push({ id, filename: file.name, status: "loading" });
  renderChips();

  const form = new FormData();
  form.append("file", file);
  form.append("persona", activePersona || "assistant");

  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const entry = pendingFiles.find(f => f.id === id);
    if (!entry) return;
    Object.assign(entry, data.mode === "inline"
      ? { status: "inline", content: data.content }
      : { status: "drive", file_id: data.file_id, url: data.url }
    );
  } catch (err) {
    const entry = pendingFiles.find(f => f.id === id);
    if (entry) { entry.status = "error"; entry.error = err.message; }
  }
  renderChips();
}

function buildFilePrefix() {
  const inline = pendingFiles.filter(f => f.status === "inline");
  const drive  = pendingFiles.filter(f => f.status === "drive");
  let prefix = "";
  if (inline.length) {
    prefix += inline.map(f =>
      `--- ${f.filename} ---\n${f.content}\n--- end ${f.filename} ---`
    ).join("\n\n") + "\n\n";
  }
  if (drive.length) {
    prefix += "Files uploaded to Google Drive (use the drive_read tool to access):\n"
      + drive.map(f => `- ${f.filename} (file_id: ${f.file_id})`).join("\n")
      + "\n\n";
  }
  return prefix;
}

// Drag-and-drop
let dragCounter = 0;
inputArea.addEventListener("dragenter", e => { e.preventDefault(); dragCounter++; inputArea.classList.add("drag-over"); });
inputArea.addEventListener("dragleave", () => { if (--dragCounter <= 0) { dragCounter = 0; inputArea.classList.remove("drag-over"); } });
inputArea.addEventListener("dragover",  e => e.preventDefault());
inputArea.addEventListener("drop", e => {
  e.preventDefault();
  dragCounter = 0;
  inputArea.classList.remove("drag-over");
  [...e.dataTransfer.files].forEach(uploadFile);
});

btnAttach.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  [...fileInput.files].forEach(uploadFile);
  fileInput.value = "";
});

// ── Mobile: keep input above keyboard via visualViewport ──────────────────────
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    document.getElementById("app").style.height = window.visualViewport.height + "px";
  });
}

init();
