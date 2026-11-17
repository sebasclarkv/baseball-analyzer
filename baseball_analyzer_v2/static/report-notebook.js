const backupFormat = "diamond-iq-report-notebook";
const backupVersion = 1;

const isRecord = (value) => value && typeof value === "object" && !Array.isArray(value);
const isPositiveId = (value) => (typeof value === "number" || (typeof value === "string" && /^\d+$/.test(value))) &&
  Number.isSafeInteger(Number(value)) && Number(value) > 0;
const isFiniteNumber = (value) => typeof value === "number" && Number.isFinite(value);
const isDate = (value) => typeof value === "string" && !Number.isNaN(Date.parse(value));
const isOptionalRecord = (value) => value == null || isRecord(value);
const isOptionalList = (value, check) => value == null || (Array.isArray(value) && value.every(check));

export function reportIdentity(analysis) {
  const playerId = analysis.player && analysis.player.id;
  const pitcherId = analysis.opponent_pitcher && analysis.opponent_pitcher.id || 0;
  const gameId = analysis.game_context && analysis.game_context.game &&
    analysis.game_context.game.id || 0;
  const window = analysis.window || {};
  return [playerId, window.games_requested || 0, window.lookback_days || 0, pitcherId, gameId].join(":");
}

export function normalizeReport(record) {
  if (!record || !record.analysis || !record.analysis.player || !record.analysis.window) return null;
  return {
    id: reportIdentity(record.analysis),
    saved_at: isDate(record.saved_at) ? record.saved_at : new Date().toISOString(),
    pinned: record.pinned === true,
    analysis: record.analysis,
  };
}

export function newestReports(records) {
  return [...records].sort((a, b) => Date.parse(b.saved_at) - Date.parse(a.saved_at));
}

export function mergeReport(current, incoming) {
  if (!current) return incoming;
  const newest = Date.parse(incoming.saved_at) > Date.parse(current.saved_at) ? incoming : current;
  const pinned = current.pinned === true || incoming.pinned === true;
  return newest.pinned === pinned ? newest : { ...newest, pinned };
}

export function encodeReportBackup(reports) {
  return JSON.stringify({
    format: backupFormat,
    version: backupVersion,
    created_at: new Date().toISOString(),
    reports,
  }, null, 2);
}

function validMatchup(matchup) {
  if (matchup == null) return true;
  if (!isRecord(matchup)) return false;
  const repertoire = matchup.pitch_repertoire;
  if (repertoire != null && (Array.isArray(repertoire)
    ? !repertoire.every(isRecord)
    : !isRecord(repertoire) || !isOptionalList(repertoire.pitches, isRecord))) return false;
  return matchup.bullpen == null || (isRecord(matchup.bullpen) &&
    isOptionalList(matchup.bullpen.relievers, isRecord));
}

function validAnalysis(analysis) {
  if (!isRecord(analysis)) return false;
  const { player, window, per_plate_appearance: rates, projection, contact, sample, trend } = analysis;
  if (!isRecord(player) || !isPositiveId(player.id) || typeof player.name !== "string" || !player.name.trim() ||
      !isRecord(window) || !isPositiveId(window.games_requested) || !isPositiveId(window.lookback_days) ||
      !isRecord(projection) || !isFiniteNumber(projection.hits) || !isFiniteNumber(projection.plate_appearances) ||
      !isRecord(contact) || !isRecord(sample) || typeof sample.confidence !== "string" || !isRecord(rates) ||
      !Array.isArray(trend) || !trend.every((point) => isRecord(point) && isDate(point.date) && isFiniteNumber(point.hit_rate))) {
    return false;
  }
  if (!["hits", "home_runs", "walks", "strikeouts", "outs"].every((key) =>
    isRecord(rates[key]) && isFiniteNumber(rates[key].rate))) return false;

  const opponent = analysis.opponent_pitcher;
  if (opponent != null && (!isRecord(opponent) || !isPositiveId(opponent.id) || typeof opponent.name !== "string")) return false;
  const context = analysis.game_context;
  if (context != null && (!isRecord(context) || !isRecord(context.game) || !isPositiveId(context.game.id))) return false;
  if (!isOptionalList(analysis.drivers, isRecord) || !isOptionalList(analysis.season_trend, isRecord) ||
      !isOptionalRecord(analysis.model) || !isOptionalRecord(analysis.explanation) ||
      !isOptionalRecord(analysis.data_quality) || !isOptionalRecord(analysis.validation)) return false;
  return validMatchup(analysis.matchup) && validMatchup(context?.matchup);
}

export function decodeReportBackup(contents) {
  let backup;
  try {
    backup = JSON.parse(contents);
  } catch (error) {
    throw new Error("El archivo debe ser un respaldo JSON de Diamond IQ.");
  }
  if (!isRecord(backup) || backup.format !== backupFormat || !Array.isArray(backup.reports)) {
    throw new Error("El archivo debe ser un respaldo de la libreta de reportes de Diamond IQ.");
  }
  if (backup.version !== backupVersion) throw new Error("Esta versión del respaldo no es compatible.");
  return backup.reports.map((record, index) => {
    if (!isRecord(record) || !isDate(record.saved_at) || !validAnalysis(record.analysis) ||
        (record.pinned !== undefined && typeof record.pinned !== "boolean")) {
      throw new Error("El respaldo contiene un reporte no válido en la posición " + (index + 1) + ".");
    }
    return normalizeReport(record);
  });
}
