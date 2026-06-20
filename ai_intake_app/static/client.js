window.__abilityClientLoaded = true;

const el = (id) => document.getElementById(id);
const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8787" : "";
const params = new URLSearchParams(window.location.search);
const sessionId = params.get("session");
let sessionData = null;
let clientBusy = false;
let transientBubble = null;
let activeMaterialTab = null;

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败：${res.status}`);
  return data;
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadClientSession() {
  if (!sessionId) {
    el("clientTitle").textContent = "链接缺少访谈编号";
    el("clientNextStep").textContent = "请联系顾问重新发送访谈链接。";
    el("clientStatus").textContent = "无法开始";
    return;
  }
  sessionData = await api(`/api/client/sessions/${sessionId}`);
  renderClientSession();
}

function renderClientSession() {
  const s = sessionData.session;
  el("clientTitle").textContent = `${s.client_name} 的会前访谈`;
  el("clientStatus").textContent = readableStatus(s.status);
  syncDefaultMaterialTab();
  renderClientProgress();
  renderClientMaterialSummary();
  renderClientChat();
  renderClientNextStep();
  focusReplyIfAnswerable();
}

function syncDefaultMaterialTab() {
  const hasBrief = Boolean(getLatestReport("client_pre_session_brief"));
  const hasMaterial = Boolean(getLatestReport("material_organization"));
  if (!activeMaterialTab) activeMaterialTab = hasBrief ? "brief" : "source";
  if (activeMaterialTab === "brief" && !hasBrief) activeMaterialTab = hasMaterial ? "source" : "brief";
  if (activeMaterialTab === "source" && !hasMaterial) activeMaterialTab = hasBrief ? "brief" : "source";
}

function readableStatus(status) {
  return {
    intake: "准备中",
    materials_organized: "可开始追问",
    questioning: "访谈中",
    ready_for_report: "信息基本足够",
    client_brief_ready: "会前信息已生成",
    ability_delivered: "已生成能力交付",
    report_ready: "顾问已整理",
  }[status] || status;
}

function renderClientChat() {
  const chat = el("clientChat");
  chat.innerHTML = "";
  const conversation = getVisibleConversation();
  const brief = getLatestReport("client_pre_session_brief")?.content_json;
  if (conversation.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "材料整理完成后，点击“开始 AI 会前访谈”，系统会生成第一道追问。";
    chat.appendChild(empty);
    if (!brief) return;
  }
  conversation.forEach((m) => {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${m.role}`;
    let meta = "";
    if (m.meta_json) {
      try {
        const parsed = JSON.parse(m.meta_json);
        meta = `<div class="meta">${escapeHtml(parsed.why || "")}</div>`;
      } catch (_) {
        meta = "";
      }
    }
    bubble.innerHTML = `${escapeHtml(m.content)}${meta}`;
    chat.appendChild(bubble);
  });
  if (brief) renderClientBriefNotice(chat, brief);
  renderTransientBubble(chat);
  chat.scrollTop = chat.scrollHeight;
}

function renderClientBriefNotice(chat, brief) {
  const notice = document.createElement("div");
  notice.className = "client-brief-notice";
  const topics = Array.isArray(brief.open_questions_for_live_session)
    ? brief.open_questions_for_live_session.slice(0, 3).map((item) => item.question || item.topic || item.why).filter(Boolean)
    : [];
  notice.innerHTML = `
    <div class="client-brief-notice-head">
      <strong>会前整理已生成</strong>
      <span>左侧可查看完整材料</span>
    </div>
    <p>${escapeHtml(brief.user_summary || "系统已经根据材料和会前访谈整理出下一次真人访谈前的基本信息。")}</p>
    ${topics.length ? `<div class="client-brief-topics"><b>下次真人访谈会优先围绕：</b><ul>${topics.map((topic) => `<li>${escapeHtml(topic)}</li>`).join("")}</ul></div>` : ""}
  `;
  chat.appendChild(notice);
}

function renderClientProgress() {
  const progress = sessionData.interview_progress || {};
  const answered = progress.answered_rounds || 0;
  const percent = Math.max(0, Math.min(progress.percent ?? 0, 100));
  const isDone = Boolean(progress.is_done || hasClientBrief());
  el("clientProgressTitle").textContent = isDone ? "会前访谈已完成" : answered > 0 ? `已回答 ${answered} 轮` : "准备开始会前访谈";
  el("clientProgressMeta").textContent = isDone ? "已进入整理" : "追问判断中";
  el("clientProgressFill").style.width = `${isDone ? 100 : percent}%`;
  if (isDone) {
    el("clientProgressHint").textContent = "会前基本信息已经生成，顾问会基于完整记录准备下一次真人访谈。";
  } else {
    el("clientProgressHint").textContent = "AI 会一边追问，一边判断信息是否足够；聊够后会结束聊天，并提示生成会前准备。";
  }
}

function getLatestReport(type) {
  return (sessionData.reports || []).find((r) => r.report_type === type) || null;
}

function renderClientMaterialSummary() {
  const target = el("clientMaterialSummary");
  const delivery = getLatestReport("ability_delivery_pack")?.content_json;
  const brief = getLatestReport("client_pre_session_brief")?.content_json;
  const material = getLatestReport("material_organization")?.content_json;
  renderMaterialTabs(Boolean(material), Boolean(brief || delivery));
  if (delivery) {
    activeMaterialTab = "brief";
    renderMaterialTabs(Boolean(material), true);
    target.innerHTML = renderAbilityDelivery(delivery);
    return;
  }
  if (activeMaterialTab === "brief") {
    target.innerHTML = brief ? renderClientBrief(brief) : `
      <div class="client-empty-material">
        <strong>会前准备还没有生成</strong>
        <span>AI 追问结束后，点击“生成会前整理”，这里会显示下一次真人访谈前的准备材料。</span>
      </div>
    `;
    return;
  }
  target.innerHTML = material ? renderMaterialOrganization(material) : `
      <div class="client-empty-material">
        <strong>材料正在整理中</strong>
        <span>顾问完成材料分析后，这里会显示访谈前可共同查看的整理信息。</span>
      </div>
    `;
}

function renderMaterialTabs(hasMaterial, hasBrief) {
  const sourceTab = el("clientSourceTab");
  const briefTab = el("clientBriefTab");
  if (!sourceTab || !briefTab) return;
  sourceTab.classList.toggle("active", activeMaterialTab === "source");
  briefTab.classList.toggle("active", activeMaterialTab === "brief");
  sourceTab.disabled = !hasMaterial;
  briefTab.disabled = !hasBrief;
  sourceTab.setAttribute("aria-selected", String(activeMaterialTab === "source"));
  briefTab.setAttribute("aria-selected", String(activeMaterialTab === "brief"));
}

function renderMaterialOrganization(report) {
  const readiness = report.readiness_assessment || {};
  const sections = [
    ["材料总览", report.overview],
    ["职业时间线", compactCards(report.career_timeline, ["period", "role_or_context", "facts", "evidence_source"])],
    ["材料来源", compactCards(report.material_inventory, ["name", "type", "summary", "limitations"])],
    ["关键经历线索", compactCards(report.project_clues, ["project", "known_facts", "known_results", "missing_for_star"])],
    ["成就故事候选", compactCards(report.achievement_story_candidates, ["story", "why_candidate", "missing_questions"])],
    ["失败/约束候选", compactCards(report.failure_or_constraint_candidates, ["event", "why_useful", "missing_questions"])],
    ["能力线索", compactCards(report.ability_evidence_clues, ["ability_hint", "evidence", "risk", "next_questions"])],
    ["认知资产线索", compactCards(report.cognitive_asset_clues, ["level", "clue", "maturity_guess", "next_questions"])],
    ["平台依赖线索", compactCards(report.platform_dependency_clues, ["case_or_ability", "possible_platform_factors", "next_questions"])],
    ["接下来优先确认", compactCards(report.priority_question_map, ["priority", "question", "why"])],
  ];
  return `
    <div class="client-material-label">会前材料整理</div>
    <div class="client-readiness ${escapeHtml(readiness.status || "")}">
      <strong>${escapeHtml(readinessLabel(readiness.status))}</strong>
      <span>${escapeHtml(readiness.reason || "材料已经整理，后续会继续通过具体经历补充。")}</span>
    </div>
    ${sections
      .map(([title, value]) => renderMaterialSection(title, value))
      .join("")}
  `;
}

function renderClientBrief(report) {
  const sections = [
    ["我们已经整理出的基本情况", report.user_summary],
    ["已经确认的信息", compactCards(report.confirmed_information, ["title", "details", "source"])],
    ["值得继续展开的经历", compactCards(report.key_story_clues, ["story", "known_facts", "why_discuss_next"])],
    ["目前看到的能力线索", compactCards(report.ability_clues, ["clue", "evidence", "status"])],
    ["目前看到的认知线索", compactCards(report.cognitive_asset_clues, ["level", "clue", "evidence"])],
    ["下次真人访谈会围绕", compactCards(report.open_questions_for_live_session, ["priority", "question", "why"])],
    ["你可以提前准备", listCards(report.what_to_prepare_next)],
    ["下一步", report.user_facing_next_step],
  ];
  return `
    <div class="client-material-label">已生成 · 会前基本信息</div>
    ${sections.map(([title, value]) => renderMaterialSection(title, value)).join("")}
  `;
}

function renderAbilityDelivery(report) {
  const sections = [
    ["阶段性总结", report.delivery_summary],
    ["能力资产地图", compactCards(report.ability_asset_map, ["asset_name", "plain_language", "evidence_cases", "transferability", "confidence"])],
    ["能力资产负债表", compactCards(report.ability_balance_sheet, ["item", "side", "current_judgment", "risk_or_gap", "next_validation"])],
    ["平台依赖初步判断", compactCards(report.platform_dependency_table, ["case_or_ability", "judgment", "missing_evidence"])],
    ["认知资产地图", compactCards(report.cognitive_asset_map, ["level", "asset", "maturity", "how_to_strengthen"])],
    ["能量与匹配度", compactCards(report.energy_and_fit_notes, ["ability_or_activity", "energy_signal", "judgment"])],
    ["暂不下结论", listCards(report.not_yet_conclusions)],
    ["建议下一步", report.recommended_next_step],
    ["结束语", report.user_facing_closing],
  ];
  return `
    <div class="client-material-label">阶段性交付材料</div>
    ${sections.map(([title, value]) => renderMaterialSection(title, value)).join("")}
  `;
}

function readinessLabel(status) {
  return {
    ready_for_interview: "材料基本够进入会前访谈",
    suggest_more_materials: "材料可访谈，但建议继续补充",
    must_collect_more_materials: "建议先补材料",
  }[status] || "材料整理已完成";
}

function compactCards(items, keys) {
  if (!Array.isArray(items) || items.length === 0) return "";
  return items.slice(0, 8).map((item) => {
    const lines = keys
      .map((key) => {
        const value = item?.[key];
        if (!value || (Array.isArray(value) && value.length === 0)) return "";
        const text = Array.isArray(value) ? value.slice(0, 5).join("；") : String(value);
        return `<div><b>${escapeHtml(fieldLabel(key))}</b>${escapeHtml(text)}</div>`;
      })
      .filter(Boolean)
      .join("");
    return `<li>${lines}</li>`;
  }).join("");
}

function listCards(items) {
  if (!Array.isArray(items) || items.length === 0) return "";
  return items.slice(0, 10).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function fieldLabel(key) {
  return {
    project: "经历：",
    name: "材料：",
    type: "类型：",
    summary: "摘要：",
    limitations: "局限：",
    period: "时间：",
    role_or_context: "角色/场景：",
    facts: "事实：",
    evidence_source: "来源：",
    known_facts: "已知：",
    known_results: "结果：",
    missing_for_star: "待补：",
    story: "故事：",
    why_candidate: "价值：",
    event: "事件：",
    why_useful: "为什么重要：",
    missing_questions: "待问：",
    ability_hint: "线索：",
    asset_name: "资产：",
    plain_language: "解释：",
    evidence_cases: "证据：",
    transferability: "迁移：",
    confidence: "置信：",
    item: "项目：",
    side: "类型：",
    current_judgment: "判断：",
    risk_or_gap: "风险：",
    next_validation: "验证：",
    case_or_ability: "案例/能力：",
    possible_platform_factors: "可能的平台因素：",
    judgment: "判断：",
    missing_evidence: "缺证据：",
    asset: "资产：",
    maturity: "成熟度：",
    how_to_strengthen: "强化：",
    ability_or_activity: "能力/活动：",
    energy_signal: "能量：",
    evidence: "证据：",
    risk: "风险：",
    next_questions: "下一问：",
    level: "层级：",
    clue: "线索：",
    maturity_guess: "成熟度：",
    title: "信息：",
    details: "详情：",
    source: "来源：",
    why_discuss_next: "为何讨论：",
    missing_details: "待补：",
    status: "状态：",
    next_probe: "追问：",
    priority: "优先级：",
    question: "问题：",
    why: "原因：",
  }[key] || `${key}：`;
}

function renderMaterialSection(title, value) {
  if (!value || (Array.isArray(value) && value.length === 0)) return "";
  const body = typeof value === "string" && !value.trim().startsWith("<li")
    ? `<p>${escapeHtml(value)}</p>`
    : `<ul>${value}</ul>`;
  return `<details class="client-material-section" open><summary>${escapeHtml(title)}</summary>${body}</details>`;
}

function renderTransientBubble(chat) {
  if (!transientBubble) return;
  const bubble = document.createElement("div");
  bubble.className = `bubble assistant transient ${transientBubble.type}`;
  if (transientBubble.type === "pending") {
    bubble.innerHTML = `
      <div class="thinking-row">
        <span class="thinking-dots"><i></i><i></i><i></i></span>
        <span>${escapeHtml(transientBubble.message)}</span>
      </div>
      <div class="meta">${escapeHtml(transientBubble.detail || "请保持页面打开，生成完成后会自动出现下一问。")}</div>
    `;
  } else {
    bubble.innerHTML = `
      <strong>${escapeHtml(transientBubble.title || "生成失败")}</strong>
      <div>${escapeHtml(transientBubble.message)}</div>
      <div class="retry-row">
        <button type="button" id="inlineRetryBtn">${escapeHtml(transientBubble.actionLabel || "重试")}</button>
      </div>
    `;
  }
  chat.appendChild(bubble);
  const retry = bubble.querySelector("#inlineRetryBtn");
  if (retry) retry.addEventListener("click", retryAfterError);
}

function getVisibleConversation() {
  return (sessionData.conversation || []).filter((m) => {
    if (m.role === "user") return true;
    if (m.role !== "assistant") return false;
    if (!m.meta_json) return true;
    try {
      const parsed = JSON.parse(m.meta_json);
      if (parsed.status === "ready" && hasClientBrief()) return false;
      return Boolean(parsed.question || parsed.focus || parsed.status === "questioning" || parsed.status === "ready");
    } catch (_) {
      return true;
    }
  });
}

function renderClientNextStep() {
  const conversation = getVisibleConversation();
  const latest = conversation[conversation.length - 1];
  const form = el("clientReplyForm");
  const textarea = form.elements.content;
  const button = form.querySelector("button");
  const startBtn = el("clientStartBtn");
  const status = sessionData.session.status;
  const hasBrief = hasClientBrief();

  startBtn.hidden = true;
  setReplyEnabled(false);
  textarea.placeholder = "等待 AI 问题生成后再回答。";

  if (hasBrief || status === "client_brief_ready") {
    el("clientNextStep").textContent = "会前信息已经整理完成，左侧可以查看整理结果。顾问会查看完整记录，并与你预约下一次真人诊断。";
    textarea.placeholder = "会前访谈已完成。";
    startBtn.textContent = "重新生成会前整理";
    startBtn.hidden = false;
    startBtn.disabled = clientBusy;
    return;
  }

  if (status === "ready_for_report") {
    el("clientNextStep").textContent = "这轮会前访谈已结束，输入框已关闭。你可以生成会前整理，然后联系顾问继续真人访谈。";
    textarea.placeholder = "会前访谈已结束，请生成会前整理。";
    startBtn.textContent = "生成会前整理";
    startBtn.hidden = false;
    startBtn.disabled = clientBusy;
    return;
  }

  if (status === "intake") {
    el("clientNextStep").textContent = "顾问还在整理你的材料。请稍后再回来，或等待顾问通知。";
    textarea.placeholder = "顾问整理材料后，这里会开放回答。";
    return;
  }

  if (!latest) {
    el("clientNextStep").textContent = "材料已整理。点击开始后，AI 会根据你的材料先问第一道问题。";
    textarea.placeholder = "请先点击上方“开始 AI 会前访谈”。";
    startBtn.textContent = "开始 AI 会前访谈";
    startBtn.hidden = false;
    startBtn.disabled = clientBusy;
    return;
  }
  if (latest.role === "assistant") {
    el("clientNextStep").textContent = "请回答上面这道问题。尽量讲具体事件，不需要写得漂亮。";
    textarea.placeholder = "请尽量用具体事件回答：背景、你做了什么、结果、别人怎么反馈。";
    setReplyEnabled(!clientBusy);
  } else {
    el("clientNextStep").textContent = "你的回答已保存，但 AI 下一步还没有生成。点击“继续生成下一问”，系统会接着追问；如果信息已经足够，会自动整理会前信息。";
    textarea.placeholder = "上一条回答已保存。请先点击“继续生成下一问”。";
    startBtn.textContent = "继续生成下一问";
    startBtn.hidden = false;
    startBtn.disabled = clientBusy;
  }
}

function setReplyEnabled(enabled) {
  const form = el("clientReplyForm");
  const textarea = form.elements.content;
  const button = form.querySelector("button");
  textarea.disabled = !enabled;
  textarea.readOnly = !enabled;
  button.disabled = !enabled;
  if (enabled) {
    textarea.removeAttribute("disabled");
    textarea.removeAttribute("readonly");
    button.removeAttribute("disabled");
  } else {
    textarea.setAttribute("disabled", "");
    button.setAttribute("disabled", "");
  }
}

function focusReplyIfAnswerable() {
  const conversation = getVisibleConversation();
  const latest = conversation[conversation.length - 1];
  const textarea = el("clientReplyForm").elements.content;
  if (latest?.role === "assistant" && !clientBusy && !textarea.disabled) {
    setTimeout(() => textarea.focus(), 30);
  }
}

async function ensureAiReady() {
  const health = await api("/api/health");
  if (!health.has_api_key) {
    throw new Error("顾问后台还没有连接 AI。请联系顾问先在后台右上角设置 API Key，然后再开始会前访谈。");
  }
  return health;
}

function showClientError(prefix, err) {
  transientBubble = {
    type: "error",
    title: prefix,
    message: humanizeError(err),
    actionLabel: "重试生成下一问",
  };
  renderClientChat();
  el("clientNextStep").textContent = "刚才没有生成成功。可以点击聊天区里的“重试生成下一问”，或稍后再试。";
}

function setPending(message, detail = "") {
  transientBubble = { type: "pending", message, detail };
  renderClientChat();
}

function clearTransient() {
  transientBubble = null;
}

function humanizeError(err) {
  const raw = err?.message || String(err);
  if (/api key|OPENAI_API_KEY|还没有设置|没有连接 AI/i.test(raw)) {
    return "模型接口还没有连接好。请联系顾问检查后台右上角的模型 Key 和接口路由。";
  }
  if (/quota|额度|billing|insufficient/i.test(raw)) {
    return "模型接口额度不足或账单不可用。请联系顾问更换可用 Key。";
  }
  if (/timeout|超时|timed out/i.test(raw)) {
    return "模型接口响应超时。你的回答通常已经保存，可以直接重试生成下一问。";
  }
  if (/network|failed to fetch|连接.*失败|远端断开|SSL/i.test(raw)) {
    return "模型接口连接不稳定。你的回答通常已经保存，可以稍后重试。";
  }
  if (/model|模型不可用|not found|404/i.test(raw)) {
    return "当前模型或接口路由不可用。请联系顾问检查模型名称和接口地址。";
  }
  return raw;
}

async function retryAfterError() {
  if (clientBusy) return;
  await startClientInterview({ retry: true });
}

function hasClientBrief() {
  return (sessionData.reports || []).some((r) => r.report_type === "client_pre_session_brief");
}

async function startClientInterview(options = {}) {
  if (clientBusy) return;
  clientBusy = true;
  const shouldBuildBrief = ["ready_for_report", "client_brief_ready"].includes(sessionData?.session?.status) || hasClientBrief();
  setPending(
    shouldBuildBrief ? "AI 正在整理会前基本信息..." : options.retry ? "AI 正在重新生成下一问..." : "AI 正在生成下一问...",
    shouldBuildBrief ? "整理完成后，左侧会切换成你和顾问共同查看的会前材料。" : "正在结合材料和刚才的回答判断下一步该问什么。"
  );
  renderClientNextStep();
  try {
    await ensureAiReady();
    if (shouldBuildBrief) {
      setPending("AI 认为信息基本足够，正在整理会前基本信息...", "整理完成后你会在下方看到会前摘要。");
      await api(`/api/sessions/${sessionId}/client-brief`, { method: "POST" });
    } else {
      await api(`/api/sessions/${sessionId}/next-question`, { method: "POST" });
    }
    clearTransient();
    await loadClientSession();
  } catch (err) {
    showClientError(shouldBuildBrief ? "会前整理生成失败" : "暂时不能开始 AI 会前访谈", err);
  } finally {
    clientBusy = false;
    renderClientNextStep();
  }
}

async function submitClientReply(e) {
  e.preventDefault();
  if (clientBusy) return;
  const form = e.currentTarget;
  const content = form.elements.content.value.trim();
  if (!content) return;
  form.elements.content.value = "";
  clientBusy = true;
  setPending("AI 正在阅读你的回答...", "正在提取关键经历、个人动作和证据缺口。");
  renderClientNextStep();
  try {
    await ensureAiReady();
    await api(`/api/sessions/${sessionId}/client-chat-turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    clearTransient();
    await loadClientSession();
  } catch (err) {
    await loadClientSession().catch(() => {});
    showClientError("提交后生成下一步失败", err);
  } finally {
    clientBusy = false;
    renderClientNextStep();
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  el("clientReplyForm").addEventListener("submit", submitClientReply);
  el("clientStartBtn").addEventListener("click", () => startClientInterview());
  el("clientSourceTab")?.addEventListener("click", () => {
    activeMaterialTab = "source";
    renderClientMaterialSummary();
  });
  el("clientBriefTab")?.addEventListener("click", () => {
    activeMaterialTab = "brief";
    renderClientMaterialSummary();
  });
  try {
    await loadClientSession();
  } catch (err) {
    el("clientTitle").textContent = "访谈载入失败";
    el("clientNextStep").textContent = err.message;
    el("clientStatus").textContent = "异常";
  }
});
