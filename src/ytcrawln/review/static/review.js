"use strict";

const clipList = document.getElementById("clip-list");
const clipCount = document.getElementById("clip-count");
const clipListMessage = document.getElementById("clip-list-message");
const videoPlayer = document.getElementById("video-player");
const playerMessage = document.getElementById("player-message");
const playerMessageTitle = document.getElementById("player-message-title");
const playerMessageDetail = document.getElementById("player-message-detail");

const playerState = {
  selectedClip: null,
  selectedButton: null,
  videoRefId: null,
  status: "idle",
  requestId: 0,
  requestController: null,
  mediaController: null,
};

function showPlayerMessage(title, detail) {
  playerMessageTitle.textContent = title;
  playerMessageDetail.textContent = detail;
  playerMessage.hidden = false;
  videoPlayer.hidden = true;
}

function selectCandidate(clip, button) {
  playerState.selectedButton?.classList.remove("is-selected");
  playerState.selectedButton?.removeAttribute("aria-current");
  button.classList.add("is-selected");
  button.setAttribute("aria-current", "true");
  playerState.selectedClip = clip;
  playerState.selectedButton = button;
  videoPlayer.setAttribute("aria-label", `영상 ${clip.video_ref_id}의 원본 영상`);
  videoPlayer.pause();

  // Keep both pending loads and connected media when only the candidate changes.
  if (playerState.videoRefId === clip.video_ref_id && playerState.status !== "error") {
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

  // Clear the previous source before fetching so it cannot play under a new selection.
  if (videoPlayer.hasAttribute("src")) {
    videoPlayer.removeAttribute("src");
    videoPlayer.load();
  }

  const controller = new AbortController();
  playerState.requestController = controller;
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
    showPlayerMessage("원본 영상 정보를 불러오지 못했습니다.", "후보를 다시 선택해 주세요.");
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
