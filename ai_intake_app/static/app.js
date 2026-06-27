let currentSessionId = null;
let currentSession = null;
let aiBusy = false;
let activeAdvisorReportTab = null;
let progressTimers = [];
window.__abilityIntakeLoaded = true;
const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8787" : "";
const ADMIN_TOKEN_KEY = "ability_intake_admin_token";
const MANUAL_TASK_LABELS = {
  material_structuring: "材料规整",
  material_organization: "材料分析",
};

const MANUAL_TASK_HINTS = {
  material_structuring: "第一步：生成材料规整输入包。网页版返回 JSON 后粘回保存，系统会保存为材料规整底稿。",
  material_organization: "第二步：建议先保存材料规整结果，再生成材料分析输入包。保存后系统会进入 materials_organized 状态。",
};

const ZHANGLU_DELIVERABLE_ID = "zhanglu-business-preference-constraints";
const ZHANGLU_PREFERENCE_RESULT_LINK = "/static/deliverables/zhanglu-business-preference-result.html";
const STANDARD_DELIVERABLE_SLOTS = [
  {
    key: "complete",
    label: "能力校准工作台完整版本",
    description: "保留完整证据链、判断过程和会谈校准材料。",
  },
  {
    key: "markdown",
    label: "下载完整版本 Markdown",
    description: "用于归档、二次编辑和交付物版本管理。",
  },
  {
    key: "visual",
    label: "可视化版本",
    description: "适合会中共创和用户阅读的视觉化工作台。",
  },
  {
    key: "preference",
    label: "商业方向偏好与约束表",
    description: "进入第二步商业方向判断前，收集用户偏好、边界和风险承受度。",
  },
];
const BUSINESS_PREFERENCE_LINK = "/static/deliverables/zhanglu-business-preference-constraints.html";
const BOUND_DELIVERABLES = [
  {
    matchName: "张麓",
    eyebrow: "张麓 · 第一步交付物",
    title: "第一环节标准交付物",
    slots: {
      complete: { href: "/static/deliverables/zhanglu-co-creation-workbench.html", target: "_blank" },
      markdown: { href: "/static/deliverables/zhanglu-co-creation-materials.md", download: true },
      visual: { href: "/static/deliverables/zhanglu-ability-assets-final-workbench.html", target: "_blank" },
      preference: { href: BUSINESS_PREFERENCE_LINK, target: "_blank" },
    },
  },
  {
    matchName: "梁焱",
    eyebrow: "梁焱 · 第一步交付物",
    title: "第一环节标准交付物",
    slots: {
      complete: { href: "/static/deliverables/liangyan-ability-asset-hypothesis-evidence-workbench.html", target: "_blank" },
      markdown: { href: "/static/deliverables/梁焱-第一步能力资产假设校准材料.md", download: true },
      visual: { href: "/static/deliverables/梁焱-第一步能力资产假设校准工作台.html", target: "_blank" },
      preference: { href: BUSINESS_PREFERENCE_LINK, target: "_blank" },
    },
  },
];

const el = (id) => document.getElementById(id);

function showBusy(text) {
  el("busyText").textContent = text;
  el("busyDialog").showModal();
}

function hideBusy() {
  el("busyDialog").close();
}

function showFeedback(message, type = "info") {
  const box = el("saveFeedback");
  if (!box) return;
  box.className = `save-feedback ${type}`;
  box.textContent = message;
}

function setNextStep(message, type = "info") {
  const box = el("nextStep");
  if (!box) return;
  box.className = `next-step ${type}`;
  box.textContent = message;
}

function setManualSaveStatus(message, type = "info") {
  const box = el("manualSaveStatus");
  if (!box) return;
  box.className = `manual-save-status ${type}`;
  box.textContent = message || "";
}

function setAiBusy(isBusy) {
  aiBusy = isBusy;
  ["organizeBtn", "askBtn", "reportBtn"].forEach((id) => {
    const button = el(id);
    if (button) button.disabled = isBusy;
  });
}

function resetProgress() {
  progressTimers.forEach((timer) => clearTimeout(timer));
  progressTimers = [];
}

function showOrganizeProgress() {
  resetProgress();
  const box = el("organizeProgress");
  box.hidden = false;
  box.className = "progress-card active";
  setProgress(8, "正在做本地清洗", "本地程序正在过滤网页噪音、重复行和格式残留。", "progressStepLocal");
  progressTimers.push(setTimeout(() => setProgress(28, "AI 正在规整材料", "第一轮模型调用：完整整理材料类型、事实、项目卡片和认知表达。", "progressStepStruct"), 900));
  progressTimers.push(setTimeout(() => setProgress(58, "AI 正在分析材料", "第二轮模型调用：识别项目线索、能力证据、平台依赖和追问地图。", "progressStepAnalyze"), 4500));
  progressTimers.push(setTimeout(() => setProgress(78, "正在等待模型返回", "深度整理会保留更多信息，材料较多时会多等一会儿。", "progressStepAnalyze"), 11000));
}

function setProgress(percent, title, detail, activeStepId) {
  el("progressTitle").textContent = title;
  el("progressPercent").textContent = `${percent}%`;
  el("progressFill").style.width = `${percent}%`;
  el("progressDetail").textContent = detail;
  ["progressStepLocal", "progressStepStruct", "progressStepAnalyze", "progressStepSave"].forEach((id) => {
    const step = el(id);
    step.className = id === activeStepId ? "active" : "";
  });
}

function completeOrganizeProgress() {
  resetProgress();
  setProgress(100, "材料整理完成", "已生成材料规整底稿、材料分析和材料就绪判断。", "progressStepSave");
  el("organizeProgress").className = "progress-card done";
}

function failOrganizeProgress(message) {
  resetProgress();
  el("organizeProgress").hidden = false;
  el("organizeProgress").className = "progress-card failed";
  el("progressTitle").textContent = "材料整理失败";
  el("progressPercent").textContent = "未完成";
  el("progressDetail").textContent = message;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = localStorage.getItem(ADMIN_TOKEN_KEY);
  if (token) headers.set("X-Admin-Token", token);
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && data.auth_required) {
      showAdminGate("顾问台已开启保护，请先输入访问口令。");
    }
    throw new Error(data.error || `请求失败：${res.status}`);
  }
  return data;
}

function showAdminGate(message = "") {
  const gate = el("adminGate");
  if (!gate) return;
  gate.hidden = false;
  el("adminGateMessage").textContent = message;
}

function hideAdminGate() {
  const gate = el("adminGate");
  if (!gate) return;
  gate.hidden = true;
  el("adminGateMessage").textContent = "";
}

async function loadAuthStatus() {
  const data = await api("/api/auth/status");
  if (data.enabled && !data.authenticated) {
    showAdminGate("请输入顾问台访问口令。");
    return false;
  }
  hideAdminGate();
  return true;
}

async function loginAdmin(e) {
  e.preventDefault();
  const formEl = e.currentTarget;
  const password = formEl.elements.password.value.trim();
  if (!password) {
    el("adminGateMessage").textContent = "请输入访问口令。";
    return;
  }
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    localStorage.setItem(ADMIN_TOKEN_KEY, data.token || "");
    formEl.reset();
    hideAdminGate();
    await initAdvisorWorkspace();
  } catch (err) {
    el("adminGateMessage").textContent = err.message;
  }
}

function formatDate(s) {
  if (!s) return "";
  return s.replace("T", " ").slice(0, 16);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadHealth() {
  try {
    const data = await api("/api/health");
    el("apiStatus").textContent = data.has_api_key ? `模型：${data.model}` : "未设置模型 Key";
    el("apiStatus").className = data.has_api_key ? "status ready" : "status warn";
    const baseInput = el("apiKeyForm")?.elements.base_url;
    if (baseInput && data.base_url) baseInput.value = data.base_url;
  } catch (e) {
    el("apiStatus").textContent = "服务异常";
    el("apiStatus").className = "status error";
  }
}

async function saveApiKey(e) {
  e.preventDefault();
  const formEl = e.currentTarget;
  const apiKey = formEl.elements.api_key.value.trim();
  const model = formEl.elements.model.value.trim() || "gpt-5.5";
  const baseUrl = formEl.elements.base_url.value.trim() || "https://api.openai.com/v1";
  if (!apiKey) {
    showFeedback("请先填写模型 API Key。", "error");
    return;
  }
  showBusy("正在设置模型接口...");
  try {
    const data = await api("/api/settings/openai-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey, model, base_url: baseUrl }),
    });
    formEl.elements.api_key.value = "";
    formEl.elements.model.value = data.model;
    formEl.elements.base_url.value = data.base_url;
    el("apiStatus").textContent = `模型：${data.model}`;
    el("apiStatus").className = "status ready";
    showFeedback("模型接口已设置到当前本地服务进程，可以继续点击“整理材料”或进入用户访谈页。", "success");
    setNextStep("模型接口已就绪。若材料已整理，用户可以进入会前访谈页开始 AI 追问。", "success");
  } catch (err) {
    showFeedback(`设置模型接口失败：${err.message}`, "error");
  } finally {
    hideBusy();
  }
}

async function loadSessions() {
  const data = await api("/api/sessions");
  const list = el("sessionList");
  list.innerHTML = "";
  if (!data.sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "还没有诊断档案。";
    list.appendChild(empty);
  }
  data.sessions.forEach((s) => list.appendChild(renderSessionCard(s, "active")));
  renderDeletedSessions(data.deleted_sessions || []);
}

function renderDeletedSessions(sessions) {
  const list = el("deletedSessionList");
  if (!list) return;
  list.innerHTML = "";
  if (!sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list compact";
    empty.textContent = "暂无已删除档案。";
    list.appendChild(empty);
    return;
  }
  sessions.forEach((s) => list.appendChild(renderSessionCard(s, "deleted")));
}

function renderSessionCard(session, mode) {
  const card = document.createElement("div");
  const isDeleted = mode === "deleted";
  card.className = `session-card ${session.id === currentSessionId ? "active" : ""} ${isDeleted ? "deleted" : ""}`;
  card.innerHTML = `
    <div class="session-card-main">
      <strong>${escapeHtml(session.client_name)}</strong>
      <span>${escapeHtml(session.status)} · ${formatDate(isDeleted ? session.deleted_at : session.updated_at)}</span>
    </div>
    <button type="button" class="session-card-action ${isDeleted ? "restore" : "delete"}">
      ${isDeleted ? "恢复" : "删除"}
    </button>
  `;
  if (!isDeleted) {
    card.addEventListener("click", () => selectSession(session.id));
    card.querySelector(".session-card-action").addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(session);
    });
  } else {
    card.querySelector(".session-card-action").addEventListener("click", () => restoreSession(session));
  }
  return card;
}

async function deleteSession(session) {
  if (!confirm(`确认删除「${session.client_name}」这个档案？删除后可以从“已删除档案”里恢复。`)) return;
  try {
    await api(`/api/sessions/${session.id}`, { method: "DELETE" });
    if (currentSessionId === session.id) {
      clearCurrent();
    } else {
      await loadSessions();
    }
    showFeedback(`已删除档案：${session.client_name}。可以在左侧“已删除档案”中恢复。`, "success");
  } catch (err) {
    showFeedback(`删除失败：${err.message}`, "error");
  }
}

async function restoreSession(session) {
  try {
    await api(`/api/sessions/${session.id}/restore`, { method: "POST" });
    await loadSessions();
    await selectSession(session.id);
    showFeedback(`已恢复档案：${session.client_name}。`, "success");
  } catch (err) {
    showFeedback(`恢复失败：${err.message}`, "error");
  }
}

async function loadDeliverableSubmissions() {
  if (!shouldShowZhangluDeliverables()) return;
  const list = el("deliverableSubmissionsList");
  if (!list) return;
  list.textContent = "正在检查提交结果...";
  try {
    const data = await api(`/api/deliverable-submissions?deliverable_id=${ZHANGLU_DELIVERABLE_ID}&limit=1`);
    const submissions = data.submissions || [];
    if (!submissions.length) {
      list.textContent = "还没有收到用户提交。用户填写后需要点击“提交保存给顾问”。";
      return;
    }
    list.innerHTML = submissions.slice(0, 1).map((item) => `
      <article class="deliverable-submission-card">
        <strong>${escapeHtml(item.client_name || "未命名用户")} · ${escapeHtml(item.title || "商业方向偏好与约束表")}</strong>
        <span>最新提交 · ${formatDate(item.created_at)} · 提交 ID ${item.id}</span>
        ${renderCommercialPreferenceSubmissionSummary(item)}
        <div class="deliverable-submission-actions">
          <a class="button-link" href="${ZHANGLU_PREFERENCE_RESULT_LINK}" target="_blank" rel="noopener">查看可视化结果</a>
          <button type="button" data-copy-submission="${item.id}">复制完整 Markdown</button>
        </div>
      </article>
    `).join("");
    list.querySelectorAll("[data-copy-submission]").forEach((button) => {
      button.addEventListener("click", () => {
        const item = submissions.find((submission) => String(submission.id) === button.dataset.copySubmission);
        if (item) copySubmissionMarkdown(item.markdown || "");
      });
    });
  } catch (err) {
    list.textContent = `读取提交结果失败：${err.message}`;
  }
}

function markdownPreview(markdown) {
  const text = String(markdown || "").trim();
  if (!text) return "提交内容为空。";
  return text.length > 1200 ? `${text.slice(0, 1200)}\n\n...` : text;
}

function parseCommercialPreferenceMarkdown(markdown) {
  const lines = String(markdown || "").split(/\r?\n/).filter((line) => line.trim().startsWith("|"));
  if (lines.length < 4) return null;
  const splitRow = (line) => line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
  const header = splitRow(lines[0]).slice(1);
  const pref = splitRow(lines.find((line) => line.includes("| 偏好 |")) || "").slice(1);
  const cons = splitRow(lines.find((line) => line.includes("| 约束 |")) || "").slice(1);
  if (!header.length) return null;
  return header.map((title, index) => ({
    title,
    preference: pref[index] || "未填写",
    constraint: cons[index] || "未填写",
  }));
}

function compactCommercialText(text, max = 145) {
  const clean = String(text || "")
    .replace(/<br\s*\/?>/g, "；")
    .replace(/\s+/g, " ")
    .trim();
  if (!clean || clean === "未填写") return "未填写";
  return clean.length > max ? `${clean.slice(0, max)}...` : clean;
}

function renderCommercialPreferencePreview(markdown) {
  const items = parseCommercialPreferenceMarkdown(markdown);
  if (!items) {
    return `<pre>${escapeHtml(markdownPreview(markdown))}</pre>`;
  }
  const filled = items.filter((item) => item.preference !== "未填写" || item.constraint !== "未填写").length;
  const signalItems = items
    .filter((item) => item.preference !== "未填写")
    .slice(0, 4)
    .map((item) => `<li><b>${escapeHtml(item.title)}</b><span>${escapeHtml(compactCommercialText(item.preference, 110))}</span></li>`)
    .join("");
  return `
    <div class="commercial-preview">
      <div class="commercial-preview-hero">
        <p class="eyebrow">商业方向偏好与约束结果</p>
        <h4>轻验证、低压力、产品化优先</h4>
        <span>${filled}/${items.length} 类已填写。先看边界，再收敛第二步商业切口。</span>
      </div>
      <div class="commercial-signal-list">
        ${signalItems || "<p>还没有可展示的偏好信号。</p>"}
      </div>
      <div class="commercial-matrix">
        ${items.map((item) => `
          <section>
            <strong>${escapeHtml(item.title)}</strong>
            <p><b>偏好</b>${escapeHtml(compactCommercialText(item.preference))}</p>
            <p><b>约束</b>${escapeHtml(compactCommercialText(item.constraint))}</p>
          </section>
        `).join("")}
      </div>
    </div>
  `;
}

function renderCommercialPreferenceSubmissionSummary(item) {
  const items = parseCommercialPreferenceMarkdown(item.markdown);
  if (!items) {
    return `<pre>${escapeHtml(markdownPreview(item.markdown))}</pre>`;
  }
  const filled = items.filter((entry) => entry.preference !== "未填写" || entry.constraint !== "未填写").length;
  const signals = items
    .filter((entry) => entry.preference !== "未填写")
    .slice(0, 3)
    .map((entry) => `<li><b>${escapeHtml(entry.title)}</b><span>${escapeHtml(compactCommercialText(entry.preference, 90))}</span></li>`)
    .join("");
  return `
    <div class="deliverable-submission-summary">
      <div>
        <p class="eyebrow">提交摘要</p>
        <strong>${filled}/${items.length} 类已填写</strong>
        <span>完整偏好/约束矩阵已移到独立结果页，避免工作台拥挤。</span>
      </div>
      <ul>
        ${signals || "<li><span>还没有可展示的偏好信号。</span></li>"}
      </ul>
    </div>
  `;
}

async function copySubmissionMarkdown(markdown) {
  try {
    await navigator.clipboard.writeText(markdown);
    showFeedback("提交结果 Markdown 已复制。", "success");
  } catch (_) {
    showFeedback("浏览器没有开放自动复制权限，请在卡片中手动选择复制。", "info");
  }
}

async function selectSession(id) {
  currentSessionId = id;
  currentSession = await api(`/api/sessions/${id}`);
  renderSession();
  await loadSessions();
}

function renderSession() {
  if (!currentSession) return;
  const s = currentSession.session;
  el("pageTitle").textContent = `${s.client_name} · 能力资产与认知资产诊断`;
  el("workspaceEyebrow").textContent = "顾问工作台 · 当前档案";
  el("sessionStatus").textContent = s.status;
  renderSessionForm();
  renderWorkflow();
  const downloadButton = el("downloadMarkdownBtn");
  if (downloadButton) downloadButton.disabled = false;
  renderClientLink(s.id);
  renderFiles();
  renderManualWorkflow();
  renderChat();
  renderReport();
  renderMaterialDecision();
  renderBoundDeliverables();
  renderNextStep();
}

function renderWorkflow() {
  const stages = ["stageUpload", "stageOrganize", "stageInterview", "stageDeliver"];
  stages.forEach((id) => {
    const node = el(id);
    if (!node) return;
    node.classList.remove("active", "done", "locked");
  });

  const hint = el("workflowHint");
  if (!currentSession) {
    stages.forEach((id, index) => el(id)?.classList.add(index === 0 ? "active" : "locked"));
    if (hint) hint.textContent = "先创建或选择一个档案。";
    return;
  }

  const files = currentSession.files || [];
  const materialOrg = getFreshMaterialOrganizationReport();
  const briefReport = getLatestReport("client_pre_session_brief");
  const hasMessages = (currentSession.conversation || []).length > 0;
  const uploadDone = files.length > 0;
  const organizeDone = Boolean(materialOrg);
  const interviewDone = Boolean(briefReport) || currentSession.session.status === "client_brief_ready";

  el("stageUpload")?.classList.add(uploadDone ? "done" : "active");
  el("stageOrganize")?.classList.add(organizeDone ? "done" : uploadDone ? "active" : "locked");
  el("stageInterview")?.classList.add(interviewDone ? "done" : organizeDone || hasMessages ? "active" : "locked");
  el("stageDeliver")?.classList.add(interviewDone ? "active" : "locked");

  if (!uploadDone) {
    if (hint) hint.textContent = "当前重点：先完成用户档案和材料上传。";
  } else if (!organizeDone) {
    if (hint) hint.textContent = `已保存 ${files.length} 份材料，下一步做材料规整与材料分析。`;
  } else if (!interviewDone) {
    if (hint) hint.textContent = hasMessages ? "用户正在会前访谈中，等待会前整理生成。" : "材料已整理，可以把用户访谈链接发给用户。";
  } else if (hint) {
    hint.textContent = "会前整理已生成，可以进入真人共创校准。";
  }
}

function renderSessionForm() {
  const form = el("sessionForm");
  if (!form) return;
  const submitButton = el("sessionSubmitBtn");
  const note = el("sessionFormNote");
  if (!currentSession) {
    form.elements.client_name.value = "";
    form.elements.contact.value = "";
    form.elements.goal.value = "";
    if (submitButton) submitButton.textContent = "创建诊断档案";
    if (note) {
      note.textContent = "先创建一个诊断档案，再把客户材料按三种方式放进来：文件、链接、补充文本。";
    }
    return;
  }
  const s = currentSession.session || {};
  form.elements.client_name.value = s.client_name || "";
  form.elements.contact.value = s.contact || "";
  form.elements.goal.value = s.goal || "";
  if (submitButton) submitButton.textContent = "保存档案信息";
  if (note) {
    note.textContent = "正在编辑当前诊断档案。修改姓名、联系方式或当前困惑后点击保存，不会影响已上传材料。";
  }
}

function shouldShowZhangluDeliverables() {
  const name = currentSession?.session?.client_name || "";
  return name.replace(/\s/g, "").includes("张麓");
}

function currentBoundDeliverable() {
  const name = (currentSession?.session?.client_name || "").replace(/\s/g, "");
  const matched = BOUND_DELIVERABLES.find((item) => name.includes(item.matchName));
  if (matched) return matched;
  const displayName = currentSession?.session?.client_name || "当前用户";
  return {
    matchName: "",
    eyebrow: `${displayName} · 第一步交付物`,
    title: "第一环节标准交付物",
    slots: {
      preference: { href: BUSINESS_PREFERENCE_LINK, target: "_blank" },
    },
  };
}

async function renderBoundDeliverables() {
  const deliverable = currentBoundDeliverable();
  const strip = el("boundDeliverableStrip");
  const links = el("boundDeliverableLinks");
  const empty = el("boundDeliverableEmpty");
  const panel = el("zhangluSubmissionPanel");
  if (strip) strip.hidden = !deliverable;
  if (empty) empty.hidden = Boolean(deliverable);
  if (links) links.innerHTML = "";
  if (panel) panel.hidden = !shouldShowZhangluDeliverables();
  if (!deliverable) {
    const list = el("deliverableSubmissionsList");
    if (list) list.textContent = "";
    return;
  }
  el("boundDeliverableEyebrow").textContent = deliverable.eyebrow;
  el("boundDeliverableTitle").textContent = deliverable.title;
  if (links) {
    links.innerHTML = STANDARD_DELIVERABLE_SLOTS.map((slot) => {
      const link = deliverable.slots?.[slot.key];
      if (!link?.href) {
        return `
          <div class="deliverable-link pending">
            <strong>${escapeHtml(slot.label)}</strong>
            <span>${escapeHtml(slot.description)}</span>
            <em>待生成</em>
          </div>
        `;
      }
      const target = link.target ? ` target="${link.target}" rel="noopener"` : "";
      const download = link.download ? " download" : "";
      return `
        <a class="deliverable-link" href="${escapeHtml(link.href)}"${target}${download}>
          <strong>${escapeHtml(slot.label)}</strong>
          <span>${escapeHtml(slot.description)}</span>
        </a>
      `;
    }).join("");
  }
  if (shouldShowZhangluDeliverables()) {
    await loadDeliverableSubmissions();
  }
}

function renderClientLink(sessionId) {
  const input = el("clientLink");
  if (!input) return;
  input.value = `${window.location.origin}/client?session=${sessionId}`;
}

function renderFiles() {
  const list = el("fileList");
  list.innerHTML = "";
  const files = currentSession.files || [];
  el("savedCount").textContent = `${files.length} 份`;
  if (files.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "还没有保存材料。";
    list.appendChild(empty);
    return;
  }
  files.forEach((f) => {
    const div = document.createElement("div");
    div.className = "file-item";
    div.innerHTML = `
      <strong>${escapeHtml(f.original_name)}</strong>
      <span>${f.text_len || 0} 字</span>
    `;
    list.appendChild(div);
  });
}

function renderNextStep() {
  if (!currentSession) {
    setNextStep("先创建诊断档案，再上传材料。", "info");
    return;
  }
  const files = currentSession.files || [];
  const materialOrg = getFreshMaterialOrganizationReport();
  const hasMaterialOrg = Boolean(materialOrg);
  const readiness = materialOrg?.content_json?.readiness_assessment;
  const hasMessages = (currentSession.conversation || []).length > 0;
  if (files.length === 0) {
    setNextStep("第一步：在左侧上传文件、链接或补充文本，然后点击“保存材料”。", "info");
  } else if (!hasMaterialOrg) {
    setNextStep(`已保存 ${files.length} 份材料。下一步：在“网页版 AI 人工处理”里先生成“材料规整”输入，去 GPT 网页版处理后粘回输出；再做“材料分析”。`, "success");
  } else if (readiness?.status === "must_collect_more_materials") {
    setNextStep(`材料整理完成，但当前不建议进入会前访谈。建议先让用户补充材料：${readiness.missing_materials?.join("、") || readiness.reason}`, "error");
  } else if (readiness?.status === "suggest_more_materials") {
    setNextStep(`材料整理完成，建议先补充材料，也可以直接进入会前访谈。缺口：${readiness.missing_materials?.join("、") || readiness.reason}`, "info");
  } else if (!hasMessages) {
    setNextStep("材料已整理。下一步：复制“用户自助访谈链接”发给用户；用户会在聊天页和 AI 自动追问。你也可以手动点击“生成下一问”接管节奏。", "success");
  } else if (currentSession.session.status === "client_brief_ready") {
    setNextStep("用户 AI 会前访谈已完成，并已生成会前基本信息整理。下一步可以查看完整聊天记录，并与用户约真人诊断时间。", "success");
  } else {
    setNextStep("用户正在回答 AI 追问。等系统生成“会前基本信息整理”后，你就可以根据完整记录约真人诊断时间。", "info");
  }
}

function getFreshMaterialOrganizationReport() {
  const reports = currentSession?.reports || [];
  const materialOrg = reports.find((r) => r.report_type === "material_organization");
  const materialStruct = reports.find((r) => r.report_type === "material_structuring");
  if (!materialOrg) return null;
  if (!materialStruct) return materialOrg;
  return materialOrg.created_at >= materialStruct.created_at ? materialOrg : null;
}

function getLatestReport(type) {
  return (currentSession?.reports || []).find((r) => r.report_type === type) || null;
}

function renderManualWorkflow() {
  if (!currentSession) return;
  const structReport = getLatestReport("material_structuring");
  const orgReport = getFreshMaterialOrganizationReport();
  const selectedType = el("manualTaskType")?.value || "material_structuring";
  const recommendedType = structReport ? "material_organization" : "material_structuring";
  if (
    el("manualTaskType") &&
    selectedType !== recommendedType &&
    !el("manualPromptBox").value &&
    !el("manualOutputBox").value
  ) {
    el("manualTaskType").value = recommendedType;
    updateManualTaskHint();
  }

  el("manualStepStruct")?.classList.toggle("done", Boolean(structReport));
  el("manualStepStruct")?.classList.toggle("active", selectedType === "material_structuring" && !structReport);
  el("manualStepAnalyze")?.classList.toggle("active", selectedType === "material_organization" || Boolean(structReport));
  el("manualStepAnalyze")?.classList.toggle("done", Boolean(orgReport));

  const context = el("manualContext");
  if (!context) return;
  if (!structReport) {
    context.hidden = true;
    return;
  }
  const data = structReport.content_json || {};
  context.hidden = false;
  el("manualContextMeta").textContent = `已保存于 ${formatDate(structReport.created_at)}`;
  el("manualContextOverview").textContent = data.structured_overview || "第一步材料规整结果已保存，第二步会把它作为 structured_material_draft 带入。";
  el("manualContextMaterials").textContent = String((data.structured_materials || []).length);
  el("manualContextCareer").textContent = String((data.career_facts || []).length);
  el("manualContextProjects").textContent = String((data.project_fact_cards || []).length);
  el("manualContextCognition").textContent = String((data.method_or_cognitive_expressions || []).length);
}

function renderChat() {
  const chat = el("chat");
  chat.innerHTML = "";
  (currentSession.conversation || []).forEach((m) => {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${m.role}`;
    let meta = "";
    if (m.meta_json) {
      try {
        const parsed = JSON.parse(m.meta_json);
        meta = `<div class="meta">${escapeHtml(parsed.focus || "")}${parsed.why ? " · " + escapeHtml(parsed.why) : ""}</div>`;
      } catch (_) {
        meta = "";
      }
    }
    bubble.innerHTML = `${escapeHtml(m.content)}${meta}`;
    chat.appendChild(bubble);
  });
  chat.scrollTop = chat.scrollHeight;
}

function renderReport() {
  const target = el("report");
  const materialReport = getFreshMaterialOrganizationReport();
  const briefReport = getLatestReport("client_pre_session_brief");
  if (!materialReport && !briefReport) {
    target.className = "report empty";
    target.textContent = "还没有生成材料线索或会前准备。请先完成材料分析，再让用户完成 AI 会前访谈。";
    return;
  }
  if (!activeAdvisorReportTab) activeAdvisorReportTab = briefReport ? "brief" : "source";
  if (activeAdvisorReportTab === "brief" && !briefReport) activeAdvisorReportTab = materialReport ? "source" : "brief";
  if (activeAdvisorReportTab === "source" && !materialReport) activeAdvisorReportTab = briefReport ? "brief" : "source";
  target.className = "report";
  target.innerHTML = `
    <div class="advisor-report-tabs" role="tablist" aria-label="顾问准备材料切换">
      <button type="button" data-report-tab="source" class="${activeAdvisorReportTab === "source" ? "active" : ""}" ${materialReport ? "" : "disabled"}>1. 材料线索</button>
      <button type="button" data-report-tab="brief" class="${activeAdvisorReportTab === "brief" ? "active" : ""}" ${briefReport ? "" : "disabled"}>2. 会前准备</button>
    </div>
    <div class="advisor-report-body">
      ${activeAdvisorReportTab === "brief"
        ? briefReport ? renderReadableReportSections(clientBriefSections(briefReport.content_json)) : `<p class="muted">用户完成 AI 会前访谈并生成会前整理后，这里会显示准备材料。</p>`
        : materialReport ? renderReadableReportSections(materialOrganizationSections(materialReport.content_json)) : `<p class="muted">还没有生成材料线索。</p>`}
    </div>
  `;
  target.querySelectorAll("[data-report-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      activeAdvisorReportTab = button.dataset.reportTab;
      renderReport();
    });
  });
}

function materialOrganizationSections(report) {
  return [
    ["材料就绪判断", report.readiness_assessment],
    ["材料整理总览", report.overview],
    ["材料清单", report.material_inventory],
    ["职业时间线", report.career_timeline],
    ["项目线索", report.project_clues],
    ["成就故事候选", report.achievement_story_candidates],
    ["失败/约束候选", report.failure_or_constraint_candidates],
    ["能力证据线索", report.ability_evidence_clues],
    ["认知资产线索", report.cognitive_asset_clues],
    ["平台依赖线索", report.platform_dependency_clues],
    ["优先追问地图", report.priority_question_map],
  ];
}

function clientBriefSections(report) {
  return [
    ["会前整理摘要", report.user_summary],
    ["已确认信息", report.confirmed_information],
    ["关键经历线索", report.key_story_clues],
    ["能力线索", report.ability_clues],
    ["认知资产线索", report.cognitive_asset_clues],
    ["真人访谈导航", report.open_questions_for_live_session],
    ["下次访谈前可准备", report.what_to_prepare_next],
    ["用户可见下一步", report.user_facing_next_step],
    ["顾问约访备注", report.advisor_scheduling_note],
  ];
}

function renderReadableReportSections(sections) {
  return sections
    .map(([title, value]) => renderReadableReportSection(title, value))
    .join("");
}

function renderReadableReportSection(title, value) {
  if (!value || (Array.isArray(value) && value.length === 0)) return "";
  let body = "";
  if (typeof value === "string") {
    body = `<p>${escapeHtml(value).replaceAll("\n", "<br>")}</p>`;
  } else if (Array.isArray(value)) {
    body = renderReadableReportCards(value);
  } else if (typeof value === "object") {
    body = renderReadableObjectCard(value);
  } else {
    body = `<p>${escapeHtml(String(value))}</p>`;
  }
  return `<div class="report-section"><h4>${escapeHtml(title)}</h4>${body}</div>`;
}

function renderReadableReportCards(items) {
  if (!Array.isArray(items) || items.length === 0) return "";
  return `<ul class="readable-card-list">${items
    .slice(0, 12)
    .map((item) => `<li>${renderReadableObjectLines(item)}</li>`)
    .join("")}</ul>`;
}

function renderReadableObjectCard(value) {
  return `<div class="readable-object-card">${renderReadableObjectLines(value)}</div>`;
}

function renderReadableObjectLines(value) {
  if (typeof value !== "object" || value === null) {
    return escapeHtml(String(value || ""));
  }
  return Object.entries(value)
    .filter(([, itemValue]) => itemValue && (!Array.isArray(itemValue) || itemValue.length > 0))
    .map(([key, itemValue]) => {
      const text = Array.isArray(itemValue)
        ? itemValue.slice(0, 8).map((item) => formatReadableValue(item)).join("；")
        : formatReadableValue(itemValue);
      return `<div><b>${escapeHtml(reportFieldLabel(key))}</b>${escapeHtml(text)}</div>`;
    })
    .join("");
}

function formatReadableValue(value) {
  if (typeof value === "object" && value !== null) {
    return Object.entries(value)
      .filter(([, nestedValue]) => nestedValue && (!Array.isArray(nestedValue) || nestedValue.length > 0))
      .map(([key, nestedValue]) => `${reportFieldLabel(key)}${Array.isArray(nestedValue) ? nestedValue.join("；") : nestedValue}`)
      .join("；");
  }
  return String(value);
}

function reportFieldLabel(key) {
  return {
    status: "状态：",
    reason: "原因：",
    missing_materials: "缺少材料：",
    suggested_user_request: "给用户的补充请求：",
    if_continue_interview_first_questions: "继续访谈优先问：",
    advisor_recommendation: "顾问建议：",
    name: "材料：",
    type: "类型：",
    summary: "摘要：",
    limitations: "局限：",
    period: "时间：",
    role_or_context: "角色/场景：",
    facts: "事实：",
    evidence_source: "来源：",
    project: "经历：",
    known_facts: "已知：",
    known_results: "结果：",
    missing_for_star: "待补：",
    story: "故事：",
    why_candidate: "价值：",
    event: "事件：",
    why_useful: "为什么重要：",
    missing_questions: "待问：",
    ability_hint: "线索：",
    evidence: "证据：",
    risk: "风险：",
    next_questions: "下一问：",
    level: "层级：",
    clue: "线索：",
    maturity_guess: "成熟度：",
    case_or_ability: "案例/能力：",
    possible_platform_factors: "可能的平台因素：",
    priority: "优先级：",
    question: "问题：",
    why: "原因：",
    title: "信息：",
    details: "详情：",
    source: "来源：",
    current_judgment: "判断：",
    risk_or_gap: "风险：",
    next_validation: "验证：",
  }[key] || `${key}：`;
}

function downloadAdvisorMarkdown() {
  if (!currentSession) {
    showFeedback("请先创建或选择一个诊断档案，再下载 Markdown。", "error");
    return;
  }
  const markdown = buildAdvisorMarkdown();
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = advisorMarkdownFilename();
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showFeedback("已生成 Markdown 工作底稿。", "success");
}

function buildAdvisorMarkdown() {
  const session = currentSession.session || {};
  const materialStruct = getLatestReport("material_structuring");
  const materialOrg = getFreshMaterialOrganizationReport();
  const briefReport = getLatestReport("client_pre_session_brief");
  const lines = [];
  lines.push(`# ${mdText(session.client_name || "未命名客户")} · 能力资产诊断工作底稿`);
  lines.push("");
  lines.push(`> 导出时间：${formatDate(new Date().toISOString())}`);
  lines.push(`> 档案状态：${mdText(session.status || "未知")}`);
  lines.push("");
  lines.push("## 1. 档案信息");
  lines.push("");
  lines.push(`- 用户姓名：${mdText(session.client_name || "")}`);
  lines.push(`- 联系方式：${mdText(session.contact || "")}`);
  lines.push(`- 当前困惑：${mdText(session.goal || "")}`);
  lines.push(`- 创建时间：${formatDate(session.created_at)}`);
  lines.push(`- 更新时间：${formatDate(session.updated_at)}`);
  lines.push("");
  lines.push("## 2. 整理后的用户原始材料");
  lines.push("");
  appendCleanMaterialExcerpts(lines);
  appendReportToMarkdown(lines, "### 2.1 材料规整底稿", materialStruct?.content_json, [
    ["材料规整总览", "structured_overview"],
    ["材料清单", "structured_materials"],
    ["职业事实", "career_facts"],
    ["项目事实卡", "project_fact_cards"],
    ["方法/认知表达", "method_or_cognitive_expressions"],
    ["明显缺口", "obvious_gaps"],
  ]);
  appendReportToMarkdown(lines, "## 3. 顾问准备材料：材料线索", materialOrg?.content_json, materialOrganizationSectionsForMarkdown());
  appendReportToMarkdown(lines, "## 4. 顾问准备材料：会前准备", briefReport?.content_json, clientBriefSectionsForMarkdown());
  appendConversationToMarkdown(lines);
  lines.push("");
  return `${lines.join("\n")}\n`;
}

function appendCleanMaterialExcerpts(lines) {
  const materials = currentSession.materials || [];
  if (!materials.length) {
    lines.push("_暂无已抽取材料。_");
    lines.push("");
    return;
  }
  materials.forEach((material, index) => {
    lines.push(`### 2.0.${index + 1} ${mdText(material.name || `材料 ${index + 1}`)}`);
    lines.push("");
    lines.push(`- 清洗后长度：${material.length || 0} 字`);
    lines.push(`- 原始长度：${material.raw_length || 0} 字`);
    if (material.cleaning_notes?.length) {
      lines.push("- 清洗说明：");
      material.cleaning_notes.forEach((note) => lines.push(`  - ${mdText(note)}`));
    }
    lines.push("");
    lines.push("```text");
    lines.push(String(material.excerpt || "").trim() || "无内容");
    lines.push("```");
    lines.push("");
  });
}

function appendReportToMarkdown(lines, title, report, sections) {
  lines.push(title);
  lines.push("");
  if (!report) {
    lines.push("_暂无内容。_");
    lines.push("");
    return;
  }
  sections.forEach(([sectionTitle, key]) => {
    const value = report[key];
    if (!hasMarkdownValue(value)) return;
    lines.push(`### ${mdText(sectionTitle)}`);
    lines.push("");
    lines.push(markdownValue(value));
    lines.push("");
  });
}

function materialOrganizationSectionsForMarkdown() {
  return [
    ["材料就绪判断", "readiness_assessment"],
    ["材料整理总览", "overview"],
    ["材料清单", "material_inventory"],
    ["职业时间线", "career_timeline"],
    ["项目线索", "project_clues"],
    ["成就故事候选", "achievement_story_candidates"],
    ["失败/约束候选", "failure_or_constraint_candidates"],
    ["能力证据线索", "ability_evidence_clues"],
    ["认知资产线索", "cognitive_asset_clues"],
    ["平台依赖线索", "platform_dependency_clues"],
    ["优先追问地图", "priority_question_map"],
  ];
}

function clientBriefSectionsForMarkdown() {
  return [
    ["会前整理摘要", "user_summary"],
    ["已确认信息", "confirmed_information"],
    ["关键经历线索", "key_story_clues"],
    ["能力线索", "ability_clues"],
    ["认知资产线索", "cognitive_asset_clues"],
    ["真人访谈导航", "open_questions_for_live_session"],
    ["下次访谈前可准备", "what_to_prepare_next"],
    ["用户可见下一步", "user_facing_next_step"],
    ["顾问约访备注", "advisor_scheduling_note"],
  ];
}

function appendConversationToMarkdown(lines) {
  const messages = currentSession.conversation || [];
  lines.push("## 5. 用户 AI 会前访谈记录");
  lines.push("");
  if (!messages.length) {
    lines.push("_暂无会前访谈记录。_");
    lines.push("");
    return;
  }
  messages.forEach((message, index) => {
    const role = message.role === "assistant" ? "AI" : "用户";
    lines.push(`### ${index + 1}. ${role} · ${formatDate(message.created_at)}`);
    lines.push("");
    lines.push(mdText(message.content || ""));
    lines.push("");
  });
}

function markdownValue(value, depth = 0) {
  if (!hasMarkdownValue(value)) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return mdText(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => markdownListItem(item, depth)).join("\n");
  }
  return Object.entries(value)
    .filter(([, itemValue]) => hasMarkdownValue(itemValue))
    .map(([key, itemValue]) => {
      if (typeof itemValue === "object" && itemValue !== null) {
        return `- **${mdText(reportFieldLabel(key).replace(/：$/, ""))}**：\n${indentMarkdown(markdownValue(itemValue, depth + 1), 2)}`;
      }
      return `- **${mdText(reportFieldLabel(key).replace(/：$/, ""))}**：${mdText(itemValue)}`;
    })
    .join("\n");
}

function markdownListItem(item, depth = 0) {
  if (typeof item === "object" && item !== null) {
    const content = markdownValue(item, depth + 1);
    return `- ${content.startsWith("- ") ? `\n${indentMarkdown(content, 2)}` : content}`;
  }
  return `- ${mdText(item)}`;
}

function indentMarkdown(text, spaces) {
  const pad = " ".repeat(spaces);
  return String(text)
    .split("\n")
    .map((line) => (line ? `${pad}${line}` : line))
    .join("\n");
}

function hasMarkdownValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.some((item) => hasMarkdownValue(item));
  if (typeof value === "object") return Object.values(value).some((item) => hasMarkdownValue(item));
  return true;
}

function mdText(value) {
  return String(value ?? "").replace(/\r\n/g, "\n").trim();
}

function advisorMarkdownFilename() {
  const name = sanitizeFilename(currentSession.session?.client_name || "客户");
  const date = new Date().toISOString().slice(0, 10);
  return `${name}-能力资产诊断工作底稿-${date}.md`;
}

function sanitizeFilename(value) {
  return String(value || "客户")
    .replace(/[\\/:*?"<>|]/g, "-")
    .replace(/\s+/g, "")
    .slice(0, 40) || "客户";
}

function renderMaterialDecision() {
  const panel = el("materialDecision");
  if (!panel || !currentSession) return;
  const materialOrg = getFreshMaterialOrganizationReport();
  const readiness = materialOrg?.content_json?.readiness_assessment;
  if (!readiness) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const statusLabels = {
    ready_for_interview: "可以进入会前访谈",
    suggest_more_materials: "建议补材料，也可以进入访谈",
    must_collect_more_materials: "建议先补材料",
  };
  const status = readiness.status || "unknown";
  el("decisionStatus").textContent = statusLabels[status] || status;
  el("decisionStatus").className = `decision-status ${status}`;
  el("decisionReason").textContent = readiness.reason || "材料分析已完成，请根据缺口决定下一步。";
  const missing = readiness.missing_materials || [];
  el("decisionMissing").textContent = missing.length ? JSON.stringify(missing, null, 2) : "暂无明确缺口。";
  el("decisionUserRequest").value = readiness.suggested_user_request || "";
  const questions = readiness.if_continue_interview_first_questions || [];
  el("decisionInterviewQuestions").textContent = questions.length ? JSON.stringify(questions, null, 2) : "可直接进入会前访谈，让 AI 从基础经历继续追问。";
  el("decisionAdvisor").textContent = readiness.advisor_recommendation || "";
}

async function organizeMaterials() {
  if (aiBusy) return;
  if (!currentSessionId) {
    showFeedback("请先创建或选择一个诊断档案。", "error");
    setNextStep("还没有诊断档案。请先在左侧填写用户信息并创建档案。", "error");
    return;
  }
  if (!currentSession || (currentSession.files || []).length === 0) {
    showFeedback("还没有保存任何材料。请先上传文件、链接或补充文本，并点击“保存材料”。", "error");
    setNextStep("第一步还没完成：请先保存至少一份材料。", "error");
    return;
  }
  setAiBusy(true);
  showOrganizeProgress();
  try {
    await api(`/api/sessions/${currentSessionId}/organize`, { method: "POST" });
    completeOrganizeProgress();
    await loadHealth();
    await selectSession(currentSessionId);
    showFeedback("材料整理完成。可以复制用户自助访谈链接，让用户进入会前访谈页和 AI 继续聊。", "success");
  } catch (err) {
    failOrganizeProgress(err.message);
    showFeedback(`整理材料失败：${err.message}`, "error");
    setNextStep(`整理失败：${err.message}`, "error");
  } finally {
    setAiBusy(false);
  }
}

async function createSession(e) {
  e.preventDefault();
  const formEl = e.currentTarget;
  const form = new FormData(formEl);
  const payload = Object.fromEntries(form.entries());
  const editingSessionId = currentSessionId;
  showBusy(editingSessionId ? "保存档案信息..." : "创建诊断档案...");
  try {
    const data = await api(editingSessionId ? `/api/sessions/${editingSessionId}/profile` : "/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await selectSession(editingSessionId || data.id);
    showFeedback(
      editingSessionId
        ? "档案信息已保存。你可以继续上传材料、整理材料或进入后续步骤。"
        : "诊断档案已创建。现在可以上传三种材料：文件、链接、补充文本。",
      "success"
    );
  } catch (err) {
    showFeedback(`${editingSessionId ? "保存" : "创建"}失败：${err.message}`, "error");
  } finally {
    hideBusy();
  }
}

async function uploadMaterials(e) {
  e.preventDefault();
  const formEl = e.currentTarget;
  if (!currentSessionId) {
    showFeedback("请先创建或选择一个诊断档案。", "error");
    return;
  }
  const form = new FormData(formEl);
  const hasFiles = formEl.elements.files.files.length > 0;
  const hasLinks = (form.get("links") || "").trim().length > 0;
  const hasNotes = (form.get("notes") || "").trim().length > 0;
  if (!hasFiles && !hasLinks && !hasNotes) {
    showFeedback("请至少上传一种材料：文件、链接或补充文本。", "error");
    return;
  }
  showBusy("保存并抽取材料...");
  try {
    const result = await api(`/api/sessions/${currentSessionId}/upload`, { method: "POST", body: form });
    formEl.reset();
    await selectSession(currentSessionId);
    const names = (result.saved || []).map((x) => x.name).join("、");
    showFeedback(`已保存 ${result.saved?.length || 0} 份材料：${names || "材料"}。下一步请在“网页版 AI 人工处理”里生成材料规整输入。`, "success");
  } catch (err) {
    showFeedback(`保存材料失败：${err.message}`, "error");
  } finally {
    hideBusy();
  }
}

async function submitMessage(e) {
  e.preventDefault();
  const formEl = e.currentTarget;
  if (!currentSessionId) return showFeedback("请先创建或选择一个诊断档案。", "error");
  const textarea = formEl.elements.content;
  const content = textarea.value.trim();
  if (!content) return;
  showBusy("保存回答...");
  try {
    await api(`/api/sessions/${currentSessionId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    textarea.value = "";
    await selectSession(currentSessionId);
  } catch (err) {
    showFeedback(`保存回答失败：${err.message}`, "error");
  } finally {
    hideBusy();
  }
}

async function askNext() {
  if (aiBusy) return;
  if (!currentSessionId) return showFeedback("请先创建或选择一个诊断档案。", "error");
  setAiBusy(true);
  showBusy("AI 正在阅读材料并生成追问...");
  try {
    await api(`/api/sessions/${currentSessionId}/next-question`, { method: "POST" });
    await loadHealth();
    await selectSession(currentSessionId);
  } catch (err) {
    showFeedback(`生成追问失败：${err.message}`, "error");
    setNextStep(`生成追问失败：${err.message}`, "error");
  } finally {
    setAiBusy(false);
    hideBusy();
  }
}

async function generateReport() {
  if (aiBusy) return;
  if (!currentSessionId) return showFeedback("请先创建或选择一个诊断档案。", "error");
  setAiBusy(true);
  showBusy("AI 正在整理真人审阅材料...");
  try {
    await api(`/api/sessions/${currentSessionId}/report`, { method: "POST" });
    await loadHealth();
    await selectSession(currentSessionId);
  } catch (err) {
    showFeedback(`生成审阅包失败：${err.message}`, "error");
    setNextStep(`生成审阅包失败：${err.message}`, "error");
  } finally {
    setAiBusy(false);
    hideBusy();
  }
}

async function generateManualPrompt() {
  if (!currentSessionId) {
    showFeedback("请先创建或选择一个诊断档案。", "error");
    return;
  }
  if (!currentSession || (currentSession.files || []).length === 0) {
    showFeedback("请先保存至少一份材料，再生成网页版输入。", "error");
    return;
  }
  const type = el("manualTaskType").value || "material_structuring";
  if (type === "material_organization" && !getLatestReport("material_structuring")) {
    showFeedback("请先完成并保存第 1 步“材料规整”，第 2 步会自动带入第一步结果。", "error");
    setNextStep("还缺第 1 步材料规整结果。请先生成材料规整输入，并保存网页版输出。", "error");
    return;
  }
  showBusy(`正在生成${MANUAL_TASK_LABELS[type]}输入...`);
  try {
    setManualSaveStatus("", "info");
    const data = await api(`/api/sessions/${currentSessionId}/manual-ai-prompt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type }),
    });
    el("manualPromptBox").value = data.prompt;
    showFeedback(`${MANUAL_TASK_LABELS[type]}输入包已生成，可以复制到 GPT 网页版。`, "success");
    setNextStep(`把“${MANUAL_TASK_LABELS[type]}”输入包复制到 GPT 网页版。拿到完整 JSON 后，粘回“网页版输出”并点击保存。`, "success");
  } catch (err) {
    showFeedback(`生成网页版输入失败：${err.message}`, "error");
  } finally {
    hideBusy();
  }
}

async function copyManualPrompt() {
  const text = el("manualPromptBox").value.trim();
  if (!text) {
    showFeedback("还没有网页版输入，请先点击“生成网页版输入”。", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showFeedback("网页版输入已复制。", "success");
  } catch (_) {
    el("manualPromptBox").select();
    showFeedback("浏览器没有开放自动复制权限，已帮你选中输入内容，可以手动复制。", "info");
  }
}

async function importManualOutput() {
  if (!currentSessionId) {
    showFeedback("请先创建或选择一个诊断档案。", "error");
    return;
  }
  const type = el("manualTaskType").value || "material_structuring";
  const content = el("manualOutputBox").value.trim();
  if (!content) {
    showFeedback("请先粘贴 GPT 网页版返回的完整 JSON。", "error");
    setManualSaveStatus("还没有粘贴 GPT 网页版返回的完整 JSON。", "error");
    return;
  }
  showBusy(`正在保存${MANUAL_TASK_LABELS[type]}输出...`);
  try {
    setManualSaveStatus("正在保存并校验 JSON...", "info");
    await api(`/api/sessions/${currentSessionId}/manual-ai-output`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, content }),
    });
    el("manualOutputBox").value = "";
    await selectSession(currentSessionId);
    showFeedback(`${MANUAL_TASK_LABELS[type]}输出已保存到本地数据库。`, "success");
    setManualSaveStatus(`${MANUAL_TASK_LABELS[type]}已保存成功。`, "success");
  if (type === "material_structuring") {
      el("manualTaskType").value = "material_organization";
      el("manualPromptBox").value = "";
      el("manualOutputBox").value = "";
      updateManualTaskHint();
      renderManualWorkflow();
      setNextStep("材料规整已保存，并已自动切到第 2 步“材料分析”。第二步会带入原始材料和第一步规整结果。", "success");
    } else if (type === "material_organization") {
      setNextStep("材料分析已保存。请查看“材料就绪判断”，决定先让用户补材料，还是直接进入会前访谈。", "success");
    }
  } catch (err) {
    showFeedback(`保存网页版输出失败：${err.message}`, "error");
    setManualSaveStatus(`没有保存成功：${err.message}。请确认从第一个 { 复制到最后一个 }，不要漏掉末尾内容。`, "error");
    el("manualOutputBox").focus();
  } finally {
    hideBusy();
  }
}

function updateManualTaskHint() {
  const type = el("manualTaskType")?.value || "material_structuring";
  if (el("manualTaskHint")) {
    const relation =
      type === "material_organization"
        ? "本步输入会同时包含用户原始材料，以及第 1 步保存的 structured_material_draft。"
        : "";
    el("manualTaskHint").textContent = [MANUAL_TASK_HINTS[type], relation].filter(Boolean).join(" ");
  }
  renderManualWorkflow();
}

async function copyClientLink() {
  const text = el("clientLink").value.trim();
  if (!text) {
    showFeedback("请先创建或选择一个诊断档案。", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showFeedback("用户自助访谈链接已复制。", "success");
  } catch (_) {
    el("clientLink").select();
    showFeedback("浏览器没有开放自动复制权限，已帮你选中链接，可以手动复制。", "info");
  }
}

async function copyMaterialRequest() {
  const text = el("decisionUserRequest")?.value.trim();
  if (!text) {
    showFeedback("当前没有可复制的补材料请求。", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showFeedback("补材料请求已复制。", "success");
  } catch (_) {
    el("decisionUserRequest").select();
    showFeedback("浏览器没有开放自动复制权限，已帮你选中内容，可以手动复制。", "info");
  }
}

function openClientLink() {
  const text = el("clientLink").value.trim();
  if (!text) {
    showFeedback("请先创建或选择一个诊断档案。", "error");
    return;
  }
  window.open(text, "_blank", "noopener,noreferrer");
}

function clearCurrent() {
  currentSessionId = null;
  currentSession = null;
  el("pageTitle").textContent = "能力资产与认知资产诊断";
  el("workspaceEyebrow").textContent = "顾问工作台";
  el("sessionStatus").textContent = "未开始";
  renderSessionForm();
  renderWorkflow();
  el("fileList").innerHTML = "";
  el("savedCount").textContent = "0 份";
  el("clientLink").value = "";
  el("chat").innerHTML = "";
  if (el("manualPromptBox")) el("manualPromptBox").value = "";
  if (el("manualOutputBox")) el("manualOutputBox").value = "";
  if (el("manualTaskType")) {
    el("manualTaskType").value = "material_structuring";
    updateManualTaskHint();
  }
  el("report").className = "report empty";
  el("report").textContent = "还没有生成材料整理包或审阅材料。请先完成第一步上传材料，再点击“整理材料”。";
  el("boundDeliverableEmpty").hidden = false;
  el("boundDeliverableStrip").hidden = true;
  el("boundDeliverableLinks").innerHTML = "";
  el("zhangluSubmissionPanel").hidden = true;
  el("deliverableSubmissionsList").textContent = "";
  showFeedback("", "info");
  setNextStep("先创建诊断档案，再上传材料。", "info");
  loadSessions();
}

window.addEventListener("DOMContentLoaded", async () => {
  el("adminLoginForm")?.addEventListener("submit", loginAdmin);
  el("sessionForm").addEventListener("submit", createSession);
  el("apiKeyForm").addEventListener("submit", saveApiKey);
  el("uploadForm").addEventListener("submit", uploadMaterials);
  el("messageForm").addEventListener("submit", submitMessage);
  el("organizeBtn")?.addEventListener("click", organizeMaterials);
  el("askBtn")?.addEventListener("click", askNext);
  el("reportBtn")?.addEventListener("click", generateReport);
  el("downloadMarkdownBtn")?.addEventListener("click", downloadAdvisorMarkdown);
  el("manualPromptBtn").addEventListener("click", generateManualPrompt);
  el("copyManualPromptBtn").addEventListener("click", copyManualPrompt);
  el("importManualOutputBtn").addEventListener("click", importManualOutput);
  el("manualTaskType").addEventListener("change", updateManualTaskHint);
  el("copyClientLinkBtn").addEventListener("click", copyClientLink);
  el("openClientLinkBtn").addEventListener("click", openClientLink);
  el("copyMaterialRequestBtn").addEventListener("click", copyMaterialRequest);
  el("copyClientLinkFromDecisionBtn").addEventListener("click", copyClientLink);
  el("openClientLinkFromDecisionBtn").addEventListener("click", openClientLink);
  el("refreshDeliverableSubmissionsBtn")?.addEventListener("click", loadDeliverableSubmissions);
  el("newSessionBtn").addEventListener("click", clearCurrent);
  updateManualTaskHint();
  const canLoad = await loadAuthStatus();
  if (!canLoad) return;
  await initAdvisorWorkspace();
});

async function initAdvisorWorkspace() {
  await loadHealth();
  await loadSessions();
  await loadDeliverableSubmissions();
}
