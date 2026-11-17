const button = document.getElementById("update-prospective");
const status = document.getElementById("prospective-status");
const results = document.getElementById("prospective-results");
const decimal = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 3 });
const factorNames = { lineup: "Orden al bate", starter: "Abridor", bullpen: "Bullpen" };

async function request(path, method = "GET") {
  const response = await fetch(path, { method, headers: { Accept: "application/json" } });
  const body = await response.json();
  if (!response.ok) {
    const explanation = typeof body.detail === "string" ? body.detail : "No se pudo consultar el cruce de resultados.";
    throw new Error(explanation);
  }
  return body;
}

function render(report) {
  results.replaceChildren();
  status.textContent = report.predictions
    ? `${report.predictions} pronósticos comparados con resultados finales.`
    : "Por ahora no hay pronósticos elegibles con un resultado final de MLB.";
  for (const [version, summary] of Object.entries(report.by_model_version || {})) {
    const block = document.createElement("p");
    block.className = "cohort-report-overview";
    block.textContent = `Modelo ${version} · ${summary.games} juegos · ${summary.players} bateadores · error medio de ${decimal.format(summary.metrics.mean_absolute_error_hits)} hits.`;
    const factors = document.createElement("ul");
    factors.className = "cohort-scope";
    for (const [factor, label] of Object.entries(factorNames)) {
      const pair = summary.factor_comparisons[factor];
      if (!pair?.paired_predictions) continue;
      const item = document.createElement("li");
      const withFactor = decimal.format(pair.with_factor.ranked_probability_score);
      const withoutFactor = decimal.format(pair.without_factor.ranked_probability_score);
      item.textContent = `${label} · error de probabilidad ${withFactor} con el factor y ${withoutFactor} al retirarlo (${pair.paired_predictions} pronósticos).`;
      factors.appendChild(item);
    }
    results.appendChild(block);
    if (factors.childElementCount) results.appendChild(factors);
  }
}

button.addEventListener("click", async () => {
  button.disabled = true;
  status.textContent = "Comprobando qué partidos ya terminaron…";
  try {
    const submitted = await request("/api/evaluation/prospective/jobs", "POST");
    const path = "/api/jobs/" + encodeURIComponent(submitted.id);
    const deadline = Date.now() + 3 * 60 * 1000;
    while (Date.now() < deadline) {
      const job = await request(path);
      if (job.status === "complete") {
        render(job.result);
        return;
      }
      if (job.status === "error") throw new Error(job.message);
      if (job.total) status.textContent = `Revisando pronósticos de MLB · ${job.completed} de ${job.total}.`;
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    status.textContent = "El cruce sigue en curso. El reporte estará disponible cuando termine.";
  } catch (error) {
    status.textContent = error.message || "No se pudo actualizar el reporte.";
  } finally {
    button.disabled = false;
  }
});

const details = button.closest("details");
function loadLastReport() {
  if (!details?.open) return;
  details.removeEventListener("toggle", loadLastReport);
  request("/api/evaluation/prospective?summary=true").then((report) => {
    if (!button.disabled && report.status !== "not_run") render(report);
  }).catch(() => {
    // The user's action reports failures when they request an update.
  });
}
details?.addEventListener("toggle", loadLastReport);
loadLastReport();
