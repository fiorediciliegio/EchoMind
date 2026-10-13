const AUTH_KEY = "echomind.auth";

const state = {
  sessionId: null,
  mode: null,
  noticeVersion: null,
  privateHistory: [],
  contactReportId: null,
  contactStatus: "NONE",
  sending: false,
  profile: null,
  modelName: "mock"
};

const els = {
  serviceState: document.querySelector("#serviceState"),
  modelState: document.querySelector("#modelState"),
  activeAccount: document.querySelector("#activeAccount"),
  switchAccount: document.querySelector("#switchAccount"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  newSession: document.querySelector("#newSession"),
  sessionBadge: document.querySelector("#sessionBadge"),
  studentGreeting: document.querySelector("#studentGreeting"),
  charCount: document.querySelector("#charCount"),
  modeDialog: document.querySelector("#modeDialog"),
  modeLabel: document.querySelector("#modeLabel"),
  providerNotice: document.querySelector("#providerNotice"),
  assistedNotice: document.querySelector("#assistedNotice"),
  privateNotice: document.querySelector("#privateNotice"),
  modeError: document.querySelector("#modeError"),
  chooseAssisted: document.querySelector("#chooseAssisted"),
  choosePrivate: document.querySelector("#choosePrivate"),
  historyList: document.querySelector("#historyList"),
  refreshHistory: document.querySelector("#refreshHistory"),
  counselorReplies: document.querySelector("#counselorReplies"),
  contactPanel: document.querySelector("#contactPanel"),
  contactState: document.querySelector("#contactState"),
  openContactForm: document.querySelector("#openContactForm"),
  contactForm: document.querySelector("#contactForm"),
  contactPhone: document.querySelector("#contactPhone"),
  contactConsent: document.querySelector("#contactConsent"),
  closeContactForm: document.querySelector("#closeContactForm"),
  cancelContactRequest: document.querySelector("#cancelContactRequest"),
  contactError: document.querySelector("#contactError")
};

function readAuth() {
  try {
    return JSON.parse(sessionStorage.getItem(AUTH_KEY) || "null");
  } catch {
    return null;
  }
}

function clearAuth() {
  sessionStorage.removeItem(AUTH_KEY);
}

function authHeader() {
  const auth = readAuth();
  if (!auth?.token) {
    window.location.replace("/");
    return "";
  }
  return `Basic ${auth.token}`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: authHeader() };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response;
}

function setPill(el, text, tone = "ok") {
  el.textContent = text;
  el.className = `pill ${tone}`;
}

function isAdmin(profile) {
  return profile.roles?.some((role) => role.authority === "ROLE_ADMIN");
}

function displayModel(model) {
  return model || "";
}

function studentDisplayName(profile) {
  return profile.displayName === "Demo Student" ? "Student" : (profile.displayName || profile.username || "Student");
}

function greetingForNow() {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 11) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function updateCharCount() {
  els.charCount.textContent = `${els.messageInput.value.length} / 1000`;
}

async function checkHealth() {
  try {
    const response = await fetch("/actuator/health");
    const body = await response.json();
    setPill(els.serviceState, body.status === "UP" ? "服务正常" : `服务 ${body.status}`, body.status === "UP" ? "ok" : "danger");
  } catch {
    setPill(els.serviceState, "服务 DOWN", "danger");
  }
}

async function loadProfile() {
  try {
    const response = await api("/api/profile");
    const profile = await response.json();
    if (isAdmin(profile)) {
      window.location.replace("/admin.html");
      return null;
    }
    state.profile = profile;
    const displayName = studentDisplayName(profile);
    els.activeAccount.textContent = displayName;
    els.studentGreeting.textContent = `${greetingForNow()}，${displayName}`;
    return profile;
  } catch {
    clearAuth();
    window.location.replace("/");
    return null;
  }
}

async function loadAgentStatus() {
  const response = await api("/api/agent/status");
  const status = await response.json();
  state.modelName = status.model || "mock";
  if (status.realModelEnabled) {
    setPill(els.modelState, `${status.provider} / ${displayModel(state.modelName)}`, "ok");
  } else {
    setPill(els.modelState, "mock 演示", "warn");
  }
}

function clearWelcome() {
  const empty = els.messages.querySelector(".empty");
  if (empty) empty.remove();
}

function addMessage(role, content) {
  clearWelcome();
  const row = document.createElement("article");
  row.className = `message ${role}`;
  row.innerHTML = `
    <div class="message-role">${role === "user" ? "我" : "EchoMind"}</div>
    <div class="bubble"></div>
  `;
  row.querySelector(".bubble").textContent = content;
  els.messages.append(row);
  els.messages.scrollTop = els.messages.scrollHeight;
  return row.querySelector(".bubble");
}

function showModeDialog() {
  state.mode = null;
  state.sessionId = null;
  state.privateHistory = [];
  showContactOffer(null);
  els.messageInput.disabled = true;
  els.sendButton.disabled = true;
  els.modeLabel.textContent = "未选择模式";
  els.modeError.textContent = "";
  els.modeDialog.hidden = false;
}

function activateMode(mode) {
  state.mode = mode;
  state.privateHistory = [];
  if (mode === "PRIVATE") showContactOffer(null);
  els.modeLabel.textContent = mode === "PRIVATE" ? "仅 AI · 不留存" : "留存 · 可获人工回复";
  els.messageInput.disabled = false;
  els.sendButton.disabled = false;
  els.modeDialog.hidden = true;
  els.messageInput.focus();
}

function showContactOffer(reportId, status = "NONE") {
  state.contactReportId = reportId;
  state.contactStatus = status;
  els.contactPanel.hidden = !reportId;
  els.contactForm.hidden = true;
  els.contactError.textContent = "";
  els.contactPhone.value = "";
  els.contactConsent.checked = false;
  const open = status === "PENDING" || status === "CLAIMED";
  els.openContactForm.hidden = !reportId || open || ["CONTACTED", "UNREACHABLE", "CLOSED"].includes(status);
  els.cancelContactRequest.hidden = !open;
  els.contactState.textContent = ({
    PENDING: "联系请求已提交，等待工作人员认领。这不是实时危机救援。",
    CLAIMED: "工作人员已认领，可能会主动联系你；撤回无法取消已发生的身份查看。",
    CONTACTED: "工作人员已记录联系结果。",
    UNREACHABLE: "工作人员记录为暂未联系上；如仍需帮助，请重新发起对话。",
    CLOSED: "该联系请求已结案。",
    CANCELLED: "你已撤回请求；之前已经发生的身份查看无法撤销。"
  })[status] || "你可以主动留下联系方式，由工作人员后续联系你。这不是实时危机救援。";
}

async function submitContactRequest(event) {
  event.preventDefault();
  if (!state.contactReportId || state.mode !== "ASSISTED") return;
  els.contactError.textContent = "";
  try {
    const response = await api(`/api/student/reports/${state.contactReportId}/contact-request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: els.contactPhone.value, consentToContact: els.contactConsent.checked })
    });
    const saved = await response.json();
    showContactOffer(saved.reportId, saved.status);
  } catch (error) {
    els.contactError.textContent = `提交失败：${error.message}`;
  }
}

async function cancelContactRequest() {
  if (!state.contactReportId) return;
  els.contactError.textContent = "";
  try {
    const response = await api(`/api/student/reports/${state.contactReportId}/contact-request`, { method: "DELETE" });
    const saved = await response.json();
    showContactOffer(saved.reportId, saved.status);
  } catch (error) {
    els.contactError.textContent = `撤回失败：${error.message}`;
  }
}

async function loadNotice() {
  const response = await api("/api/privacy/notice");
  const notice = await response.json();
  state.noticeVersion = notice.version;
  els.providerNotice.textContent = `两种模式都会把本次输入发送给 ${notice.provider} 模型处理；模型提供方的数据处理遵循其自身规则。`;
  els.assistedNotice.textContent = notice.assisted;
  els.privateNotice.textContent = notice.private;
}

function chooseMode(mode) {
  els.modeError.textContent = "";
  if (!state.noticeVersion) {
    els.modeError.textContent = "隐私告知尚未加载，请稍后再试";
    return;
  }
  activateMode(mode);
}

const reviewLabels = { UNREAD: "有待查看回复", WAITING: "等待管理员回复", READ: "已查看回复", NONE: "尚无心理报告" };

async function loadHistory() {
  try {
    const response = await api("/api/student/sessions");
    const sessions = await response.json();
    els.historyList.innerHTML = "";
    if (!sessions.length) {
      els.historyList.textContent = "还没有留存会话";
      return;
    }
    for (const session of sessions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      const title = document.createElement("strong");
      title.textContent = session.title || "新会话";
      const meta = document.createElement("span");
      meta.textContent = `${reviewLabels[session.reviewStatus] || ""} · ${new Date(session.updatedAt).toLocaleString()}`;
      button.append(title, meta);
      button.addEventListener("click", () => openHistory(session.sessionId));
      els.historyList.append(button);
    }
  } catch (error) {
    els.historyList.textContent = `历史读取失败：${error.message}`;
  }
}

async function openHistory(sessionId) {
  if (state.sending) return;
  try {
    const response = await api(`/api/student/sessions/${encodeURIComponent(sessionId)}`);
    const detail = await response.json();
    state.sessionId = sessionId;
    activateMode("ASSISTED");
    els.messages.innerHTML = "";
    for (const message of detail.messages) addMessage(message.role.toLowerCase(), message.content);
    const latestOffer = detail.contactOffers?.at(-1);
    showContactOffer(latestOffer?.reportId || null, latestOffer?.status || "NONE");
    if (!detail.messages.length) resetMessages();
    els.counselorReplies.innerHTML = "";
    els.counselorReplies.hidden = !detail.replies.length;
    for (const reply of detail.replies) {
      const article = document.createElement("article");
      const heading = document.createElement("strong");
      heading.textContent = `心理中心回复 · ${new Date(reply.createdAt).toLocaleString()}`;
      const body = document.createElement("p");
      body.textContent = reply.content;
      article.append(heading, body);
      els.counselorReplies.append(article);
    }
    setPill(els.sessionBadge, "READY");
    await loadHistory();
  } catch (error) {
    els.historyList.textContent = `会话读取失败：${error.message}`;
  }
}

function parseSse(buffer, onEvent) {
  const parts = buffer.split("\n\n");
  const rest = parts.pop();
  for (const part of parts) {
    const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
    if (!dataLine) continue;
    onEvent(JSON.parse(dataLine.slice(6)));
  }
  return rest;
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.sending || !state.mode) return;
  const message = els.messageInput.value.trim();
  if (!message) return;
  state.sending = true;
  els.sendButton.disabled = true;
  setPill(els.sessionBadge, "THINKING", "warn");
  els.messageInput.value = "";
  updateCharCount();
  addMessage("user", message);
  const assistant = addMessage("assistant", "");
  let raw = "";

  try {
    const privateMode = state.mode === "PRIVATE";
    if (!privateMode && !state.sessionId) {
      const started = await api("/api/student/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ noticeVersion: state.noticeVersion })
      });
      state.sessionId = (await started.json()).sessionId;
    }
    const response = await api(privateMode ? "/api/chat/private-stream" : "/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(privateMode
        ? { noticeVersion: state.noticeVersion, message, history: state.privateHistory.slice(-20) }
        : { sessionId: state.sessionId, message })
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamFailed = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = parseSse(buffer, (eventData) => {
        if (eventData.type === "meta" && !privateMode) state.sessionId = eventData.sessionId;
        if (eventData.type === "token") {
          raw += eventData.content || "";
          assistant.textContent = raw;
          els.messages.scrollTop = els.messages.scrollHeight;
        }
        if (eventData.type === "error") {
          streamFailed = true;
          if (!raw) assistant.textContent = eventData.message || "MCP 工具调用失败";
          setPill(els.sessionBadge, "ERROR", "danger");
        }
        if (eventData.type === "done" && !privateMode && eventData.contactOffer && eventData.reportId) {
          showContactOffer(eventData.reportId);
        }
      });
    }
    if (!streamFailed) {
      if (privateMode && raw) state.privateHistory.push({ role: "user", content: message }, { role: "assistant", content: raw.slice(-1000) });
      else loadHistory();
      setPill(els.sessionBadge, "DONE", "ok");
    }
  } catch (error) {
    assistant.textContent = `发送失败：${error.message}`;
    setPill(els.sessionBadge, "ERROR", "danger");
  } finally {
    state.sending = false;
    els.sendButton.disabled = false;
  }
}

function resetMessages() {
  els.messages.innerHTML = `
    <div class="empty student-welcome">
      <span class="welcome-kicker">A FRESH START</span>
      <strong>新会话已经准备好</strong>
      <p>不用延续刚才的话题，你可以从此刻最想说的一件事重新开始。</p>
    </div>
  `;
  els.messageInput.value = "";
  updateCharCount();
  setPill(els.sessionBadge, "READY");
}

function resetSession() {
  if (state.sending) return;
  resetMessages();
  els.counselorReplies.hidden = true;
  els.counselorReplies.innerHTML = "";
  showContactOffer(null);
  showModeDialog();
}

function logout() {
  clearAuth();
  window.location.assign("/");
}

document.querySelectorAll("[data-quick]").forEach((button) => {
  button.addEventListener("click", () => {
    els.messageInput.value = button.dataset.quick;
    updateCharCount();
    els.messageInput.focus();
  });
});
els.messageInput.addEventListener("input", updateCharCount);
els.chatForm.addEventListener("submit", sendMessage);
els.newSession.addEventListener("click", resetSession);
els.chooseAssisted.addEventListener("click", () => chooseMode("ASSISTED"));
els.choosePrivate.addEventListener("click", () => chooseMode("PRIVATE"));
els.refreshHistory.addEventListener("click", loadHistory);
els.openContactForm.addEventListener("click", () => {
  els.contactForm.hidden = false;
  els.openContactForm.hidden = true;
  els.contactPhone.focus();
});
els.closeContactForm.addEventListener("click", () => {
  els.contactForm.hidden = true;
  els.openContactForm.hidden = false;
  els.contactError.textContent = "";
});
els.contactForm.addEventListener("submit", submitContactRequest);
els.cancelContactRequest.addEventListener("click", cancelContactRequest);
els.switchAccount.addEventListener("click", logout);

checkHealth();
loadProfile().then((profile) => {
  if (profile) {
    loadAgentStatus();
    loadHistory();
    loadNotice().catch((error) => { els.modeError.textContent = `隐私告知加载失败：${error.message}`; });
  }
});
updateCharCount();
showModeDialog();
