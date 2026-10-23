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
const savedReportsWrap = byId("saved-reports-wrap");
const savedReports = byId("saved-reports");
const installButton = byId("install-app");
const installDialog = byId("install-help");
const installInstructions = byId("install-instructions");
const validationMetrics = byId("validation-metrics");
const validationStatus = byId("validation-status");
const validationButton = byId("run-validation");
const savedReportsKey = "diamond-iq:saved-reports:v1";

let selectedPlayer = null;
let selectedPitcher = null;
let searchTimer = null;
let searchController = null;
let pitcherSearchTimer = null;
let pitcherSearchController = null;
let latestAnalysis = null;
let activeSuggestionIndex = -1;
let activePitcherSuggestionIndex = -1;
let deferredInstallPrompt = null;
let validationController = null;

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

function choosePlayer(player) {
  selectedPlayer = player;
  queryInput.value = player.name;
  analyzeButton.disabled = false;
  updateAnalyzeLabel();
  clearSuggestions();
  updateSearchMessage(player.years ? "Carrera MLB: " + player.years : "Jugador seleccionado");
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
  label.textContent = selectedPitcher ? "Proyectar matchup" : "Analizar bateador";
}

function savedReportsList() {
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

function savedDateLabel(value) {
  if (!value) return "guardado";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "guardado"
    : parsed.toLocaleDateString("es-MX", { month: "short", day: "numeric" });
}

function renderSavedReports() {
  const reports = savedReportsList();
  savedReports.replaceChildren();
  savedReportsWrap.hidden = reports.length === 0;
  reports.forEach((record) => {
    if (!record.analysis.player) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "saved-report-button";
    const opponent = record.analysis.opponent_pitcher
      ? " vs " + record.analysis.opponent_pitcher.name
      : "";
    button.textContent = record.analysis.player.name + opponent + " · " +
      (record.analysis.window.games_requested || "—") + " juegos · " + savedDateLabel(record.saved_at);
    button.addEventListener("click", () => showSavedReport(record.analysis));
    savedReports.appendChild(button);
  });
}

function saveAnalysisLocally(analysis) {
  const reports = savedReportsList().filter(
    (record) => !(
      Number(record.analysis.player.id) === Number(analysis.player.id) &&
      Number(record.analysis.window.games_requested) === Number(analysis.window.games_requested) &&
      Number(record.analysis.window.lookback_days) === Number(analysis.window.lookback_days) &&
      Number(record.analysis.opponent_pitcher && record.analysis.opponent_pitcher.id || 0) ===
        Number(analysis.opponent_pitcher && analysis.opponent_pitcher.id || 0)
    ),
  );
  reports.unshift({ saved_at: new Date().toISOString(), analysis });
  try {
    localStorage.setItem(savedReportsKey, JSON.stringify(reports.slice(0, 8)));
  } catch (error) {
    // A private browsing session can disable local storage; the live report still works.
  }
  renderSavedReports();
}

function reportSourceDate(analysis) {
  const generatedAt = analysis.model && analysis.model.generated_at;
  return generatedAt ? new Date(generatedAt).toLocaleString("es-MX") : "fecha desconocida";
}

function showSavedReport(analysis, scroll = true) {
  if (validationController) validationController.abort();
  validationController = null;
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
  selectedPlayer = null;
  analyzeButton.disabled = true;
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

  const player = { ...selectedPlayer };
  analyzeButton.disabled = true;
  analyzeButton.classList.add("is-loading");
  analyzeButton.querySelector("span").textContent = "Analizando Statcast";
  updateSearchMessage("");
  message("Descargando el historial Statcast y ajustando forma, contacto y matchup…", "loading");

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        player_id: player.id,
        player_name: player.name,
        opponent_pitcher_id: selectedPitcher ? selectedPitcher.id : null,
        opponent_pitcher_name: selectedPitcher ? selectedPitcher.name : null,
        games: Number(byId("games-select").value),
        lookback_days: Number(byId("window-select").value),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "No se pudo completar el análisis.");
    latestAnalysis = payload;
    saveAnalysisLocally(payload);
    renderAnalysis(payload);
    message("", "");
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
    const cached = savedReportsList().find(
      (record) => Number(record.analysis.player.id) === Number(player.id) &&
        Number(record.analysis.opponent_pitcher && record.analysis.opponent_pitcher.id || 0) ===
          Number(selectedPitcher && selectedPitcher.id || 0),
    );
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
    analyzeButton.disabled = !selectedPlayer;
    analyzeButton.classList.remove("is-loading");
    updateAnalyzeLabel();
  }
});

function setTrack(id, value, max) {
  const track = byId(id);
  if (!track) return;
  const normalized = Math.max(0, Math.min(100, (Number(value || 0) / max) * 100));
  track.style.width = normalized + "%";
}

function renderAnalysis(data) {
  const rates = data.per_plate_appearance;
  const projection = data.projection;
  const contact = data.contact;
  const player = data.player;

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
  setText(
    "projected-range",
    number(projection.hits_low, 2) + "–" + number(projection.hits_high, 2) + " hits"
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
    data.insight || "El modelo combina resultados observados y calidad de contacto disponible.",
  );
  setText(
    "model-note",
    data.model.name + ": historial con vida media de " +
      (data.model.half_life_days || 365) + " días; forma reciente con vida media de " +
      (data.model.form_half_life_days || 45) + " días y shrinkage por tamaño de muestra. " +
      (data.model.hit_estimate || "") + " " + (data.model.plate_appearance_forecast || "")
  );

  renderDrivers(data.drivers || []);
  renderSeasons(data.season_trend || []);

  validationButton.disabled = false;
  validationButton.textContent = data.validation ? "Volver a medir" : "Medir precisión histórica";
  renderTrend(data.trend);
  renderRecentGames(data.trend);
  renderValidation(data.validation || null);
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

async function runHistoricalValidation(analysis) {
  if (validationController) validationController.abort();
  validationController = new AbortController();
  const controller = validationController;
  validationButton.disabled = true;
  validationButton.textContent = "Midiendo juegos no usados…";
  renderValidation(null, "Comparando el modelo con juegos posteriores que no usó para estimar…");

  try {
    const response = await fetch("/api/backtest", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        player_id: analysis.player.id,
        player_name: analysis.player.name,
        games: analysis.window.games_requested || 20,
        lookback_days: analysis.window.lookback_days || 180,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "No se pudo medir el error histórico.");
    if (latestAnalysis !== analysis) return;
    analysis.validation = payload;
    saveAnalysisLocally(analysis);
    renderValidation(payload);
  } catch (error) {
    if (error.name === "AbortError" || latestAnalysis !== analysis) return;
    renderValidation(null, error.message || "No se pudo completar la validación histórica.");
  } finally {
    if (latestAnalysis === analysis) {
      validationButton.disabled = false;
      validationButton.textContent = "Volver a medir";
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

byId("clear-saved-reports").addEventListener("click", () => {
  try {
    localStorage.removeItem(savedReportsKey);
  } catch (error) {
    // Continue to clear the visible history even when storage is unavailable.
  }
  renderSavedReports();
  message("Historial local borrado.", "success");
});

validationButton.addEventListener("click", () => {
  if (latestAnalysis) void runHistoricalValidation(latestAnalysis);
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
  addInstallStep("iPhone o iPad · Safari", "Abre Compartir y elige Añadir a pantalla de inicio.");
  addInstallStep("Android · Chrome", "Abre el menú ⋮ y toca Instalar aplicación o Añadir a pantalla de inicio.");
  addInstallStep("Computadora · Chrome o Edge", "Usa el icono Instalar de la barra de direcciones o el menú del navegador.");
  if (typeof installDialog.showModal === "function") installDialog.showModal();
  else message("Usa el menú de tu navegador y elige Añadir a pantalla de inicio.", "success");
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

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/static/service-worker.js").catch(() => {
    // Service-worker support is optional; the responsive web app remains usable.
  });
}

renderSavedReports();
const latestSavedReport = savedReportsList()[0];
if (latestSavedReport) showSavedReport(latestSavedReport.analysis, false);
