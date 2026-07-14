const REVIEWED_STATUSES = ["accepted", "rejected", "needs_review"];
const SEGMENT_PREVIEW_INTERVAL_MS = 100;
const YOUTUBE_IFRAME_API_URL = "https://www.youtube.com/iframe_api";
const YOUTUBE_IFRAME_API_TIMEOUT_MS = 15000;

const state = {
  videos: [],
  nextStartId: 1,
  hasMore: true,
  currentVideoId: null,
  currentDetail: null,
  lockedUsername: "",
  reviewStatus: "pending",
  includeReviewed: false,
  reviewSegments: [],
  editingSegmentIndex: null,
  playerKind: "none",
  playerReady: false,
  youtubePlayer: null,
  playerGeneration: 0,
  previewTimer: null,
  previewSegmentIndex: null,
};

const els = {};
let youtubeIframeApiPromise = null;

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  bindEvents();
  updateLoginControls();
  updateReviewControls();
  loadVideos();
});

function bindElements() {
  els.loginForm = document.getElementById("login-form");
  els.loginUsernameInput = document.getElementById("login-username-input");
  els.loginPasswordInput = document.getElementById("login-password-input");
  els.loginButton = document.getElementById("login-button");
  els.includeReviewedInput = document.getElementById("include-reviewed-input");
  els.videoList = document.getElementById("video-list");
  els.loadMore = document.getElementById("load-more-button");
  els.videoPlayer = document.getElementById("video-player");
  els.embedPlayer = document.getElementById("embed-player");
  els.playerMessage = document.getElementById("player-message");
  els.overviewPanel = document.getElementById("overview-panel");
  els.detailPanel = document.getElementById("detail-panel");
  els.noteInput = document.getElementById("note-input");
  els.segmentStartInput = document.getElementById("segment-start-input");
  els.segmentEndInput = document.getElementById("segment-end-input");
  els.segmentStartCapture = document.getElementById("segment-start-capture");
  els.segmentEndCapture = document.getElementById("segment-end-capture");
  els.segmentCommit = document.getElementById("segment-commit-button");
  els.segmentCancel = document.getElementById("segment-cancel-button");
  els.segmentList = document.getElementById("segment-list");
  els.segmentCount = document.getElementById("segment-count");
  els.segmentMessage = document.getElementById("segment-message");
  els.reviewMessage = document.getElementById("review-message");
  els.statusButtons = Array.from(document.querySelectorAll(".status-button"));
  els.tabButtons = Array.from(document.querySelectorAll(".tab-button"));
}

function bindEvents() {
  els.loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.lockedUsername) {
      unlockReviewer();
    } else {
      lockReviewer();
    }
  });

  els.includeReviewedInput.addEventListener("change", async () => {
    state.includeReviewed = els.includeReviewedInput.checked;
    renderVideoList();
    if (currentVideoIsHidden()) {
      await selectNextVisibleVideo();
    }
  });

  els.loadMore.addEventListener("click", () => loadVideos());

  els.statusButtons.forEach((button) => {
    button.addEventListener("click", () => saveReview(button.dataset.status));
  });

  els.segmentStartCapture.addEventListener("click", () => captureSegmentTime("start"));
  els.segmentEndCapture.addEventListener("click", () => captureSegmentTime("end"));
  els.segmentCommit.addEventListener("click", commitSegmentDraft);
  els.segmentCancel.addEventListener("click", cancelSegmentDraft);
  els.segmentStartInput.addEventListener("input", updateSegmentControls);
  els.segmentEndInput.addEventListener("input", updateSegmentControls);

  els.videoPlayer.addEventListener("loadedmetadata", () => {
    if (state.playerKind === "local") {
      state.playerReady = true;
      updateSegmentControls();
    }
  });
  els.videoPlayer.addEventListener("error", () => {
    if (state.playerKind === "local") {
      state.playerReady = false;
      stopSegmentPreview({pause: false});
      updateSegmentControls();
    }
  });

  els.tabButtons.forEach((button) => {
    button.addEventListener("click", () => activateTab(button.id));
  });
}

async function lockReviewer() {
  const username = els.loginUsernameInput.value.trim();
  if (!username) {
    setReviewMessage("username을 입력해 주세요.", true);
    return;
  }
  state.lockedUsername = username;
  els.loginPasswordInput.value = "";
  state.includeReviewed = false;
  els.includeReviewedInput.checked = false;
  updateLoginControls();
  await reloadQueue();
  setReviewMessage(`${username} 사용자로 검토합니다.`);
}

async function unlockReviewer() {
  state.lockedUsername = "";
  state.includeReviewed = false;
  els.includeReviewedInput.checked = false;
  updateLoginControls();
  await reloadQueue();
  setReviewMessage("사용자 고정을 해제했습니다.");
}

async function reloadQueue() {
  state.videos = [];
  state.nextStartId = 1;
  state.hasMore = true;
  clearSelection();
  await loadVideos();
}

async function loadVideos() {
  if (!state.hasMore) {
    return;
  }

  els.loadMore.disabled = true;
  els.loadMore.textContent = "불러오는 중";
  try {
    let url = `/api/videos?start_id=${state.nextStartId}&rows=50`;
    if (state.lockedUsername) {
      url += `&username=${encodeURIComponent(state.lockedUsername)}`;
    }
    const payload = await fetchJson(url);
    state.videos = state.videos.concat(payload.items);
    state.nextStartId = payload.next_start_id;
    state.hasMore = payload.has_more;
    renderVideoList();

    if (state.currentVideoId === null) {
      const firstVideo = visibleVideos()[0];
      if (firstVideo) {
        await selectVideo(firstVideo.id);
      }
    }
  } catch (error) {
    setReviewMessage(error.message, true);
  } finally {
    els.loadMore.disabled = !state.hasMore;
    els.loadMore.textContent = state.hasMore ? "더 보기" : "목록 끝";
  }
}

function renderVideoList() {
  const visible = visibleVideos();
  if (visible.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state video-empty-state";
    empty.textContent = "표시할 영상이 없습니다.";
    els.videoList.replaceChildren(empty);
    return;
  }

  els.videoList.replaceChildren(
    ...visible.map((video) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = [
        "video-item",
        video.id === state.currentVideoId ? "active" : "",
        video.reviewed ? "reviewed" : "",
      ].filter(Boolean).join(" ");
      button.dataset.videoId = String(video.id);
      button.addEventListener("click", () => selectVideo(video.id));

      const number = document.createElement("span");
      number.className = "video-item-number";
      number.textContent = `#${video.id}`;

      const content = document.createElement("span");
      content.className = "video-item-content";

      const title = document.createElement("span");
      title.className = "video-item-title";
      title.textContent = video.title || "(제목 없음)";

      const meta = document.createElement("span");
      meta.className = "video-item-meta";
      meta.append(
        createPill(video.video_id || "video_id 없음"),
        createPill(formatDate(video.publishTime)),
        createPill(video.has_path ? "파일 있음" : "파일 없음", video.has_path ? "ready" : "missing"),
        createPill(video.has_embed ? "Embed 있음" : "Embed 없음", video.has_embed ? "ready" : "missing"),
      );
      if (video.review_status && video.review_status !== "pending") {
        meta.append(createPill(video.review_status, video.review_status));
      }

      content.append(title, meta);
      button.append(number, content);
      return button;
    }),
  );
}

function visibleVideos() {
  return state.videos.filter((video) => !(video.reviewed && !state.includeReviewed));
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

function clearSelection() {
  state.currentVideoId = null;
  state.currentDetail = null;
  state.reviewStatus = "pending";
  clearPlayer("동영상을 선택해 주세요.");
  renderInfo(null);
  resetReviewFieldsForLoading();
  renderVideoList();
}

function renderPlayer(detail) {
  resetPlayerSurfaces();
  if (detail.media && detail.media.available && detail.media.url) {
    state.playerKind = "local";
    state.playerReady = false;
    els.videoPlayer.src = detail.media.url;
    els.videoPlayer.load();
    els.videoPlayer.classList.remove("hidden");
    hidePlayerMessage();
    if (els.videoPlayer.readyState >= 1) {
      state.playerReady = true;
    }
    updateSegmentControls();
    return;
  }

  const embedSrc = extractEmbedSrc(detail.video && detail.video.embed_code);
  if (embedSrc) {
    state.playerKind = "embed";
    state.playerReady = false;
    const generation = state.playerGeneration;
    const iframe = document.createElement("iframe");
    iframe.id = `youtube-player-${detail.video.id}-${generation}`;
    iframe.src = buildControllableEmbedSrc(embedSrc);
    iframe.title = detail.video && detail.video.title ? detail.video.title : "YouTube embedded video";
    iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
    iframe.allowFullscreen = true;
    iframe.referrerPolicy = "strict-origin-when-cross-origin";
    els.embedPlayer.replaceChildren(iframe);
    els.embedPlayer.classList.remove("hidden");
    els.embedPlayer.setAttribute("aria-hidden", "false");
    hidePlayerMessage();
    updateSegmentControls();
    initializeYouTubePlayer(iframe, generation);
    return;
  }

  clearPlayer("재생 가능한 로컬 파일이나 임베드 코드가 없습니다.");
}

function clearPlayer(message = "동영상을 선택해 주세요.") {
  resetPlayerSurfaces();
  els.playerMessage.textContent = message;
  els.playerMessage.classList.remove("hidden");
}

function resetPlayerSurfaces() {
  stopSegmentPreview({render: false});
  state.playerGeneration += 1;
  const youtubePlayer = state.youtubePlayer;
  state.youtubePlayer = null;
  state.playerKind = "none";
  state.playerReady = false;
  if (youtubePlayer && typeof youtubePlayer.destroy === "function") {
    try {
      youtubePlayer.destroy();
    } catch {
      // The iframe may already have been removed during navigation.
    }
  }
  els.videoPlayer.pause();
  els.videoPlayer.removeAttribute("src");
  els.videoPlayer.load();
  els.videoPlayer.classList.add("hidden");
  els.embedPlayer.replaceChildren();
  els.embedPlayer.classList.add("hidden");
  els.embedPlayer.setAttribute("aria-hidden", "true");
  updateSegmentControls();
}

function hidePlayerMessage() {
  els.playerMessage.classList.add("hidden");
  els.playerMessage.textContent = "";
}

function buildControllableEmbedSrc(embedSrc) {
  const url = new URL(embedSrc);
  url.searchParams.set("enablejsapi", "1");
  url.searchParams.set("playsinline", "1");
  if (window.location.origin && window.location.origin !== "null") {
    url.searchParams.set("origin", window.location.origin);
  }
  return url.toString();
}

async function initializeYouTubePlayer(iframe, generation) {
  try {
    const youtubeApi = await loadYouTubeIframeApi();
    if (
      generation !== state.playerGeneration
      || state.playerKind !== "embed"
      || !iframe.isConnected
    ) {
      return;
    }

    const player = new youtubeApi.Player(iframe, {
      events: {
        onReady: (event) => {
          if (
            generation !== state.playerGeneration
            || state.playerKind !== "embed"
          ) {
            event.target.destroy();
            return;
          }
          state.youtubePlayer = event.target;
          state.playerReady = true;
          updateSegmentControls();
        },
        onError: () => {
          if (generation !== state.playerGeneration) {
            return;
          }
          state.playerReady = false;
          stopSegmentPreview({pause: false});
          updateSegmentControls();
          setSegmentMessage(
            "Embed 시간 제어를 사용할 수 없습니다. 시간은 직접 입력할 수 있습니다.",
            true,
          );
        },
      },
    });
    state.youtubePlayer = player;
  } catch (error) {
    if (generation !== state.playerGeneration || state.playerKind !== "embed") {
      return;
    }
    state.playerReady = false;
    updateSegmentControls();
    setSegmentMessage(
      "Embed 시간 제어를 불러오지 못했습니다. 시간은 직접 입력할 수 있습니다.",
      true,
    );
  }
}

function loadYouTubeIframeApi() {
  if (window.YT && typeof window.YT.Player === "function") {
    return Promise.resolve(window.YT);
  }
  if (youtubeIframeApiPromise) {
    return youtubeIframeApiPromise;
  }

  youtubeIframeApiPromise = new Promise((resolve, reject) => {
    let settled = false;
    let createdScript = false;
    let script = document.querySelector(`script[src="${YOUTUBE_IFRAME_API_URL}"]`);
    const previousReady = window.onYouTubeIframeAPIReady;

    const finish = (callback) => {
      if (settled) {
        return;
      }
      settled = true;
      window.clearTimeout(timeoutId);
      callback();
    };

    const fail = (message) => {
      finish(() => {
        if (createdScript && script) {
          script.remove();
        }
        youtubeIframeApiPromise = null;
        reject(new Error(message));
      });
    };

    const timeoutId = window.setTimeout(() => {
      fail("YouTube IFrame API load timed out.");
    }, YOUTUBE_IFRAME_API_TIMEOUT_MS);

    window.onYouTubeIframeAPIReady = () => {
      if (typeof previousReady === "function") {
        try {
          previousReady();
        } catch {
          // Keep this page's player initialization independent.
        }
      }
      finish(() => resolve(window.YT));
    };

    if (!script) {
      script = document.createElement("script");
      script.src = YOUTUBE_IFRAME_API_URL;
      script.async = true;
      script.dataset.ytcrawlYoutubeApi = "true";
      createdScript = true;
      document.head.append(script);
    }
    script.addEventListener(
      "error",
      () => fail("YouTube IFrame API failed to load."),
      {once: true},
    );
  });

  return youtubeIframeApiPromise;
}

function extractEmbedSrc(embedCode) {
  if (!embedCode) {
    return null;
  }

  const doc = new DOMParser().parseFromString(embedCode, "text/html");
  const iframe = doc.querySelector("iframe[src]");
  if (!iframe) {
    return null;
  }

  const rawSrc = iframe.getAttribute("src");
  if (!rawSrc) {
    return null;
  }

  try {
    const normalizedSrc = rawSrc.startsWith("//") ? `https:${rawSrc}` : rawSrc;
    const url = new URL(normalizedSrc, window.location.href);
    const hostname = url.hostname.toLowerCase();
    const allowedHost = hostname === "youtube.com"
      || hostname.endsWith(".youtube.com")
      || hostname === "youtube-nocookie.com"
      || hostname.endsWith(".youtube-nocookie.com");
    if (!allowedHost || !url.pathname.startsWith("/embed/")) {
      return null;
    }
    url.protocol = "https:";
    return url.toString();
  } catch {
    return null;
  }
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
  const mediaStatus = media.available
    ? "local file"
    : (extractEmbedSrc(video.embed_code) ? "embed" : "unavailable");

  const overviewRows = [
    ["제목", video.title],
    ["video_id", video.video_id],
    ["게시일", formatDate(video.publishTime)],
    ["duration", meta.duration],
    ["resolution", meta.resolution],
    ["license", meta.license],
    ["caption", formatBoolean(meta.has_caption)],
    ["synthetic media", formatBoolean(meta.is_synthetic_marked)],
    ["view / like / comment", [meta.view_count, meta.like_count, meta.comment_count].map(formatValue).join(" / ")],
    ["download", attemptStatus],
    ["media", mediaStatus],
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

function normalizeReviewSegments(segments) {
  if (!Array.isArray(segments)) {
    return [];
  }
  return segments
    .map((segment) => ({
      start_ms: Number(segment.start_ms),
      end_ms: Number(segment.end_ms),
    }))
    .filter((segment) => {
      return Number.isInteger(segment.start_ms)
        && Number.isInteger(segment.end_ms)
        && segment.start_ms >= 0
        && segment.end_ms > segment.start_ms;
    })
    .sort(compareSegments);
}

function compareSegments(left, right) {
  return left.start_ms - right.start_ms || left.end_ms - right.end_ms;
}

function parseTimeInput(value) {
  const text = String(value || "").trim();
  if (!text) {
    return null;
  }

  const parts = text.split(":");
  if (parts.length > 3 || parts.some((part) => part === "")) {
    return null;
  }

  const secondsMatch = parts.at(-1).match(/^(\d+)(?:\.(\d{1,3}))?$/);
  if (!secondsMatch) {
    return null;
  }
  const leading = parts.slice(0, -1);
  if (leading.some((part) => !/^\d+$/.test(part))) {
    return null;
  }

  const seconds = Number(secondsMatch[1]);
  if (parts.length > 1 && seconds >= 60) {
    return null;
  }
  const milliseconds = Number((secondsMatch[2] || "").padEnd(3, "0") || 0);

  let totalSeconds = seconds;
  if (parts.length === 2) {
    totalSeconds += Number(leading[0]) * 60;
  } else if (parts.length === 3) {
    const minutes = Number(leading[1]);
    if (minutes >= 60) {
      return null;
    }
    totalSeconds += Number(leading[0]) * 3600 + minutes * 60;
  }

  const totalMilliseconds = totalSeconds * 1000 + milliseconds;
  return Number.isSafeInteger(totalMilliseconds) ? totalMilliseconds : null;
}

function formatTimeInput(milliseconds) {
  const value = Math.max(0, Math.round(milliseconds));
  const hours = Math.floor(value / 3600000);
  const minutes = Math.floor((value % 3600000) / 60000);
  const seconds = Math.floor((value % 60000) / 1000);
  const remainder = value % 1000;
  return [hours, minutes, seconds]
    .map((part) => String(part).padStart(2, "0"))
    .join(":") + `.${String(remainder).padStart(3, "0")}`;
}

function hasSegmentDraft() {
  return state.editingSegmentIndex !== null
    || els.segmentStartInput.value.trim() !== ""
    || els.segmentEndInput.value.trim() !== "";
}

function captureSegmentTime(target) {
  const currentTime = getCurrentPlaybackMs();
  if (currentTime === null) {
    setSegmentMessage("현재 재생 시각을 가져올 수 없습니다.", true);
    return;
  }
  const input = target === "start" ? els.segmentStartInput : els.segmentEndInput;
  input.value = formatTimeInput(currentTime);
  setSegmentMessage("");
  updateSegmentControls();
}

function commitSegmentDraft() {
  const startMs = parseTimeInput(els.segmentStartInput.value);
  const endMs = parseTimeInput(els.segmentEndInput.value);
  if (startMs === null || endMs === null) {
    setSegmentMessage("시간을 HH:MM:SS.mmm 형식으로 입력해 주세요.", true);
    return;
  }

  const validationError = validateSegmentRange(
    startMs,
    endMs,
    state.editingSegmentIndex,
  );
  if (validationError) {
    setSegmentMessage(validationError, true);
    return;
  }

  const updated = state.reviewSegments.slice();
  const segment = {start_ms: startMs, end_ms: endMs};
  if (state.editingSegmentIndex === null) {
    updated.push(segment);
  } else {
    updated[state.editingSegmentIndex] = segment;
  }
  state.reviewSegments = updated.sort(compareSegments);
  const action = state.editingSegmentIndex === null ? "추가" : "수정";
  resetSegmentDraft();
  setSegmentMessage(`구간을 ${action}했습니다.`);
}

function validateSegmentRange(startMs, endMs, editingIndex) {
  if (startMs < 0) {
    return "시작 시간은 0 이상이어야 합니다.";
  }
  if (endMs <= startMs) {
    return "종료 시간은 시작 시간보다 커야 합니다.";
  }

  const durationMs = getPlaybackDurationMs();
  if (durationMs !== null && endMs > durationMs) {
    return `종료 시간은 영상 길이 ${formatTimeInput(durationMs)}를 넘을 수 없습니다.`;
  }

  for (const [index, segment] of state.reviewSegments.entries()) {
    if (index === editingIndex) {
      continue;
    }
    if (startMs === segment.start_ms && endMs === segment.end_ms) {
      return "동일한 구간이 이미 있습니다.";
    }
    if (startMs < segment.end_ms && endMs > segment.start_ms) {
      return "기존 구간과 겹치지 않게 입력해 주세요.";
    }
  }
  return null;
}

function editSegment(index) {
  const segment = state.reviewSegments[index];
  if (!segment) {
    return;
  }
  stopSegmentPreview();
  state.editingSegmentIndex = index;
  els.segmentStartInput.value = formatTimeInput(segment.start_ms);
  els.segmentEndInput.value = formatTimeInput(segment.end_ms);
  setSegmentMessage("");
  updateSegmentControls();
  els.segmentStartInput.focus();
}

function deleteSegment(index) {
  if (!state.reviewSegments[index]) {
    return;
  }
  stopSegmentPreview();
  state.reviewSegments.splice(index, 1);
  if (state.editingSegmentIndex === index) {
    resetSegmentDraft();
  } else {
    if (
      state.editingSegmentIndex !== null
      && state.editingSegmentIndex > index
    ) {
      state.editingSegmentIndex -= 1;
    }
    updateSegmentControls();
  }
  setSegmentMessage("구간을 삭제했습니다.");
}

function cancelSegmentDraft() {
  if (!hasSegmentDraft()) {
    return;
  }
  resetSegmentDraft();
  setSegmentMessage("구간 입력을 취소했습니다.");
}

function resetSegmentDraft() {
  state.editingSegmentIndex = null;
  els.segmentStartInput.value = "";
  els.segmentEndInput.value = "";
  updateSegmentControls();
}

function renderSegmentList() {
  els.segmentCount.textContent = `${state.reviewSegments.length}개`;
  if (state.reviewSegments.length === 0) {
    const empty = document.createElement("p");
    empty.className = "segment-empty";
    empty.textContent = "저장할 구간이 없습니다.";
    els.segmentList.replaceChildren(empty);
    return;
  }

  const editable = Boolean(state.currentVideoId && state.lockedUsername);
  const previewable = editable && state.playerReady;
  els.segmentList.replaceChildren(
    ...state.reviewSegments.map((segment, index) => {
      const row = document.createElement("div");
      row.className = index === state.editingSegmentIndex
        ? "segment-row editing"
        : "segment-row";

      const info = document.createElement("div");
      info.className = "segment-row-info";
      const number = document.createElement("span");
      number.className = "segment-row-index";
      number.textContent = `#${index + 1}`;
      const range = document.createElement("span");
      range.className = "segment-row-range";
      range.textContent = `${formatTimeInput(segment.start_ms)} - ${formatTimeInput(segment.end_ms)}`;
      const duration = document.createElement("span");
      duration.className = "segment-row-duration";
      duration.textContent = formatTimeInput(segment.end_ms - segment.start_ms);
      info.append(number, range, duration);

      const actions = document.createElement("div");
      actions.className = "segment-row-actions";
      const preview = document.createElement("button");
      preview.type = "button";
      preview.className = state.previewSegmentIndex === index
        ? "segment-row-button previewing"
        : "segment-row-button";
      preview.textContent = state.previewSegmentIndex === index ? "중지" : "미리보기";
      preview.disabled = !previewable;
      preview.addEventListener("click", () => {
        if (state.previewSegmentIndex === index) {
          stopSegmentPreview();
        } else {
          previewSegment(index);
        }
      });

      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "segment-row-button";
      edit.textContent = "수정";
      edit.disabled = !editable;
      edit.addEventListener("click", () => editSegment(index));

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "segment-row-button delete";
      remove.textContent = "삭제";
      remove.disabled = !editable;
      remove.addEventListener("click", () => deleteSegment(index));
      actions.append(preview, edit, remove);
      row.append(info, actions);
      return row;
    }),
  );
}

function updateSegmentControls() {
  const editable = Boolean(state.currentVideoId && state.lockedUsername);
  const playbackReady = editable && state.playerReady;
  els.segmentStartInput.disabled = !editable;
  els.segmentEndInput.disabled = !editable;
  els.segmentStartCapture.disabled = !playbackReady;
  els.segmentEndCapture.disabled = !playbackReady;
  els.segmentCommit.disabled = !editable;
  els.segmentCommit.textContent = state.editingSegmentIndex === null
    ? "추가"
    : "수정 완료";
  els.segmentCancel.disabled = !editable || !hasSegmentDraft();
  renderSegmentList();
}

function setSegmentMessage(message, isError = false) {
  els.segmentMessage.textContent = message;
  els.segmentMessage.classList.toggle("error", isError);
}

function getCurrentPlaybackMs() {
  if (!state.playerReady) {
    return null;
  }
  try {
    let seconds = null;
    if (state.playerKind === "local") {
      seconds = els.videoPlayer.currentTime;
    } else if (
      state.playerKind === "embed"
      && state.youtubePlayer
      && typeof state.youtubePlayer.getCurrentTime === "function"
    ) {
      seconds = state.youtubePlayer.getCurrentTime();
    }
    if (!Number.isFinite(seconds) || seconds < 0) {
      return null;
    }
    const currentMs = Math.round(seconds * 1000);
    const durationMs = getPlaybackDurationMs();
    return durationMs === null ? currentMs : Math.min(currentMs, durationMs);
  } catch {
    return null;
  }
}

function getPlaybackDurationMs() {
  if (!state.playerReady) {
    return null;
  }
  try {
    let seconds = null;
    if (state.playerKind === "local") {
      seconds = els.videoPlayer.duration;
    } else if (
      state.playerKind === "embed"
      && state.youtubePlayer
      && typeof state.youtubePlayer.getDuration === "function"
    ) {
      seconds = state.youtubePlayer.getDuration();
    }
    if (!Number.isFinite(seconds) || seconds <= 0) {
      return null;
    }
    return Math.round(seconds * 1000);
  } catch {
    return null;
  }
}

function seekPlayback(milliseconds) {
  try {
    const seconds = milliseconds / 1000;
    if (state.playerKind === "local" && state.playerReady) {
      els.videoPlayer.currentTime = seconds;
      return true;
    }
    if (
      state.playerKind === "embed"
      && state.playerReady
      && state.youtubePlayer
      && typeof state.youtubePlayer.seekTo === "function"
    ) {
      state.youtubePlayer.seekTo(seconds, true);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

async function playPlayback() {
  if (state.playerKind === "local" && state.playerReady) {
    await els.videoPlayer.play();
    return true;
  }
  if (
    state.playerKind === "embed"
    && state.playerReady
    && state.youtubePlayer
    && typeof state.youtubePlayer.playVideo === "function"
  ) {
    state.youtubePlayer.playVideo();
    return true;
  }
  return false;
}

function pausePlayback() {
  try {
    if (state.playerKind === "local") {
      els.videoPlayer.pause();
    } else if (
      state.playerKind === "embed"
      && state.youtubePlayer
      && typeof state.youtubePlayer.pauseVideo === "function"
    ) {
      state.youtubePlayer.pauseVideo();
    }
  } catch {
    // The player may be removed while navigating to another video.
  }
}

async function previewSegment(index) {
  const segment = state.reviewSegments[index];
  if (!segment || !state.playerReady) {
    setSegmentMessage("현재 플레이어에서는 구간을 미리 볼 수 없습니다.", true);
    return;
  }
  const durationMs = getPlaybackDurationMs();
  if (durationMs !== null && segment.end_ms > durationMs) {
    setSegmentMessage("구간 종료 시간이 현재 영상 길이를 넘습니다.", true);
    return;
  }

  stopSegmentPreview();
  if (!seekPlayback(segment.start_ms)) {
    setSegmentMessage("구간 시작점으로 이동하지 못했습니다.", true);
    return;
  }

  state.previewSegmentIndex = index;
  renderSegmentList();
  try {
    if (!await playPlayback()) {
      throw new Error("Playback is unavailable.");
    }
  } catch {
    stopSegmentPreview({pause: false});
    setSegmentMessage("구간 재생을 시작하지 못했습니다.", true);
    return;
  }

  state.previewTimer = window.setInterval(() => {
    const currentMs = getCurrentPlaybackMs();
    if (currentMs !== null && currentMs >= segment.end_ms) {
      stopSegmentPreview();
    }
  }, SEGMENT_PREVIEW_INTERVAL_MS);
  setSegmentMessage("");
}

function stopSegmentPreview({pause = true, render = true} = {}) {
  const wasPreviewing = state.previewTimer !== null
    || state.previewSegmentIndex !== null;
  if (state.previewTimer !== null) {
    window.clearInterval(state.previewTimer);
  }
  state.previewTimer = null;
  state.previewSegmentIndex = null;
  if (pause && wasPreviewing) {
    pausePlayback();
  }
  if (render && els.segmentList) {
    renderSegmentList();
  }
}

async function loadCurrentReview() {
  const username = state.lockedUsername;
  if (!state.currentVideoId || username === "") {
    els.noteInput.value = "";
    state.reviewStatus = "pending";
    state.reviewSegments = [];
    resetSegmentDraft();
    setSegmentMessage("");
    updateReviewControls();
    return;
  }

  try {
    const review = await fetchJson(
      `/api/videos/${state.currentVideoId}/review/${encodeURIComponent(username)}`,
    );
    els.noteInput.value = review.note || "";
    state.reviewStatus = review.status || "pending";
    state.reviewSegments = normalizeReviewSegments(review.segments);
    resetSegmentDraft();
    setSegmentMessage("");
    updateReviewControls();
    setReviewMessage(review.persisted ? "저장된 리뷰를 불러왔습니다." : "아직 저장된 리뷰가 없습니다.");
  } catch (error) {
    setReviewMessage(error.message, true);
  }
}

async function saveReview(status) {
  const username = state.lockedUsername;
  if (!state.currentVideoId || username === "") {
    setReviewMessage("login 후 저장할 수 있습니다.", true);
    updateReviewControls();
    return;
  }
  if (hasSegmentDraft()) {
    setSegmentMessage("구간 입력을 추가·수정 완료하거나 취소해 주세요.", true);
    return;
  }

  stopSegmentPreview();
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
          segments: state.reviewSegments.map((segment) => ({
            start_ms: segment.start_ms,
            end_ms: segment.end_ms,
          })),
        }),
      },
    );
    state.reviewStatus = review.status;
    state.reviewSegments = normalizeReviewSegments(review.segments);
    resetSegmentDraft();
    setSegmentMessage("");
    markVideoReviewed(review);
    if (isReviewedStatus(review.status) && !state.includeReviewed) {
      await selectNextVisibleVideo();
    } else {
      updateReviewControls();
    }
    setReviewMessage("리뷰를 저장했습니다.");
  } catch (error) {
    setReviewMessage(error.message, true);
  } finally {
    updateReviewControls();
  }
}

function markVideoReviewed(review) {
  const video = state.videos.find((item) => item.id === review.video_ref_id);
  if (!video) {
    return;
  }
  video.review_status = review.status;
  video.reviewed = isReviewedStatus(review.status);
  renderVideoList();
}

async function selectNextVisibleVideo() {
  const currentIndex = state.videos.findIndex((video) => video.id === state.currentVideoId);
  const visibleAfterCurrent = state.videos.slice(currentIndex + 1).find((video) => {
    return !(video.reviewed && !state.includeReviewed);
  });
  if (visibleAfterCurrent) {
    await selectVideo(visibleAfterCurrent.id);
    return;
  }
  clearSelection();
}

function currentVideoIsHidden() {
  const current = state.videos.find((video) => video.id === state.currentVideoId);
  return Boolean(current && current.reviewed && !state.includeReviewed);
}

function isReviewedStatus(status) {
  return REVIEWED_STATUSES.includes(status);
}

function updateLoginControls() {
  const locked = Boolean(state.lockedUsername);
  els.loginUsernameInput.disabled = locked;
  els.loginPasswordInput.disabled = locked;
  els.loginButton.textContent = locked ? "사용자 변경" : "login";
  els.includeReviewedInput.disabled = !locked;
  if (!locked) {
    state.includeReviewed = false;
    els.includeReviewedInput.checked = false;
  }
}

function updateReviewControls() {
  const enabled = Boolean(state.currentVideoId && state.lockedUsername);
  els.noteInput.disabled = !enabled;
  updateSegmentControls();
  setStatusButtonsEnabled(enabled);
  els.statusButtons.forEach((button) => {
    const active = enabled && button.dataset.status === state.reviewStatus;
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function resetReviewFieldsForLoading() {
  stopSegmentPreview();
  els.noteInput.value = "";
  state.reviewSegments = [];
  state.editingSegmentIndex = null;
  els.segmentStartInput.value = "";
  els.segmentEndInput.value = "";
  setSegmentMessage("");
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
        message = formatApiErrorDetail(payload.detail);
      }
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

function formatApiErrorDetail(detail) {
  if (!Array.isArray(detail)) {
    return String(detail);
  }
  const messages = detail.map((item) => {
    if (!item || typeof item !== "object") {
      return String(item);
    }
    const location = Array.isArray(item.loc)
      ? item.loc.filter((part) => part !== "body").join(".")
      : "";
    const description = item.msg || "입력값이 올바르지 않습니다.";
    return location ? `${location}: ${description}` : description;
  });
  return messages.join("; ");
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
