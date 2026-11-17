const byId = (id) => document.getElementById(id);
const setText = (id, value) => { byId(id).textContent = value; };
const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
const valueOrDash = (value) => isNumber(value) ? String(value) : "—";
const percentFormat = new Intl.NumberFormat("es-MX", { style: "percent", maximumFractionDigits: 1 });
const decimalFormat = new Intl.NumberFormat("es-MX", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const clockFormat = new Intl.DateTimeFormat("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
const updateInterval = 15000;
const exampleGame = 777283;
const exampleMoments = [
  { label: "Antes del jonrón", at: "2025-07-01T20:45:40Z" },
  { label: "Después del jonrón", at: "2025-07-01T20:47:10Z" },
];

let session = null;
let selectedGame = null;

function textNode(tag, value, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

function formattedTime(value) {
  if (!value) return "";
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? "" : clockFormat.format(instant);
}

export function gameFollowKind(game) {
  const state = String(game?.status || "").toLowerCase();
  const code = String(game?.status_code || "").toLowerCase();
  if (/postpon|cancel|suspend/.test(state)) return null;
  if (code === "final" || /^final|game over|completed/.test(state)) return "final";
  if (code === "live" || code === "in_progress" || /in progress|in game|live|delay|challenge|review/.test(state)) return "live";
  return null;
}

function setGameButton(game) {
  const followKind = gameFollowKind(game);
  const button = byId("open-live-panel");
  button.hidden = !followKind;
  button.textContent = followKind === "final" ? "Ver cierre del juego" : "Ver en vivo";
}

function activePlayers(snapshot) {
  if (!snapshot?.game) return [];
  return ["away", "home"].flatMap((side) => snapshot.game[side]?.lineup || [])
    .filter((player) => player?.id && player.name);
}

function selectedPlayer(snapshot) {
  const playerId = byId("live-player-select").value;
  return activePlayers(snapshot).find((player) => String(player.id) === (playerId || String(snapshot?.batter?.id || ""))) ||
    (!playerId ? snapshot?.batter : null);
}

function updatePlayerSelector(state) {
  const { snapshot } = state;
  const selector = byId("live-player-select");
  const options = activePlayers(snapshot);
  const selection = selector.value;
  const optionKey = JSON.stringify([snapshot.batter?.id, snapshot.batter?.name, options.map(({ id, name }) => [id, name])]);
  if (optionKey !== state.optionKey) {
    state.optionKey = optionKey;
    const auto = textNode("option", snapshot.batter?.name ? `Al bate · ${snapshot.batter.name}` : "Elige un bateador");
    auto.value = "";
    selector.replaceChildren(auto);
    const added = new Set();
    for (const side of ["away", "home"]) {
      const team = snapshot.game[side];
      const group = document.createElement("optgroup");
      group.label = team?.abbreviation || team?.name || (side === "away" ? "Visitante" : "Local");
      for (const player of (team?.lineup || [])) {
        if (!player?.id || !player.name || added.has(player.id)) continue;
        const option = textNode("option", player.name);
        option.value = player.id;
        group.appendChild(option);
        added.add(player.id);
      }
      if (group.children.length) selector.appendChild(group);
    }
    selector.value = Array.from(selector.options).some((option) => option.value === selection) ? selection : "";
    if (selection && !selector.value) setText("live-estimate-status", "El bateador ya no está en la alineación activa.");
  }
  const player = selectedPlayer(snapshot);
  const hasTotals = isNumber(player?.hits) && isNumber(player?.plate_appearances);
  setText("live-player-totals", hasTotals
    ? `${player.name} · ${player.hits} ${player.hits === 1 ? "hit" : "hits"} en ${player.plate_appearances} PA en este juego.`
    : player ? `Totales de ${player.name} aún sin confirmar.` : "Elige un bateador para consultar sus hits y apariciones.");
}

function renderTeam(side, snapshot) {
  const team = snapshot.game[side];
  const row = byId(`live-${side}-team`);
  const teamLabel = textNode("div", "", "live-team-label");
  const abbreviation = textNode("span", team?.abbreviation || (side === "away" ? "VIS" : "LOC"), "live-team-code");
  if (team?.name) abbreviation.setAttribute("aria-hidden", "true");
  teamLabel.append(abbreviation, textNode("strong", team?.name || (side === "away" ? "Visitante" : "Local"), "live-team-name"));
  row.dataset.batting = String(snapshot.batting_side === side && snapshot.inning_state === "playing");
  row.replaceChildren(teamLabel, textNode("strong", valueOrDash(team?.score), "live-runs"));
}

function renderSituation(snapshot) {
  const between = snapshot.inning_state === "between";
  const half = snapshot.half === "top" ? "Alta" : snapshot.half === "bottom" ? "Baja" : "Entrada";
  setText("live-inning", between ? `Entre entradas${snapshot.inning ? ` · ${snapshot.inning}ª` : ""}`
    : isNumber(snapshot.inning) ? `${half} de la ${snapshot.inning}ª` : "Entrada por confirmar");
  setText("live-outs", between ? "—" : valueOrDash(snapshot.count?.outs));
  setText("live-balls", snapshot.plate_appearance_active ? valueOrDash(snapshot.count?.balls) : "—");
  setText("live-strikes", snapshot.plate_appearance_active ? valueOrDash(snapshot.count?.strikes) : "—");
  const runners = [];
  ["first", "second", "third"].forEach((base, index) => {
    const runner = between ? null : snapshot.bases?.[base];
    byId(`live-base-${index + 1}`).classList.toggle("occupied", Boolean(runner));
    if (runner) runners.push(`${["1ª", "2ª", "3ª"][index]} ${runner.name || "ocupada"}`);
  });
  setText("live-bases-label", runners.join(" · ") || (snapshot.inning_state === "playing" && snapshot.bases ? "Bases vacías" : "Corredores —"));
  setText("live-batter", snapshot.batter?.name || (between ? "Entre entradas" : "Turno por confirmar"));
  const hand = { L: "zurdo", R: "derecho" }[snapshot.pitcher?.hand];
  setText("live-pitcher", snapshot.pitcher?.name ? `${snapshot.pitcher.name}${hand ? ` · ${hand}` : ""}` : "Pitcher por confirmar");
}

function renderWin(snapshot) {
  const win = snapshot.win_probability;
  const grid = byId("live-win-grid");
  const meter = byId("live-win-meter");
  const hasWin = isNumber(win?.away) && isNumber(win?.home);
  grid.hidden = !hasWin;
  meter.hidden = !hasWin;
  if (!hasWin) {
    const unavailable = snapshot.warnings?.find((notice) => notice.includes("probabilidad"));
    setText("live-win-note", unavailable || "MLB aún no publicó esta probabilidad para la jugada consultada.");
    return;
  }
  grid.replaceChildren(...["away", "home"].map((side) => {
    const item = textNode("div", "", "live-win-side");
    const change = win[`${side}_change`];
    item.append(textNode("span", snapshot.game[side]?.abbreviation || snapshot.game[side]?.name || "Equipo"),
      textNode("strong", percentFormat.format(win[side])));
    if (isNumber(change)) {
      const points = change * 100;
      item.append(textNode("small", `${points > 0 ? "+" : ""}${decimalFormat.format(points)} pp`));
    }
    return item;
  }));
  meter.children[0].style.width = `${win.away * 100}%`;
  meter.children[1].style.width = `${win.home * 100}%`;
  const playTime = formattedTime(win.play_at);
  const sourceStatus = win.source?.stale ? " · dato guardado" : "";
  setText("live-win-note", `MLB tras el último turno con probabilidad publicada${playTime ? ` · ${playTime}` : ""}${sourceStatus}. El cambio se muestra en puntos porcentuales.`);
}

function addEstimateDetails(root, estimate) {
  const sample = estimate.sample || {};
  const notes = [...(estimate.warnings || []), ...(estimate.assumptions || [])];
  if (isNumber(sample.batter_pa)) notes.unshift(`Historial del bateador · ${sample.batter_pa} apariciones.`);
  if (isNumber(sample.pitcher_pa) && sample.pitcher_applied) notes.push(`Historial del pitcher · ${sample.pitcher_pa} apariciones rivales.`);
  if (!notes.length) return;
  const detail = document.createElement("details");
  const list = document.createElement("ul");
  list.append(...notes.map((note) => textNode("li", note)));
  detail.append(textNode("summary", "Muestra y supuestos"), list);
  root.appendChild(detail);
}

function renderEstimate(payload) {
  const estimate = payload.estimate || {};
  const result = byId("live-estimate-result");
  result.dataset.current = "true";
  result.replaceChildren();
  if (!isNumber(estimate.next_pa?.probability)) {
    result.append(textNode("p", "No hay muestra suficiente para estimar un hit de este bateador en esta situación."));
  } else {
    const next = estimate.next_pa;
    const matchup = payload.pitcher?.name || estimate.pitcher?.name;
    const hitter = payload.player?.name || estimate.player?.name || "Bateador";
    const label = next.scope === "current" ? "en este turno" : "en su próxima aparición";
    const summary = textNode("p", "", "live-estimate-summary");
    summary.append(textNode("strong", percentFormat.format(next.probability)), textNode("span", ` de hit ${label}`));
    result.append(textNode("p", `${hitter}${matchup ? ` vs. ${matchup}` : ""}`), summary);
    if (payload.pitcher_reference === "last_known" && matchup) result.append(textNode("p", "Se usa el último pitcher publicado para el rival."));
    if (next.count_applied && next.count) {
      const reference = isNumber(estimate.rate) ? ` Referencia al inicio del turno ${percentFormat.format(estimate.rate)}.` : "";
      result.append(textNode("p", `Incluye la cuenta ${next.count.balls}–${next.count.strikes}.${reference}`));
    } else if (next.scope === "current" && ["insufficient", "unavailable"].includes(next.count_status)) {
      result.append(textNode("p", "El cálculo usa la referencia al inicio del turno. Faltan datos para ajustar la cuenta."));
    }
  }
  if (estimate.scenarios?.length) {
    const list = textNode("ul", "", "live-estimate-scenarios");
    for (const entry of estimate.scenarios) {
      const option = textNode("li", `${entry.plate_appearances} ${entry.plate_appearances === 1 ? "aparición" : "apariciones"}`);
      option.append(textNode("strong", isNumber(entry.probability_one_or_more_hits) ? percentFormat.format(entry.probability_one_or_more_hits) : "—"));
      list.append(option);
    }
    result.append(textNode("p", "Probabilidad de al menos un hit adicional si disputa estas apariciones."), list);
  }
  addEstimateDetails(result, estimate);
  result.hidden = false;
}

function updateEstimateState(state) {
  const phase = state.snapshot?.game?.phase;
  const canAnalyze = phase === "live" && !state.snapshot?.source?.stale && !state.error;
  const result = byId("live-estimate-result");
  byId("live-estimate").disabled = !canAnalyze || !selectedPlayer(state.snapshot) || Boolean(state.hitterRequest);
  byId("live-estimate").textContent = state.hitterRequest ? "Estimando…" : state.estimate ? "Recalcular" : "Estimar hits";
  if (state.estimate) {
    const oldEstimate = state.estimate.snapshot_id !== state.snapshot?.snapshot_id || !canAnalyze;
    result.dataset.current = String(!oldEstimate);
    if (oldEstimate) {
      const time = formattedTime(state.estimate.observed_at);
      setText("live-estimate-status", `Cálculo anterior${time ? ` · ${time}` : ""}. Recalcula con la situación actual.`);
    }
  }
}

function updateStatus(state) {
  const snapshot = state.snapshot;
  const phase = snapshot?.game?.phase;
  const stale = Boolean(state.error || snapshot?.source?.stale);
  byId("live-panel").dataset.stale = String(stale);
  byId("live-panel").dataset.phase = state.at ? "replay" : phase || "loading";
  byId("live-refresh").hidden = Boolean(state.at) && !state.error;
  byId("live-refresh").disabled = Boolean(state.feedRequest);
  byId("live-pause").hidden = Boolean(state.at) || (phase && !["live", "paused"].includes(phase));
  byId("live-pause").textContent = state.paused ? "Reanudar actualizaciones" : "Pausar actualizaciones";
  setText("live-mode", state.at ? "Repetición" : ({ live: "En vivo", final: "Final", paused: "Juego demorado", preview: "Previa", unavailable: "Seguimiento" }[phase] || "Seguimiento"));
  const messages = [];
  if (state.error) messages.push(snapshot ? `${state.error} Se conserva el último dato consultado.` : state.error);
  else if (!snapshot) messages.push("Consultando el estado del partido…");
  else if (snapshot.source?.stale) messages.push("MLB no actualizó la consulta. Se conserva el último dato guardado.");
  else if (phase === "final") messages.push("Resultado final publicado por MLB.");
  else if (phase === "paused") messages.push("El juego está demorado según MLB.");
  else if (phase === "preview") messages.push("MLB aún no publica actividad de este juego.");
  else if (phase === "unavailable") messages.push("MLB no ofrece seguimiento activo en este estado.");
  if (state.paused && ["live", "paused"].includes(phase)) messages.push("Actualizaciones pausadas.");
  if (snapshot && !state.at && !state.paused && ["live", "paused"].includes(phase) && !stale) messages.push("Consulta automática cada 15 segundos.");
  if (state.at && snapshot && !stale) messages.push("Juego finalizado el 1 de julio de 2025. Explora dos momentos de la cuarta entrada.");
  const status = byId("live-status");
  const message = messages.join(" ");
  if (status.textContent !== message) status.textContent = message;
  updateEstimateState(state);
}

function renderSnapshot(state) {
  const snapshot = state.snapshot;
  const { game } = snapshot;
  setText("live-title", `${game.away?.name || "Visitante"} contra ${game.home?.name || "Local"}`);
  byId("live-content").hidden = false;
  renderTeam("away", snapshot);
  renderTeam("home", snapshot);
  renderSituation(snapshot);
  const active = game.phase === "live" || game.phase === "paused";
  ["live-situation", "live-matchup", "live-win-panel"].forEach((id) => { byId(id).hidden = !active; });
  byId("live-hitter-panel").hidden = game.phase !== "live";
  setText("live-last-play", snapshot.last_play?.description || "MLB aún no publica una jugada completa.");
  const eventTime = formattedTime(snapshot.snapshot_at);
  const retrievedTime = state.at ? "" : formattedTime(snapshot.source?.fetched_at);
  setText("live-times", [snapshot.source?.name || "MLB", eventTime ? `Datos del proveedor · ${eventTime}` : "", retrievedTime ? `Consultado · ${retrievedTime}` : ""].filter(Boolean).join(" · "));
  if (active) renderWin(snapshot);
  updatePlayerSelector(state);
  updateStatus(state);
}

function clearRefresh(state) {
  if (state.timer) clearTimeout(state.timer);
  state.timer = null;
}

function scheduleRefresh(state) {
  clearRefresh(state);
  const phase = state.snapshot?.game?.phase;
  if (session !== state || state.at || state.paused || document.hidden || navigator.onLine === false ||
      (phase && !["live", "paused"].includes(phase))) return;
  state.timer = setTimeout(() => void fetchSnapshot(state), updateInterval);
}

async function requestJson(url, options) {
  const response = await fetch(url, { ...options, cache: "no-store", headers: { Accept: "application/json", ...options.headers } });
  let payload;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok || !payload) {
    const error = new Error(typeof payload?.detail === "string" ? payload.detail : "No se pudo actualizar el seguimiento de MLB.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function liveUrl(state, hitter = false) {
  const path = `/api/games/${encodeURIComponent(state.gameId)}/live${hitter ? "/hitter" : ""}`;
  return state.at ? `${path}?at=${encodeURIComponent(state.at)}` : path;
}

async function fetchSnapshot(state) {
  if (session !== state || state.feedRequest) return;
  clearRefresh(state);
  if (navigator.onLine === false) {
    state.error = "Sin conexión.";
    updateStatus(state);
    return;
  }
  const controller = new AbortController();
  state.feedRequest = controller;
  const timeout = setTimeout(() => controller.abort(), 12000);
  updateStatus(state);
  try {
    const snapshot = await requestJson(liveUrl(state), { signal: controller.signal });
    if (session !== state || controller.signal.aborted) return;
    if (Number(snapshot.game?.id) !== state.gameId || !snapshot.snapshot_id) throw new Error("La fuente no devolvió una foto válida de este partido.");
    state.snapshot = snapshot;
    state.error = "";
    renderSnapshot(state);
  } catch (error) {
    if (session !== state) return;
    state.error = error.name === "AbortError" ? "MLB no respondió a tiempo." : error.message || "No se pudo consultar MLB.";
  } finally {
    clearTimeout(timeout);
    state.feedRequest = null;
    if (session === state) {
      updateStatus(state);
      scheduleRefresh(state);
    }
  }
}

function cancelHitter(state) {
  state.hitterRequest?.abort();
  state.hitterRequest = null;
}

async function fetchHitter(state) {
  if (!state.snapshot || state.hitterRequest || !selectedPlayer(state.snapshot)) return;
  const controller = new AbortController();
  state.hitterRequest = controller;
  const requestedSnapshot = state.snapshot.snapshot_id;
  const selected = byId("live-player-select").value;
  const body = { snapshot_id: requestedSnapshot, ...(selected ? { player_id: Number(selected) } : {}) };
  const timeout = setTimeout(() => controller.abort(), 45000);
  setText("live-estimate-status", "Consultando historial de Statcast para este ajuste…");
  updateEstimateState(state);
  try {
    const result = await requestJson(liveUrl(state, true), {
      method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (session !== state || state.hitterRequest !== controller || controller.signal.aborted) return;
    if (requestedSnapshot !== state.snapshot?.snapshot_id || result.snapshot_id !== requestedSnapshot) {
      setText("live-estimate-status", "La situación cambió durante el cálculo. Vuelve a calcular con el marcador actualizado.");
      return;
    }
    state.estimate = result;
    const observed = formattedTime(result.observed_at);
    setText("live-estimate-status", observed ? `Calculado a partir de la situación publicada a las ${observed}.` : "Estimación de la situación consultada.");
    renderEstimate(result);
  } catch (error) {
    if (session !== state || state.hitterRequest !== controller) return;
    if (error.status === 409) {
      setText("live-estimate-status", "MLB cambió la situación. Actualizando el marcador para que puedas recalcular.");
      void fetchSnapshot(state);
    } else {
      setText("live-estimate-status", error.name === "AbortError" ? "Statcast no respondió a tiempo. Puedes reintentar." : error.message || "No se pudo estimar el turno.");
    }
  } finally {
    clearTimeout(timeout);
    if (session === state && state.hitterRequest === controller) {
      state.hitterRequest = null;
      updateEstimateState(state);
    }
  }
}

function closeSession({ restoreFocus = false } = {}) {
  const old = session;
  session = null;
  if (old) {
    clearRefresh(old);
    old.feedRequest?.abort();
    cancelHitter(old);
  }
  byId("live-panel").hidden = true;
  if (restoreFocus && old?.trigger && !old.trigger.hidden) old.trigger.focus();
}

function openSession(gameId, { at = null, trigger = null, focus = true } = {}) {
  closeSession();
  session = { gameId: Number(gameId), at, trigger, paused: false, error: "", snapshot: null, timer: null, estimate: null, optionKey: "" };
  const section = byId("live-panel");
  section.hidden = false;
  byId("live-content").hidden = true;
  byId("live-estimate-result").hidden = true;
  byId("live-player-select").replaceChildren();
  setText("live-estimate-status", "");
  setText("live-title", at ? "Yankees contra Blue Jays" : "Seguimiento del juego");
  byId("live-replay-switch").hidden = !at;
  for (const choice of byId("live-replay-switch").children) choice.setAttribute("aria-pressed", String(choice.dataset.at === at));
  updateStatus(session);
  if (focus) {
    byId("live-title").focus({ preventScroll: true });
    section.scrollIntoView({ block: "start" });
  }
  void fetchSnapshot(session);
}

export function initLiveBoard() {
  const exampleTrigger = byId("try-live-replay");
  const selectedTrigger = byId("open-live-panel");
  if (!exampleTrigger || !selectedTrigger) return;
  for (const moment of exampleMoments) {
    const button = textNode("button", moment.label, "quiet-button");
    button.type = "button";
    button.dataset.at = moment.at;
    button.addEventListener("click", () => openSession(exampleGame, { at: moment.at, trigger: exampleTrigger, focus: false }));
    byId("live-replay-switch").appendChild(button);
  }
  exampleTrigger.addEventListener("click", () => {
    if (selectedGame) byId("clear-game-context").click();
    openSession(exampleGame, { at: exampleMoments[0].at, trigger: exampleTrigger });
  });
  selectedTrigger.addEventListener("click", () => {
    if (selectedGame) openSession(selectedGame.id, { trigger: selectedTrigger });
  });
  byId("close-live-panel").addEventListener("click", () => closeSession({ restoreFocus: true }));
  byId("live-refresh").addEventListener("click", () => { if (session) void fetchSnapshot(session); });
  byId("live-pause").addEventListener("click", () => {
    if (!session) return;
    session.paused = !session.paused;
    clearRefresh(session);
    updateStatus(session);
    if (!session.paused) void fetchSnapshot(session);
  });
  byId("live-estimate").addEventListener("click", () => { if (session) void fetchHitter(session); });
  byId("live-player-select").addEventListener("change", () => {
    if (!session?.snapshot) return;
    cancelHitter(session);
    session.estimate = null;
    byId("live-estimate-result").hidden = true;
    setText("live-estimate-status", "");
    updatePlayerSelector(session);
    updateEstimateState(session);
  });
  window.addEventListener("diamondiq:game-cleared", () => {
    selectedGame = null;
    setGameButton(null);
    closeSession();
  });
  window.addEventListener("diamondiq:game-selected", (event) => {
    selectedGame = event.detail.game;
    setGameButton(selectedGame);
    if (gameFollowKind(selectedGame) === "live") {
      openSession(selectedGame.id, { trigger: selectedTrigger });
    }
  });
  window.addEventListener("diamondiq:game-context", (event) => {
    selectedGame = event.detail.context.game;
    setGameButton(selectedGame);
  });
  window.addEventListener("offline", () => {
    if (!session || session.at) return;
    clearRefresh(session);
    session.error = "Sin conexión.";
    updateStatus(session);
  });
  window.addEventListener("online", () => { if (session && !session.at && !session.paused && session.snapshot?.game?.phase !== "final") void fetchSnapshot(session); });
  document.addEventListener("visibilitychange", () => {
    if (!session || session.at) return;
    if (document.hidden) clearRefresh(session);
    else if (!session.paused && (!session.snapshot || ["live", "paused"].includes(session.snapshot.game.phase))) void fetchSnapshot(session);
  });
}
