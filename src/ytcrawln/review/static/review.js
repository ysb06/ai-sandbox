"use strict";

const clipList = document.getElementById("clip-list");
const clipCount = document.getElementById("clip-count");
const clipListMessage = document.getElementById("clip-list-message");
const videoPlayer = document.getElementById("video-player");
const playerMessage = document.getElementById("player-message");
const playerMessageTitle = document.getElementById("player-message-title");
const playerMessageDetail = document.getElementById("player-message-detail");
const clipPlayButton = document.getElementById("clip-play-button");
const clipPlaybackMessage = document.getElementById("clip-playback-message");
const metadataContent = document.getElementById("metadata-content");
const metadataMessage = document.getElementById("metadata-message");
const overviewPanel = document.getElementById("overview-panel");
const detailPanel = document.getElementById("detail-panel");
const infoTabs = [document.getElementById("overview-tab"), document.getElementById("detail-tab")];
const infoPanels = [overviewPanel, detailPanel];
const reviewerForm = document.getElementById("reviewer-form");
const reviewerName = document.getElementById("reviewer-name");
const reviewerApply = document.getElementById("reviewer-apply");
const reviewerMessage = document.getElementById("reviewer-message");
// const reviewProgress = document.getElementById("review-progress");
const clipListRetry = document.getElementById("clip-list-retry");
// const reviewSelection = document.getElementById("review-selection");
const reviewEditor = document.getElementById("review-editor");
const reviewNote = document.getElementById("review-note");
const reviewSave = document.getElementById("review-save");
const reviewSaveNext = document.getElementById("review-save-next");
const reviewSaveState = document.getElementById("review-save-state");
const reviewMessage = document.getElementById("review-message");
const reviewRecordsMessage = document.getElementById("review-records-message");
const reviewRetry = document.getElementById("review-retry");
const reviewTable = document.getElementById("review-table");
const reviewTableBody = document.getElementById("review-table-body");
const reviewStatusButtons = [...document.querySelectorAll("[data-review-status]")];
const unsavedDialog = document.getElementById("unsaved-dialog");
const unsavedMessage = document.getElementById("unsaved-message");
const unsavedSave = document.getElementById("unsaved-save");
const unsavedDiscard = document.getElementById("unsaved-discard");
const unsavedCancel = document.getElementById("unsaved-cancel");

const REVIEWER_STORAGE_KEY = "ytcrawln.review.username";
const REVIEW_LABELS = { accepted: "적합", rejected: "부적합", needs_review: "보류" };
const candidateButtons = new Map();
const listState = { items: [], status: "idle", requestId: 0, controller: null };
const reviewState = {
  username: null, clipId: null, status: "idle", records: [], saved: null,
  draftStatus: null, draftNote: "", saving: false,
  requestId: 0, controller: null, pendingTransition: null,
};

function selectInfoTab(index) {
  infoTabs.forEach((tab, tabIndex) => {
    const selected = tabIndex === index;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    infoPanels[tabIndex].hidden = !selected;
  });
}

infoTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectInfoTab(index));
  tab.addEventListener("keydown", (event) => {
    let nextIndex;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % infoTabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (index + infoTabs.length - 1) % infoTabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = infoTabs.length - 1;
    else return;
    event.preventDefault();
    selectInfoTab(nextIndex);
    infoTabs[nextIndex].focus();
  });
});

function showMetadataMessage(message, loading = false) {
  overviewPanel.replaceChildren();
  detailPanel.replaceChildren();
  metadataMessage.textContent = message;
  metadataMessage.hidden = false;
  metadataContent.setAttribute("aria-busy", String(loading));
}

function formatMetadataValue(value) {
  if (value === null || value === undefined || (typeof value === "string" && !value.trim())) return "—";
  if (typeof value === "boolean") return value ? "예" : "아니오";
  if (typeof value === "number") return value.toLocaleString("ko-KR");
  if (Array.isArray(value)) return value.length ? value.map(formatMetadataValue).join("\n") : "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function createMetadataList(rows) {
  const list = document.createElement("dl");
  list.className = "info-list";
  rows.forEach(([label, value, isTitle = false]) => {
    const row = document.createElement("div");
    row.className = isTitle ? "info-row info-row-title" : "info-row";
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = formatMetadataValue(value);
    description.classList.toggle("muted", description.textContent === "—");
    row.append(term, description);
    list.append(row);
  });
  return list;
}

function renderMetadata(data) {
  const { video, media } = data;
  const detail = data.detail ?? {};
  overviewPanel.replaceChildren(createMetadataList([
    ["제목", video.title, true],
    ["영상 ID", String(video.id)],
    ["YouTube ID", video.video_id],
    ["게시일", video.publishTime],
    ["영상 길이", detail.duration],
    ["해상도", detail.resolution],
    ["라이선스", detail.license],
    ["자막", detail.has_caption],
    ["합성 콘텐츠 표시", detail.is_synthetic_marked],
    ["조회 / 좋아요 / 댓글", [detail.view_count, detail.like_count, detail.comment_count].map(formatMetadataValue).join(" / ")],
    ["원본 파일", media.available ? "있음" : "없음"],
  ]));

  const fragment = document.createDocumentFragment();
  for (const [name, value] of [["video", video], ["detail", data.detail], ["media", media]]) {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    heading.className = "metadata-group-title";
    heading.textContent = name;
    section.append(heading);
    if (value == null) {
      const message = document.createElement("p");
      message.className = "metadata-message";
      message.textContent = "저장된 상세 메타데이터가 없습니다.";
      section.append(message);
    } else {
      const rows = Object.entries(value).map(([key, item]) => [key, key === "id" ? String(item) : item]);
      section.append(createMetadataList(rows));
    }
    fragment.append(section);
  }
  detailPanel.replaceChildren(fragment);
  metadataMessage.hidden = true;
  metadataContent.setAttribute("aria-busy", "false");
}

const playerState = {
  selectedClip: null,
  selectedButton: null,
  videoRefId: null,
  status: "idle",
  requestId: 0,
  requestController: null,
  mediaController: null,
};

const clipPlayback = { range: null, run: null };

function calculateClipRange(timeSec, duration) {
  if (!Number.isFinite(duration) || duration <= 0
    || !Number.isFinite(timeSec) || timeSec < 0 || timeSec > duration) return null;
  const length = Math.min(6, duration);
  const start = Math.max(0, Math.min(timeSec - 3, duration - length));
  return { start, end: Math.min(duration, start + length) };
}

function showClipMessage(message = "") {
  clipPlaybackMessage.textContent = message;
  clipPlaybackMessage.hidden = !message;
}

function cancelClipPlayback(pause = false) {
  const run = clipPlayback.run;
  clipPlayback.run = null;
  if (run) {
    run.controller.abort();
    if (run.frameCallbackId !== null) {
      videoPlayer.cancelVideoFrameCallback(run.frameCallbackId);
    }
  }
  if (pause) videoPlayer.pause();
}

function updateClipButton() {
  const ready = playerState.status === "ready"
    && playerState.selectedClip?.video_ref_id === playerState.videoRefId
    && videoPlayer.readyState >= videoPlayer.HAVE_METADATA && !videoPlayer.error;
  const range = ready
    ? calculateClipRange(playerState.selectedClip.time_sec, videoPlayer.duration)
    : null;
  const run = clipPlayback.run;
  if (run && (!range || range.start !== run.range.start || range.end !== run.range.end)) {
    cancelClipPlayback(true);
  }
  clipPlayback.range = range;
  clipPlayButton.disabled = !range;
  showClipMessage(ready && !range
    ? "영상 길이가 유효하지 않거나 검출 시각이 원본 범위를 벗어나 클립을 재생할 수 없습니다."
    : "");
}

function isCurrentClipRun(run) {
  return clipPlayback.run === run && !run.controller.signal.aborted
    && playerState.requestId === run.sourceRequestId
    && playerState.selectedClip === run.clip && playerState.status === "ready";
}

function seekToClipStart(run) {
  const target = run.range.start;
  if (!videoPlayer.seeking && videoPlayer.currentTime === target) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const signal = run.controller.signal;
    const cleanup = () => {
      videoPlayer.removeEventListener("seeked", onSeeked);
      signal.removeEventListener("abort", onAbort);
    };
    const onAbort = () => {
      cleanup();
      reject(new DOMException("Clip playback cancelled", "AbortError"));
    };
    const onSeeked = () => {
      if (videoPlayer.seeking) return;
      // Allow reduced currentTime precision, but do not resume a manual seek elsewhere.
      if (Math.abs(videoPlayer.currentTime - target) > 0.1) {
        onAbort();
        return;
      }
      cleanup();
      resolve();
    };
    if (signal.aborted) {
      onAbort();
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
    videoPlayer.addEventListener("seeked", onSeeked);
    try {
      videoPlayer.currentTime = target;
    } catch (error) {
      cleanup();
      reject(error);
    }
  });
}

async function playSelectedClip() {
  cancelClipPlayback(true);
  updateClipButton();
  if (!clipPlayback.range) return;

  const run = {
    range: clipPlayback.range,
    clip: playerState.selectedClip,
    sourceRequestId: playerState.requestId,
    controller: new AbortController(),
    frameCallbackId: null,
  };
  clipPlayback.run = run;
  try {
    await seekToClipStart(run);
    if (!isCurrentClipRun(run)) return;

    // Native seeking returns to ordinary video playback; pausing can resume the clip.
    videoPlayer.addEventListener("seeking", () => {
      if (isCurrentClipRun(run) && videoPlayer.seeking) cancelClipPlayback();
    }, { signal: run.controller.signal });
    videoPlayer.addEventListener("ended", () => {
      if (isCurrentClipRun(run) && videoPlayer.ended) cancelClipPlayback();
    }, { signal: run.controller.signal });
    const watchEnd = (_now, metadata) => {
      run.frameCallbackId = null;
      if (!isCurrentClipRun(run)) return;
      if (videoPlayer.seeking) {
        cancelClipPlayback();
        return;
      }
      if (metadata.mediaTime >= run.range.end) {
        cancelClipPlayback(true);
        return;
      }
      run.frameCallbackId = videoPlayer.requestVideoFrameCallback(watchEnd);
    };
    run.frameCallbackId = videoPlayer.requestVideoFrameCallback(watchEnd);
    await videoPlayer.play();
  } catch (error) {
    if (!isCurrentClipRun(run)) return;
    cancelClipPlayback(true);
    // A native pause or a superseding seek is a normal cancellation.
    if (error.name === "AbortError") return;
    showClipMessage("클립을 재생하지 못했습니다. 클립 재생 버튼을 다시 눌러 주세요.");
    console.error("Failed to play selected clip", error);
  }
}

clipPlayButton.addEventListener("click", playSelectedClip);

function showPlayerMessage(title, detail) {
  cancelClipPlayback(true);
  updateClipButton();
  playerMessageTitle.textContent = title;
  playerMessageDetail.textContent = detail;
  playerMessage.hidden = false;
  videoPlayer.hidden = true;
}

function selectCandidate(clip, button) {
  cancelClipPlayback(true);
  playerState.selectedButton?.classList.remove("is-selected");
  playerState.selectedButton?.removeAttribute("aria-current");
  button.classList.add("is-selected");
  button.setAttribute("aria-current", "true");
  playerState.selectedClip = clip;
  playerState.selectedButton = button;
  videoPlayer.setAttribute("aria-label", `영상 ${clip.video_ref_id}의 원본 영상`);

  // Reviews belong to a candidate, even when its original video is reused below.
  void loadReviews();

  // Keep both pending loads and connected media when only the candidate changes.
  if (playerState.videoRefId === clip.video_ref_id && playerState.status !== "error") {
    updateClipButton();
    if (playerState.status === "ready") {
      void playSelectedClip();
    }
    return;
  }
  loadVideo(clip.video_ref_id);
}

async function loadVideo(videoRefId) {
  const requestId = ++playerState.requestId;
  playerState.requestController?.abort();
  playerState.mediaController?.abort();
  playerState.mediaController = null;
  playerState.videoRefId = videoRefId;
  playerState.status = "loading";
  showPlayerMessage("원본 영상을 불러오는 중입니다.", `영상 ${videoRefId}`);
  showMetadataMessage("영상 정보를 불러오는 중입니다.", true);

  // Clear the previous source before fetching so it cannot play under a new selection.
  if (videoPlayer.hasAttribute("src")) {
    videoPlayer.removeAttribute("src");
    videoPlayer.load();
  }

  const controller = new AbortController();
  playerState.requestController = controller;
  let metadataLoaded = false;
  try {
    const response = await fetch(`/videos/${videoRefId}`, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`Video detail request failed: ${response.status}`);
    }
    const data = await response.json();
    if (requestId !== playerState.requestId) return;
    if (data?.video?.id !== videoRefId || typeof data?.media?.available !== "boolean") {
      throw new Error("Invalid video detail response");
    }
    // Stored metadata remains useful even when the media is missing or unplayable.
    renderMetadata(data);
    metadataLoaded = true;
    if (!data.media.available) {
      playerState.status = "error";
      showPlayerMessage("원본 영상 파일이 없습니다.", "파일을 확인한 뒤 후보를 다시 선택해 주세요.");
      return;
    }
    if (typeof data.media.url !== "string" || !data.media.url.trim()) {
      throw new Error("Missing video media URL");
    }

    const sourceUrl = new URL(data.media.url, document.baseURI).href;
    const mediaController = new AbortController();
    playerState.mediaController = mediaController;
    const isCurrentSource = () => requestId === playerState.requestId
      && videoPlayer.currentSrc === sourceUrl;

    videoPlayer.addEventListener("loadedmetadata", () => {
      if (!isCurrentSource() || videoPlayer.readyState < videoPlayer.HAVE_METADATA) return;
      playerState.status = "ready";
      videoPlayer.hidden = false;
      playerMessage.hidden = true;
      updateClipButton();
      void playSelectedClip();
    }, { signal: mediaController.signal });

    videoPlayer.addEventListener("durationchange", () => {
      if (isCurrentSource()) updateClipButton();
    }, { signal: mediaController.signal });

    videoPlayer.addEventListener("error", () => {
      if (requestId !== playerState.requestId || !videoPlayer.error) return;
      playerState.status = "error";
      videoPlayer.pause();
      showPlayerMessage("영상을 재생할 수 없습니다.", "파일이나 연결 상태를 확인한 뒤 후보를 다시 선택해 주세요.");
    }, { signal: mediaController.signal });

    videoPlayer.src = sourceUrl;
    videoPlayer.load();
  } catch (error) {
    if (requestId !== playerState.requestId || controller.signal.aborted) return;
    playerState.status = "error";
    if (!metadataLoaded) {
      showMetadataMessage("영상 정보를 불러오지 못했습니다. 후보를 다시 선택해 주세요.");
    }
    showPlayerMessage(metadataLoaded ? "영상을 재생할 수 없습니다." : "원본 영상 정보를 불러오지 못했습니다.", "후보를 다시 선택해 주세요.");
    console.error("Failed to load selected video", error);
  } finally {
    if (playerState.requestController === controller) {
      playerState.requestController = null;
    }
  }
}

function formatDetectionTime(seconds) {
  const totalMilliseconds = Math.round(seconds * 1000);
  const milliseconds = String(totalMilliseconds % 1000).padStart(3, "0");
  const totalSeconds = Math.floor(totalMilliseconds / 1000);
  const secondsPart = String(totalSeconds % 60).padStart(2, "0");
  const minutesPart = String(Math.floor(totalSeconds / 60) % 60).padStart(2, "0");
  const hours = Math.floor(totalSeconds / 3600);
  const hoursPart = hours > 0 ? `${String(hours).padStart(2, "0")}:` : "";
  return `${hoursPart}${minutesPart}:${secondsPart}.${milliseconds}`;
}

function createSpan(className, text) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
}

function createCandidate(clip, index) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.className = "candidate";
  button.type = "button";
  button.dataset.clipId = clip.id;
  button.dataset.videoRefId = clip.video_ref_id;
  button.dataset.frame = clip.frame;
  button.addEventListener("click", () => requestTransition({ type: "candidate", clipId: clip.id }));

  const number = createSpan("candidate-number", String(index + 1).padStart(2, "0"));
  const content = createSpan("candidate-content", "");
  const title = createSpan("candidate-title", "");
  const titleText = createSpan("candidate-title-text", clip.title?.trim() || "제목 없음");
  const badge = createSpan("candidate-review-badge", "");
  title.append(badge, titleText);
  const metadata = createSpan("candidate-meta", `Clip ${clip.video_ref_id}`);
  // metadata.append(createSpan("", `#F ${clip.frame}`));
  const time = createSpan("candidate-time", formatDetectionTime(clip.time_sec));
  metadata.append(time);
  content.append(title, metadata);
  button.append(number, content);
  candidateButtons.set(clip.id, button);
  if (playerState.selectedClip?.id === clip.id) {
    button.classList.add("is-selected");
    button.setAttribute("aria-current", "true");
    playerState.selectedButton = button;
  }
  item.append(button);
  return item;
}

function isClipItem(clip) {
  return clip !== null && typeof clip === "object"
    && Number.isInteger(clip.id) && clip.id > 0
    && Number.isInteger(clip.video_ref_id)
    && Number.isInteger(clip.frame)
    && Number.isFinite(clip.time_sec) && clip.time_sec >= 0
    && (clip.title === null || typeof clip.title === "string");
}

function normalizeNote(value) {
  // Match Python str.strip() in ReviewSaveRequest, including its control separators.
  return value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, "") || null;
}

function validUsername(value) {
  return typeof value === "string" && value.trim().length > 0
    && [...value.trim()].length <= 128;
}

function isReviewItem(value) {
  return value !== null && typeof value === "object"
    && Number.isInteger(value.id) && value.id > 0
    && Number.isInteger(value.clip_candidate_id) && value.clip_candidate_id > 0
    && validUsername(value.username) && value.username === value.username.trim()
    && Object.hasOwn(REVIEW_LABELS, value.status)
    && (value.note === null || typeof value.note === "string")
    && typeof value.created_at === "string" && Number.isFinite(Date.parse(value.created_at))
    && typeof value.updated_at === "string" && Number.isFinite(Date.parse(value.updated_at));
}

async function requestJson(url, options, fallbackMessage) {
  let response;
  let data;
  try {
    response = await fetch(url, options);
    data = await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error(fallbackMessage);
  }
  if (!response.ok) {
    const message = typeof data?.detail === "string" && data.detail.trim()
      ? data.detail : response.status === 422
        ? "검수자 이름과 판정을 확인해 주세요." : fallbackMessage;
    throw new Error(message);
  }
  return data;
}

function setReviewMessage(message = "", isError = false) {
  reviewMessage.textContent = message;
  reviewMessage.classList.toggle("is-error", isError);
}

function isReviewDirty() {
  return reviewState.draftStatus !== (reviewState.saved?.status ?? null)
    || normalizeNote(reviewState.draftNote) !== normalizeNote(reviewState.saved?.note ?? "");
}

function reviewIsReady() {
  return Boolean(reviewState.username) && listState.status === "ready"
    && reviewState.status === "ready" && reviewState.clipId === playerState.selectedClip?.id;
}

function updateReviewControls() {
  const locked = reviewState.saving || Boolean(reviewState.pendingTransition);
  const editable = reviewIsReady() && !locked;
  reviewEditor.disabled = !editable;
  reviewSave.disabled = !editable || !reviewState.draftStatus;
  reviewSaveNext.disabled = reviewSave.disabled;
  reviewerName.disabled = locked;
  reviewerApply.disabled = locked;
  reviewerApply.textContent = reviewState.username ? "변경 적용" : "적용";
  clipListRetry.disabled = locked || listState.status === "loading";
  reviewRetry.disabled = locked || listState.status !== "ready";
  for (const button of candidateButtons.values()) {
    button.disabled = locked || listState.status !== "ready";
  }
  reviewStatusButtons.forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.reviewStatus === reviewState.draftStatus));
  });
  unsavedSave.disabled = reviewState.saving || !reviewIsReady() || !reviewState.draftStatus;
  unsavedDiscard.disabled = reviewState.saving;
  unsavedCancel.disabled = reviewState.saving;
  reviewSaveState.textContent = reviewState.saving ? "저장 중…"
    : isReviewDirty() ? "저장하지 않은 변경"
      : !reviewState.username ? "검수자 이름을 적용해 주세요."
        : !playerState.selectedClip ? "후보를 선택해 주세요."
          : listState.status !== "ready" || reviewState.status !== "ready" ? "검수 상태 확인 필요"
            : reviewState.saved ? "저장됨" : "미검수";
}

function updateListReviews() {
  const known = Boolean(reviewState.username) && listState.status === "ready";
  let completed = 0;
  let held = 0;
  listState.items.forEach(clip => {
    const status = clip.my_review?.status;
    if (status === "accepted" || status === "rejected") completed += 1;
    if (status === "needs_review") held += 1;
    const badge = candidateButtons.get(clip.id)?.querySelector(".candidate-review-badge");
    if (!badge) return;
    badge.hidden = !reviewState.username;
    badge.dataset.status = known ? status ?? "unreviewed" : "unknown";
    badge.textContent = known ? REVIEW_LABELS[status] ?? "미검수" : "확인 필요";
  });
  // reviewProgress.textContent = !reviewState.username ? "이름을 적용하면 내 검수 진행을 표시합니다."
  //   : known ? `내 검수 완료 ${completed} / ${listState.items.length} · 보류 ${held}`
  //     : "내 검수 진행을 확인할 수 없습니다.";
}

function renderReviewRecords() {
  reviewTableBody.replaceChildren();
  reviewTable.hidden = reviewState.status !== "ready" || reviewState.records.length === 0;
  if (reviewState.status !== "ready") return;
  reviewRecordsMessage.textContent = reviewState.records.length ? "검수자별 최신 판정입니다." : "저장된 검수 기록이 없습니다.";
  reviewRecordsMessage.classList.remove("is-error");
  const fragment = document.createDocumentFragment();
  reviewState.records.forEach(review => {
    const row = document.createElement("tr");
    for (const value of [review.username, REVIEW_LABELS[review.status], review.note ?? "—", new Date(review.updated_at).toLocaleString()]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    fragment.append(row);
  });
  reviewTableBody.append(fragment);
}

function resetReview() {
  reviewState.controller?.abort();
  reviewState.controller = null;
  reviewState.requestId += 1;
  reviewState.clipId = playerState.selectedClip?.id ?? null;
  reviewState.status = "idle";
  reviewState.records = [];
  reviewState.saved = null;
  reviewState.draftStatus = null;
  reviewState.draftNote = "";
  reviewNote.value = "";
  // reviewSelection.textContent = reviewState.clipId === null ? "후보를 선택해 주세요." : `후보 ${reviewState.clipId}`;
  reviewRecordsMessage.textContent = reviewState.clipId === null ? "후보를 선택하면 검수 기록을 표시합니다." : "검수 기록을 불러오는 중입니다.";
  reviewRecordsMessage.classList.remove("is-error");
  reviewRetry.hidden = true;
  setReviewMessage();
  renderReviewRecords();
  updateReviewControls();
}

async function loadReviews() {
  if (reviewState.saving) return;
  resetReview();
  const clipId = reviewState.clipId;
  if (clipId === null || listState.status !== "ready") return;
  const username = reviewState.username;
  const requestId = reviewState.requestId;
  const controller = new AbortController();
  reviewState.controller = controller;
  reviewState.status = "loading";
  updateReviewControls();
  const isCurrent = () => requestId === reviewState.requestId && !controller.signal.aborted
    && username === reviewState.username && clipId === playerState.selectedClip?.id;
  try {
    const data = await requestJson(`/clips/${clipId}/reviews`, { signal: controller.signal }, "검수 기록을 불러오지 못했습니다. 다시 시도해 주세요.");
    if (!isCurrent()) return;
    if (data?.clip_candidate_id !== clipId || !Array.isArray(data.items)
      || !data.items.every(item => isReviewItem(item) && item.clip_candidate_id === clipId)
      || new Set(data.items.map(item => item.username)).size !== data.items.length) {
      throw new Error("검수 기록 응답을 확인할 수 없습니다. 다시 시도해 주세요.");
    }
    reviewState.records = data.items;
    reviewState.saved = data.items.find(item => item.username === username) ?? null;
    reviewState.draftStatus = reviewState.saved?.status ?? null;
    reviewState.draftNote = reviewState.saved?.note ?? "";
    reviewNote.value = reviewState.draftNote;
    reviewState.status = "ready";
    if (username) playerState.selectedClip.my_review = reviewState.saved;
    renderReviewRecords();
    updateListReviews();
  } catch (error) {
    if (!isCurrent()) return;
    reviewState.status = "error";
    reviewRecordsMessage.textContent = error.message;
    reviewRecordsMessage.classList.add("is-error");
    reviewRetry.hidden = false;
  } finally {
    if (isCurrent()) {
      reviewState.controller = null;
      updateReviewControls();
    }
  }
}

function clearMissingSelection() {
  cancelClipPlayback(true);
  playerState.requestController?.abort();
  playerState.mediaController?.abort();
  playerState.requestId += 1;
  playerState.selectedClip = null;
  playerState.selectedButton = null;
  playerState.videoRefId = null;
  playerState.status = "idle";
  videoPlayer.removeAttribute("src");
  videoPlayer.load();
  showPlayerMessage("검수 대상을 선택해 주세요.", "선택했던 후보가 목록에 없습니다.");
  showMetadataMessage("검수 대상을 선택해 주세요.");
  resetReview();
}

async function loadClips() {
  if (reviewState.saving || isReviewDirty()) return;
  listState.controller?.abort();
  const requestId = ++listState.requestId;
  const controller = new AbortController();
  const username = reviewState.username;
  listState.controller = controller;
  listState.status = "loading";
  resetReview();
  clipList.setAttribute("aria-busy", "true");
  clipCount.textContent = "—";
  clipListMessage.hidden = false;
  clipListMessage.classList.remove("is-error");
  clipListMessage.textContent = "목록을 불러오는 중입니다.";
  clipListRetry.hidden = true;
  updateListReviews();
  updateReviewControls();
  const isCurrent = () => requestId === listState.requestId && !controller.signal.aborted && username === reviewState.username;
  try {
    const query = username ? `?${new URLSearchParams({ username })}` : "";
    const data = await requestJson(`/clips${query}`, { signal: controller.signal }, "목록을 불러오지 못했습니다. 다시 시도해 주세요.");
    if (!isCurrent()) return;
    if (!Array.isArray(data?.items) || !data.items.every(clip => isClipItem(clip)
      && (clip.my_review === null || (username && isReviewItem(clip.my_review)
        && clip.my_review.clip_candidate_id === clip.id && clip.my_review.username === username)))
      || new Set(data.items.map(clip => clip.id)).size !== data.items.length) {
      throw new Error("후보 목록 응답을 확인할 수 없습니다. 다시 시도해 주세요.");
    }
    // Keep object identity for the active frame callback while only the reviewer changes.
    const existing = new Map(listState.items.map(clip => [clip.id, clip]));
    listState.items = data.items.map(clip => Object.assign(existing.get(clip.id) ?? {}, clip));
    listState.status = "ready";
    candidateButtons.clear();
    const fragment = document.createDocumentFragment();
    listState.items.forEach((clip, index) => fragment.append(createCandidate(clip, index)));
    clipList.replaceChildren(fragment);
    if (playerState.selectedClip && !candidateButtons.has(playerState.selectedClip.id)) clearMissingSelection();
    clipCount.textContent = String(listState.items.length);
    clipListMessage.textContent = listState.items.length === 0 ? "검수 대상이 없습니다." : "";
    clipListMessage.hidden = listState.items.length > 0;
    updateListReviews();
    if (playerState.selectedClip) void loadReviews();
  } catch (error) {
    if (!isCurrent()) return;
    listState.status = "error";
    clipListMessage.hidden = false;
    clipListMessage.textContent = error.message;
    clipListMessage.classList.add("is-error");
    clipListRetry.hidden = false;
    reviewRecordsMessage.textContent = "후보 목록을 다시 불러온 뒤 검수할 수 있습니다.";
    updateListReviews();
  } finally {
    if (isCurrent()) {
      listState.controller = null;
      clipList.setAttribute("aria-busy", "false");
      updateReviewControls();
    }
  }
}

function applyReviewer(username) {
  reviewState.username = username;
  reviewerName.value = username;
  reviewerMessage.textContent = `현재 검수자: ${username}`;
  reviewerMessage.classList.remove("is-error");
  try {
    localStorage.setItem(REVIEWER_STORAGE_KEY, username);
  } catch {
    reviewerMessage.textContent += " · 이 브라우저에서는 이름을 기억할 수 없습니다.";
  }
  void loadClips();
}

function performTransition(transition) {
  if (transition.type === "username") {
    applyReviewer(transition.username);
  } else {
    const clip = listState.items.find(item => item.id === transition.clipId);
    const button = candidateButtons.get(transition.clipId);
    if (clip && button) selectCandidate(clip, button);
  }
}

function requestTransition(transition) {
  if (reviewState.saving || reviewState.pendingTransition) return;
  if (transition.type === "candidate" && listState.status !== "ready") return;
  if (transition.type === "username" && transition.username === reviewState.username) {
    reviewerName.value = reviewState.username;
    return;
  }
  if (transition.type === "candidate" && transition.clipId === playerState.selectedClip?.id && isReviewDirty()) return;
  if (isReviewDirty()) {
    reviewState.pendingTransition = transition;
    unsavedMessage.textContent = reviewState.draftStatus ? "" : "저장하려면 먼저 판정을 선택해 주세요.";
    unsavedDialog.showModal();
    updateReviewControls();
    return;
  }
  performTransition(transition);
}

function closeUnsavedDialog() {
  reviewState.pendingTransition = null;
  unsavedDialog.close();
  reviewerName.value = reviewState.username ?? "";
  updateReviewControls();
}

async function saveReview() {
  if (reviewState.saving || !reviewIsReady() || !reviewState.draftStatus) return false;
  const clipId = reviewState.clipId;
  const username = reviewState.username;
  const payload = { username, status: reviewState.draftStatus, note: normalizeNote(reviewState.draftNote) };
  // Invalidate reads started before this write so they cannot undo its UI result.
  listState.controller?.abort();
  listState.requestId += 1;
  reviewState.controller?.abort();
  reviewState.requestId += 1;
  reviewState.saving = true;
  setReviewMessage("저장 중입니다.");
  unsavedMessage.textContent = "저장 중입니다.";
  updateReviewControls();
  try {
    const saved = await requestJson(`/clips/${clipId}/review`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }, "저장 결과를 확인하지 못했습니다. 입력은 유지됩니다. 다시 저장해 주세요.");
    if (!isReviewItem(saved) || saved.clip_candidate_id !== clipId || saved.username !== username) {
      throw new Error("저장 응답을 확인할 수 없습니다. 입력은 유지됩니다. 다시 저장해 주세요.");
    }
    reviewState.saved = saved;
    reviewState.draftStatus = saved.status;
    reviewState.draftNote = saved.note ?? "";
    reviewNote.value = reviewState.draftNote;
    reviewState.records = reviewState.records.filter(item => item.username !== username).concat(saved)
      .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at) || right.id - left.id);
    const clip = listState.items.find(item => item.id === clipId);
    if (clip) clip.my_review = saved;
    renderReviewRecords();
    updateListReviews();
    setReviewMessage("저장했습니다.");
    return true;
  } catch (error) {
    setReviewMessage(error.message, true);
    unsavedMessage.textContent = error.message;
    return false;
  } finally {
    reviewState.saving = false;
    updateReviewControls();
  }
}

reviewerForm.addEventListener("submit", event => {
  event.preventDefault();
  if (!validUsername(reviewerName.value)) {
    reviewerMessage.textContent = "검수자 이름을 1~128자로 입력해 주세요.";
    reviewerMessage.classList.add("is-error");
    return;
  }
  requestTransition({ type: "username", username: reviewerName.value.trim() });
});
reviewStatusButtons.forEach(button => button.addEventListener("click", () => {
  if (!reviewIsReady() || reviewState.saving || reviewState.pendingTransition) return;
  reviewState.draftStatus = button.dataset.reviewStatus;
  setReviewMessage();
  updateReviewControls();
}));
reviewNote.addEventListener("input", () => {
  reviewState.draftNote = reviewNote.value;
  setReviewMessage();
  updateReviewControls();
});
reviewSave.addEventListener("click", () => { void saveReview(); });
reviewSaveNext.addEventListener("click", async () => {
  if (!await saveReview()) return;
  const index = listState.items.findIndex(clip => clip.id === reviewState.clipId);
  const next = listState.items.slice(index + 1).find(clip => clip.my_review === null);
  if (!next) {
    setReviewMessage("저장했습니다. 뒤쪽에 미검수 후보가 없습니다.");
    return;
  }
  performTransition({ type: "candidate", clipId: next.id });
});
clipListRetry.addEventListener("click", () => { void loadClips(); });
reviewRetry.addEventListener("click", () => { void loadReviews(); });
unsavedSave.addEventListener("click", async () => {
  const transition = reviewState.pendingTransition;
  if (!transition || !await saveReview()) return;
  closeUnsavedDialog();
  performTransition(transition);
});
unsavedDiscard.addEventListener("click", () => {
  if (reviewState.saving) return;
  const transition = reviewState.pendingTransition;
  reviewState.draftStatus = reviewState.saved?.status ?? null;
  reviewState.draftNote = reviewState.saved?.note ?? "";
  reviewNote.value = reviewState.draftNote;
  closeUnsavedDialog();
  if (transition) performTransition(transition);
});
unsavedCancel.addEventListener("click", closeUnsavedDialog);
unsavedDialog.addEventListener("cancel", event => {
  event.preventDefault();
  if (!reviewState.saving) closeUnsavedDialog();
});
window.addEventListener("beforeunload", event => {
  if (!isReviewDirty() && !reviewState.saving) return;
  event.preventDefault();
  event.returnValue = "";
});

try {
  const savedUsername = localStorage.getItem(REVIEWER_STORAGE_KEY);
  if (validUsername(savedUsername)) {
    reviewState.username = savedUsername.trim();
    reviewerName.value = reviewState.username;
    reviewerMessage.textContent = `현재 검수자: ${reviewState.username}`;
  }
} catch {
  reviewerMessage.textContent = "이름을 입력해 주세요. 이 브라우저에서는 이름을 기억할 수 없습니다.";
}

// Fetch the whole list once per applied reviewer; initial candidate selection stays manual.
void loadClips();
