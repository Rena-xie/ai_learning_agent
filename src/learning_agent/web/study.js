/* 最小学习工作台：Digest → 笔记 → 自测/闪卡 → 复习。 */
const studyState = {
  notes: [],
  currentNote: null,
  digestDate: null,
  digestItems: [],
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
    const button = studyElement(
      "button",
      "primary",
      findNoteForItem(item.item_id) ? "查看笔记" : "生成笔记",
    );
    button.type = "button";
    button.addEventListener("click", () => {
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
    actions.append(link, button);
    card.append(actions);
    studyElements.digestItems.append(card);
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
    await Promise.all([loadNotes(), loadDigest(), loadDueReviews(), loadProgress()]);
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

refreshStudy();
