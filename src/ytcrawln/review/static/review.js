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

  // Keep both pending loads and connected media when only the candidate changes.
  if (playerState.videoRefId === clip.video_ref_id && playerState.status !== "error") {
    updateClipButton();
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
  button.dataset.videoRefId = clip.video_ref_id;
  button.dataset.frame = clip.frame;
  button.addEventListener("click", () => selectCandidate(clip, button));

  const number = createSpan("candidate-number", String(index + 1).padStart(2, "0"));
  const content = createSpan("candidate-content", "");
  const title = createSpan("candidate-title", clip.title?.trim() || "제목 없음");
  const metadata = createSpan("candidate-meta", `Clip ${clip.video_ref_id}`);
  // metadata.append(createSpan("", `#F ${clip.frame}`));
  const time = createSpan("candidate-time", formatDetectionTime(clip.time_sec));
  metadata.append(time);
  content.append(title, metadata);
  button.append(number, content);
  item.append(button);
  return item;
}

function isClipItem(clip) {
  return clip !== null && typeof clip === "object"
    && Number.isInteger(clip.video_ref_id)
    && Number.isInteger(clip.frame)
    && Number.isFinite(clip.time_sec) && clip.time_sec >= 0
    && (clip.title === null || typeof clip.title === "string");
}

async function loadClips() {
  clipList.setAttribute("aria-busy", "true");
  clipCount.textContent = "—";
  clipListMessage.hidden = false;
  clipListMessage.textContent = "목록을 불러오는 중입니다.";

  try {
    const response = await fetch("/clips");
    if (!response.ok) {
      throw new Error(`Clip list request failed: ${response.status}`);
    }
    const data = await response.json();
    if (!Array.isArray(data?.items) || !data.items.every(isClipItem)) {
      throw new Error("Invalid clip list response");
    }

    const fragment = document.createDocumentFragment();
    data.items.forEach((clip, index) => {
      fragment.append(createCandidate(clip, index));
    });
    clipList.replaceChildren(fragment);
    clipCount.textContent = String(data.items.length);
    clipListMessage.textContent = data.items.length === 0 ? "검수 대상이 없습니다." : "";
    clipListMessage.hidden = data.items.length > 0;
  } catch (error) {
    clipList.replaceChildren();
    clipCount.textContent = "—";
    clipListMessage.hidden = false;
    clipListMessage.textContent = "목록을 불러오지 못했습니다. 페이지를 새로고침해 주세요.";
    console.error("Failed to load review candidates", error);
  } finally {
    clipList.setAttribute("aria-busy", "false");
  }
}

// This script is deferred: the DOM is ready, and each page load fetches once.
loadClips();
