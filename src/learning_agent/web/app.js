/* 最小聊天界面：对话管理、消息展示、运行状态与来源链接。 */
const state = {
  conversations: [],
  currentId: null,
  messages: [],
  busy: false,
};

const elements = {
  conversationList: document.getElementById("conversation-list"),
  newButton: document.getElementById("new-conversation"),
  title: document.getElementById("conversation-title"),
  deleteButton: document.getElementById("delete-conversation"),
  messageList: document.getElementById("message-list"),
  statusBar: document.getElementById("status-bar"),
  composer: document.getElementById("composer"),
  input: document.getElementById("message-input"),
  sendButton: document.getElementById("send-button"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = data?.error?.message || `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return data;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const TERMINAL_RUN_STATUSES = ["completed", "failed", "cancelled"];

async function waitForRun(runId, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  let delay = 1000;
  for (;;) {
    const run = await api(`/api/v1/runs/${runId}`);
    if (TERMINAL_RUN_STATUSES.includes(run.status)) {
      return run;
    }
    if (Date.now() > deadline) {
      throw new Error("运行超时，请稍后在对话中查看结果");
    }
    await sleep(delay);
    delay = Math.min(Math.round(delay * 1.4), 3000);
  }
}

function setStatus(text, kind) {
  if (!text) {
    elements.statusBar.hidden = true;
    elements.statusBar.textContent = "";
    elements.statusBar.className = "status-bar";
    return;
  }
  elements.statusBar.hidden = false;
  elements.statusBar.textContent = text;
  elements.statusBar.className = `status-bar ${kind || ""}`.trim();
}

function setBusy(busy) {
  state.busy = busy;
  elements.sendButton.disabled = busy;
  elements.input.disabled = busy;
}

function formatTime(isoText) {
  const date = new Date(isoText);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN");
}

function renderConversations() {
  elements.conversationList.replaceChildren();
  for (const conversation of state.conversations) {
    const item = document.createElement("li");
    item.textContent = conversation.title || "未命名对话";
    item.title = conversation.title || "";
    if (conversation.id === state.currentId) {
      item.classList.add("active");
    }
    item.addEventListener("click", () => selectConversation(conversation.id));
    elements.conversationList.append(item);
  }
}

function renderSources(sources) {
  const wrapper = document.createElement("div");
  wrapper.className = "sources";
  const label = document.createElement("div");
  label.className = "sources-title";
  label.textContent = "来源";
  wrapper.append(label);
  for (const source of sources) {
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const truncated = source.truncated ? "，内容已截断" : "";
    link.textContent = `[${source.source_id}] ${source.title}（获取于 ${formatTime(
      source.fetched_at,
    )}${truncated}）`;
    wrapper.append(link);
  }
  return wrapper;
}

function renderMessages() {
  elements.messageList.replaceChildren();
  if (state.messages.length === 0) {
    const hint = document.createElement("p");
    hint.className = "empty-hint";
    hint.textContent =
      "发送一个受支持的官方技术文档 URL，我会基于文档内容讲解核心概念并给出来源。";
    elements.messageList.append(hint);
    return;
  }
  for (const message of state.messages) {
    const article = document.createElement("article");
    article.className = `message message--${message.role}`;
    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = `${message.role === "user" ? "我" : "助手"} · ${formatTime(
      message.created_at,
    )}`;
    const body = document.createElement("div");
    body.textContent = message.content;
    article.append(meta, body);
    if (message.sources && message.sources.length > 0) {
      article.append(renderSources(message.sources));
    }
    elements.messageList.append(article);
  }
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
}

async function loadConversations() {
  state.conversations = await api("/api/v1/conversations");
  renderConversations();
}

async function loadMessages(conversationId) {
  state.messages = await api(`/api/v1/conversations/${conversationId}/messages`);
  renderMessages();
}

async function selectConversation(conversationId) {
  state.currentId = conversationId;
  const conversation = state.conversations.find((item) => item.id === conversationId);
  elements.title.textContent = conversation ? conversation.title : "对话";
  elements.deleteButton.disabled = false;
  renderConversations();
  await loadMessages(conversationId);
}

async function createConversation() {
  const created = await api("/api/v1/conversations", {
    method: "POST",
    body: JSON.stringify({}),
  });
  await loadConversations();
  await selectConversation(created.id);
  return created.id;
}

async function deleteConversation() {
  if (!state.currentId) {
    return;
  }
  const confirmed = window.confirm(
    "删除该对话及其全部消息、运行记录与文档快照？此操作不可撤销。",
  );
  if (!confirmed) {
    return;
  }
  try {
    await api(`/api/v1/conversations/${state.currentId}`, { method: "DELETE" });
    setStatus("对话已删除", "ok");
  } catch (error) {
    setStatus(`删除失败：${error.message}`, "error");
    return;
  }
  state.currentId = null;
  state.messages = [];
  elements.title.textContent = "未选择对话";
  elements.deleteButton.disabled = true;
  await loadConversations();
  renderMessages();
  if (state.conversations.length > 0) {
    await selectConversation(state.conversations[0].id);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.busy) {
    return;
  }
  const content = elements.input.value.trim();
  if (!content) {
    setStatus("请输入内容后再发送", "error");
    return;
  }
  setBusy(true);
  try {
    if (!state.currentId) {
      await createConversation();
    }
    setStatus("正在处理：获取文档并生成回答，请稍候…", "loading");
    state.messages.push({
      id: -1,
      role: "user",
      content,
      created_at: new Date().toISOString(),
      sources: [],
    });
    elements.input.value = "";
    renderMessages();
    const accepted = await api(
      `/api/v1/conversations/${state.currentId}/messages?mode=async`,
      { method: "POST", body: JSON.stringify({ content }) },
    );
    setStatus("运行中：正在获取文档并生成回答…", "loading");
    const run = await waitForRun(accepted.run_id);
    await loadMessages(state.currentId);
    await loadConversations();
    if (run.status === "completed") {
      setStatus("运行完成", "ok");
    } else {
      setStatus(`运行未完成（${run.error_code || run.status}）`, "error");
    }
  } catch (error) {
    if (state.currentId) {
      await loadMessages(state.currentId).catch(() => {});
    }
    setStatus(`运行失败：${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

elements.newButton.addEventListener("click", () => {
  createConversation().catch((error) => setStatus(`新建失败：${error.message}`, "error"));
});
elements.deleteButton.addEventListener("click", () => {
  deleteConversation().catch((error) => setStatus(`删除失败：${error.message}`, "error"));
});
elements.composer.addEventListener("submit", sendMessage);

(async function init() {
  try {
    await loadConversations();
    if (state.conversations.length > 0) {
      await selectConversation(state.conversations[0].id);
    } else {
      renderMessages();
    }
  } catch (error) {
    setStatus(`初始化失败：${error.message}`, "error");
  }
})();
