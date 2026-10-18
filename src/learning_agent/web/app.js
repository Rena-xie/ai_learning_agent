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

const INLINE_PATTERN = /(\*\*[^*]+\*\*|`[^`]+`|\[S\d{1,3}\])/g;

function renderInline(text, sourcesById) {
  const nodes = [];
  let lastIndex = 0;
  let match = INLINE_PATTERN.exec(text);
  while (match !== null) {
    if (match.index > lastIndex) {
      nodes.push(document.createTextNode(text.slice(lastIndex, match.index)));
    }
    const token = match[0];
    if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      nodes.push(strong);
    } else if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      nodes.push(code);
    } else {
      // [S1]：来源编号由服务端校验，这里链接到服务端提供的真实来源地址
      const sourceId = token.slice(1, -1);
      const source = sourcesById.get(sourceId);
      if (source) {
        const link = document.createElement("a");
        link.className = "citation";
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = `[${sourceId}]`;
        nodes.push(link);
      } else {
        nodes.push(document.createTextNode(token));
      }
    }
    lastIndex = INLINE_PATTERN.lastIndex;
    match = INLINE_PATTERN.exec(text);
  }
  if (lastIndex < text.length) {
    nodes.push(document.createTextNode(text.slice(lastIndex)));
  }
  return nodes;
}

// 极简渲染器：只处理标题、列表、粗体、行内代码与 [S1]，一律生成 DOM 节点，
// 不使用 innerHTML，因此模型输出中的 HTML/脚本不会被当作标记执行。
function renderMarkdown(content, sources) {
  const sourcesById = new Map(
    (sources || []).map((source) => [source.source_id, source]),
  );
  const container = document.createElement("div");
  container.className = "message-body";
  const lines = String(content || "").split("\n");
  let list = null;
  let listType = null;

  const flushList = () => {
    if (list) {
      container.append(list);
      list = null;
      listType = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line.trim()) {
      flushList();
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushList();
      const level = Math.min(heading[1].length + 2, 6);
      const el = document.createElement(`h${level}`);
      el.append(...renderInline(heading[2], sourcesById));
      container.append(el);
      continue;
    }
    const unordered = /^[-*]\s+(.*)$/.exec(line);
    const ordered = /^\d+[.)]\s+(.*)$/.exec(line);
    if (unordered || ordered) {
      const wanted = ordered ? "ol" : "ul";
      if (listType !== wanted) {
        flushList();
        list = document.createElement(wanted);
        listType = wanted;
      }
      const item = document.createElement("li");
      item.append(...renderInline(ordered ? ordered[1] : unordered[1], sourcesById));
      list.append(item);
      continue;
    }
    flushList();
    const paragraph = document.createElement("p");
    paragraph.append(...renderInline(line, sourcesById));
    container.append(paragraph);
  }
  flushList();
  return container;
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
    const fromKnowledge = source.provider === "knowledge_base";
    const origin = fromKnowledge ? "知识库" : "网页";
    const when = fromKnowledge ? "召回于" : "获取于";
    link.textContent = `[${source.source_id}] ${source.title}（${origin}，${when} ${formatTime(
      source.fetched_at,
    )}${truncated}）`;
    wrapper.append(link);
  }
  return wrapper;
}

function renderMessages() {
  const list = elements.messageList;
  const atBottom =
    list.scrollHeight - list.scrollTop - list.clientHeight < 48;
  list.replaceChildren();
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
    const notice = message.kind === "ingestion_notice" ? " message--notice" : "";
    article.className = `message message--${message.role}${notice}`;
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const speaker =
      message.kind === "ingestion_notice"
        ? "知识库"
        : message.role === "user"
          ? "我"
          : "助手";
    meta.textContent = `${speaker} · ${formatTime(message.created_at)}`;
    const body = renderMarkdown(message.content, message.sources);
    article.append(meta, body);
    if (message.sources && message.sources.length > 0) {
      article.append(renderSources(message.sources));
    }
    elements.messageList.append(article);
  }
  if (atBottom) {
    list.scrollTop = list.scrollHeight;
  }
}

async function loadConversations() {
  state.conversations = await api("/api/v1/conversations");
  renderConversations();
}

async function loadMessages(conversationId) {
  state.messages = await api(`/api/v1/conversations/${conversationId}/messages`);
  renderMessages();
}

function startMessagePolling() {
  // 后台 watcher 会写入入库完成/失败通知；页面可见时轻量轮询消息。
  setInterval(async () => {
    if (
      document.visibilityState !== "visible" ||
      state.busy ||
      !state.currentId
    ) {
      return;
    }
    try {
      await loadMessages(state.currentId);
      await loadConversations();
    } catch {
      // 本地轮询失败不打断输入与当前界面
    }
  }, 5000);
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
  startMessagePolling();
})();
