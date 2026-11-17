const byId = (id) => document.getElementById(id);

let activeContext = null;
let activeTeamSide = "away";
let latestOfficialAnalysis = null;
let lastScenario = null;
let comparisonController = null;
let scenarioController = null;

const probability = (value) =>
  typeof value === "number" && Number.isFinite(value) ? (value * 100).toFixed(1) + "%" : "—";

const count = (value, digits = 2) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";

function addText(parent, tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value === null || value === undefined ? "" : String(value);
  parent.appendChild(element);
  return element;
}

function setStatus(id, text) {
  byId(id).textContent = text || "";
}

function eligibleForPregame(game) {
  if (!game || !game.start_time) return false;
  const start = new Date(game.start_time);
  if (Number.isNaN(start.getTime()) || start.getTime() <= Date.now()) return false;
  const state = String(game.status || "").toLowerCase();
  const code = String(game.status_code || "").toLowerCase();
  if (/final|complete|game over|in progress|live|postpon|suspend|cancel/.test(state)) return false;
  if (["f", "final", "live", "in_progress", "completed", "postponed", "suspended", "cancelled"].includes(code)) {
    return false;
  }
  return ["preview", "scheduled", "s", "p", "w", "warmup"].includes(code) ||
    /scheduled|pre-game|preview|pregame|warm/.test(state);
}

function opposingSide(side) {
  return side === "home" ? "away" : "home";
}

function friendlyStatus(game) {
  const state = String(game.status || "").toLowerCase();
  const code = String(game.status_code || "").toLowerCase();
  if (code === "final" || /final|game over|complete/.test(state)) return "Final";
  if (code === "live" || code === "in_progress" || /in progress|live/.test(state)) return "En juego";
  if (/postpon/.test(state)) return "Pospuesto";
  if (/suspend/.test(state)) return "Suspendido";
  if (/cancel/.test(state)) return "Cancelado";
  if (code === "preview" || /scheduled|pre-game|preview/.test(state)) return "Programado";
  return game.status || "Estado no disponible";
}

function playersForSide(side) {
  const team = activeContext && activeContext.teams && activeContext.teams[side];
  if (!team) return { team: null, players: [], isLineup: false };
  const lineup = Array.isArray(team.lineup) ? team.lineup : [];
  const isLineup = lineup.length > 0;
  const available = isLineup ? lineup : (Array.isArray(team.roster) ? team.roster : []);
  const players = available.filter((player) =>
    player && Number(player.id) > 0 && player.name && player.is_pitcher !== true &&
      !/^(p|sp|rp|pitcher)$/i.test(String(player.position || "").trim()),
  );
  return { team, players, isLineup };
}

function pitchersForSide(side) {
  const team = activeContext && activeContext.teams && activeContext.teams[side];
  if (!team || !Array.isArray(team.roster)) return [];
  return team.roster.filter((player) =>
    player && Number(player.id) > 0 && player.name &&
      (player.is_pitcher === true || /^(p|sp|rp)$/i.test(String(player.position || "").trim())),
  );
}

function fillSelect(select, players, placeholder) {
  select.replaceChildren();
  const prompt = document.createElement("option");
  prompt.value = "";
  prompt.textContent = placeholder;
  select.appendChild(prompt);
  players.forEach((player) => {
    const option = document.createElement("option");
    option.value = String(player.id);
    const order = player.batting_order ? player.batting_order + " · " : "";
    const position = player.position ? " · " + player.position : "";
    option.textContent = order + player.name + position;
    select.appendChild(option);
  });
}

function updateActionStates(players, pitchers) {
  const game = activeContext && activeContext.game;
  const canProject = eligibleForPregame(game);
  const compareA = byId("compare-player-a").value;
  const compareB = byId("compare-player-b").value;
  byId("run-hitter-comparison").disabled = !canProject || !compareA || !compareB || compareA === compareB;

  const hitterId = byId("scenario-hitter").value;
  const pitcherId = byId("scenario-pitcher").value;
  byId("run-pitcher-scenario").disabled = !canProject || !hitterId || !pitcherId;
  if (!canProject) {
    byId("compare-guidance").textContent = "Este juego no admite una proyección previa. El marcador queda disponible como consulta.";
    byId("scenario-guidance").textContent = "Los escenarios solo están disponibles antes del primer lanzamiento.";
  } else if (!players.length) {
    byId("compare-guidance").textContent = "No hay dos bateadores disponibles en el roster publicado para comparar.";
    byId("scenario-guidance").textContent = "No hay bateadores elegibles en el equipo seleccionado.";
  } else if (!pitchers.length) {
    byId("scenario-guidance").textContent = "MLB no entregó pitchers identificados en el roster rival para construir un escenario.";
  }
}

function renderLineScore(context) {
  const game = context.game || {};
  const innings = Array.isArray(context.innings) ? context.innings : [];
  const table = byId("inning-scorecard");
  table.replaceChildren();
  const head = document.createElement("thead");
  const header = document.createElement("tr");
  addText(header, "th", "", "EQUIPO").setAttribute("scope", "col");
  innings.forEach((inning, index) => {
    const number = inning.number ?? index + 1;
    const cell = addText(header, "th", "", number);
    cell.setAttribute("scope", "col");
  });
  const totalHeader = addText(header, "th", "scorecard-total", "R");
  totalHeader.setAttribute("scope", "col");
  head.appendChild(header);
  table.appendChild(head);

  const body = document.createElement("tbody");
  ["away", "home"].forEach((side) => {
    const team = game[side] || {};
    const row = document.createElement("tr");
    addText(row, "th", "", team.abbreviation || team.name || (side === "away" ? "Visitante" : "Local"))
      .setAttribute("scope", "row");
    innings.forEach((inning) => {
      const value = inning[side];
      addText(row, "td", value === null || value === undefined ? "scorecard-empty" : "", value ?? "—");
    });
    addText(row, "td", "scorecard-total", team.score ?? "—");
    body.appendChild(row);
  });
  table.appendChild(body);

  const hasRunsByInning = innings.some((inning) => inning.away !== null && inning.away !== undefined ||
    inning.home !== null && inning.home !== undefined);
  const pregame = eligibleForPregame(game);
  byId("scorecard-status").textContent = friendlyStatus(game);
  byId("scorecard-note").textContent = hasRunsByInning
    ? "R = carreras. Un guion indica que MLB aún no publica esa entrada."
    : pregame
      ? "PREVIA · Aún no hay entradas jugadas; los guiones no representan ceros ni resultados estimados."
      : innings.length
        ? "MLB entregó las columnas de entradas, pero todavía no hay carreras publicadas por entrada."
        : "MLB no entregó detalle de entradas para este marcador.";
}

function renderSelectors(side) {
  byId("run-hitter-comparison").textContent = "Comparar bateadores";
  byId("run-pitcher-scenario").textContent = "Proyectar escenario";
  activeTeamSide = side === "home" ? "home" : "away";
  const { team, players, isLineup } = playersForSide(activeTeamSide);
  const opponent = opposingSide(activeTeamSide);
  const official = activeContext && activeContext.game && activeContext.game[opponent] &&
    activeContext.game[opponent].probable_pitcher;
  const pitchers = pitchersForSide(opponent).filter((pitcher) =>
    !official || Number(pitcher.id) !== Number(official.id),
  );

  const teamLabel = team && team.name || (activeTeamSide === "away" ? "visitante" : "local");
  fillSelect(byId("compare-player-a"), players, "Elige bateador");
  fillSelect(byId("compare-player-b"), players, "Elige bateador");
  fillSelect(byId("scenario-hitter"), players, "Elige bateador");
  fillSelect(byId("scenario-pitcher"), pitchers, "Elige un pitcher distinto");

  const lineupLabel = isLineup
    ? "Alineación publicada por MLB; puede cambiar antes del juego."
    : "Alineación sin publicar; las opciones son bateadores del roster, no titulares confirmados.";
  byId("compare-guidance").textContent = players.length >= 2
    ? teamLabel + " · " + lineupLabel + " Se comparan frente al abridor probable publicado."
    : "No hay dos bateadores disponibles en el roster para comparar.";
  byId("scenario-guidance").textContent = players.length && pitchers.length
    ? "" + teamLabel + " al bate · abridor oficial: " +
      (official && official.name || "por confirmar") +
      ". El bullpen se recalcula sin el pitcher elegido; la carga reciente no cambia numéricamente la proyección."
    : !players.length
      ? "No hay bateadores elegibles en el equipo seleccionado."
      : "No hay pitchers alternativos identificados en el roster rival.";

  byId("compare-results").hidden = true;
  byId("compare-results").replaceChildren();
  byId("scenario-results").hidden = true;
  byId("scenario-results").replaceChildren();
  byId("analyze-official-scenario").hidden = true;
  lastScenario = null;
  setStatus("compare-status", "");
  setStatus("scenario-status", "");
  updateActionStates(players, pitchers);
}

function requestBody(player) {
  return {
    player_id: Number(player.id),
    player_name: player.name,
    game_id: Number(activeContext.game.id),
    games: Number(byId("games-select")?.value || 40),
    lookback_days: Number(byId("window-select")?.value || 1095),
  };
}

async function postAnalysis(path, body, signal) {
  const response = await fetch(path, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    throw new Error("El servidor devolvió una respuesta ilegible.");
  }
  if (!response.ok) throw new Error(payload.detail || "No se pudo calcular la proyección.");
  return payload;
}

function appendProbabilityBreakdown(parent, projection) {
  const rows = [
    ["0 H", projection.probability_zero_hits],
    ["1 H", projection.probability_exactly_one_hit],
    ["2+ H", projection.probability_two_or_more_hits],
  ];
  const grid = document.createElement("div");
  grid.className = "comparison-probabilities";
  rows.forEach(([label, value]) => {
    const item = document.createElement("div");
    addText(item, "span", "", label);
    addText(item, "strong", "", probability(value));
    grid.appendChild(item);
  });
  parent.appendChild(grid);
}

function projectionCard(result, className = "comparison-card") {
  const card = document.createElement("article");
  card.className = className;
  const projection = result.projection || {};
  addText(card, "h4", "", result.player && result.player.name || "Bateador");
  const interval = projection.predictive_interval_hits;
  const primary = document.createElement("div");
  primary.className = "comparison-primary";
  addText(primary, "span", "", "Hits esperados");
  addText(primary, "strong", "", count(projection.hits));
  card.appendChild(primary);
  addText(
    card,
    "p",
    "comparison-pitcher",
    "Abridor probable · " + (result.opponent_pitcher && result.opponent_pitcher.name || "por confirmar") +
      (interval ? " · rango " + count(interval.low, 0) + "–" + count(interval.high, 0) + " hits" : ""),
  );
  appendProbabilityBreakdown(card, projection);
  const quality = result.data_quality || {};
  addText(
    card,
    "p",
    "comparison-quality",
    "Datos " + (quality.status === "ready" ? "listos" : "provisionales") +
      (Array.isArray(quality.missing) && quality.missing.length ? " · " + quality.missing.length + " faltantes" : ""),
  );
  return card;
}

async function runComparison() {
  if (!activeContext || !eligibleForPregame(activeContext.game)) return;
  const firstId = Number(byId("compare-player-a").value);
  const secondId = Number(byId("compare-player-b").value);
  const { players } = playersForSide(activeTeamSide);
  const selected = [firstId, secondId].map((id) => players.find((player) => Number(player.id) === id));
  if (!selected[0] || !selected[1] || firstId === secondId) return;

  if (comparisonController) comparisonController.abort();
  comparisonController = new AbortController();
  const controller = comparisonController;
  const button = byId("run-hitter-comparison");
  const results = byId("compare-results");
  button.disabled = true;
  button.textContent = "Comparando…";
  results.hidden = true;
  setStatus("compare-status", "Consultando ambos historiales con el mismo juego y abridor oficial…");
  try {
    const analyses = [];
    const bodies = selected.map(requestBody);
    for (const body of bodies) {
      const analysis = await postAnalysis("/api/analyze", body, controller.signal);
      if (controller.signal.aborted || controller !== comparisonController) return;
      analyses.push(analysis);
    }
    if (controller !== comparisonController) return;
    results.replaceChildren(...analyses.map((analysis) => projectionCard(analysis)));
    results.hidden = false;
    setStatus("compare-status", "Dos proyecciones separadas · mismo juego, mismo abridor probable y misma ventana histórica.");
  } catch (error) {
    if (error.name !== "AbortError" && controller === comparisonController && !controller.signal.aborted) {
      setStatus("compare-status", error.message || "No se pudo completar la comparación.");
    }
  } finally {
    if (controller === comparisonController) {
      button.textContent = "Comparar bateadores";
      updateActionStates(players, pitchersForSide(opposingSide(activeTeamSide)));
    }
  }
}

function sameOfficialRun(analysis, state) {
  const request = state.request || {};
  const window = analysis && analysis.window || {};
  const official = state.payload.scenario && state.payload.scenario.official_starter;
  const analysisPitcher = analysis && analysis.opponent_pitcher;
  return analysis && analysis.player && Number(analysis.player.id) === Number(state.player.id) &&
    analysis.game_context && analysis.game_context.game && activeContext && activeContext.game &&
    Number(analysis.game_context.game.id) === Number(activeContext.game.id) &&
    Number(window.games_requested) === Number(request.games) &&
    Number(window.lookback_days) === Number(request.lookback_days) &&
    Number(analysisPitcher && analysisPitcher.id || 0) === Number(official && official.id || 0);
}

function signed(value, digits, suffix) {
  if (!Number.isFinite(value)) return "—";
  return (value > 0 ? "+" : "") + value.toFixed(digits) + suffix;
}

function renderScenarioComparison(state) {
  const container = byId("scenario-results");
  const data = state.payload.projection || {};
  if (!sameOfficialRun(latestOfficialAnalysis, state)) {
    addText(
      container,
      "p",
      "scenario-meta",
      "Para comparar este escenario con el abridor oficial, usa el botón de análisis oficial.",
    );
    return;
  }
  const official = latestOfficialAnalysis.projection || {};
  const delta = document.createElement("div");
  delta.className = "scenario-delta";
  const hitDelta = document.createElement("div");
  addText(hitDelta, "span", "", "Diferencia de hits esperados · escenario − oficial");
  addText(hitDelta, "strong", "", signed(Number(data.hits) - Number(official.hits), 2, " H"));
  const chanceDelta = document.createElement("div");
  addText(chanceDelta, "span", "", "Diferencia de prob. de 1+ hit");
  const probabilityChange = (Number(data.probability_one_or_more_hits) -
    Number(official.probability_one_or_more_hits)) * 100;
  addText(chanceDelta, "strong", "", signed(probabilityChange, 1, " pp"));
  delta.append(hitDelta, chanceDelta);
  container.appendChild(delta);
}

function renderScenarioResult(result, player, pitcher) {
  const container = byId("scenario-results");
  container.replaceChildren();
  const scenario = result.scenario || {};
  const summary = projectionCard(result, "scenario-summary");
  summary.querySelector(".comparison-pitcher").textContent =
    "Abridor hipotético · " + (scenario.pitcher && scenario.pitcher.name || pitcher.name) +
      " · oficial publicado · " + (scenario.official_starter && scenario.official_starter.name || "por confirmar");
  const bullpenNote = result.game_context && result.game_context.matchup && result.game_context.matchup.bullpen;
  addText(
    summary,
    "p",
    "scenario-bullpen-note",
    bullpenNote
      ? "Bullpen: pool recalculado sin el pitcher hipotético. La carga reciente se muestra como contexto y no modifica numéricamente la proyección."
      : "Bullpen: sin historial disponible se usa la referencia de liga; la carga reciente no modifica numéricamente la proyección.",
  );
  container.appendChild(summary);
  renderScenarioComparison(lastScenario);
  container.hidden = false;
  byId("analyze-official-scenario").hidden = false;
}

async function runPitcherScenario() {
  if (!activeContext || !eligibleForPregame(activeContext.game)) return;
  const hitterId = Number(byId("scenario-hitter").value);
  const pitcherId = Number(byId("scenario-pitcher").value);
  const { players } = playersForSide(activeTeamSide);
  const pitcherSide = opposingSide(activeTeamSide);
  const pitcher = pitchersForSide(pitcherSide).find((item) => Number(item.id) === pitcherId);
  const player = players.find((item) => Number(item.id) === hitterId);
  if (!player || !pitcher) return;

  if (scenarioController) scenarioController.abort();
  scenarioController = new AbortController();
  const controller = scenarioController;
  const button = byId("run-pitcher-scenario");
  button.disabled = true;
  button.textContent = "Calculando escenario…";
  byId("scenario-results").hidden = true;
  byId("analyze-official-scenario").hidden = true;
    setStatus("scenario-status", "Calculando un escenario hipotético con el abridor alternativo…");
  try {
    const body = {
      ...requestBody(player),
      scenario_pitcher_id: pitcher.id,
      scenario_pitcher_name: pitcher.name,
    };
    const payload = await postAnalysis("/api/scenario", body, controller.signal);
    if (controller.signal.aborted || controller !== scenarioController) return;
    lastScenario = { payload, player, pitcher, request: body, side: activeTeamSide };
    renderScenarioResult(payload, player, pitcher);
    setStatus(
      "scenario-status",
      "Escenario de esta sesión · no se mezcla con pronósticos oficiales. Calcula la proyección oficial para guardarla en tu libreta.",
    );
  } catch (error) {
    if (error.name !== "AbortError" && controller === scenarioController && !controller.signal.aborted) {
      setStatus("scenario-status", error.message || "No se pudo calcular el escenario.");
    }
  } finally {
    if (controller === scenarioController) {
      button.textContent = "Proyectar escenario";
      updateActionStates(players, pitchersForSide(pitcherSide));
    }
  }
}

function analyzeOfficialScenario() {
  const state = lastScenario;
  if (!state || !activeContext || !eligibleForPregame(activeContext.game)) return;
  const expectedGameId = Number(state.request.game_id);
  const selectAndAnalyze = () => {
    if (!activeContext || Number(activeContext.game.id) !== expectedGameId ||
        !eligibleForPregame(activeContext.game)) return;
    const rosterButton = [...document.querySelectorAll("#team-roster .roster-player")].find((button) =>
      Number(button.dataset.playerId) === Number(state.player.id),
    );
    if (!rosterButton) {
      setStatus("scenario-status", "El bateador ya no está disponible en la plantilla seleccionada.");
      return;
    }
    rosterButton.click();
    window.setTimeout(() => {
      const analyzeButton = byId("analyze-button");
      if (analyzeButton && !analyzeButton.disabled) analyzeButton.click();
    }, 0);
  };
  if (state.side !== activeTeamSide) {
    const sideButton = byId("team-tab-" + state.side);
    if (!sideButton) return;
    sideButton.click();
    window.setTimeout(selectAndAnalyze, 0);
  } else {
    selectAndAnalyze();
  }
}

function updateScenarioComparison() {
  if (!lastScenario || byId("scenario-results").hidden) return;
  const result = byId("scenario-results");
  const oldDelta = result.querySelector(".scenario-delta");
  const oldNote = result.querySelector(".scenario-meta");
  if (oldDelta) oldDelta.remove();
  if (oldNote) oldNote.remove();
  renderScenarioComparison(lastScenario);
}

export function initMatchupTools() {
  const root = byId("matchup-tools");
  if (!root || root.dataset.initialized === "true") return;
  root.dataset.initialized = "true";
  ["compare-player-a", "compare-player-b", "scenario-hitter", "scenario-pitcher"].forEach((id) => {
    byId(id).addEventListener("change", () => {
      if (comparisonController) comparisonController.abort();
      if (scenarioController) scenarioController.abort();
      comparisonController = null;
      scenarioController = null;
      byId("compare-results").hidden = true;
      byId("scenario-results").hidden = true;
      byId("analyze-official-scenario").hidden = true;
      lastScenario = null;
      byId("run-hitter-comparison").textContent = "Comparar bateadores";
      byId("run-pitcher-scenario").textContent = "Proyectar escenario";
      const { players } = playersForSide(activeTeamSide);
      updateActionStates(players, pitchersForSide(opposingSide(activeTeamSide)));
    });
  });
  byId("run-hitter-comparison").addEventListener("click", () => void runComparison());
  byId("run-pitcher-scenario").addEventListener("click", () => void runPitcherScenario());
  byId("analyze-official-scenario").addEventListener("click", analyzeOfficialScenario);
  ["games-select", "window-select"].forEach((id) => {
    byId(id).addEventListener("change", () => {
      if (comparisonController) comparisonController.abort();
      if (scenarioController) scenarioController.abort();
      comparisonController = null;
      scenarioController = null;
      byId("compare-results").hidden = true;
      byId("scenario-results").hidden = true;
      byId("analyze-official-scenario").hidden = true;
      lastScenario = null;
      byId("run-hitter-comparison").textContent = "Comparar bateadores";
      byId("run-pitcher-scenario").textContent = "Proyectar escenario";
      setStatus("compare-status", "La ventana cambió; vuelve a comparar con los mismos parámetros.");
      setStatus("scenario-status", "La ventana cambió; vuelve a calcular el escenario.");
      const { players } = playersForSide(activeTeamSide);
      updateActionStates(players, pitchersForSide(opposingSide(activeTeamSide)));
    });
  });

  window.addEventListener("diamondiq:game-context", (event) => {
    if (comparisonController) comparisonController.abort();
    if (scenarioController) scenarioController.abort();
    comparisonController = null;
    scenarioController = null;
    activeContext = event.detail && event.detail.context || null;
    activeTeamSide = event.detail && event.detail.side === "home" ? "home" : "away";
    if (!activeContext) return;
    root.hidden = false;
    renderLineScore(activeContext);
    renderSelectors(activeTeamSide);
  });
  window.addEventListener("diamondiq:team-side", (event) => {
    const side = event.detail && event.detail.side;
    if (!activeContext || !["away", "home"].includes(side)) return;
    if (comparisonController) comparisonController.abort();
    if (scenarioController) scenarioController.abort();
    comparisonController = null;
    scenarioController = null;
    renderSelectors(side);
  });
  window.addEventListener("diamondiq:game-cleared", () => {
    if (comparisonController) comparisonController.abort();
    if (scenarioController) scenarioController.abort();
    comparisonController = null;
    scenarioController = null;
    activeContext = null;
    lastScenario = null;
    root.hidden = true;
  });
  window.addEventListener("diamondiq:analysis-result", (event) => {
    latestOfficialAnalysis = event.detail && event.detail.analysis || null;
    updateScenarioComparison();
  });
}
