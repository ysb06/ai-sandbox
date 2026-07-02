const state = {
  videos: [],
  nextStartId: 1,
  hasMore: true,
  currentVideoId: null,
  currentDetail: null,
  username: "",
  reviewStatus: "pending",
  reviewTimer: null,
};

const els = {};

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  bindEvents();
  setStatusButtonsEnabled(false);
  loadVideos();
});

function bindElements() {
  els.videoList = document.getElementById("video-list");
  els.loadMore = document.getElementById("load-more-button");
  els.videoPlayer = document.getElementById("video-player");
  els.playerMessage = document.getElementById("player-message");
  els.overviewPanel = document.getElementById("overview-panel");
  els.detailPanel = document.getElementById("detail-panel");
  els.usernameInput = document.getElementById("username-input");
  els.noteInput = document.getElementById("note-input");
  els.reviewMessage = document.getElementById("review-message");
  els.statusButtons = Array.from(document.querySelectorAll(".status-button"));
  els.tabButtons = Array.from(document.querySelectorAll(".tab-button"));
}

function bindEvents() {
  els.loadMore.addEventListener("click", () => loadVideos());

  els.usernameInput.addEventListener("input", () => {
    state.username = els.usernameInput.value;
    setReviewMessage("");
    updateReviewControls();
    window.clearTimeout(state.reviewTimer);
    state.reviewTimer = window.setTimeout(() => {
      loadCurrentReview();
    }, 350);
  });

  els.statusButtons.forEach((button) => {
    button.addEventListener("click", () => saveReview(button.dataset.status));
  });

  els.tabButtons.forEach((button) => {
    button.addEventListener("click", () => activateTab(button.id));
  });
}

async function loadVideos() {
  if (!state.hasMore) {
    return;
  }

  els.loadMore.disabled = true;
  els.loadMore.textContent = "불러오는 중";
  try {
    const payload = await fetchJson(`/api/videos?start_id=${state.nextStartId}&rows=50`);
    state.videos = state.videos.concat(payload.items);
    state.nextStartId = payload.next_start_id;
    state.hasMore = payload.has_more;
    renderVideoList();

    if (state.currentVideoId === null && state.videos.length > 0) {
      await selectVideo(state.videos[0].id);
    }
  } catch (error) {
    setReviewMessage(error.message, true);
  } finally {
    els.loadMore.disabled = !state.hasMore;
    els.loadMore.textContent = state.hasMore ? "더 보기" : "목록 끝";
  }
}

function renderVideoList() {
  els.videoList.replaceChildren(
    ...state.videos.map((video) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `video-item${video.id === state.currentVideoId ? " active" : ""}`;
      button.dataset.videoId = String(video.id);
      button.addEventListener("click", () => selectVideo(video.id));

      const title = document.createElement("span");
      title.className = "video-item-title";
      title.textContent = video.title || "(제목 없음)";

      const meta = document.createElement("span");
      meta.className = "video-item-meta";
      meta.append(
        createPill(`#${video.id}`),
        createPill(video.video_id || "video_id 없음"),
        createPill(formatDate(video.publishTime)),
        createPill(video.has_path ? "파일 있음" : "파일 없음", video.has_path ? "ready" : "missing"),
      );

      button.append(title, meta);
      return button;
    }),
  );
}

async function selectVideo(videoId) {
  state.currentVideoId = videoId;
  state.currentDetail = null;
  state.reviewStatus = "pending";
  clearPlayer();
  renderVideoList();
  renderInfo(null);
  resetReviewFieldsForLoading();

  try {
    const detail = await fetchJson(`/api/videos/${videoId}`);
    state.currentDetail = detail;
    renderPlayer(detail);
    renderInfo(detail);
    await loadCurrentReview();
  } catch (error) {
    clearPlayer("영상을 불러오지 못했습니다.");
    renderInfo(null);
    setReviewMessage(error.message, true);
  }
}

function renderPlayer(detail) {
  if (detail.media && detail.media.available && detail.media.url) {
    els.videoPlayer.src = detail.media.url;
    els.videoPlayer.load();
    els.playerMessage.classList.add("hidden");
    els.playerMessage.textContent = "";
  } else {
    clearPlayer("재생 가능한 로컬 파일이 없습니다.");
  }
}

function clearPlayer(message = "동영상을 선택해 주세요.") {
  els.videoPlayer.removeAttribute("src");
  els.videoPlayer.load();
  els.playerMessage.textContent = message;
  els.playerMessage.classList.remove("hidden");
}

function renderInfo(detail) {
  if (!detail) {
    els.overviewPanel.innerHTML = '<p class="empty-state">선택한 영상 정보가 없습니다.</p>';
    els.detailPanel.innerHTML = '<p class="empty-state">선택한 영상 정보가 없습니다.</p>';
    return;
  }

  const video = detail.video || {};
  const meta = detail.detail || {};
  const attempt = detail.latest_download_attempt || {};
  const media = detail.media || {};
  const attemptStatus = attempt.id
    ? (attempt.error_type ? `실패 (${attempt.error_type})` : "성공")
    : "기록 없음";

  const overviewRows = [
    ["제목", video.title],
    ["video_id", video.video_id],
    ["게시일", formatDate(video.publishTime)],
    ["duration", meta.duration],
    ["resolution", meta.resolution],
    ["license", meta.license],
    ["caption", formatBoolean(meta.has_caption)],
    ["view / like / comment", [meta.view_count, meta.like_count, meta.comment_count].map(formatValue).join(" / ")],
    ["download", attemptStatus],
    ["media", media.available ? "available" : "unavailable"],
  ];

  els.overviewPanel.replaceChildren(createDefinitionList(overviewRows));
  els.detailPanel.replaceChildren(createDefinitionList(flattenDetail(detail)));
}

function flattenDetail(detail) {
  const rows = [];
  appendObjectRows(rows, "video", detail.video);
  appendObjectRows(rows, "search_run", detail.search_run);
  appendObjectRows(rows, "detail", detail.detail);
  appendObjectRows(rows, "latest_download_attempt", detail.latest_download_attempt);
  appendObjectRows(rows, "media", detail.media);
  return rows;
}

function appendObjectRows(rows, prefix, value) {
  if (!value) {
    rows.push([prefix, null]);
    return;
  }
  Object.entries(value).forEach(([key, item]) => {
    rows.push([`${prefix}.${key}`, formatValue(item)]);
  });
}

function createDefinitionList(rows) {
  const dl = document.createElement("dl");
  dl.className = "info-list";
  rows.forEach(([label, value]) => {
    const wrapper = document.createElement("div");
    wrapper.className = "info-row";
    const dt = document.createElement("dt");
    dt.textContent = label;
    dt.title = label;
    const dd = document.createElement("dd");
    dd.textContent = formatValue(value);
    wrapper.append(dt, dd);
    dl.append(wrapper);
  });
  return dl;
}

async function loadCurrentReview() {
  const username = state.username.trim();
  if (!state.currentVideoId || username === "") {
    els.noteInput.value = "";
    state.reviewStatus = "pending";
    updateReviewControls();
    return;
  }

  try {
    const review = await fetchJson(
      `/api/videos/${state.currentVideoId}/review/${encodeURIComponent(username)}`,
    );
    els.noteInput.value = review.note || "";
    state.reviewStatus = review.status || "pending";
    updateReviewControls();
    setReviewMessage(review.persisted ? "저장된 리뷰를 불러왔습니다." : "아직 저장된 리뷰가 없습니다.");
  } catch (error) {
    setReviewMessage(error.message, true);
  }
}

async function saveReview(status) {
  const username = state.username.trim();
  if (!state.currentVideoId || username === "") {
    setReviewMessage("username을 입력한 뒤 저장할 수 있습니다.", true);
    updateReviewControls();
    return;
  }

  setStatusButtonsEnabled(false);
  try {
    const review = await fetchJson(
      `/api/videos/${state.currentVideoId}/review/${encodeURIComponent(username)}`,
      {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          status,
          note: els.noteInput.value || null,
        }),
      },
    );
    state.reviewStatus = review.status;
    updateReviewControls();
    setReviewMessage("리뷰를 저장했습니다.");
  } catch (error) {
    setReviewMessage(error.message, true);
  } finally {
    updateReviewControls();
  }
}

function updateReviewControls() {
  const enabled = Boolean(state.currentVideoId && state.username.trim());
  setStatusButtonsEnabled(enabled);
  els.statusButtons.forEach((button) => {
    const active = enabled && button.dataset.status === state.reviewStatus;
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function resetReviewFieldsForLoading() {
  els.noteInput.value = "";
  setReviewMessage("");
  updateReviewControls();
}

function setStatusButtonsEnabled(enabled) {
  els.statusButtons.forEach((button) => {
    button.disabled = !enabled;
  });
}

function activateTab(tabId) {
  const overviewActive = tabId === "overview-tab";
  document.getElementById("overview-tab").classList.toggle("active", overviewActive);
  document.getElementById("overview-tab").setAttribute("aria-selected", overviewActive ? "true" : "false");
  document.getElementById("detail-tab").classList.toggle("active", !overviewActive);
  document.getElementById("detail-tab").setAttribute("aria-selected", overviewActive ? "false" : "true");
  els.overviewPanel.hidden = !overviewActive;
  els.overviewPanel.classList.toggle("active", overviewActive);
  els.detailPanel.hidden = overviewActive;
  els.detailPanel.classList.toggle("active", !overviewActive);
}

function createPill(text, modifier = "") {
  const span = document.createElement("span");
  span.className = modifier ? `pill ${modifier}` : "pill";
  span.textContent = formatValue(text);
  return span;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `요청 실패 (${response.status})`;
    try {
      const payload = await response.json();
      if (payload.detail) {
        message = String(payload.detail);
      }
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

function setReviewMessage(message, isError = false) {
  els.reviewMessage.textContent = message;
  els.reviewMessage.classList.toggle("error", isError);
}

function formatDate(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toISOString().slice(0, 10);
}

function formatBoolean(value) {
  if (value === true) {
    return "true";
  }
  if (value === false) {
    return "false";
  }
  return "-";
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
