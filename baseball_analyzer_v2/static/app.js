import { loadGamesApi, loadContextApi } from "./api.js";
import { initMatchupTools } from "./matchup-tools.js";
import { gameFollowKind, initLiveBoard } from "./live-board.js";
import { decodeReportBackup, encodeReportBackup, mergeReport, newestReports, normalizeReport, reportIdentity } from "./report-notebook.js";

const byId = (id) => document.getElementById(id);
const form = byId("search-form");
const queryInput = byId("player-query");
const suggestions = byId("player-suggestions");
const pitcherQueryInput = byId("pitcher-query");
const pitcherSuggestions = byId("pitcher-suggestions");
const analyzeButton = byId("analyze-button");
const formMessage = byId("form-message");
const notice = byId("notice");
const dashboard = byId("dashboard");
const emptyState = byId("empty-state");
const savedReports = byId("saved-reports");
const reportGameFilter = byId("reports-game-filter");
const reportBackupInput = byId("reports-backup-input");
const exportReportsButton = byId("export-saved-reports");
const clearReportsButton = byId("clear-saved-reports");
const installButton = byId("install-app");
const installDialog = byId("install-help");
const installInstructions = byId("install-instructions");
const validationMetrics = byId("validation-metrics");
const validationStatus = byId("validation-status");
const validationButton = byId("run-validation");
const gameDateInput = byId("game-date");
const gameList = byId("game-list");
const scheduleStatus = byId("schedule-status");
const gameContextPanel = byId("game-context-panel");
const pitcherField = document.querySelector(".pitcher-field");
const savedReportsKey = "diamond-iq:saved-reports:v1";
const legacyMigrationKey = "diamond-iq:indexeddb-migrated:v1";
const localDatabaseName = "diamond-iq-local";
const localDatabaseVersion = 1;

let selectedPlayer = null;
let selectedPitcher = null;
let selectedGame = null;
let selectedGameContext = null;
let selectedTeamSide = "away";
let selectedGameForAnalysis = null;
let selectedPlayerSide = null;
let scheduleGames = [];
let searchTimer = null;
let searchController = null;
let pitcherSearchTimer = null;
let pitcherSearchController = null;
let latestAnalysis = null;
let activeSuggestionIndex = -1;
let activePitcherSuggestionIndex = -1;
let deferredInstallPrompt = null;
let analysisController = null;
let validationController = null;
let localDatabasePromise = null;
let localDataReady = Promise.resolve();
let savedReportsCache = [];
let favoritePlayers = [];
let scheduleController = null;
let contextController = null;

const percent = (value, digits) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return (Number(value) * 100).toFixed(digits === undefined ? 1 : digits) + "%";
};

const number = (value, digits) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits === undefined ? 1 : digits);
};

const setText = (id, value) => {
  const node = byId(id);
  if (node) node.textContent = value === null || value === undefined ? "—" : String(value);
};

function message(text, kind) {
  notice.textContent = text;
  notice.className = "notice" + (kind ? " " + kind : "");
  notice.hidden = !text;
}

function updateSearchMessage(text) {
  formMessage.textContent = text || "";
}

function clearSuggestions() {
  suggestions.replaceChildren();
  suggestions.hidden = true;
  activeSuggestionIndex = -1;
  queryInput.setAttribute("aria-expanded", "false");
  queryInput.removeAttribute("aria-activedescendant");
}

function choosePlayer(player, options = {}) {
  cancelPendingReportWork();
  selectedPlayer = player;
  selectedGameForAnalysis = options.fromGame && selectedGame &&
    isPregameGame(selectedGame) ? selectedGame.id : null;
  selectedPlayerSide = selectedGameForAnalysis ? options.side || selectedTeamSide : null;
  form.classList.toggle("game-mode", Boolean(selectedGameForAnalysis));
  if (pitcherField) pitcherField.hidden = Boolean(selectedGameForAnalysis);
  if (selectedGameForAnalysis) {
    selectedPitcher = null;
    pitcherQueryInput.value = "";
    clearPitcherSuggestions();
  }
  queryInput.value = player.name;
  analyzeButton.disabled = false;
  updateAnalyzeLabel();
  clearSuggestions();
  if (selectedGameForAnalysis) {
    const side = options.side === "home" ? "local" : "visitante";
    const selectedTeam = selectedGameContext && selectedGameContext.teams &&
      selectedGameContext.teams[options.side || selectedTeamSide];
    const lineupStatus = selectedTeam && selectedTeam.lineup_status;
    updateSearchMessage(
      (lineupStatus === "confirmed" ? "Alineación publicada por MLB" : "Selección manual de plantilla") +
        " · " + side + " · pitcher probable desde el juego",
    );
  } else {
    updateSearchMessage(player.years ? "Carrera MLB: " + player.years : "Jugador seleccionado");
  }
  queryInput.setAttribute("aria-expanded", "false");
}

function showSuggestions(players) {
  suggestions.replaceChildren();
  activeSuggestionIndex = -1;
  if (!players.length) {
    const empty = document.createElement("small");
    empty.textContent = "No encontramos coincidencias. Prueba con nombre y apellido.";
    suggestions.appendChild(empty);
    suggestions.hidden = false;
    queryInput.setAttribute("aria-expanded", "true");
    return;
  }

  players.forEach((player) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "suggestion-option";
    option.setAttribute("role", "option");
    option.id = "player-option-" + player.id;
    option.setAttribute("aria-selected", "false");

    const name = document.createElement("span");
    name.className = "suggestion-name";
    name.textContent = player.name;
    option.appendChild(name);

    const meta = document.createElement("small");
    meta.textContent = player.years || "MLB";
    option.appendChild(meta);
    option.addEventListener("click", () => choosePlayer(player));
    suggestions.appendChild(option);
  });
  suggestions.hidden = false;
  queryInput.setAttribute("aria-expanded", "true");
}

function clearPitcherSuggestions() {
  pitcherSuggestions.replaceChildren();
  pitcherSuggestions.hidden = true;
  activePitcherSuggestionIndex = -1;
  pitcherQueryInput.setAttribute("aria-expanded", "false");
  pitcherQueryInput.removeAttribute("aria-activedescendant");
}

function choosePitcher(pitcher) {
  cancelPendingReportWork();
  selectedGameForAnalysis = null;
  selectedPlayerSide = null;
  form.classList.remove("game-mode");
  if (pitcherField) pitcherField.hidden = false;
  selectedPitcher = pitcher;
  pitcherQueryInput.value = pitcher.name;
  clearPitcherSuggestions();
  updateAnalyzeLabel();
  pitcherQueryInput.setAttribute("aria-expanded", "false");
  updateSearchMessage(
    pitcher.years
      ? "Pitcher seleccionado · carrera MLB " + pitcher.years
      : "Pitcher seleccionado para el matchup",
  );
}

function showPitcherSuggestions(players) {
  pitcherSuggestions.replaceChildren();
  activePitcherSuggestionIndex = -1;
  if (!players.length) {
    const empty = document.createElement("small");
    empty.textContent = "No encontramos coincidencias. Prueba con nombre y apellido.";
    pitcherSuggestions.appendChild(empty);
    pitcherSuggestions.hidden = false;
    pitcherQueryInput.setAttribute("aria-expanded", "true");
    return;
  }
  players.forEach((player) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "suggestion-option";
    option.setAttribute("role", "option");
    option.id = "pitcher-option-" + player.id;
    option.setAttribute("aria-selected", "false");
    const name = document.createElement("span");
    name.className = "suggestion-name";
    name.textContent = player.name;
    const meta = document.createElement("small");
    meta.textContent = player.years || "MLB";
    option.append(name, meta);
    option.addEventListener("click", () => choosePitcher(player));
    pitcherSuggestions.appendChild(option);
  });
  pitcherSuggestions.hidden = false;
  pitcherQueryInput.setAttribute("aria-expanded", "true");
}

function updateAnalyzeLabel() {
  const label = analyzeButton.querySelector("span");
  if (!label || analyzeButton.classList.contains("is-loading")) return;
  label.textContent = selectedGameForAnalysis
    ? "Proyectar este juego"
    : selectedPitcher
      ? "Proyectar matchup"
      : "Analizar bateador";
}

function resetAnalyzeButton() {
  analyzeButton.disabled = !selectedPlayer;
  analyzeButton.classList.remove("is-loading");
  updateAnalyzeLabel();
}

function resetValidationButton() {
  validationButton.disabled = false;
  validationButton.textContent = latestAnalysis?.validation ? "Volver a medir" : "Medir precisión histórica";
}

function cancelHistoricalValidation() {
  if (!validationController) return;
  validationController.abort();
  validationController = null;
  resetValidationButton();
  renderValidation(latestAnalysis?.validation || null);
}

function cancelPendingReportWork() {
  if (analysisController) {
    analysisController.abort();
    analysisController = null;
    resetAnalyzeButton();
    if (notice.classList.contains("loading")) message("", "");
  }
  cancelHistoricalValidation();
}

function legacyReportsList() {
  try {
    const reports = JSON.parse(localStorage.getItem(savedReportsKey) || "[]");
    return Array.isArray(reports)
      ? reports.filter((item) => item && item.analysis && item.analysis.player &&
        item.analysis.player.id && item.analysis.window)
      : [];
  } catch (error) {
    return [];
  }
}

function savedReportsList() {
  return savedReportsCache;
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("No se pudo leer la base local."));
  });
}

function transactionResult(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("No se pudo guardar localmente."));
    transaction.onabort = () => reject(transaction.error || new Error("El guardado local se canceló."));
  });
}

function openLocalDatabase() {
  if (localDatabasePromise) return localDatabasePromise;
  if (!("indexedDB" in window)) return Promise.resolve(null);
  localDatabasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(localDatabaseName, localDatabaseVersion);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("reports")) {
        database.createObjectStore("reports", { keyPath: "id" });
      }
      if (!database.objectStoreNames.contains("favorites")) {
        database.createObjectStore("favorites", { keyPath: "player_id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB no está disponible."));
    request.onblocked = () => reject(new Error("Cierra otra pestaña de Diamond IQ para migrar el historial."));
  }).catch((error) => {
    localDatabasePromise = null;
    throw error;
  });
  return localDatabasePromise;
}

async function readStore(database, storeName) {
  const transaction = database.transaction(storeName, "readonly");
  const complete = transactionResult(transaction);
  const result = await requestResult(transaction.objectStore(storeName).getAll());
  await complete;
  return result;
}

async function commitReports(database, update) {
  if (!database) {
    const reports = new Map(legacyReportsList().map(normalizeReport).filter(Boolean).map((record) => [record.id, record]));
    update(reports);
    const next = newestReports(reports.values());
    localStorage.setItem(savedReportsKey, JSON.stringify(next));
    return next;
  }
  const transaction = database.transaction("reports", "readwrite");
  const complete = transactionResult(transaction);
  const store = transaction.objectStore("reports");
  const request = store.getAll();
  let next;
  request.onsuccess = () => {
    // Read and change the current snapshot together, including changes from other tabs.
    const existing = new Map(request.result.map((record) => [record.id, record]));
    const updated = new Map(existing);
    try {
      update(updated);
      for (const id of existing.keys()) {
        if (!updated.has(id)) store.delete(id);
      }
      for (const [id, record] of updated) {
        if (record !== existing.get(id)) store.put(record);
      }
      next = newestReports(updated.values());
    } catch (error) {
      transaction.abort();
    }
  };
  await complete;
  return next;
}

async function updateSavedReports(update) {
  const database = await openLocalDatabase();
  savedReportsCache = await commitReports(database, update);
  renderSavedReports();
}

async function writeFavorites() {
  const database = await openLocalDatabase();
  if (!database) return;
  const transaction = database.transaction("favorites", "readwrite");
  const complete = transactionResult(transaction);
  const store = transaction.objectStore("favorites");
  store.clear();
  favoritePlayers.forEach((favorite) => store.put(favorite));
  await complete;
}

async function migrateLegacyReports(database) {
  let migrated = false;
  try {
    migrated = localStorage.getItem(legacyMigrationKey) === "complete";
  } catch (error) {
    // Storage access may be denied; repeat the idempotent migration on the next visit.
  }
  if (migrated) return;

  const legacy = legacyReportsList().map(normalizeReport).filter(Boolean);
  if (legacy.length) {
    await commitReports(database, (reports) => {
      legacy.forEach((record) => reports.set(record.id, mergeReport(reports.get(record.id), record)));
    });
  }

  try {
    localStorage.setItem(legacyMigrationKey, "complete");
    localStorage.removeItem(savedReportsKey);
  } catch (error) {
    // The report copy is already committed; leaving the legacy copy is harmless.
  }
}

async function initializeLocalData() {
  try {
    const database = await openLocalDatabase();
    if (database) {
      await migrateLegacyReports(database);
      savedReportsCache = newestReports(await readStore(database, "reports"));
      favoritePlayers = (await readStore(database, "favorites"))
        .sort((a, b) => String(a.name).localeCompare(String(b.name), "es"));
    } else {
      savedReportsCache = legacyReportsList().map(normalizeReport).filter(Boolean);
    }
  } catch (error) {
    savedReportsCache = legacyReportsList().map(normalizeReport).filter(Boolean);
    message("No se pudo abrir la base local; los reportes se conservarán durante esta sesión.", "error");
  }
  renderSavedReports();
  renderFavoritePlayers();
  const latest = savedReportsCache[0];
  if (latest && !latestAnalysis) showSavedReport(latest.analysis, false);
}

function updateFavoriteButton(player) {
  const button = byId("favorite-player");
  if (!button || !player) return;
  const isFavorite = favoritePlayers.some(
    (favorite) => Number(favorite.player_id) === Number(player.id),
  );
  button.setAttribute("aria-pressed", String(isFavorite));
  button.setAttribute(
    "aria-label",
    isFavorite ? "Quitar jugador de favoritos" : "Guardar jugador en favoritos",
  );
  const star = button.querySelector(".favorite-star");
  const label = button.querySelector(".favorite-label");
  if (star) star.textContent = isFavorite ? "★" : "☆";
  if (label) label.textContent = isFavorite ? "Guardado" : "Guardar";
}

function renderFavoritePlayers() {
  const panel = byId("favorites-panel");
  const list = byId("favorite-list");
  if (!panel || !list) return;
  list.replaceChildren();
  panel.hidden = favoritePlayers.length === 0;
  favoritePlayers.forEach((favorite) => {
    const row = document.createElement("div");
    row.className = "favorite-row";
    const choose = document.createElement("button");
    choose.type = "button";
    choose.className = "favorite-player-button";
    choose.textContent = favorite.name;
    choose.addEventListener("click", () => {
      choosePlayer({ id: favorite.player_id, name: favorite.name, years: null });
      byId("search-form").scrollIntoView({ behavior: "smooth", block: "center" });
      queryInput.focus();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "favorite-remove-button";
    remove.textContent = "Quitar";
    remove.setAttribute("aria-label", "Quitar " + favorite.name + " de favoritos");
    remove.addEventListener("click", () => void removeFavorite(favorite.player_id));
    row.append(choose, remove);
    list.appendChild(row);
  });
  if (latestAnalysis && latestAnalysis.player) updateFavoriteButton(latestAnalysis.player);
}

async function toggleFavorite(player) {
  await localDataReady;
  const playerId = Number(player && player.id);
  if (!playerId || !player.name) return;
  const current = favoritePlayers.find((favorite) => Number(favorite.player_id) === playerId);
  if (current) {
    favoritePlayers = favoritePlayers.filter(
      (favorite) => Number(favorite.player_id) !== playerId,
    );
  } else {
    favoritePlayers = favoritePlayers.concat({
      player_id: playerId,
      name: player.name,
      added_at: new Date().toISOString(),
    });
  }
  renderFavoritePlayers();
  updateFavoriteButton(player);
  try {
    await writeFavorites();
    message(
      current ? "Jugador quitado de tus favoritos." : "Jugador guardado en este dispositivo.",
      "success",
    );
  } catch (error) {
    message("No se pudo guardar el favorito localmente.", "error");
  }
}

async function removeFavorite(playerId) {
  favoritePlayers = favoritePlayers.filter(
    (favorite) => Number(favorite.player_id) !== Number(playerId),
  );
  renderFavoritePlayers();
  if (latestAnalysis && Number(latestAnalysis.player.id) === Number(playerId)) {
    updateFavoriteButton(latestAnalysis.player);
  }
  try {
    await writeFavorites();
  } catch (error) {
    message("No se pudo actualizar la libreta local.", "error");
  }
}

function localDateString(day) {
  const year = day.getFullYear();
  const month = String(day.getMonth() + 1).padStart(2, "0");
  const date = String(day.getDate()).padStart(2, "0");
  return year + "-" + month + "-" + date;
}

function moveScheduleDate(days) {
  const current = new Date((gameDateInput.value || localDateString(new Date())) + "T12:00:00");
  current.setDate(current.getDate() + days);
  gameDateInput.value = localDateString(current);
  clearGameContext();
  void loadSchedule();
}

function gameTimeLabel(value) {
  if (!value) return "Hora por confirmar";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : parsed.toLocaleTimeString("es-MX", { hour: "numeric", minute: "2-digit" });
}

function displayGameStatus(game) {
  const status = String(game.status || "").toLowerCase();
  if (status.includes("final") || status.includes("completed") || status.includes("game over")) {
    return "Final";
  }
  if (status.includes("progress") || status.includes("live") || status.includes("in game")) {
    return "En vivo";
  }
  if (status.includes("postpon")) return "Pospuesto";
  if (status.includes("suspend")) return "Suspendido";
  if (status.includes("cancel")) return "Cancelado";
  if (status.includes("delay")) return "Demorado";
  if (status.includes("warm")) return "Calentamiento";
  if (status.includes("preview")) return "Previa";
  if (status.includes("schedule") || status.includes("pre-game")) return "Programado";
  return game.status || "Estado no disponible";
}

function isPregameGame(game) {
  if (!game || !game.start_time) return false;
  const status = String(game.status || "").toLowerCase();
  const code = String(game.status_code || "").toLowerCase();
  if (/final|complete|game over|in progress|live|postpon|suspend|cancel/.test(status)) {
    return false;
  }
  if (["f", "final", "live", "in_progress", "completed", "postponed", "suspended", "cancelled"].includes(code)) {
    return false;
  }
  return /scheduled|pre-game|preview|warm|pregame/.test(status) ||
    ["s", "scheduled", "p", "preview", "w", "warmup"].includes(code);
}

function appendText(parent, tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text === null || text === undefined ? "" : String(text);
  parent.appendChild(node);
  return node;
}

function teamSideNode(team, side, game) {
  const node = document.createElement("span");
  node.className = "game-team game-team-" + side;
  const main = document.createElement("span");
  main.className = "game-team-main";
  appendText(main, "span", "team-abbreviation", team.abbreviation || (side === "away" ? "VIS" : "LOC"));
  appendText(main, "strong", "team-name", team.name || "Equipo por confirmar");
  node.appendChild(main);
  const score = team.score;
  if (score !== null && score !== undefined) {
    appendText(node, "span", "team-score", score);
  } else {
    const probable = team.probable_pitcher && team.probable_pitcher.name;
    const pitcher = appendText(
      node,
      "span",
      "probable-pitcher",
      probable ? "Probable · " + probable : "Probable por confirmar",
    );
    pitcher.title = "Pitcher probable, no alineación confirmada";
  }
  return node;
}

function renderGames(payload) {
  if (Array.isArray(payload.games)) scheduleGames = payload.games;
  const games = scheduleGames;
  gameList.replaceChildren();
  if (!games.length) {
    const empty = document.createElement("div");
    empty.className = "slate-empty";
    appendText(empty, "strong", "", "No hay juegos publicados para esta fecha.");
    appendText(empty, "span", "", "Puedes buscar un bateador abajo para hacer un análisis sin juego.");
    gameList.appendChild(empty);
  }

  games.forEach((game) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "game-card";
    card.dataset.gameId = String(game.id);
    card.setAttribute("aria-pressed", String(
      selectedGame && String(selectedGame.id) === String(game.id),
    ));
    card.setAttribute(
      "aria-label",
      (game.away && game.away.name || "Visitante") + " contra " +
        (game.home && game.home.name || "Local") + " · " +
        displayGameStatus(game) + " · " + gameTimeLabel(game.start_time),
    );
    card.addEventListener("click", () => void selectGame(game));

    const top = document.createElement("span");
    top.className = "game-card-top";
    appendText(top, "span", "game-card-time", gameTimeLabel(game.start_time));
    appendText(
      top,
      "span",
      "game-status game-status-" + (gameFollowKind(game) === "live" ? "live" : isPregameGame(game) ? "pregame" : "closed"),
      displayGameStatus(game),
    );
    card.appendChild(top);

    const matchup = document.createElement("span");
    matchup.className = "game-matchup";
    matchup.append(
      teamSideNode(game.away || {}, "away", game),
      appendText(document.createElement("span"), "span", "at-mark", "@"),
      teamSideNode(game.home || {}, "home", game),
    );
    card.appendChild(matchup);

    const venueName = game.venue && game.venue.name || "Sede por confirmar";
    appendText(card, "span", "game-card-venue", venueName);
    gameList.appendChild(card);
  });

  const source = payload.source || {};
  const sourceText = source.name ? " · " + source.name : "";
  const staleText = source.stale ? " · datos guardados en caché" : "";
  const warningText = Array.isArray(payload.warnings) && payload.warnings.length
    ? " · " + payload.warnings.length + " aviso(s)"
    : "";
  scheduleStatus.textContent = games.length
    ? games.length + " juegos · " + payload.date + sourceText + staleText + warningText
    : "Sin juegos publicados · " + payload.date + sourceText;
  scheduleStatus.classList.toggle("is-stale", Boolean(source.stale));
}

async function loadSchedule() {
  if (!gameDateInput.value) gameDateInput.value = localDateString(new Date());
  if (scheduleController) scheduleController.abort();
  scheduleController = new AbortController();
  const requestedDate = gameDateInput.value;
  const controller = scheduleController;
  scheduleStatus.textContent = "Cargando juegos de " + requestedDate + "…";
  scheduleStatus.classList.add("is-loading");
  try {
    const payload = await loadGamesApi(requestedDate, controller.signal);
    if (controller !== scheduleController) return;
    renderGames(payload);
  } catch (error) {
    if (error.name === "AbortError") return;
    gameList.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "slate-empty slate-error";
    appendText(empty, "strong", "", "No se pudo cargar la jornada.");
    appendText(
      empty,
      "span",
      "",
      navigator.onLine === false
        ? "Conéctate a Internet y vuelve a intentar."
        : error.message || "La fuente de calendario no respondió.",
    );
    gameList.appendChild(empty);
    scheduleStatus.textContent = "Calendario no disponible · " + requestedDate;
  } finally {
    if (controller === scheduleController) scheduleStatus.classList.remove("is-loading");
  }
}

function gameContextWarnings(warnings) {
  const list = byId("context-warnings");
  list.replaceChildren();
  const items = Array.isArray(warnings) ? warnings : [];
  list.hidden = items.length === 0;
  items.forEach((warning) => appendText(list, "li", "", warning));
}

function renderProbablePitchers(game) {
  const container = byId("context-probables");
  container.replaceChildren();
  [
    ["away", game.away || {}],
    ["home", game.home || {}],
  ].forEach(([side, team]) => {
    const probable = team.probable_pitcher;
    const card = document.createElement("div");
    card.className = "probable-card";
    appendText(card, "span", "probable-side", side === "away" ? "ABRIDOR VISITANTE" : "ABRIDOR LOCAL");
    appendText(
      card,
      "strong",
      "",
      probable && probable.name ? probable.name : "Por confirmar",
    );
    appendText(card, "small", "", probable ? "Pitcher probable" : "Sin probable publicado");
    container.appendChild(card);
  });
}

function selectTeamSide(side) {
  if (!selectedGameContext || !selectedGameContext.teams[side]) return;
  selectedTeamSide = side;
  renderTeamTabs();
  renderTeamRoster();
  window.dispatchEvent(new CustomEvent("diamondiq:team-side", { detail: { side } }));
}

function renderTeamTabs() {
  const tabs = byId("team-tabs");
  tabs.replaceChildren();
  const teams = selectedGameContext && selectedGameContext.teams;
  if (!teams) return;
  ["away", "home"].forEach((side) => {
    const team = teams[side];
    if (!team) return;
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "team-tab" + (side === selectedTeamSide ? " is-active" : "");
    tab.id = "team-tab-" + side;
    tab.setAttribute("aria-pressed", String(side === selectedTeamSide));
    tab.textContent = (side === "away" ? "VISITANTE · " : "LOCAL · ") + team.name;
    tab.addEventListener("click", () => selectTeamSide(side));
    tabs.appendChild(tab);
  });
}

function renderTeamRoster() {
  const team = selectedGameContext && selectedGameContext.teams &&
    selectedGameContext.teams[selectedTeamSide];
  const roster = byId("team-roster");
  const status = byId("team-lineup-status");
  const guidance = byId("lineup-guidance");
  roster.replaceChildren();
  if (!team) {
    status.textContent = "Alineación no disponible";
    guidance.textContent = "No hay información de equipo para este juego.";
    return;
  }

  const lineup = Array.isArray(team.lineup) ? team.lineup : [];
  const published = team.lineup_status === "confirmed";
  const selection = lineup.length ? lineup : (Array.isArray(team.roster) ? team.roster : []);
  const inGame = gameFollowKind(selectedGame) === "live";
  status.textContent = published ? "PUBLICADA POR MLB" : "NO PUBLICADA";
  status.className = "lineup-status " + (published ? "lineup-published" : "lineup-pending");
  guidance.textContent = inGame
    ? "Consulta el tablero en vivo para elegir un bateador de la alineación actual."
    : published
    ? "Alineación publicada por MLB; puede cambiar antes del primer lanzamiento. Elige un bateador para proyectarlo."
    : lineup.length
      ? "MLB todavía no marca esta alineación como publicada. Los nombres visibles son preliminares, no titulares confirmados."
      : "La alineación no está publicada. Elige manualmente desde la plantilla oficial; no asumimos quién será titular.";

  if (!selection.length) {
    const empty = document.createElement("p");
    empty.className = "roster-empty";
    empty.textContent = "MLB aún no publica la alineación ni una plantilla para este equipo.";
    roster.appendChild(empty);
    return;
  }

  const canProject = selectedGame && isPregameGame(selectedGame);
  selection.forEach((player) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "roster-player" + (
      published && lineup.length ? " is-lineup" : " is-roster-choice"
    );
    button.dataset.playerId = String(player.id);
    button.disabled = !canProject;
    const isSelected = selectedPlayer &&
      Number(selectedPlayer.id) === Number(player.id) &&
      Number(selectedGameForAnalysis) === Number(selectedGame && selectedGame.id);
    button.setAttribute("aria-pressed", String(Boolean(isSelected)));
    button.addEventListener("click", () => choosePlayer(
      { id: player.id, name: player.name, years: null },
      { fromGame: true, side: selectedTeamSide },
    ));

    const order = player.batting_order;
    appendText(
      button,
      "span",
      "lineup-order",
      published && order ? String(order) : player.position || "•",
    );
    const details = document.createElement("span");
    details.className = "lineup-player-details";
    appendText(details, "strong", "", player.name || "Jugador sin nombre");
    appendText(
      details,
      "small",
      "",
      player.position || (published ? "Alineación publicada" : "Plantilla MLB"),
    );
    button.appendChild(details);
    appendText(button, "span", "lineup-select-mark", "↗");
    roster.appendChild(button);
  });

  if (!canProject) {
    const blocked = document.createElement("p");
    blocked.className = "roster-blocked";
    blocked.textContent = "Este juego ya comenzó, terminó o no tiene hora válida; no se genera una proyección previa.";
    roster.appendChild(blocked);
  }
}

function weatherDescription(weather) {
  if (!weather || weather.status === "unavailable") {
    return weather && weather.message || "Clima no disponible para este juego.";
  }
  if (weather.status === "not_applicable") {
    return weather.message || "El techo o la sede hacen que el clima exterior no aplique.";
  }
  const items = [];
  if (weather.temperature_c !== null && weather.temperature_c !== undefined) {
    items.push(number(weather.temperature_c, 0) + " °C");
  }
  if (weather.wind_kmh !== null && weather.wind_kmh !== undefined) {
    items.push("viento " + number(weather.wind_kmh, 0) + " km/h");
  }
  if (weather.wind_direction) items.push(String(weather.wind_direction));
  if (weather.roof) {
    const roofLabels = {
      Open: "techo abierto",
      Dome: "domo",
      Retractable: "techo retráctil · apertura actual desconocida",
      desconocido: "techo desconocido",
    };
    items.push(roofLabels[weather.roof] || "techo " + weather.roof);
  }
  return items.length ? items.join(" · ") : weather.message || "Condiciones sin dato público.";
}

function renderGameContext(context) {
  selectedGameContext = context;
  const game = context.game || selectedGame;
  selectedGame = game;
  const away = game.away && game.away.name || "Visitante";
  const home = game.home && game.home.name || "Local";
  setText("game-context-title", away + " @ " + home);
  setText(
    "game-context-subtitle",
    gameTimeLabel(game.start_time) + " · " + displayGameStatus(game) + " · " +
      (game.venue && game.venue.name || "Sede por confirmar"),
  );
  renderProbablePitchers(game);
  const teams = context.teams || {};
  if (!teams[selectedTeamSide]) {
    selectedTeamSide = teams.away ? "away" : "home";
  }
  renderTeamTabs();
  renderTeamRoster();

  const weather = context.weather || {};
  const weatherText = weatherDescription(weather);
  const weatherNote = weather.applied_to_projection === false
    ? " · contexto descriptivo, no cambia la proyección"
    : "";
  setText("context-weather", "CLIMA · " + weatherText + weatherNote);
  const source = context.source || {};
  setText(
    "context-source",
    (source.name || "Fuente de MLB") +
      (source.fetched_at ? " · datos de fuente al " + new Date(source.fetched_at).toLocaleString("es-MX") : "") +
      (source.stale ? " · caché anterior" : ""),
  );
  gameContextWarnings(context.warnings);
  window.dispatchEvent(new CustomEvent("diamondiq:game-context", {
    detail: { context, side: selectedTeamSide },
  }));
}

async function selectGame(game) {
  cancelPendingReportWork();
  selectedGame = game;
  selectedGameContext = null;
  window.dispatchEvent(new CustomEvent("diamondiq:game-cleared"));
  selectedTeamSide = "away";
  selectedPlayerSide = null;
  selectedGameForAnalysis = isPregameGame(game) ? game.id : null;
  selectedPlayer = null;
  selectedPitcher = null;
  queryInput.value = "";
  pitcherQueryInput.value = "";
  analyzeButton.disabled = true;
  form.classList.toggle("game-mode", Boolean(selectedGameForAnalysis));
  if (pitcherField) pitcherField.hidden = Boolean(selectedGameForAnalysis);
  updateAnalyzeLabel();
  updateSearchMessage(
    selectedGameForAnalysis
      ? "Elige un bateador de la alineación o plantilla de este juego."
      : gameFollowKind(game) === "live"
        ? "Para estimar hits de este partido, elige un bateador en el tablero en vivo."
      : "Este juego no admite una proyección previa; puedes usar la búsqueda manual sin adjuntarlo.",
  );
  [...gameList.querySelectorAll(".game-card")].forEach((card) => {
    card.setAttribute("aria-pressed", String(card.dataset.gameId === String(game.id)));
  });

  gameContextPanel.hidden = false;
  window.dispatchEvent(new CustomEvent("diamondiq:game-selected", { detail: { game } }));
  setText("game-context-title", "Cargando contexto del juego…");
  setText("game-context-subtitle", "");
  byId("context-probables").replaceChildren();
  byId("team-tabs").replaceChildren();
  byId("team-roster").replaceChildren();
  byId("context-weather").textContent = "Clima · consultando fuente disponible…";
  byId("context-source").textContent = "";
  gameContextWarnings([]);
  if (contextController) contextController.abort();
  contextController = new AbortController();
  const controller = contextController;
  try {
    const payload = await loadContextApi(game.id, controller.signal);
    if (controller !== contextController) return;
    renderGameContext(payload);
  } catch (error) {
    if (controller !== contextController || error.name === "AbortError") return;
    setText("game-context-title", (game.away && game.away.name || "Visitante") + " @ " +
      (game.home && game.home.name || "Local"));
    setText(
      "game-context-subtitle",
      "No pudimos cargar la alineación. No seleccionamos titulares por nuestra cuenta.",
    );
    setText("context-weather", error.message || "Contexto no disponible.");
  }
}

function clearGameContext() {
  cancelPendingReportWork();
  if (contextController) contextController.abort();
  contextController = null;
  selectedGame = null;
  selectedGameContext = null;
  selectedGameForAnalysis = null;
  selectedTeamSide = "away";
  window.dispatchEvent(new CustomEvent("diamondiq:game-cleared"));
  selectedPlayerSide = null;
  gameContextPanel.hidden = true;
  form.classList.remove("game-mode");
  if (pitcherField) pitcherField.hidden = false;
  [...gameList.querySelectorAll(".game-card")].forEach((card) => {
    card.setAttribute("aria-pressed", "false");
  });
  updateAnalyzeLabel();
  if (selectedPlayer) {
    analyzeButton.disabled = false;
    updateSearchMessage("Juego quitado; el análisis se hará sin ese contexto.");
  } else {
    analyzeButton.disabled = true;
  }
}

function savedDateLabel(value) {
  if (!value) return "guardado";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "guardado"
    : parsed.toLocaleDateString("es-MX", { month: "short", day: "numeric" });
}

function reportGame(record) {
  return record.analysis.game_context?.game;
}

function reportGameLabel(game) {
  if (!game) return "Sin partido";
  const away = game.away?.abbreviation || game.away?.name || "Visitante";
  const home = game.home?.abbreviation || game.home?.name || "Local";
  const date = game.official_date || String(game.start_time || "").slice(0, 10);
  const time = game.start_time ? gameTimeLabel(game.start_time) : "Juego " + game.id;
  return [away + " @ " + home, date, time].filter(Boolean).join(" · ");
}

function renderSavedReports() {
  const reports = savedReportsList();
  const focusId = document.activeElement?.dataset.reportPin;
  const selectedGame = reportGameFilter.value;
  const games = new Map();
  reports.forEach((record) => {
    const game = reportGame(record);
    games.set(String(game?.id || "no-game"), reportGameLabel(game));
  });
  reportGameFilter.replaceChildren(new Option("Todos los reportes", ""));
  games.forEach((label, id) => reportGameFilter.add(new Option(label, id)));
  reportGameFilter.value = games.has(selectedGame) ? selectedGame : "";
  reportGameFilter.disabled = reports.length === 0;
  exportReportsButton.disabled = reports.length === 0;
  clearReportsButton.disabled = reports.length === 0;

  const filtered = reports.filter((record) => !reportGameFilter.value ||
    String(reportGame(record)?.id || "no-game") === reportGameFilter.value);
  const sorted = filtered.filter((record) => record.pinned)
    .concat(filtered.filter((record) => !record.pinned));
  setText("saved-reports-count", reports.length === 0
    ? "El primer análisis se guarda aquí. También puedes importar un respaldo."
    : sorted.length + " de " + reports.length + " reportes");
  savedReports.replaceChildren();
  sorted.forEach((record) => {
    const row = document.createElement("div");
    row.className = "saved-report-row";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "saved-report-button";
    const opponent = record.analysis.opponent_pitcher
      ? " vs " + record.analysis.opponent_pitcher.name
      : "";
    const title = record.analysis.player.name + opponent;
    appendText(button, "strong", "saved-report-title", title);
    appendText(button, "small", "saved-report-meta", [
      reportGameLabel(reportGame(record)),
      (record.analysis.window.games_requested || "—") + " juegos de forma",
      "Guardado " + savedDateLabel(record.saved_at),
    ].join(" · "));
    button.addEventListener("click", () => showSavedReport(record.analysis));
    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "saved-report-pin";
    pin.dataset.reportPin = record.id;
    pin.textContent = record.pinned ? "Fijado" : "Fijar";
    pin.setAttribute("aria-label", "Fijar reporte de " + title);
    pin.setAttribute("aria-pressed", String(record.pinned === true));
    pin.addEventListener("click", () => void toggleReportPin(record.id));
    row.append(button, pin);
    savedReports.appendChild(row);
    if (focusId === record.id) pin.focus({ preventScroll: true });
  });
}

async function toggleReportPin(id) {
  await localDataReady;
  let pinned;
  try {
    await updateSavedReports((reports) => {
      const record = reports.get(id);
      if (!record) return;
      pinned = !record.pinned;
      reports.set(id, { ...record, pinned });
    });
    setText("notebook-status", pinned === undefined ? "El reporte ya no está en la libreta."
      : pinned ? "Reporte fijado." : "Reporte desfijado.");
  } catch (error) {
    setText("notebook-status", "No se pudo guardar el cambio en este navegador.");
  }
}

async function saveAnalysisLocally(analysis, shouldSave = () => true) {
  await localDataReady;
  if (!shouldSave()) return false;
  const identity = reportIdentity(analysis);
  let saved = false;
  const update = (reports) => {
    if (!shouldSave()) return;
    reports.set(identity, {
      id: identity,
      saved_at: new Date().toISOString(),
      pinned: reports.get(identity)?.pinned === true,
      analysis,
    });
    saved = true;
  };
  try {
    await updateSavedReports(update);
    return saved;
  } catch (error) {
    if (!shouldSave()) return false;
    const session = new Map(savedReportsCache.map((record) => [record.id, record]));
    update(session);
    savedReportsCache = newestReports(session.values());
    renderSavedReports();
    return false;
  }
}

function reportSourceDate(analysis) {
  const generatedAt = analysis.model && analysis.model.generated_at;
  return generatedAt ? new Date(generatedAt).toLocaleString("es-MX") : "fecha desconocida";
}

function showSavedReport(analysis, scroll = true) {
  // An explicit selection takes priority. Automatic restoration must wait for the active request.
  if (!scroll && analysisController) return;
  cancelPendingReportWork();
  latestAnalysis = analysis;
  renderAnalysis(analysis);
  message(
    "Reporte guardado localmente · generado " + reportSourceDate(analysis) + ". Puede estar desactualizado; vuelve a analizar para consultar datos recientes.",
    "success",
  );
  if (scroll) dashboard.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function searchPlayers(query) {
  if (searchController) searchController.abort();
  searchController = new AbortController();
  const requestedQuery = query;
  try {
    const response = await fetch("/api/players?query=" + encodeURIComponent(query), {
      signal: searchController.signal,
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "No se pudo buscar jugadores.");
    if (queryInput.value.trim() !== requestedQuery) return;
    showSuggestions(payload.players || []);
  } catch (error) {
    if (error.name === "AbortError") return;
    clearSuggestions();
    updateSearchMessage(
      navigator.onLine === false
        ? "Sin conexión con el servidor. Abre uno de tus reportes guardados o vuelve a conectarte."
        : error.message || "No se pudo buscar jugadores.",
    );
  }
}

async function searchPitchers(query) {
  if (pitcherSearchController) pitcherSearchController.abort();
  pitcherSearchController = new AbortController();
  const requestedQuery = query;
  try {
    const response = await fetch("/api/players?query=" + encodeURIComponent(query), {
      signal: pitcherSearchController.signal,
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "No se pudo buscar pitchers.");
    if (pitcherQueryInput.value.trim() !== requestedQuery) return;
    showPitcherSuggestions(payload.players || []);
  } catch (error) {
    if (error.name === "AbortError") return;
    clearPitcherSuggestions();
    updateSearchMessage(
      navigator.onLine === false
        ? "Sin conexión con el servidor. Puedes analizar al bateador sin elegir pitcher."
        : error.message || "No se pudo buscar el pitcher rival.",
    );
  }
}

queryInput.addEventListener("input", () => {
  cancelPendingReportWork();
  selectedPlayer = null;
  selectedGameForAnalysis = null;
  selectedPlayerSide = null;
  form.classList.remove("game-mode");
  if (pitcherField) pitcherField.hidden = false;
  analyzeButton.disabled = true;
  updateAnalyzeLabel();
  updateSearchMessage("");
  if (searchController) searchController.abort();
  clearSuggestions();
  const query = queryInput.value.trim();
  clearTimeout(searchTimer);
  if (query.length < 2) {
    clearSuggestions();
    return;
  }
  searchTimer = setTimeout(() => searchPlayers(query), 260);
});

queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape") clearSuggestions();
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !suggestions.hidden) {
    const options = [...suggestions.querySelectorAll(".suggestion-option")];
    if (options.length) {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      activeSuggestionIndex = activeSuggestionIndex === -1
        ? direction === 1 ? 0 : options.length - 1
        : (activeSuggestionIndex + direction + options.length) % options.length;
      options.forEach((option, index) => {
        option.setAttribute("aria-selected", String(index === activeSuggestionIndex));
        option.classList.toggle("is-active", index === activeSuggestionIndex);
      });
      queryInput.setAttribute("aria-activedescendant", options[activeSuggestionIndex].id);
    }
    return;
  }
  if (event.key === "Enter" && !selectedPlayer && !suggestions.hidden) {
    const options = suggestions.querySelectorAll(".suggestion-option");
    const option = options[activeSuggestionIndex] || options[0];
    if (option) {
      event.preventDefault();
      option.click();
    }
  }
});

pitcherQueryInput.addEventListener("input", () => {
  cancelPendingReportWork();
  selectedPitcher = null;
  updateAnalyzeLabel();
  if (pitcherSearchController) pitcherSearchController.abort();
  clearPitcherSuggestions();
  const query = pitcherQueryInput.value.trim();
  clearTimeout(pitcherSearchTimer);
  if (query.length < 2) return;
  pitcherSearchTimer = setTimeout(() => searchPitchers(query), 260);
});

pitcherQueryInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape") clearPitcherSuggestions();
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !pitcherSuggestions.hidden) {
    const options = [...pitcherSuggestions.querySelectorAll(".suggestion-option")];
    if (options.length) {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      activePitcherSuggestionIndex = activePitcherSuggestionIndex === -1
        ? direction === 1 ? 0 : options.length - 1
        : (activePitcherSuggestionIndex + direction + options.length) % options.length;
      options.forEach((option, index) => {
        option.setAttribute("aria-selected", String(index === activePitcherSuggestionIndex));
        option.classList.toggle("is-active", index === activePitcherSuggestionIndex);
      });
      pitcherQueryInput.setAttribute(
        "aria-activedescendant",
        options[activePitcherSuggestionIndex].id,
      );
    }
    return;
  }
  if (event.key === "Enter" && !selectedPitcher && !pitcherSuggestions.hidden) {
    const options = pitcherSuggestions.querySelectorAll(".suggestion-option");
    const option = options[activePitcherSuggestionIndex] || options[0];
    if (option) {
      event.preventDefault();
      option.click();
    }
  }
});

document.addEventListener("click", (event) => {
  if (!suggestions.contains(event.target) && event.target !== queryInput) {
    clearSuggestions();
  }
  if (!pitcherSuggestions.contains(event.target) && event.target !== pitcherQueryInput) {
    clearPitcherSuggestions();
  }
});

byId("games-select").addEventListener("change", cancelPendingReportWork);
byId("window-select").addEventListener("change", cancelPendingReportWork);
byId("backtest-max-games").addEventListener("change", cancelHistoricalValidation);

function requestedReportIdentity(request) {
  const opponentSide = selectedPlayerSide === "away" ? "home" : "away";
  // A game request leaves the pitcher to MLB. Match the probable rival shown at submission.
  const pitcherId = request.game_id
    ? selectedGameContext?.game?.[opponentSide]?.probable_pitcher?.id
    : request.opponent_pitcher_id;
  return reportIdentity({
    player: { id: request.player_id },
    opponent_pitcher: { id: pitcherId },
    game_context: { game: { id: request.game_id } },
    window: { games_requested: request.games, lookback_days: request.lookback_days },
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedPlayer) {
    updateSearchMessage("Selecciona un jugador de la lista para continuar.");
    queryInput.focus();
    return;
  }
  if (pitcherQueryInput.value.trim() && !selectedPitcher) {
    updateSearchMessage("Selecciona un pitcher rival de la lista o borra el campo para analizar sin matchup.");
    pitcherQueryInput.focus();
    return;
  }
  if (selectedPitcher && Number(selectedPitcher.id) === Number(selectedPlayer.id)) {
    updateSearchMessage("El pitcher rival debe ser distinto del bateador.");
    pitcherQueryInput.focus();
    return;
  }
  if (selectedGameForAnalysis && (!selectedGame || !isPregameGame(selectedGame))) {
    updateSearchMessage("Este juego ya comenzó o no admite una proyección previa.");
    return;
  }

  const request = {
    player_id: selectedPlayer.id,
    player_name: selectedPlayer.name,
    opponent_pitcher_id: selectedPitcher?.id || null,
    opponent_pitcher_name: selectedPitcher?.name || null,
    game_id: selectedGameForAnalysis || null,
    games: Number(byId("games-select").value),
    lookback_days: Number(byId("window-select").value),
  };
  const reportId = requestedReportIdentity(request);
  cancelPendingReportWork();
  const controller = new AbortController();
  analysisController = controller;
  const isCurrent = () => analysisController === controller;
  analyzeButton.disabled = true;
  analyzeButton.classList.add("is-loading");
  analyzeButton.querySelector("span").textContent = request.game_id
    ? "Proyectando juego"
    : "Analizando Statcast";
  updateSearchMessage("");
  message(
    request.game_id
      ? "Consultando alineación, pitcher probable e historial disponible antes del juego…"
      : "Descargando el historial Statcast y ajustando forma, contacto y matchup…",
    "loading",
  );

  let canUseSavedReport = true;
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(request),
    });
    canUseSavedReport = response.status >= 500;
    const payload = await response.json();
    if (!isCurrent()) return;
    if (!response.ok) throw new Error(payload.detail || "No se pudo completar el análisis.");
    const savedLocally = await saveAnalysisLocally(payload, isCurrent);
    if (!isCurrent()) return;
    cancelHistoricalValidation();
    latestAnalysis = payload;
    renderAnalysis(payload);
    message(
      savedLocally
        ? ""
        : "Proyección lista. El navegador no pudo guardarla de forma permanente; seguirá visible durante esta sesión.",
      savedLocally ? "" : "warning",
    );
    const possibleBacktestGames = Math.max(
      0,
      Number(payload.window.games_available || 0) - Number(payload.window.games_requested || 20),
    );
    if (possibleBacktestGames >= 8) {
      void runHistoricalValidation(payload);
    } else {
      renderValidation(
        null,
        possibleBacktestGames > 0
          ? "Hay " + possibleBacktestGames + " juegos posibles para validar; es una muestra corta. Pulsa el botón para medirla."
          : "No hay suficientes juegos previos para validar esta configuración. Prueba una ventana de datos más amplia o menos juegos de entrenamiento.",
      );
    }
    dashboard.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (!isCurrent() || error.name === "AbortError") return;
    if (canUseSavedReport) await localDataReady;
    if (!isCurrent()) return;
    const cached = canUseSavedReport && savedReportsList().find((record) => record.id === reportId);
    if (cached) {
      showSavedReport(cached.analysis);
    } else {
      message(
        navigator.onLine === false
          ? "Sin conexión. Conéctate a Internet para consultar Statcast."
          : error.message || "Ocurrió un error al consultar los datos.",
        "error",
      );
    }
  } finally {
    if (isCurrent()) {
      analysisController = null;
      resetAnalyzeButton();
    }
  }
});

function setTrack(id, value, max) {
  const track = byId(id);
  if (!track) return;
  const normalized = Math.max(0, Math.min(100, (Number(value || 0) / max) * 100));
  track.style.width = normalized + "%";
}

function renderHitDistribution(projection, explanation) {
  const panel = byId("hit-distribution");
  const valuesNode = byId("distribution-values");
  const outcomes = [
    ["probability_zero_hits", "probability-zero-hits", "probability-zero-bar"],
    ["probability_exactly_one_hit", "probability-one-hit", "probability-one-bar"],
    ["probability_two_or_more_hits", "probability-two-plus-hits", "probability-two-plus-bar"],
  ];
  const values = outcomes.map(([key]) => projection[key]);
  const valid = values.every((value) =>
    typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1,
  );
  const total = valid ? values.reduce((sum, value) => sum + value, 0) : 0;
  const coherent = valid && Math.abs(total - 1) <= 0.015;
  panel.hidden = false;
  valuesNode.hidden = !coherent;
  setText(
    "distribution-note",
    coherent ? "Probabilidades por juego · estimación" : "No disponible",
  );
  setText(
    "distribution-footnote",
    coherent
      ? "Los tres resultados suman 100%. Son probabilidades estimadas, no una garantía."
      : "La fuente no devolvió una distribución completa para este informe; no se deriva de hits esperados.",
  );
  if (!coherent) return;

  outcomes.forEach(([sourceKey, valueId, barId]) => {
    setText(valueId, percent(projection[sourceKey]));
    setTrack(barId, projection[sourceKey] * 100, 100);
  });
  const limitation = Array.isArray(explanation && explanation.limitations)
    ? explanation.limitations.find((item) => /independent|independ|apariciones/i.test(item))
    : null;
  if (limitation) {
    setText("distribution-footnote", limitation);
  }
}

function renderQuality(quality, explanation) {
  const panel = byId("data-quality");
  if (!quality) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.className = "data-quality quality-" + (quality.status || "provisional");
  const ready = quality.status === "ready";
  setText("data-quality-status", ready ? "Lista" : "Provisional");
  setText(
    "quality-summary",
    explanation && explanation.summary ||
      (ready
        ? "Los datos requeridos para esta proyección están disponibles."
        : "Faltan datos para completar el contexto; revisa los elementos indicados."),
  );
  const missing = Array.isArray(quality.missing) ? quality.missing : [];
  const missingList = byId("quality-missing");
  missingList.replaceChildren();
  missingList.hidden = missing.length === 0;
  missing.forEach((item) => appendText(missingList, "li", "", item));

  const warnings = Array.isArray(quality.warnings) ? quality.warnings : [];
  const warningList = byId("quality-warnings");
  warningList.replaceChildren();
  warningList.hidden = warnings.length === 0;
  warnings.forEach((item) => appendText(warningList, "li", "", item));

  const sources = Array.isArray(quality.sources) ? quality.sources.join(" · ") : "";
  const asOf = quality.as_of
    ? "Análisis generado: " + new Date(quality.as_of).toLocaleString("es-MX")
    : "Fecha de generación no especificada.";
  setText("quality-as-of", [sources, asOf].filter(Boolean).join(" · "));
  const indicator = byId("data-quality-indicator");
  indicator.className = "quality-indicator " + (ready ? "quality-dot-ready" : "quality-dot-provisional");
}

function renderAnalysisGameContext(context, matchup) {
  const section = byId("analysis-game-context");
  if (!context || !context.game) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const game = context.game;
  const teams = context.teams || {};
  const away = game.away && game.away.name || "Visitante";
  const home = game.home && game.home.name || "Local";
  setText("analysis-context-title", away + " @ " + home);

  const statuses = ["away", "home"].map((side) => {
    const team = teams[side];
    if (!team) return null;
    const label = team.lineup_status === "confirmed" ? "alineación publicada" : "alineación no publicada";
    return (team.name || (side === "away" ? "Visitante" : "Local")) + " · " + label;
  }).filter(Boolean);
  const published = ["away", "home"].some(
    (side) => teams[side] && teams[side].lineup_status === "confirmed",
  );
  const statusNode = byId("analysis-lineup-status");
  statusNode.textContent = statuses.length
    ? statuses.join(" · ")
    : "Estado de alineaciones no disponible";
  statusNode.className = "lineup-status " + (published ? "lineup-published" : "lineup-pending");

  const body = byId("analysis-context-body");
  body.replaceChildren();
  const gameMeta = document.createElement("div");
  gameMeta.className = "analysis-context-item";
  appendText(gameMeta, "span", "context-item-label", "JUEGO");
  appendText(
    gameMeta,
    "strong",
    "",
    gameTimeLabel(game.start_time) + " · " + (game.venue && game.venue.name || "Sede sin dato"),
  );
  body.appendChild(gameMeta);

  const pitchers = document.createElement("div");
  pitchers.className = "analysis-context-item";
  appendText(pitchers, "span", "context-item-label", "PITCHERS PROBABLES");
  const awayPitcher = game.away && game.away.probable_pitcher;
  const homePitcher = game.home && game.home.probable_pitcher;
  appendText(
    pitchers,
    "strong",
    "",
    (awayPitcher && awayPitcher.name || "Visitante: sin probable") +
      " · " + (homePitcher && homePitcher.name || "Local: sin probable"),
  );
  body.appendChild(pitchers);

  const weather = context.weather;
  if (weather) {
    const weatherItem = document.createElement("div");
    weatherItem.className = "analysis-context-item";
    appendText(weatherItem, "span", "context-item-label", "CLIMA · CONTEXTO");
    appendText(
      weatherItem,
      "strong",
      "",
      weatherDescription(weather) +
        (weather.applied_to_projection === false
          ? " · no aplicado al cálculo"
          : ""),
    );
    body.appendChild(weatherItem);
  }

  const repertoire = matchup && matchup.pitch_repertoire;
  if (repertoire && (Array.isArray(repertoire) ? repertoire.length : true)) {
    const repertoireItem = document.createElement("div");
    repertoireItem.className = "analysis-context-item";
    appendText(repertoireItem, "span", "context-item-label", "REPERTORIO DEL PITCHER");
    const pitchNames = {
      FF: "Recta de cuatro costuras",
      SI: "Sinker",
      SL: "Slider",
      CH: "Cambio",
      CU: "Curva",
      FC: "Cutter",
      FS: "Splitter",
      ST: "Sweeper",
    };
    const pitchRows = Array.isArray(repertoire) ? repertoire : repertoire.pitches;
    const summary = Array.isArray(pitchRows) && pitchRows.length
      ? pitchRows.slice(0, 4).map((pitch) => {
        const name = pitchNames[pitch.pitch_type] || pitch.pitch_type || "Lanzamiento";
        const share = typeof pitch.share === "number" ? " " + percent(pitch.share) : "";
        const velocity = typeof pitch.velocity_mph === "number"
          ? " · " + number(pitch.velocity_mph, 1) + " mph"
          : "";
        return name + share + velocity;
      }).join(" · ")
      : repertoire.summary || "Repertorio no disponible.";
    appendText(
      repertoireItem,
      "strong",
      "",
      summary + (
        (Array.isArray(pitchRows)
          ? pitchRows.some((pitch) => pitch.applied_to_projection === false)
          : repertoire.applied_to_projection === false)
          ? " · descriptivo, no aplicado"
          : ""
      ),
    );
    body.appendChild(repertoireItem);
  }

  const bullpen = matchup && matchup.bullpen;
  if (bullpen && Array.isArray(bullpen.relievers) && bullpen.relievers.length) {
    const bullpenItem = document.createElement("div");
    bullpenItem.className = "analysis-context-item";
    appendText(bullpenItem, "span", "context-item-label", "ACTIVIDAD RECIENTE DEL BULLPEN");
    const recent = bullpen.relievers.slice(0, 3).map((reliever) =>
      reliever.name + (
        reliever.pitches_last_3_days !== null && reliever.pitches_last_3_days !== undefined
          ? " · " + reliever.pitches_last_3_days + " lanzamientos/3d"
          : ""
      ) + (reliever.stale ? " · dato anterior" : "")
    ).join(" · ");
    appendText(
      bullpenItem,
      "strong",
      "",
      recent + (bullpen.workload_applied === false ? " · carga no aplicada a la proyección" : ""),
    );
    body.appendChild(bullpenItem);
  }

  const profile = matchup && matchup.player_profile;
  if (profile) {
    const profileItem = document.createElement("div");
    profileItem.className = "analysis-context-item";
    appendText(profileItem, "span", "context-item-label", "PERFIL DEL JUGADOR");
    const facts = [];
    if (profile.age !== null && profile.age !== undefined) facts.push("edad " + profile.age);
    if (profile.days_since_last_game !== null && profile.days_since_last_game !== undefined) {
      facts.push(profile.days_since_last_game + " días desde el último juego");
    }
    appendText(
      profileItem,
      "strong",
      "",
      facts.length
        ? facts.join(" · ") + (profile.applied_to_projection === false ? " · contexto, no aplicado" : "")
        : "Sin datos de edad o último juego.",
    );
    body.appendChild(profileItem);
  }

  const source = context.source || {};
  setText(
    "analysis-context-source",
    (source.name || "Fuente de MLB") +
      (source.fetched_at ? " · datos de fuente al " + new Date(source.fetched_at).toLocaleString("es-MX") : "") +
      (source.stale ? " · dato en caché" : ""),
  );
}

function renderAnalysis(data) {
  const rates = data.per_plate_appearance;
  const projection = data.projection;
  const contact = data.contact;
  const player = data.player;
  const model = data.model || {};
  const explanation = data.explanation || {};

  emptyState.hidden = true;
  dashboard.hidden = false;
  setText("player-name", player.name);
  setText(
    "player-subtitle",
    "MLB ID " + player.id + " · " + (data.window.games_available || data.window.games_found) +
      " juegos históricos · " + (data.window.seasons || "—") + " temporadas" +
      (data.opponent_pitcher ? " · vs " + data.opponent_pitcher.name : "")
  );

  const confidence = byId("sample-confidence");
  confidence.textContent = "MUESTRA " + data.sample.confidence.toUpperCase();
  confidence.className = "confidence-pill confidence-" + data.sample.confidence;

  setText("projected-hits", number(projection.hits, 2));
  const predictiveInterval = projection.predictive_interval_hits;
  const intervalLow = predictiveInterval
    ? predictiveInterval.low
    : projection.hits_low;
  const intervalHigh = predictiveInterval
    ? predictiveInterval.high
    : projection.hits_high;
  setText(
    "projected-range",
    number(intervalLow, predictiveInterval ? 0 : 2) + "–" +
      number(intervalHigh, predictiveInterval ? 0 : 2) + " hits",
  );
  setText(
    "projected-interval-note",
    predictiveInterval
      ? Math.round((predictiveInterval.level || 0.9) * 100) + "% " +
        (predictiveInterval.calibrated ? "calibrado" : "aprox. · no calibrado")
      : "intervalo no disponible",
  );
  setText(
    "projected-pa",
    number(projection.plate_appearances, 1) + " · rango " +
      number(projection.plate_appearances_low, 0) + "–" +
      number(projection.plate_appearances_high, 0),
  );
  setText("hit-game-chance", percent(projection.probability_one_or_more_hits));
  setText("hr-game-chance", percent(projection.probability_one_or_more_home_runs));
  setText(
    "data-window",
    (data.window.historical_from || data.window.from) + " — " +
      (data.window.historical_through || data.window.through)
  );

  setText("hit-rate", percent(rates.hits.rate));
  setText(
    "hit-count",
    rates.hits.count + " H observados · " + percent(rates.hits.sample_rate) + " histórico",
  );
  setText("hr-rate", percent(rates.home_runs.rate));
  setText("hr-count", rates.home_runs.count + " HR históricos");
  setText(
    "exit-velocity",
    contact.average_exit_velocity === null
      ? "—"
      : number(contact.average_exit_velocity) + " mph"
  );
  setText(
    "hard-hit-rate",
    contact.hard_hit_percent === null ? "—" : number(contact.hard_hit_percent) + "%"
  );
  setText("batted-ball-count", contact.batted_balls + " bolas bateadas");
  setText(
    "statcast-xba",
    contact.statcast_xba === null ? "—" : number(contact.statcast_xba * 100, 1) + "%",
  );
  setText("statcast-xba-sample", (contact.statcast_xba_contacts || 0) + " contactos con xBA");
  setTrack("hit-track", rates.hits.rate * 100, 45);
  setTrack("hr-track", rates.home_runs.rate * 100, 12);
  setTrack("ev-track", contact.average_exit_velocity || 0, 115);
  setTrack("hard-hit-track", contact.hard_hit_percent || 0, 100);

  const profileMetrics = [
    ["hits", "profile-hits", "profile-hits-bar"],
    ["home_runs", "profile-hr", "profile-hr-bar"],
    ["walks", "profile-walks", "profile-walks-bar"],
    ["strikeouts", "profile-strikeouts", "profile-strikeouts-bar"],
    ["outs", "profile-outs", "profile-outs-bar"],
  ];
  profileMetrics.forEach(([key, labelId, barId]) => {
    setText(labelId, percent(rates[key].rate));
    setTrack(barId, rates[key].rate * 100, 45);
  });

  setText("launch-angle", contact.average_launch_angle === null
    ? "—"
    : number(contact.average_launch_angle) + "°");
  setText("barrel-rate", contact.barrel_percent === null
    ? "—"
    : number(contact.barrel_percent) + "%");
  setText("effective-pa", number(data.sample.effective_plate_appearances, 0));
  setText(
    "pa-summary",
    data.sample.plate_appearances + " PA · " + (data.window.seasons || "—") + " temporadas",
  );
  setText("trend-game-count", data.trend.length);
  setText(
    "model-insight",
    explanation.summary || data.insight ||
      "El modelo combina resultados observados y calidad de contacto disponible.",
  );
  setText(
    "model-note",
    (model.name || "Modelo de proyección") + ": historial con vida media de " +
      (model.half_life_days || 365) + " días; forma reciente con vida media de " +
      (model.form_half_life_days || 45) + " días y ajuste por tamaño de muestra. " +
      (model.hit_estimate || "") + " " + (model.plate_appearance_forecast || "") +
      (predictiveInterval && predictiveInterval.calibrated === false
        ? " El intervalo de resultados es aproximado y no está calibrado."
        : "")
  );
  const insightDetail = byId("model-insight-detail");
  insightDetail.replaceChildren();
  (Array.isArray(explanation.limitations) ? explanation.limitations : []).forEach((item) => {
    appendText(insightDetail, "p", "limitation-note", item);
  });

  renderDrivers(data.drivers || []);
  renderSeasons(data.season_trend || []);
  renderHitDistribution(projection, explanation);
  renderQuality(data.data_quality, explanation);
  renderAnalysisGameContext(data.game_context, data.game_context?.matchup ?? data.matchup);
  updateFavoriteButton(player);

  validationButton.disabled = false;
  validationButton.textContent = data.validation ? "Volver a medir" : "Medir precisión histórica";
  renderTrend(data.trend);
  renderRecentGames(data.trend);
  renderValidation(data.validation || null);
  window.dispatchEvent(new CustomEvent("diamondiq:analysis-result", { detail: { analysis: data } }));
}

function renderDrivers(drivers) {
  const list = byId("driver-list");
  list.replaceChildren();
  if (!drivers.length) {
    const item = document.createElement("li");
    item.textContent = "El informe guardado no incluye el detalle de factores de esta versión.";
    list.appendChild(item);
    return;
  }
  drivers.forEach((driver) => {
    const item = document.createElement("li");
    const heading = document.createElement("div");
    heading.className = "driver-heading";
    const title = document.createElement("strong");
    title.textContent = driver.label;
    heading.appendChild(title);
    if (driver.hit_rate_change !== undefined) {
      const change = document.createElement("span");
      change.className = "driver-impact";
      const points = Number(driver.hit_rate_change) * 100;
      change.textContent = (points > 0 ? "+" : "") + points.toFixed(1) + " pp H/PA";
      heading.appendChild(change);
    }
    const detail = document.createElement("p");
    detail.textContent = driver.description;
    item.append(heading, detail);
    list.appendChild(item);
  });
}

function renderSeasons(seasons) {
  const rows = byId("season-rows");
  rows.replaceChildren();
  if (!seasons.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.textContent = "El informe guardado no incluye el desglose por temporada.";
    row.appendChild(cell);
    rows.appendChild(row);
    return;
  }
  seasons.slice().reverse().forEach((season) => {
    const row = document.createElement("tr");
    [
      season.season,
      season.plate_appearances,
      percent(season.hit_rate),
      percent(season.xba_adjusted_rate),
      season.home_runs,
      season.strikeouts,
      season.average_exit_velocity === null
        ? "—"
        : number(season.average_exit_velocity, 1) + " mph",
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.appendChild(cell);
    });
    rows.appendChild(row);
  });
}

function renderValidation(validation, statusText) {
  if (!validation) {
    validationMetrics.hidden = true;
    validationStatus.textContent = statusText || "La validación se calcula tras el análisis.";
    return;
  }

  if (validation.status === "insufficient") {
    validationMetrics.hidden = true;
  } else {
    validationMetrics.hidden = false;
    setText("validation-count", validation.predictions);
    setText("validation-mae", number(validation.mean_absolute_error_hits, 2) + " H");
    setText("validation-baseline", number(validation.recent_rate_baseline_mae_hits, 2) + " H");
    setText("validation-brier", number(validation.brier_score_one_or_more_hits, 3));
    setText("validation-calibration", percent(validation.calibration_gap));
  }

  const dates = validation.from && validation.through
    ? " · " + validation.from + " a " + validation.through
    : "";
  validationStatus.textContent = (statusText || validation.message || "Validación completa.") + dates;
}

function renderCohortEvaluation(report) {
  const status = byId("cohort-evaluation-status");
  const result = byId("cohort-evaluation-report");
  const candidatePanel = byId("cohort-candidate");
  result.hidden = true;
  candidatePanel.hidden = true;
  status.replaceChildren();

  if (!report || report.status === "not_run") {
    status.appendChild(document.createTextNode(
      report && report.message
        ? report.message
        : "No hay un reporte multijugador guardado en este equipo.",
    ));
    status.appendChild(document.createTextNode(" "));
    const guide = document.createElement("a");
    guide.href = "https://github.com/sebasclarkv/baseball-analyzer/blob/main/docs/evaluacion.md";
    guide.target = "_blank";
    guide.rel = "noopener";
    guide.textContent = "Ver instrucciones de evaluación CLI";
    status.appendChild(guide);
    return;
  }

  const stateLabel = report.status === "available" ? "disponible" :
    report.status === "limited" ? "limitado" : String(report.status || "sin estado");
  status.textContent = "Reporte " + stateLabel + " · las métricas requieren interpretación.";
  result.hidden = false;
  const seasons = Array.isArray(report.seasons) ? report.seasons : [];
  const generated = report.generated_at ? new Date(report.generated_at) : null;
  const generatedLabel = generated && !Number.isNaN(generated.getTime())
    ? generated.toLocaleString("es-MX")
    : "fecha no indicada";
  const seasonLabel = report.holdout_season
    ? " · prueba reservada " + report.holdout_season + (seasons.length ? " · historial " + seasons.join(", ") : "")
    : " · " + seasons.length + " temporadas" + (seasons.length ? " (" + seasons.join(", ") + ")" : "");
  setText(
    "cohort-report-overview",
    number(report.predictions, 0) + " proyecciones · " + number(report.players, 0) +
      " jugadores" + seasonLabel + " · generado " + generatedLabel,
  );

  const metrics = report.metrics || {};
  const baseline = report.baseline || {};
  const buckets = metrics.hit_buckets;
  const calibration = buckets?.calibration_by_hit_bucket;
  byId("cohort-hit-buckets").hidden = !calibration;
  if (calibration) {
    const rows = byId("cohort-hit-bucket-rows");
    rows.replaceChildren();
    for (const label of ["0", "1", "2+"]) {
      const bucket = calibration[label];
      if (!bucket) continue;
      const row = document.createElement("tr");
      const name = appendText(row, "th", "", label);
      name.scope = "row";
      appendText(row, "td", "", percent(bucket.predicted));
      appendText(row, "td", "", percent(bucket.observed));
      rows.appendChild(row);
    }
    const reference = baseline.hit_buckets?.ranked_probability_score;
    setText("cohort-distribution-error", "Error de probabilidad de 0, 1 o 2+ hits (RPS): " +
      number(buckets.ranked_probability_score, 3) + (Number.isFinite(reference) ?
        " · referencia " + number(reference, 3) : "") + " · menor es mejor.");
  }
  setText("cohort-mae", number(metrics.mean_absolute_error_hits, 2) + " H");
  setText("cohort-brier", number(metrics.brier_score_one_or_more_hits, 3));
  setText("cohort-coverage", percent(metrics.predictive_interval_coverage));
  setText("cohort-baseline-mae", number(baseline.mean_absolute_error_hits, 2) + " H");
  setText("cohort-baseline-brier", number(baseline.brier_score_one_or_more_hits, 3));
  setText("cohort-baseline-coverage", "No calculada");
  setText(
    "cohort-scope",
    report.scope || "El alcance del reporte no está especificado.",
  );

  const candidate = report.candidate;
  if (candidate) {
    candidatePanel.hidden = false;
    setText("cohort-candidate-title", candidate.name || "Modelo candidato");
    const candidateMetrics = candidate.metrics || {};
    setText("candidate-mae", number(candidateMetrics.mean_absolute_error_hits, 2) + " H");
    setText("candidate-brier", number(candidateMetrics.brier_score_one_or_more_hits, 3));
    const candidateState = candidate.status === "research_only"
      ? "Candidato experimental"
      : "Estado del candidato: " + (candidate.status || "sin estado");
    const reviewState = candidate.eligible_for_review === true
      ? "Elegible para revisión manual"
      : "No marcado para revisión manual";
    setText(
      "cohort-candidate-status",
      candidateState + " · " + reviewState +
        (candidate.active_in_app === false ? " · no está activo en la proyección de la app" : ""),
    );
    setText("cohort-candidate-reason", candidate.reason || "");
    byId("cohort-candidate-reason").hidden = !candidate.reason;
  }
}

async function loadCohortEvaluation() {
  const button = byId("load-cohort-evaluation");
  const status = byId("cohort-evaluation-status");
  button.disabled = true;
  button.textContent = "Cargando reporte…";
  status.textContent = "Consultando el último reporte guardado en este equipo…";
  try {
    const response = await fetch("/api/evaluation", {
      headers: { Accept: "application/json" },
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.detail || "No se pudo cargar el reporte.");
    renderCohortEvaluation(report);
  } catch (error) {
    status.textContent = error.message || "No se pudo cargar el reporte multijugador.";
  } finally {
    button.disabled = false;
    button.textContent = "Cargar reporte";
  }
}

function waitForSignal(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      const error = new Error("Evaluación cancelada.");
      error.name = "AbortError";
      reject(error);
      return;
    }
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, milliseconds);
    const onAbort = () => {
      clearTimeout(timer);
      cleanup();
      const error = new Error("Evaluación cancelada.");
      error.name = "AbortError";
      reject(error);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function runHistoricalValidation(analysis) {
  cancelHistoricalValidation();
  validationController = new AbortController();
  const controller = validationController;
  const isCurrent = () => validationController === controller && latestAnalysis === analysis;
  validationButton.disabled = true;
  validationButton.textContent = "Midiendo juegos no usados…";
  renderValidation(null, "Comparando el modelo con juegos posteriores que no usó para estimar…");

  try {
    const response = await fetch("/api/backtest/jobs", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        player_id: analysis.player.id,
        player_name: analysis.player.name,
        games: analysis.window.games_requested || 20,
        lookback_days: analysis.window.lookback_days || 180,
        max_predictions: Number(byId("backtest-max-games").value || 100),
      }),
    });
    let job = await response.json();
    if (!isCurrent()) return;
    if (!response.ok) throw new Error(job.detail || "No se pudo iniciar la evaluación histórica.");
    if (!job.id) throw new Error("La evaluación no devolvió un identificador de tarea.");

    while (job.status !== "complete" && job.status !== "error") {
      renderValidation(
        null,
        job.status === "queued"
          ? "Evaluación en cola; el informe actual sigue disponible."
          : "Evaluando juegos no usados " +
            (Number.isFinite(Number(job.completed)) && Number.isFinite(Number(job.total))
              ? Number(job.completed) + " / " + Number(job.total)
              : "…"),
      );
      await waitForSignal(2000, controller.signal);
      const statusResponse = await fetch("/api/jobs/" + encodeURIComponent(job.id), {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      job = await statusResponse.json();
      if (!isCurrent()) return;
      if (!statusResponse.ok) {
        throw new Error(job.detail || "No se pudo consultar el avance de la evaluación.");
      }
    }
    if (job.status === "error") {
      throw new Error(job.message || "La evaluación histórica no se pudo completar.");
    }
    const payload = job.result;
    if (!payload) throw new Error("La evaluación terminó sin resultados.");
    await localDataReady;
    if (!isCurrent()) return;
    analysis.validation = payload;
    const savedLocally = await saveAnalysisLocally(analysis, isCurrent);
    if (!isCurrent()) return;
    renderValidation(
      payload,
      savedLocally
        ? null
        : "Evaluación lista en esta sesión, pero el navegador no pudo guardar el reporte actualizado.",
    );
  } catch (error) {
    if (error.name === "AbortError" || !isCurrent()) return;
    renderValidation(null, error.message || "No se pudo completar la validación histórica.");
  } finally {
    if (isCurrent()) {
      validationController = null;
      resetValidationButton();
    }
  }
}

function svgElement(name, attributes, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.keys(attributes || {}).forEach((key) => element.setAttribute(key, attributes[key]));
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderTrend(trend) {
  const chart = byId("trend-chart");
  const empty = byId("trend-empty");
  chart.replaceChildren();
  if (!trend || trend.length < 2) {
    chart.hidden = true;
    empty.hidden = false;
    return;
  }
  chart.hidden = false;
  empty.hidden = true;

  const width = 760;
  const height = 260;
  const pad = { top: 18, right: 18, bottom: 34, left: 44 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const maxRate = Math.max(0.4, Math.ceil(Math.max(...trend.map((point) => point.hit_rate)) * 10) / 10);
  const x = (index) => pad.left + (innerWidth * index) / (trend.length - 1);
  const y = (rate) => pad.top + innerHeight - (rate / maxRate) * innerHeight;

  [0, maxRate / 2, maxRate].forEach((value) => {
    const yPos = y(value);
    chart.appendChild(svgElement("line", {
      x1: pad.left, y1: yPos, x2: width - pad.right, y2: yPos, class: "trend-grid",
    }));
    chart.appendChild(svgElement("text", {
      x: pad.left - 10, y: yPos + 4, "text-anchor": "end", class: "trend-axis-label",
    }, Math.round(value * 100) + "%"));
  });

  const points = trend.map((point, index) => [x(index), y(point.hit_rate)]);
  const linePath = points.map((point, index) =>
    (index === 0 ? "M" : "L") + point[0].toFixed(1) + " " + point[1].toFixed(1)
  ).join(" ");
  const areaPath = linePath + " L " + points[points.length - 1][0] + " " +
    (pad.top + innerHeight) + " L " + points[0][0] + " " +
    (pad.top + innerHeight) + " Z";
  chart.appendChild(svgElement("path", { d: areaPath, class: "trend-area" }));
  chart.appendChild(svgElement("path", { d: linePath, class: "trend-line" }));

  points.forEach((point, index) => {
    chart.appendChild(svgElement("circle", {
      cx: point[0], cy: point[1], r: index === points.length - 1 ? 5 : 3.5,
      class: index === points.length - 1 ? "trend-point trend-point-latest" : "trend-point",
    }));
    if (index === 0 || index === points.length - 1) {
      const label = new Date(trend[index].date + "T12:00:00").toLocaleDateString("es-MX", {
        month: "short", day: "numeric",
      });
      chart.appendChild(svgElement("text", {
        x: point[0], y: height - 8, "text-anchor": index === 0 ? "start" : "end",
        class: "trend-axis-label",
      }, label));
    }
  });
}

function renderRecentGames(trend) {
  const list = byId("recent-games");
  list.replaceChildren();
  trend.slice().reverse().forEach((game, index) => {
    const row = document.createElement("div");
    row.className = "game-row";

    const dateCell = document.createElement("span");
    dateCell.className = "game-date";
    dateCell.textContent = new Date(game.date + "T12:00:00").toLocaleDateString("es-MX", {
      month: "short", day: "numeric",
    });
    row.appendChild(dateCell);

    const gameLine = document.createElement("span");
    gameLine.className = "game-line";
    const score = document.createElement("span");
    score.className = "game-score" + (game.hits === 0 ? " zero" : "");
    score.textContent = game.hits + " H";
    gameLine.appendChild(score);
    if (game.home_runs > 0) {
      const homer = document.createElement("small");
      homer.textContent = game.home_runs + " HR";
      gameLine.appendChild(homer);
    }
    row.appendChild(gameLine);

    const meta = document.createElement("span");
    meta.className = "game-meta";
    meta.textContent = game.plate_appearances + " PA · " + percent(game.hit_rate);
    row.appendChild(meta);
    if (index === 0) row.classList.add("game-row-latest");
    list.appendChild(row);
  });
}

byId("copy-report").addEventListener("click", async () => {
  if (!latestAnalysis) return;
  const data = latestAnalysis;
  const reportLines = [
    "DIAMOND IQ · " + data.player.name,
    "Historial: " + data.sample.plate_appearances + " PA en " +
      (data.window.games_available || data.window.games_found) + " juegos · " +
      (data.window.seasons || "—") + " temporadas",
    "Forma reciente: " + (data.window.games_found || "—") + " juegos · " +
      (data.sample.recent_plate_appearances || "—") + " PA",
    "H/PA ajustado con Statcast xBA y matchup: " + percent(data.per_plate_appearance.hits.rate),
    "H/PA observado: " + percent(data.per_plate_appearance.hits.sample_rate),
    "HR/PA: " + percent(data.per_plate_appearance.home_runs.rate),
    "Hits esperados: " + number(data.projection.hits, 2),
    "Apariciones estimadas: " + number(data.projection.plate_appearances, 1),
    "Intervalo predictivo de hits: " + number(
      data.projection.predictive_interval_hits && data.projection.predictive_interval_hits.low,
      0,
    ) + "–" + number(
      data.projection.predictive_interval_hits && data.projection.predictive_interval_hits.high,
      0,
    ) + " (aproximado; no calibrado)",
    "Probabilidad de 0 hits: " + percent(data.projection.probability_zero_hits),
    "Probabilidad de exactamente 1 hit: " + percent(data.projection.probability_exactly_one_hit),
    "Probabilidad de 2+ hits: " + percent(data.projection.probability_two_or_more_hits),
    "Probabilidad 1+ hit: " + percent(data.projection.probability_one_or_more_hits),
    "Probabilidad HR: " + percent(data.projection.probability_one_or_more_home_runs),
    "Confianza de muestra: " + data.sample.confidence,
    "Datos: " + (data.window.historical_from || data.window.from) + " a " +
      (data.window.historical_through || data.window.through) + " · " + data.source,
  ];
  if (data.opponent_pitcher) {
    reportLines.push("Pitcher rival: " + data.opponent_pitcher.name +
      (data.opponent_pitcher.hand ? " · " + data.opponent_pitcher.hand + "HP" : ""));
  }
  if (data.game_context && data.game_context.game) {
    const game = data.game_context.game;
    reportLines.push(
      "Juego: " + (game.away && game.away.name || "Visitante") + " @ " +
        (game.home && game.home.name || "Local") + " · " + gameTimeLabel(game.start_time),
    );
    const lineupStatuses = ["away", "home"].map((side) => {
      const team = data.game_context.teams && data.game_context.teams[side];
      if (!team) return null;
      return (team.name || side) + " · " +
        (team.lineup_status === "confirmed" ? "alineación publicada, puede cambiar" : "alineación no publicada");
    }).filter(Boolean);
    if (lineupStatuses.length) reportLines.push("Alineaciones: " + lineupStatuses.join(" · "));
  }
  (data.drivers || []).forEach((driver) => {
    if (driver.hit_rate_change === undefined) return;
    const points = Number(driver.hit_rate_change) * 100;
    reportLines.push(
      driver.label + ": " + (points > 0 ? "+" : "") + points.toFixed(1) + " pp H/PA",
    );
  });
  if (data.validation && data.validation.status !== "insufficient") {
    reportLines.push(
      "Validación histórica: " + data.validation.predictions + " juegos · MAE hits " +
        number(data.validation.mean_absolute_error_hits, 2) +
        " · Brier 1+ hit " + number(data.validation.brier_score_one_or_more_hits, 3),
    );
  }
  const text = reportLines.join("\n");
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const fallback = document.createElement("textarea");
      fallback.value = text;
      fallback.setAttribute("readonly", "");
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      fallback.select();
      const copied = document.execCommand("copy");
      fallback.remove();
      if (!copied) throw new Error("No se pudo copiar");
    }
    message("Reporte copiado al portapapeles.", "success");
  } catch (error) {
    message("El navegador no permitió copiar el reporte.", "error");
  }
});

reportGameFilter.addEventListener("change", renderSavedReports);

exportReportsButton.addEventListener("click", async () => {
  await localDataReady;
  if (!savedReportsCache.length) return;
  const backup = new Blob([encodeReportBackup(savedReportsCache)], { type: "application/json" });
  const url = URL.createObjectURL(backup);
  const link = document.createElement("a");
  link.href = url;
  link.download = "diamond-iq-reportes-" + localDateString(new Date()) + ".json";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  setText("notebook-status", "Respaldo descargado. Puedes importarlo en otro navegador.");
});

reportBackupInput.addEventListener("change", async () => {
  const file = reportBackupInput.files[0];
  reportBackupInput.value = "";
  if (!file) return;
  reportBackupInput.disabled = true;
  setText("notebook-status", "Revisando el respaldo…");
  try {
    const incoming = decodeReportBackup(await file.text());
    await localDataReady;
    await updateSavedReports((reports) => {
      incoming.forEach((record) => reports.set(record.id, mergeReport(reports.get(record.id), record)));
    });
    setText("notebook-status", incoming.length
      ? "Respaldo importado. Los reportes existentes se conservaron."
      : "El respaldo no contiene reportes.");
  } catch (error) {
    setText("notebook-status", error.message || "No se pudo importar el respaldo.");
  } finally {
    reportBackupInput.disabled = false;
  }
});

clearReportsButton.addEventListener("click", async () => {
  await localDataReady;
  if (!window.confirm("¿Borrar todos los reportes guardados en este navegador, incluidos los fijados?")) return;
  try {
    await updateSavedReports((reports) => reports.clear());
    setText("notebook-status", "Historial local borrado.");
  } catch (error) {
    setText("notebook-status", "No se pudo borrar el historial local.");
  }
});

byId("favorite-player").addEventListener("click", () => {
  if (latestAnalysis && latestAnalysis.player) {
    void toggleFavorite(latestAnalysis.player);
  }
});

validationButton.addEventListener("click", () => {
  if (latestAnalysis) void runHistoricalValidation(latestAnalysis);
});

byId("load-cohort-evaluation").addEventListener("click", () => {
  void loadCohortEvaluation();
});

function addInstallStep(title, description) {
  const step = document.createElement("section");
  step.className = "install-step";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const text = document.createElement("p");
  text.textContent = description;
  step.append(heading, text);
  installInstructions.appendChild(step);
}

function showInstallHelp() {
  installInstructions.replaceChildren();
  const onAppleMobile = /iPhone|iPad|iPod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const onAndroid = /Android/i.test(navigator.userAgent);
  let requirement = "Al terminar de cargar la app, los reportes guardados pueden abrirse sin conexión. Para consultas nuevas, el servidor local debe seguir activo y tener acceso a las fuentes.";
  if (!window.isSecureContext) {
    addInstallStep("Abrir desde tu red local", "Puedes seguir usando esta dirección HTTP en el navegador mientras el servidor esté activo.");
    addInstallStep("Habilitar instalación y uso sin conexión", "Abre Diamond IQ con una dirección HTTPS válida para este dispositivo. En la computadora que ejecuta el servidor también sirve http://localhost:8000.");
    requirement = "La dirección HTTP de tu red local no habilita el modo sin conexión de la PWA.";
  } else if (!("serviceWorker" in navigator)) {
    addInstallStep("Usar en el navegador", "Puedes seguir usando Diamond IQ mientras el servidor esté activo.");
    requirement = "Este navegador no permite guardar la app sin conexión. Prueba con Safari, Chrome o Edge actualizado.";
  } else if (onAppleMobile) {
    addInstallStep("iPhone o iPad · Safari", "En Safari abre Compartir, toca Añadir a pantalla de inicio y confirma con Añadir.");
  } else if (onAndroid) {
    addInstallStep("Android · Chrome", "Abre el menú ⋮ y toca Instalar aplicación o Añadir a pantalla de inicio.");
  } else {
    addInstallStep("Chrome o Edge", "Usa el icono Instalar de la barra de direcciones o el menú del navegador.");
    addInstallStep("Safari en Mac", "Si está disponible en tu versión de Safari, elige Archivo y Añadir al Dock.");
  }
  setText("install-requirement", requirement);
  if (typeof installDialog.showModal === "function") installDialog.showModal();
  else message(requirement, "success");
}

installButton.addEventListener("click", async () => {
  if (!deferredInstallPrompt) {
    showInstallHelp();
    return;
  }
  deferredInstallPrompt.prompt();
  const result = await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  if (result && result.outcome === "accepted") installButton.hidden = true;
  else showInstallHelp();
});

byId("close-install-help").addEventListener("click", () => installDialog.close());
installDialog.addEventListener("click", (event) => {
  if (event.target === installDialog) installDialog.close();
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
});
window.addEventListener("appinstalled", () => {
  installButton.hidden = true;
  deferredInstallPrompt = null;
});

const isInstalled = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
installButton.hidden = isInstalled;
if (!window.isSecureContext) installButton.textContent = "Cómo instalar";

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/service-worker.js", { scope: "/" }).catch(() => {
    // Service-worker support is optional; the responsive web app remains usable.
  });
}

gameDateInput.value = localDateString(new Date());
gameDateInput.addEventListener("change", () => {
  clearGameContext();
  void loadSchedule();
});
byId("previous-game-date").addEventListener("click", () => moveScheduleDate(-1));
byId("next-game-date").addEventListener("click", () => moveScheduleDate(1));
byId("load-games").addEventListener("click", () => void loadSchedule());
byId("clear-game-context").addEventListener("click", clearGameContext);

localDataReady = initializeLocalData();
initMatchupTools();
initLiveBoard();
void loadSchedule();
