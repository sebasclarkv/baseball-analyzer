function record(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function person(value) { return record(value) && Number.isInteger(value.id) && Number(value.id) > 0 && typeof value.name === 'string'; }
function game(value) { return record(value) && Number.isInteger(value.id) && Number(value.id) > 0 && person(value.home) && person(value.away) && typeof value.status_code === 'string' && typeof value.status === 'string' && record(value.venue); }
function lineup(value) { return person(value) && record(value) && ['confirmed', 'pending'].includes(String(value.lineup_status)) && Array.isArray(value.lineup) && value.lineup.every(person) && Array.isArray(value.roster) && value.roster.every(person); }
async function read(path, signal) {
    const response = await fetch(path, { signal, headers: { Accept: 'application/json' } });
    const body = await response.json();
    if (!response.ok) {
        const detail = record(body) && typeof body.detail === 'string' ? body.detail : 'No se pudo consultar la fuente pública.';
        throw new Error(detail);
    }
    return body;
}
export async function loadGamesApi(day, signal) {
    const payload = await read('/api/games?date=' + encodeURIComponent(day), signal);
    if (!record(payload) || typeof payload.date !== 'string' || !Array.isArray(payload.games) || !payload.games.every(game)) {
        throw new Error('La jornada llegó incompleta. Puedes continuar con la búsqueda manual.');
    }
    return payload;
}
export async function loadContextApi(id, signal) {
    const payload = await read('/api/games/' + encodeURIComponent(id) + '/context', signal);
    if (!record(payload) || !game(payload.game) || !record(payload.teams) || !lineup(payload.teams.home) || !lineup(payload.teams.away)) {
        throw new Error('El contexto del partido llegó incompleto; no se pueden confirmar sus titulares.');
    }
    return payload;
}
