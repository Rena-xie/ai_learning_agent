/* 最小学习工作台：Digest → 笔记 → 自测/闪卡 → 复习。 */
const studyState = {
  notes: [],
  currentNote: null,
  digestDate: null,
  digestItems: [],
  preferences: null,
  sources: [],
  profile: null,
  busy: false,
};

const studyElements = {
  refresh: document.getElementById("refresh-study"),
  stats: document.getElementById("progress-stats"),
  digestStatus: document.getElementById("digest-status"),
  digestItems: document.getElementById("digest-items"),
  notesEmpty: document.getElementById("notes-empty"),
  noteList: document.getElementById("note-list"),
  noteTitle: document.getElementById("note-detail-title"),
  noteTopic: document.getElementById("note-detail-topic"),
  noteContent: document.getElementById("note-content"),
  noteSources: document.getElementById("note-sources"),
  quizButton: document.getElementById("generate-quiz"),
  cardsButton: document.getElementById("generate-cards"),
  quizOutput: document.getElementById("quiz-output"),
  dueCount: document.getElementById("due-count"),
  reviewsEmpty: document.getElementById("reviews-empty"),
  dueReviews: document.getElementById("due-reviews"),
  preferencesForm: document.getElementById("preferences-form"),
  preferredTopics: document.getElementById("preferred-topics"),
  preferredTopN: document.getElementById("preferred-top-n"),
  preferredCatchUp: document.getElementById("preferred-catch-up"),
  sourceOptions: document.getElementById("source-options"),
  savePreferences: document.getElementById("save-preferences"),
  sourcesEmpty: document.getElementById("sources-empty"),
  sourceHealthList: document.getElementById("source-health-list"),
  sourceAddForm: document.getElementById("source-add-form"),
  sourceName: document.getElementById("source-name"),
  sourceFeedUrl: document.getElementById("source-feed-url"),
  sourceCategory: document.getElementById("source-category"),
  profileEnabledLabel: document.getElementById("profile-enabled-label"),
  toggleProfile: document.getElementById("toggle-profile"),
  profileEmpty: document.getElementById("profile-empty"),
  profileTopics: document.getElementById("profile-topics"),
  resetProfile: document.getElementById("reset-profile"),
  status: document.getElementById("study-status"),
};

async function studyApi(path, options = {}) {
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

function studyElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function setStudyStatus(text, kind) {
  if (!text) {
    studyElements.status.hidden = true;
    studyElements.status.textContent = "";
    studyElements.status.className = "study-status";
    return;
  }
  studyElements.status.hidden = false;
  studyElements.status.textContent = text;
  studyElements.status.className = `study-status ${kind || ""}`.trim();
}

function formatStudyTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN");
}

function setStudyBusy(busy) {
  studyState.busy = busy;
  studyElements.refresh.disabled = busy;
  studyElements.quizButton.disabled = busy || !studyState.currentNote;
  studyElements.cardsButton.disabled = busy || !studyState.currentNote;
  studyElements.savePreferences.disabled = busy;
  studyElements.toggleProfile.disabled = busy || studyState.profile === null;
  studyElements.resetProfile.disabled =
    busy ||
    !studyState.profile ||
    studyState.profile.topics.length === 0;
  const addSourceButton = studyElements.sourceAddForm.querySelector("button");
  if (addSourceButton) {
    addSourceButton.disabled = busy;
  }
}

function renderProgress(progress) {
  const values = [
    [progress.notes_total, "笔记"],
    [progress.quizzes_total, "自测"],
    [progress.flashcards_total, "闪卡"],
    [progress.due_total, "待复习"],
  ];
  studyElements.stats.replaceChildren();
  for (const [value, label] of values) {
    const card = studyElement("article", "stat-card");
    card.append(
      studyElement("strong", "", String(value)),
      studyElement("span", "", label),
    );
    studyElements.stats.append(card);
  }
}

function findNoteForItem(itemId) {
  return studyState.notes.find((note) => note.push_item_id === itemId);
}

const TERMINAL_INGESTION_STATUSES = ["completed", "failed", "cancelled"];

function renderDigest() {
  studyElements.digestItems.replaceChildren();
  if (studyState.digestItems.length === 0) {
    studyElements.digestStatus.hidden = false;
    studyElements.digestStatus.textContent = "今天还没有可学习的资讯。";
    return;
  }
  studyElements.digestStatus.hidden = true;
  for (const item of studyState.digestItems) {
    const card = studyElement("article", "item-card");
    card.append(
      studyElement("h3", "", item.title),
      studyElement("p", "item-summary", item.summary),
      studyElement(
        "p",
        "item-meta",
        `${item.source_name} · ${item.topic}`,
      ),
    );
    const actions = studyElement("div", "item-actions");
    const link = studyElement("a", "text-link", "打开原文");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const noteButton = studyElement(
      "button",
      "primary",
      findNoteForItem(item.item_id) ? "查看笔记" : "生成笔记",
    );
    noteButton.type = "button";
    noteButton.addEventListener("click", () => {
      const existing = findNoteForItem(item.item_id);
      if (existing) {
        selectStudyNote(existing.id).catch((error) =>
          setStudyStatus(`读取失败：${error.message}`, "error"),
        );
        return;
      }
      createStudyNote(item.item_id).catch((error) =>
        setStudyStatus(`生成失败：${error.message}`, "error"),
      );
    });

    const feedback = studyElement("div", "feedback-actions");
    feedback.append(studyElement("span", "item-meta", "反馈"));
    for (const [action, label] of [
      ["useful", "有用"],
      ["not_useful", "无用"],
    ]) {
      const feedbackButton = studyElement("button", "feedback-button", label);
      feedbackButton.type = "button";
      feedbackButton.setAttribute(
        "aria-pressed",
        String(item.feedback_action === action),
      );
      if (item.feedback_action === action) {
        feedbackButton.classList.add("selected");
      }
      feedbackButton.addEventListener("click", () => {
        submitDigestFeedback(item, action).catch((error) =>
          setStudyStatus(`反馈失败：${error.message}`, "error"),
        );
      });
      feedback.append(feedbackButton);
    }

    const ingestionLabel =
      item.ingestion_status === "completed"
        ? "已入库"
        : ["queued", "processing", "running"].includes(item.ingestion_status)
          ? "入库中"
          : "收藏到 RAG";
    const saveButton = studyElement("button", "", ingestionLabel);
    saveButton.type = "button";
    saveButton.disabled = item.ingestion_status === "completed";
    saveButton.addEventListener("click", () => {
      saveDigestItem(item).catch((error) =>
        setStudyStatus(`收藏失败：${error.message}`, "error"),
      );
    });
    actions.append(link, feedback, saveButton, noteButton);
    card.append(actions);
    studyElements.digestItems.append(card);
  }
}

async function submitDigestFeedback(item, action) {
  if (studyState.busy || !studyState.digestDate) {
    return;
  }
  setStudyBusy(true);
  try {
    const result = await studyApi(
      `/api/v1/digests/${studyState.digestDate}/feedback`,
      {
        method: "POST",
        body: JSON.stringify({ item_id: item.item_id, action }),
      },
    );
    item.feedback_action = result.action;
    renderDigest();
    setStudyStatus(
      result.action === "useful" ? "已标记为有用" : "已标记为无用",
      "ok",
    );
  } finally {
    setStudyBusy(false);
  }
}

async function saveDigestItem(item) {
  if (studyState.busy || !studyState.digestDate) {
    return;
  }
  if (item.ingestion_job_id) {
    await refreshIngestionStatus(item);
    return;
  }
  setStudyBusy(true);
  try {
    const result = await studyApi(
      `/api/v1/digests/${studyState.digestDate}/items/${item.item_id}/save`,
      { method: "POST" },
    );
    item.ingestion_job_id = result.job_id;
    item.ingestion_status = result.status;
    renderDigest();
    setStudyStatus("已开始入库，完成后会更新状态", "ok");
    scheduleIngestionPoll(item.item_id, result.job_id, 0);
  } finally {
    setStudyBusy(false);
  }
}

function scheduleIngestionPoll(itemId, jobId, attempt) {
  if (attempt >= 12) {
    return;
  }
  window.setTimeout(async () => {
    const item = studyState.digestItems.find((entry) => entry.item_id === itemId);
    if (!item || item.ingestion_job_id !== jobId) {
      return;
    }
    try {
      await refreshIngestionStatus(item);
      if (!TERMINAL_INGESTION_STATUSES.includes(item.ingestion_status)) {
        scheduleIngestionPoll(itemId, jobId, attempt + 1);
      }
    } catch {
      scheduleIngestionPoll(itemId, jobId, attempt + 1);
    }
  }, attempt === 0 ? 1500 : 3000);
}

async function refreshIngestionStatus(item) {
  if (!studyState.digestDate) {
    return;
  }
  try {
    const result = await studyApi(
      `/api/v1/digests/${studyState.digestDate}/items/${item.item_id}/ingestion`,
    );
    item.ingestion_status = result.status;
    item.ingestion_error = result.error_message;
    renderDigest();
    if (result.status === "completed") {
      setStudyStatus("资讯已完成入库", "ok");
    } else if (result.status === "failed") {
      setStudyStatus(
        result.error_message || "资讯入库失败，请稍后重试",
        "error",
      );
    }
  } catch (error) {
    if (!item.ingestion_job_id) {
      throw error;
    }
  }
}

function renderNotes() {
  studyElements.noteList.replaceChildren();
  studyElements.notesEmpty.hidden = studyState.notes.length > 0;
  for (const note of studyState.notes) {
    const button = studyElement("button", "note-list-item");
    button.type = "button";
    button.append(
      studyElement("strong", "", note.title),
      studyElement("span", "", `${note.topic_name} · ${note.digest_date}`),
    );
    if (studyState.currentNote?.id === note.id) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      selectStudyNote(note.id).catch((error) =>
        setStudyStatus(`读取失败：${error.message}`, "error"),
      );
    });
    studyElements.noteList.append(button);
  }
}

function renderNoteDetail(note) {
  studyElements.noteTitle.textContent = note.title;
  studyElements.noteTopic.textContent = `${note.topic_name} · ${note.digest_date}`;
  studyElements.noteContent.hidden = false;
  studyElements.noteContent.textContent = note.content;
  studyElements.noteSources.replaceChildren();
  for (const source of note.sources) {
    const row = studyElement("div", "source-row");
    const link = studyElement(
      "a",
      "text-link",
      `[${source.label}] ${source.publisher} · ${source.title}`,
    );
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    row.append(link);
    studyElements.noteSources.append(row);
  }
  studyElements.quizButton.disabled = studyState.busy;
  studyElements.cardsButton.disabled = studyState.busy;
}

async function selectStudyNote(noteId) {
  const note = await studyApi(`/api/v1/learning/notes/${noteId}`);
  studyState.currentNote = note;
  studyElements.quizOutput.replaceChildren();
  renderNotes();
  renderNoteDetail(note);
  document.querySelector(".study-columns")?.scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

async function createStudyNote(itemId) {
  if (studyState.busy) {
    return;
  }
  setStudyBusy(true);
  setStudyStatus("正在生成带来源的笔记…", "loading");
  try {
    const note = await studyApi("/api/v1/learning/notes", {
      method: "POST",
      body: JSON.stringify({
        digest_date: studyState.digestDate,
        item_id: itemId,
      }),
    });
    studyState.notes.unshift(note);
    studyState.currentNote = note;
    renderDigest();
    renderNotes();
    renderNoteDetail(note);
    await loadProgress();
    setStudyStatus("学习笔记已生成", "ok");
  } finally {
    setStudyBusy(false);
  }
}

function renderQuiz(quiz) {
  studyElements.quizOutput.replaceChildren();
  studyElements.quizOutput.append(studyElement("h3", "", "自测题"));
  for (const question of quiz.questions) {
    const details = studyElement("details", "quiz-question");
    details.append(
      studyElement(
        "summary",
        "",
        `${question.position}. ${question.question}`,
      ),
    );
    const answer = studyElement("p", "quiz-answer", question.answer);
    details.append(answer);
    if (question.source) {
      const link = studyElement(
        "a",
        "text-link",
        `来源 [${question.source.label}] ${question.source.publisher}`,
      );
      link.href = question.source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      details.append(link);
    }
    studyElements.quizOutput.append(details);
  }
}

async function generateQuiz() {
  if (!studyState.currentNote || studyState.busy) {
    return;
  }
  setStudyBusy(true);
  setStudyStatus("正在生成自测题…", "loading");
  try {
    const quiz = await studyApi("/api/v1/learning/quizzes", {
      method: "POST",
      body: JSON.stringify({ note_id: studyState.currentNote.id }),
    });
    renderQuiz(quiz);
    await loadProgress();
    setStudyStatus("自测题已生成", "ok");
  } finally {
    setStudyBusy(false);
  }
}

async function generateFlashcards() {
  if (!studyState.currentNote || studyState.busy) {
    return;
  }
  setStudyBusy(true);
  setStudyStatus("正在生成闪卡…", "loading");
  try {
    const cards = await studyApi("/api/v1/learning/flashcards", {
      method: "POST",
      body: JSON.stringify({ note_id: studyState.currentNote.id }),
    });
    await Promise.all([loadDueReviews(), loadProgress()]);
    setStudyStatus(`已准备 ${cards.length} 张闪卡`, "ok");
  } finally {
    setStudyBusy(false);
  }
}

function renderDueReviews(cards) {
  studyElements.dueReviews.replaceChildren();
  studyElements.dueCount.textContent = `${cards.length} 张`;
  studyElements.reviewsEmpty.hidden = cards.length > 0;
  for (const card of cards) {
    const article = studyElement("article", "review-card");
    article.append(
      studyElement("p", "item-meta", card.topic_name),
      studyElement("h3", "", card.question),
    );
    const answer = studyElement("p", "review-answer", card.answer);
    answer.hidden = true;
    const show = studyElement("button", "", "显示答案");
    show.type = "button";
    show.addEventListener("click", () => {
      answer.hidden = !answer.hidden;
      actions.hidden = answer.hidden;
      show.textContent = answer.hidden ? "显示答案" : "收起答案";
    });
    const actions = studyElement("div", "rating-actions");
    actions.hidden = true;
    for (const [rating, label] of [
      ["again", "忘记了"],
      ["hard", "有点难"],
      ["good", "记住了"],
      ["easy", "很简单"],
    ]) {
      const button = studyElement("button", `rating-${rating}`, label);
      button.type = "button";
      button.addEventListener("click", () => {
        submitCardReview(card.id, rating).catch((error) =>
          setStudyStatus(`复习提交失败：${error.message}`, "error"),
        );
      });
      actions.append(button);
    }
    article.append(answer, show, actions);
    studyElements.dueReviews.append(article);
  }
}

async function submitCardReview(cardId, rating) {
  if (studyState.busy) {
    return;
  }
  setStudyBusy(true);
  try {
    const updated = await studyApi(`/api/v1/learning/reviews/${cardId}`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    });
    setStudyStatus(
      `复习已记录，下次复习：${formatStudyTime(updated.next_review_at)}`,
      "ok",
    );
    await Promise.all([loadDueReviews(), loadProgress()]);
  } finally {
    setStudyBusy(false);
  }
}

async function loadProgress() {
  const progress = await studyApi("/api/v1/learning/progress");
  renderProgress(progress);
}

async function loadDueReviews() {
  const due = await studyApi("/api/v1/learning/reviews/due");
  renderDueReviews(due.cards);
}

async function loadDigest() {
  studyElements.digestItems.replaceChildren();
  try {
    const digest = await studyApi("/api/v1/digests/today");
    studyState.digestDate = digest.digest_date;
    studyState.digestItems = digest.items || [];
  } catch (error) {
    studyState.digestDate = null;
    studyState.digestItems = [];
    studyElements.digestStatus.hidden = false;
    studyElements.digestStatus.textContent = error.message;
    return;
  }
  renderDigest();
}

function parseTopics(value) {
  return String(value || "")
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function renderPreferences() {
  if (!studyState.preferences) {
    return;
  }
  studyElements.preferredTopics.value =
    studyState.preferences.topics.join(", ");
  studyElements.preferredTopN.value = String(studyState.preferences.top_n);
  studyElements.preferredCatchUp.value = String(
    studyState.preferences.catch_up_days,
  );
  studyElements.sourceOptions.replaceChildren();
  for (const source of studyState.sources) {
    const label = studyElement("label", "source-option");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = source.id;
    checkbox.checked =
      studyState.preferences.source_ids.length === 0 ||
      studyState.preferences.source_ids.includes(source.id);
    checkbox.disabled = !source.enabled;
    label.append(
      checkbox,
      studyElement(
        "span",
        "",
        `${source.name} · ${source.category}${source.enabled ? "" : "（已停用）"}`,
      ),
    );
    studyElements.sourceOptions.append(label);
  }
}

function sourceHealth(source) {
  if (!source.enabled) {
    return ["已停用", "disabled"];
  }
  if (source.last_error) {
    return ["异常", "error"];
  }
  if (source.last_checked_at) {
    return ["正常", "ok"];
  }
  return ["未检查", "unknown"];
}

function renderSources() {
  studyElements.sourceHealthList.replaceChildren();
  studyElements.sourcesEmpty.hidden = studyState.sources.length > 0;
  for (const source of studyState.sources) {
    const [healthLabel, healthClass] = sourceHealth(source);
    const row = studyElement("article", "source-health-row");
    const details = studyElement("div", "source-health-details");
    details.append(
      studyElement("strong", "", source.name),
      studyElement("span", "item-meta", `${source.category} · ${source.feed_url}`),
      studyElement(
        "span",
        "item-meta",
        source.last_checked_at
          ? `最近检查：${formatStudyTime(source.last_checked_at)}`
          : "尚未检查",
      ),
    );
    if (source.last_error) {
      const error = studyElement("span", "source-error", source.last_error);
      error.title = source.last_error;
      details.append(error);
    }
    const controls = studyElement("div", "source-health-controls");
    controls.append(studyElement("span", `health-badge ${healthClass}`, healthLabel));
    const toggle = studyElement(
      "button",
      "",
      source.enabled ? "停用" : "启用",
    );
    toggle.type = "button";
    toggle.addEventListener("click", () => {
      toggleSource(source).catch((error) =>
        setStudyStatus(`更新来源失败：${error.message}`, "error"),
      );
    });
    controls.append(toggle);
    row.append(details, controls);
    studyElements.sourceHealthList.append(row);
  }
}

async function loadPushSettings() {
  const [preferences, sources] = await Promise.all([
    studyApi("/api/v1/push/preferences"),
    studyApi("/api/v1/push/sources"),
  ]);
  studyState.preferences = preferences;
  studyState.sources = sources;
  renderPreferences();
  renderSources();
}

async function savePreferences(event) {
  event.preventDefault();
  if (studyState.busy) {
    return;
  }
  setStudyBusy(true);
  try {
    const sourceIds = Array.from(
      studyElements.sourceOptions.querySelectorAll('input[type="checkbox"]'),
    )
      .filter((checkbox) => checkbox.checked)
      .map((checkbox) => checkbox.value);
    const preferences = await studyApi("/api/v1/push/preferences", {
      method: "PATCH",
      body: JSON.stringify({
        topics: parseTopics(studyElements.preferredTopics.value),
        source_ids: sourceIds,
        top_n: Number(studyElements.preferredTopN.value),
        catch_up_days: Number(studyElements.preferredCatchUp.value),
      }),
    });
    studyState.preferences = preferences;
    renderPreferences();
    setStudyStatus("资讯偏好已保存，将从下一次 Digest 生效", "ok");
  } catch (error) {
    setStudyStatus(`保存偏好失败：${error.message}`, "error");
  } finally {
    setStudyBusy(false);
  }
}

async function toggleSource(source) {
  if (studyState.busy) {
    return;
  }
  setStudyBusy(true);
  try {
    const updated = await studyApi(`/api/v1/push/sources/${source.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !source.enabled }),
    });
    studyState.sources = studyState.sources.map((item) =>
      item.id === updated.id ? updated : item,
    );
    renderPreferences();
    renderSources();
    setStudyStatus(updated.enabled ? "来源已启用" : "来源已停用", "ok");
  } finally {
    setStudyBusy(false);
  }
}

async function addSource(event) {
  event.preventDefault();
  if (studyState.busy) {
    return;
  }
  const feedUrl = studyElements.sourceFeedUrl.value.trim();
  let domain;
  try {
    domain = new URL(feedUrl).hostname;
  } catch {
    setStudyStatus("Feed URL 格式不正确", "error");
    return;
  }
  setStudyBusy(true);
  try {
    await studyApi("/api/v1/push/sources", {
      method: "POST",
      body: JSON.stringify({
        name: studyElements.sourceName.value.trim(),
        feed_url: feedUrl,
        domain,
        category: studyElements.sourceCategory.value.trim() || "AI",
      }),
    });
    studyElements.sourceAddForm.reset();
    studyElements.sourceCategory.value = "AI";
    await loadPushSettings();
    setStudyStatus("资讯来源已添加", "ok");
  } catch (error) {
    setStudyStatus(`添加来源失败：${error.message}`, "error");
  } finally {
    setStudyBusy(false);
  }
}

async function loadProfile() {
  try {
    studyState.profile = await studyApi("/api/v1/learning-profile");
  } catch (error) {
    studyState.profile = null;
    studyElements.profileEnabledLabel.textContent = "不可用";
    studyElements.profileEnabledLabel.className = "badge badge--error";
    studyElements.profileEmpty.hidden = false;
    studyElements.profileEmpty.textContent = error.message;
    studyElements.profileTopics.replaceChildren();
    renderProfileControls();
    return;
  }
  renderProfile();
}

function renderProfileControls() {
  const profile = studyState.profile;
  const enabled = profile?.enabled ?? false;
  studyElements.profileEnabledLabel.textContent = enabled ? "已开启" : "已关闭";
  studyElements.profileEnabledLabel.className = enabled
    ? "badge"
    : "badge badge--muted";
  studyElements.toggleProfile.textContent = enabled ? "关闭画像" : "开启画像";
  studyElements.toggleProfile.disabled = studyState.busy || profile === null;
  studyElements.resetProfile.disabled =
    studyState.busy || !profile || profile.topics.length === 0;
}

function renderProfile() {
  const profile = studyState.profile;
  if (!profile) {
    return;
  }
  renderProfileControls();
  studyElements.profileTopics.replaceChildren();
  studyElements.profileEmpty.hidden = profile.topics.length > 0;
  for (const topic of profile.topics) {
    const article = studyElement("article", "profile-topic");
    const header = studyElement("div", "profile-topic-header");
    header.append(
      studyElement("h3", "", topic.name),
      studyElement(
        "span",
        "item-meta",
        `最近学习：${formatStudyTime(topic.last_seen_at)}`,
      ),
    );
    const topicControls = studyElement("div", "profile-topic-controls");
    const statusSelect = document.createElement("select");
    for (const [value, label] of [
      ["learning", "学习中"],
      ["paused", "已暂停"],
      ["completed", "已完成"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = topic.status === value;
      statusSelect.append(option);
    }
    statusSelect.addEventListener("change", () => {
      updateProfileTopic(topic.id, { status: statusSelect.value }).catch(
        (error) => setStudyStatus(`更新主题失败：${error.message}`, "error"),
      );
    });
    const deleteButton = studyElement("button", "danger", "删除主题");
    deleteButton.type = "button";
    deleteButton.addEventListener("click", () => {
      deleteProfileTopic(topic).catch((error) =>
        setStudyStatus(`删除主题失败：${error.message}`, "error"),
      );
    });
    topicControls.append(statusSelect, deleteButton);
    article.append(header, topicControls);

    if (topic.concepts.length === 0) {
      article.append(studyElement("p", "empty-hint", "暂无概念记录。"));
    }
    for (const concept of topic.concepts) {
      const row = studyElement("div", "profile-concept");
      row.append(
        studyElement("span", "", concept.name),
        studyElement(
          "span",
          "item-meta",
          `${concept.evidence_count} 条证据${
            concept.user_corrected ? " · 已手动校正" : ""
          }`,
        ),
      );
      const mastery = document.createElement("select");
      for (const [value, label] of [
        ["weak", "薄弱"],
        ["learning", "学习中"],
        ["strong", "已掌握"],
      ]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        option.selected = concept.mastery === value;
        mastery.append(option);
      }
      mastery.addEventListener("change", () => {
        updateProfileConcept(concept.id, mastery.value).catch((error) =>
          setStudyStatus(`更新概念失败：${error.message}`, "error"),
        );
      });
      row.append(mastery);
      article.append(row);
    }
    studyElements.profileTopics.append(article);
  }
}

async function updateProfileTopic(topicId, payload) {
  if (studyState.busy) {
    return;
  }
  setStudyBusy(true);
  try {
    await studyApi(`/api/v1/learning-profile/topics/${topicId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    await loadProfile();
    setStudyStatus("学习主题已更新", "ok");
  } finally {
    setStudyBusy(false);
  }
}

async function deleteProfileTopic(topic) {
  if (
    studyState.busy ||
    !window.confirm(`删除学习主题“${topic.name}”及其概念和证据？`)
  ) {
    return;
  }
  setStudyBusy(true);
  try {
    await studyApi(`/api/v1/learning-profile/topics/${topic.id}`, {
      method: "DELETE",
    });
    await loadProfile();
    setStudyStatus("学习主题已删除", "ok");
  } finally {
    setStudyBusy(false);
  }
}

async function updateProfileConcept(conceptId, mastery) {
  if (studyState.busy) {
    return;
  }
  setStudyBusy(true);
  try {
    await studyApi(`/api/v1/learning-profile/concepts/${conceptId}`, {
      method: "PATCH",
      body: JSON.stringify({ mastery }),
    });
    await loadProfile();
    setStudyStatus("概念掌握度已更新，后续自动规则不会覆盖", "ok");
  } finally {
    setStudyBusy(false);
  }
}

async function toggleProfile() {
  if (studyState.busy || !studyState.profile) {
    return;
  }
  setStudyBusy(true);
  try {
    const updated = await studyApi("/api/v1/learning-profile/settings", {
      method: "PATCH",
      body: JSON.stringify({ enabled: !studyState.profile.enabled }),
    });
    studyState.profile.enabled = updated.enabled;
    renderProfile();
    setStudyStatus(updated.enabled ? "学习画像已开启" : "学习画像已关闭", "ok");
  } finally {
    setStudyBusy(false);
  }
}

async function resetProfile() {
  if (
    studyState.busy ||
    !window.confirm("清空全部学习主题、概念和证据？此操作不可撤销。")
  ) {
    return;
  }
  setStudyBusy(true);
  try {
    await studyApi("/api/v1/learning-profile/reset", { method: "POST" });
    await loadProfile();
    setStudyStatus("学习画像已清空", "ok");
  } finally {
    setStudyBusy(false);
  }
}

async function loadNotes() {
  studyState.notes = await studyApi("/api/v1/learning/notes");
  if (
    studyState.currentNote &&
    !studyState.notes.some((note) => note.id === studyState.currentNote.id)
  ) {
    studyState.currentNote = null;
  }
  renderNotes();
}

async function refreshStudy() {
  if (studyState.busy) {
    return;
  }
  setStudyBusy(true);
  setStudyStatus("正在刷新…", "loading");
  try {
    await Promise.all([
      loadNotes(),
      loadDigest(),
      loadDueReviews(),
      loadProgress(),
      loadPushSettings(),
      loadProfile(),
    ]);
    setStudyStatus("已刷新", "ok");
  } catch (error) {
    setStudyStatus(`刷新失败：${error.message}`, "error");
  } finally {
    setStudyBusy(false);
  }
}

studyElements.refresh.addEventListener("click", refreshStudy);
studyElements.quizButton.addEventListener("click", () => {
  generateQuiz().catch((error) =>
    setStudyStatus(`生成失败：${error.message}`, "error"),
  );
});
studyElements.cardsButton.addEventListener("click", () => {
  generateFlashcards().catch((error) =>
    setStudyStatus(`生成失败：${error.message}`, "error"),
  );
});
studyElements.preferencesForm.addEventListener("submit", savePreferences);
studyElements.sourceAddForm.addEventListener("submit", addSource);
studyElements.toggleProfile.addEventListener("click", () => {
  toggleProfile().catch((error) =>
    setStudyStatus(`更新画像设置失败：${error.message}`, "error"),
  );
});
studyElements.resetProfile.addEventListener("click", () => {
  resetProfile().catch((error) =>
    setStudyStatus(`清空画像失败：${error.message}`, "error"),
  );
});

refreshStudy();
