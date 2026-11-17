/** Typed public-source boundaries. Compiled JS is committed; Python users need no Node. */
export interface Person { id: number; name: string }
export interface Team { id: number; name: string; abbreviation?: string; score?: number | null; probable_pitcher?: Person | null }
export interface Game { id: number; start_time?: string | null; official_date?: string; game_type?: string | null; status: string; status_code: string; venue: {id?: number; name?: string}; away: Team; home: Team }
export interface Source { name: string; fetched_at: string; stale: boolean }
export interface Schedule { date: string; games: Game[]; source: Source; warnings: string[] }
export interface LineupPlayer extends Person { position?: string; batting_order?: number; is_pitcher?: boolean }
export interface Lineup { id: number; name: string; lineup_status: 'confirmed' | 'pending'; lineup: LineupPlayer[]; roster: LineupPlayer[] }
export interface Weather { status: 'available' | 'unavailable' | 'not_applicable'; temperature_c?: number | null; wind_kmh?: number | null; wind_direction?: number | null; roof: string; applied_to_projection: false; message?: string; source?: Source }
export interface GameContext { game: Game; teams: {home: Lineup; away: Lineup}; innings: {number: number; away: number | null; home: number | null}[]; weather: Weather; source: Source; warnings: string[]; as_of: string }
export interface HitDistribution { probability_zero_hits: number; probability_exactly_one_hit: number; probability_two_or_more_hits: number; predictive_interval_hits: {low: number; high: number; level: number; calibrated: boolean} }

function record(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function person(value: unknown): value is Person { return record(value) && Number.isInteger(value.id) && Number(value.id) > 0 && typeof value.name === 'string'; }
function game(value: unknown): value is Game { return record(value) && Number.isInteger(value.id) && Number(value.id) > 0 && person(value.home) && person(value.away) && typeof value.status_code === 'string' && typeof value.status === 'string' && record(value.venue); }
function lineup(value: unknown): value is Lineup { return person(value) && record(value) && ['confirmed', 'pending'].includes(String(value.lineup_status)) && Array.isArray(value.lineup) && value.lineup.every(person) && Array.isArray(value.roster) && value.roster.every(person); }

async function read(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, {signal, headers: {Accept: 'application/json'}});
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail = record(body) && typeof body.detail === 'string' ? body.detail : 'No se pudo consultar la fuente pública.';
    throw new Error(detail);
  }
  return body;
}

export async function loadGamesApi(day: string, signal?: AbortSignal): Promise<Schedule> {
  const payload = await read('/api/games?date=' + encodeURIComponent(day), signal);
  if (!record(payload) || typeof payload.date !== 'string' || !Array.isArray(payload.games) || !payload.games.every(game)) {
    throw new Error('La jornada llegó incompleta. Puedes continuar con la búsqueda manual.');
  }
  return payload as unknown as Schedule;
}

export async function loadContextApi(id: number, signal?: AbortSignal): Promise<GameContext> {
  const payload = await read('/api/games/' + encodeURIComponent(id) + '/context', signal);
  if (!record(payload) || !game(payload.game) || !record(payload.teams) || !lineup(payload.teams.home) || !lineup(payload.teams.away)) {
    throw new Error('El contexto del partido llegó incompleto; no se pueden confirmar sus titulares.');
  }
  return payload as unknown as GameContext;
}
