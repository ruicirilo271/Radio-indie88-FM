const config = window.RADIO_CONFIG || {
  name: "Radio Indie88 FM",
  stream: "https://localradio.streamb.live/SB00348",
  defaultCover: "/default-cover.webp",
  identifySeconds: 12
};

const audio = document.getElementById("radioAudio");
const playBtn = document.getElementById("playBtn");
const playIcon = document.getElementById("playIcon");
const playText = document.getElementById("playText");
const identifyBtn = document.getElementById("identifyBtn");
const muteBtn = document.getElementById("muteBtn");
const reloadBtn = document.getElementById("reloadBtn");
const volumeSlider = document.getElementById("volumeSlider");
const volumeText = document.getElementById("volumeText");
const statusText = document.getElementById("statusText");
const identifyStatus = document.getElementById("identifyStatus");
const livePill = document.getElementById("livePill");
const liveText = document.getElementById("liveText");
const cover = document.getElementById("cover");
const trackTitle = document.getElementById("trackTitle");
const trackArtist = document.getElementById("trackArtist");
const historyList = document.getElementById("historyList");
const topList = document.getElementById("topList");
const clearHistoryBtn = document.getElementById("clearHistoryBtn");
const clearTopBtn = document.getElementById("clearTopBtn");
const copyBtn = document.getElementById("copyBtn");
const streamUrl = document.getElementById("streamUrl");

const STORAGE_HISTORY = "radio_indie88_history";
const STORAGE_TOP = "radio_indie88_top";
const STORAGE_VOLUME = "radio_indie88_volume";

let isPlaying = false;
let identifyTimer = null;
let identifyInProgress = false;
let currentTrackKey = "";

audio.src = config.stream;
audio.volume = 0.85;
cover.src = config.defaultCover;

function toast(message) {
  const old = document.querySelector(".toast");
  if (old) old.remove();

  const div = document.createElement("div");
  div.className = "toast";
  div.textContent = message;
  document.body.appendChild(div);

  requestAnimationFrame(() => div.classList.add("show"));

  window.setTimeout(() => {
    div.classList.remove("show");
    window.setTimeout(() => div.remove(), 250);
  }, 2400);
}

function setPlayingUI(playing) {
  isPlaying = playing;
  document.body.classList.toggle("playing", playing);
  livePill.classList.toggle("playing", playing);
  playBtn.setAttribute("aria-pressed", String(playing));

  if (playing) {
    playIcon.textContent = "❚❚";
    playText.textContent = "Desligar Rádio";
    statusText.textContent = "Rádio ligada";
    liveText.textContent = "AO VIVO";
  } else {
    playIcon.textContent = "▶";
    playText.textContent = "Ligar Rádio";
    statusText.textContent = "Rádio desligada";
    liveText.textContent = "OFFLINE";
  }
}

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function saveJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn("Não foi possível guardar no browser:", error);
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function addTrackToHistory(track) {
  if (!track?.title || !track?.artist) return;

  const key = `${track.artist} - ${track.title}`;
  if (key === currentTrackKey) return;
  currentTrackKey = key;

  const item = {
    title: track.title,
    artist: track.artist,
    album: track.album || "",
    cover: track.cover || config.defaultCover,
    radio: config.name,
    time: new Date().toLocaleString("pt-PT")
  };

  let history = loadJson(STORAGE_HISTORY, []);
  history.unshift(item);
  history = history.slice(0, 10);
  saveJson(STORAGE_HISTORY, history);

  const top = loadJson(STORAGE_TOP, {});
  if (!top[key]) {
    top[key] = {
      title: item.title,
      artist: item.artist,
      album: item.album,
      cover: item.cover,
      count: 0
    };
  }

  top[key].count += 1;
  top[key].cover = item.cover;
  saveJson(STORAGE_TOP, top);

  renderHistory();
  renderTop();
}

function renderHistory() {
  const history = loadJson(STORAGE_HISTORY, []);

  if (!history.length) {
    historyList.className = "list empty";
    historyList.textContent = "Ainda não há histórico.";
    return;
  }

  historyList.className = "list";
  historyList.innerHTML = "";

  history.forEach((item, index) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="item-number">${index + 1}</div>
      <div>
        <div class="item-title">${escapeHtml(item.title)}</div>
        <div class="item-sub">${escapeHtml(item.artist)} · ${escapeHtml(item.time)}</div>
      </div>
      <div class="item-count">♪</div>
    `;
    historyList.appendChild(div);
  });
}

function renderTop() {
  const top = loadJson(STORAGE_TOP, {});
  const items = Object.values(top)
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  if (!items.length) {
    topList.className = "list empty";
    topList.textContent = "Ainda não há top.";
    return;
  }

  topList.className = "list";
  topList.innerHTML = "";

  items.forEach((item, index) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="item-number">${index + 1}</div>
      <div>
        <div class="item-title">${escapeHtml(item.title)}</div>
        <div class="item-sub">${escapeHtml(item.artist)}</div>
      </div>
      <div class="item-count">${item.count}x</div>
    `;
    topList.appendChild(div);
  });
}

async function identifySong(force = false) {
  if (identifyInProgress) return;

  identifyInProgress = true;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 40_000);

  try {
    identifyStatus.textContent = "A escutar...";
    identifyBtn.disabled = true;
    identifyBtn.textContent = "⏳";

    const endpoint = force ? "/api/identify/force" : "/api/identify";
    const response = await fetch(endpoint, {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" }
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();

    if (!data.ok) {
      identifyStatus.textContent = "Não identificado";

      const message = String(data.message || "Não foi possível identificar a música.");

      // Só existe incompatibilidade de versões quando ambos os backends
      // fornecem identificadores e estes são realmente diferentes.
      if (data.build && config.build && data.build !== config.build) {
        toast(`Versões diferentes: página ${config.build} / API ${data.build}`);
        console.warn("Versões diferentes entre a página e a API:", {
          pagina: config.build,
          api: data.build,
          erro: message
        });
      } else {
        // Mostra o erro verdadeiro devolvido pelo servidor.
        toast(message);
      }
      return;
    }

    trackTitle.textContent = data.title || config.name;
    trackArtist.textContent = data.artist || "Stream ao vivo";
    cover.src = data.cover || config.defaultCover;

    identifyStatus.textContent = data.source === "itunes"
      ? "Capa iTunes"
      : data.source === "shazam"
        ? "Capa Shazam"
        : "Identificado";

    addTrackToHistory(data);
  } catch (error) {
    console.error("Erro ao identificar:", error);
    identifyStatus.textContent = error.name === "AbortError" ? "Tempo esgotado" : "Erro";
    toast(error.name === "AbortError"
      ? "A identificação demorou demasiado tempo"
      : "Erro ao identificar música");
  } finally {
    window.clearTimeout(timeout);
    identifyInProgress = false;
    identifyBtn.disabled = false;
    identifyBtn.textContent = "🔎";
  }
}

function startAutoIdentify() {
  stopAutoIdentify();

  window.setTimeout(() => {
    if (isPlaying) identifySong(true);
  }, 3500);

  identifyTimer = window.setInterval(() => {
    if (isPlaying) identifySong(false);
  }, 60_000);
}

function stopAutoIdentify() {
  if (identifyTimer) {
    window.clearInterval(identifyTimer);
    identifyTimer = null;
  }
}

async function playRadio() {
  try {
    if (!audio.src) {
      audio.src = config.stream;
      audio.load();
    }

    await audio.play();
    setPlayingUI(true);
    startAutoIdentify();
    toast(`${config.name} ligada`);
  } catch (error) {
    console.error("Erro ao iniciar rádio:", error);
    setPlayingUI(false);
    stopAutoIdentify();
    toast("Erro ao iniciar a rádio. Clica novamente.");
  }
}

function stopRadio() {
  audio.pause();
  setPlayingUI(false);
  stopAutoIdentify();
  toast(`${config.name} desligada`);
}

function toggleRadio() {
  if (isPlaying && !audio.paused) {
    stopRadio();
  } else {
    playRadio();
  }
}

function reloadStream() {
  const wasPlaying = isPlaying;

  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  setPlayingUI(false);
  stopAutoIdentify();

  window.setTimeout(() => {
    audio.src = config.stream;
    audio.load();
    if (wasPlaying) playRadio();
  }, 300);

  toast("Stream recarregado");
}

function loadVolume() {
  const saved = localStorage.getItem(STORAGE_VOLUME);
  if (saved === null) return;

  const value = Number(saved);
  if (!Number.isNaN(value) && value >= 0 && value <= 1) {
    audio.volume = value;
    volumeSlider.value = String(value);
    volumeText.textContent = `${Math.round(value * 100)}%`;
  }
}

function updateVolume(value) {
  const volume = Math.max(0, Math.min(1, Number(value)));
  audio.volume = volume;
  localStorage.setItem(STORAGE_VOLUME, String(volume));
  volumeText.textContent = `${Math.round(volume * 100)}%`;

  audio.muted = volume === 0;
  muteBtn.textContent = audio.muted ? "🔇" : "🔊";
}

function toggleMute() {
  audio.muted = !audio.muted;
  muteBtn.textContent = audio.muted ? "🔇" : "🔊";
  toast(audio.muted ? "Som desligado" : "Som ligado");
}

function clearHistory() {
  localStorage.removeItem(STORAGE_HISTORY);
  currentTrackKey = "";
  renderHistory();
  toast("Histórico limpo");
}

function clearTop() {
  localStorage.removeItem(STORAGE_TOP);
  renderTop();
  toast("Top 10 limpo");
}

async function copyStream() {
  try {
    await navigator.clipboard.writeText(config.stream);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = config.stream;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  toast("Stream copiado");
}

playBtn.addEventListener("click", toggleRadio);
identifyBtn.addEventListener("click", () => identifySong(true));
muteBtn.addEventListener("click", toggleMute);
reloadBtn.addEventListener("click", reloadStream);
volumeSlider.addEventListener("input", event => updateVolume(event.target.value));
clearHistoryBtn.addEventListener("click", clearHistory);
clearTopBtn.addEventListener("click", clearTop);
copyBtn.addEventListener("click", copyStream);

cover.addEventListener("error", () => {
  if (!cover.src.endsWith(config.defaultCover)) {
    cover.src = config.defaultCover;
  }
});

audio.addEventListener("playing", () => setPlayingUI(true));
audio.addEventListener("pause", () => setPlayingUI(false));
audio.addEventListener("waiting", () => {
  statusText.textContent = "A carregar stream...";
});
audio.addEventListener("stalled", () => {
  statusText.textContent = "A recuperar ligação...";
});
audio.addEventListener("canplay", () => {
  if (isPlaying) statusText.textContent = "Rádio ligada";
});
audio.addEventListener("error", () => {
  console.error("Erro no stream:", audio.error);
  setPlayingUI(false);
  stopAutoIdentify();
  toast("Erro no stream da rádio");
});

trackTitle.textContent = config.name;
trackArtist.textContent = "Stream ao vivo";
streamUrl.textContent = config.stream;

async function verifyBackend() {
  try {
    const response = await fetch(`/api/health?t=${Date.now()}`, {
      cache: "no-store",
      headers: { Accept: "application/json" }
    });

    if (!response.ok) {
      console.warn("O endpoint /api/health respondeu com:", response.status);
      return;
    }

    const data = await response.json();
    console.info("Indie88 backend:", data);

    if (data.build && config.build && data.build !== config.build) {
      identifyStatus.textContent = "Versões diferentes";
      toast(`A página usa ${config.build}, mas a API usa ${data.build}`);
    }
  } catch (error) {
    console.warn("Não foi possível verificar o backend:", error);
  }
}

verifyBackend();

loadVolume();
renderHistory();
renderTop();
setPlayingUI(false);
