"""Renderizado HTML profesional del Strategic Intelligence Brief.

Diseño dark premium estilo Bloomberg Terminal / Stratfor / Recorded Future.
Convierte el JSON analítico del motor en una experiencia visual ejecutiva.

Esta es la cara comercial del producto APURISK Intelligence — lo que un
CEO/COO/VP ve cuando consume el output del motor de inteligencia.
"""
from __future__ import annotations
import html as _html
from datetime import datetime


def _esc(s) -> str:
    return _html.escape(str(s or ""), quote=True)


def _fmt_iso(iso_str: str) -> str:
    """Formatea ISO 8601 a 'DD May 2026 · 14:30 PET'."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        meses = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                 "Jul", "Ago", "Set", "Oct", "Nov", "Dic"]
        return f"{dt.day:02d} {meses[dt.month-1]} {dt.year} · {dt.hour:02d}:{dt.minute:02d} PET"
    except Exception:
        return iso_str[:19]


def _color_nivel(nivel: str) -> str:
    """Retorna color hex según nivel de alerta."""
    return {
        "CRÍTICO": "#dc2626", "CRITICO": "#dc2626", "CRÍTICA": "#dc2626",
        "ALTO": "#ea580c", "ALTA": "#ea580c",
        "MEDIO": "#ca8a04", "MEDIA": "#ca8a04",
        "BAJO": "#16a34a", "BAJA": "#16a34a",
    }.get(nivel, "#3b82f6")


# =====================================================================
# CSS PREMIUM (dark theme estilo Bloomberg/Stratfor)
# =====================================================================

CSS = """
:root {
  --bg-0: #0a0e1a;
  --bg-1: #0f172a;
  --bg-2: #1e293b;
  --bg-3: #334155;
  --bg-card: rgba(15, 23, 42, 0.6);
  --txt-0: #f1f5f9;
  --txt-1: #cbd5e1;
  --txt-2: #94a3b8;
  --txt-3: #64748b;
  --accent: #38bdf8;
  --accent-2: #a78bfa;
  --gold: #d4af37;
  --crit: #dc2626;
  --high: #ea580c;
  --med: #ca8a04;
  --low: #16a34a;
  --border: #1e293b;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
  background: linear-gradient(180deg, #0a0e1a 0%, #0f172a 100%);
  color: var(--txt-0);
  font-size: 14px;
  min-height: 100vh;
  padding-bottom: 60px;
}
.container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px;
}

/* HEADER */
.brand-bar {
  background: linear-gradient(90deg, #0a0e1a 0%, #1e293b 100%);
  border-bottom: 2px solid var(--accent);
  padding: 18px 32px;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(8px);
}
.brand-bar-inner {
  max-width: 1280px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}
.brand-logo {
  width: 42px; height: 42px;
  border-radius: 6px;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  display: flex; align-items: center; justify-content: center;
  font-weight: 900; color: #0a0e1a; font-size: 18px;
  letter-spacing: -1px;
}
.brand-title {
  font-size: 18px; font-weight: 700; letter-spacing: -0.3px;
}
.brand-subtitle {
  font-size: 11px; color: var(--txt-2);
  text-transform: uppercase; letter-spacing: 2px;
}
.brand-meta {
  font-size: 11px; color: var(--txt-2); text-align: right;
  text-transform: uppercase; letter-spacing: 1px;
}
.brand-meta strong { color: var(--accent); font-size: 13px; letter-spacing: 0; }

/* HERO: Strategic Assessment */
.hero {
  background: linear-gradient(135deg, rgba(56,189,248,0.08) 0%, rgba(167,139,250,0.05) 100%);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 36px 40px;
  margin-top: 32px;
  position: relative;
}
.hero::before {
  content: "";
  position: absolute;
  top: 0; left: 0; bottom: 0;
  width: 4px;
  background: linear-gradient(180deg, var(--accent), var(--accent-2));
  border-radius: 12px 0 0 12px;
}
.hero-label {
  font-size: 10px; color: var(--accent);
  text-transform: uppercase; letter-spacing: 3px; font-weight: 600;
  margin-bottom: 14px;
}
.hero-text {
  font-size: 17px; line-height: 1.65;
  color: var(--txt-0); font-weight: 400;
  letter-spacing: -0.1px;
}

/* SECTION HEADER */
.section {
  margin-top: 40px;
}
.section-header {
  display: flex; align-items: baseline;
  justify-content: space-between;
  margin-bottom: 18px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--bg-3);
}
.section-title {
  font-size: 14px; font-weight: 700;
  color: var(--txt-0);
  text-transform: uppercase;
  letter-spacing: 2.5px;
}
.section-subtitle {
  font-size: 11px; color: var(--txt-2);
  font-style: italic;
}

/* CARDS GRID */
.grid {
  display: grid;
  gap: 16px;
}
.grid-2 { grid-template-columns: 1fr 1fr; }
.grid-3 { grid-template-columns: repeat(3, 1fr); }
.grid-4 { grid-template-columns: repeat(4, 1fr); }
@media (max-width: 900px) {
  .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
}

/* CARD GENÉRICO */
.card {
  background: var(--bg-card);
  border: 1px solid var(--bg-3);
  border-radius: 10px;
  padding: 22px 24px;
  transition: border-color 0.15s;
}
.card:hover { border-color: var(--bg-3); }
.card-label {
  font-size: 10px; color: var(--txt-2);
  text-transform: uppercase; letter-spacing: 2px; margin-bottom: 8px;
}
.card-value { font-size: 28px; font-weight: 700; line-height: 1.1; }
.card-meta { font-size: 12px; color: var(--txt-2); margin-top: 6px; }

/* THREAT CARDS (convergencias / anomalías) */
.threat-card {
  background: var(--bg-card);
  border: 1px solid var(--bg-3);
  border-left: 4px solid var(--med);
  border-radius: 6px;
  padding: 18px 22px;
}
.threat-card.crit { border-left-color: var(--crit); }
.threat-card.high { border-left-color: var(--high); }
.threat-card.med { border-left-color: var(--med); }
.threat-card.low { border-left-color: var(--low); }
.threat-card h3 {
  font-size: 13px; font-weight: 700;
  margin-bottom: 8px; letter-spacing: -0.1px;
}
.threat-card p { font-size: 13px; color: var(--txt-1); line-height: 1.55; }
.threat-card .meta { font-size: 11px; color: var(--txt-2); margin-top: 10px; font-style: italic; }

/* I&W SCENARIO */
.scenario {
  background: var(--bg-card);
  border: 1px solid var(--bg-3);
  border-radius: 8px;
  padding: 20px 22px;
  margin-bottom: 14px;
}
.scenario-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}
.scenario-name { font-size: 14px; font-weight: 700; }
.scenario-pill {
  font-size: 10px; padding: 4px 10px; border-radius: 12px;
  font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;
}
.scenario-bar {
  height: 4px; background: var(--bg-3); border-radius: 2px;
  overflow: hidden; margin-bottom: 12px;
}
.scenario-bar-fill {
  height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2));
}
.indicator { font-size: 12px; color: var(--txt-1); padding: 4px 0; }
.indicator.active { color: var(--txt-0); }
.indicator.active::before { content: "● "; color: var(--high); }
.indicator.latent { color: var(--txt-3); }
.indicator.latent::before { content: "○ "; color: var(--txt-3); }

/* STAKEHOLDER MOVEMENT */
.stakeholder-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid var(--bg-3);
}
.stakeholder-row:last-child { border-bottom: none; }
.stakeholder-name { font-size: 13px; font-weight: 600; }
.stakeholder-delta {
  font-size: 14px; font-weight: 700; padding: 4px 12px;
  border-radius: 4px; letter-spacing: -0.5px;
}
.stakeholder-delta.up { background: rgba(220,38,38,0.15); color: var(--crit); }
.stakeholder-delta.down { background: rgba(202,138,4,0.15); color: var(--med); }

/* RECOMMENDATION (caja final destacada) */
.recommendation {
  background: linear-gradient(135deg, rgba(212,175,55,0.10) 0%, rgba(167,139,250,0.05) 100%);
  border: 2px solid var(--gold);
  border-radius: 12px;
  padding: 32px 36px;
  margin-top: 40px;
}
.recommendation-label {
  font-size: 10px; color: var(--gold);
  text-transform: uppercase; letter-spacing: 3px;
  font-weight: 700; margin-bottom: 16px;
}
.recommendation-action {
  font-size: 18px; line-height: 1.55; font-weight: 600;
  color: var(--txt-0); margin-bottom: 18px;
}
.recommendation-meta {
  display: grid; grid-template-columns: 1fr 1fr; gap: 16px 32px;
  font-size: 12px;
}
.recommendation-meta dt {
  font-size: 10px; color: var(--txt-2);
  text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600;
  margin-bottom: 4px;
}
.recommendation-meta dd { color: var(--txt-1); font-size: 13px; }

/* TABLE */
table.bench {
  width: 100%; border-collapse: collapse;
  font-size: 13px; margin-top: 12px;
}
table.bench th {
  text-align: left; font-size: 10px;
  color: var(--txt-2); text-transform: uppercase; letter-spacing: 2px;
  padding: 10px 12px; border-bottom: 1px solid var(--bg-3); font-weight: 600;
}
table.bench td {
  padding: 12px; border-bottom: 1px solid var(--bg-2);
  color: var(--txt-1);
}
table.bench td.numeric {
  font-family: 'SF Mono', Menlo, monospace;
  font-variant-numeric: tabular-nums;
  font-weight: 600; color: var(--txt-0);
}

/* FOOTER */
.footer-note {
  margin-top: 60px;
  padding: 20px;
  border-top: 1px solid var(--bg-3);
  text-align: center;
  font-size: 11px; color: var(--txt-3); font-style: italic;
  line-height: 1.6;
}
.empty {
  color: var(--txt-3); font-style: italic;
  padding: 24px; text-align: center;
}

/* BACK BUTTON */
.back-link {
  display: inline-block;
  margin-top: 16px;
  color: var(--accent); text-decoration: none;
  font-size: 12px; letter-spacing: 1px;
}
.back-link:hover { text-decoration: underline; }
"""


def render_intelligence_html(brief: dict, snap: dict = None) -> str:
    """Renderiza el Strategic Intelligence Brief como página HTML premium."""
    snap = snap or {}
    riesgo = snap.get("riesgo") or {}
    score = riesgo.get("global", "—")
    nivel = riesgo.get("nivel", "—")

    convergencias = brief.get("convergencias", [])
    anomalias = brief.get("anomalias", [])
    silencios = brief.get("silencios_inusuales", [])
    iw = brief.get("indicators_warnings", {})
    sm = brief.get("stakeholder_movement", {})
    bench = brief.get("comparative_benchmark", {})
    reco = brief.get("strategic_recommendation", {})

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>APURISK Intelligence Platform — Strategic Brief</title>
<style>{CSS}</style>
</head>
<body>

<!-- BRAND BAR -->
<div class="brand-bar">
  <div class="brand-bar-inner">
    <div class="brand">
      <div class="brand-logo">A</div>
      <div>
        <div class="brand-title">APURISK Intelligence Platform</div>
        <div class="brand-subtitle">Strategic Risk Intelligence · Peru</div>
      </div>
    </div>
    <div class="brand-meta">
      Brief generado<br>
      <strong>{_esc(_fmt_iso(brief.get('generado','')))}</strong>
    </div>
  </div>
</div>

<div class="container">

  <!-- HERO: STRATEGIC ASSESSMENT -->
  <div class="hero">
    <div class="hero-label">▸ Strategic Assessment</div>
    <div class="hero-text">{_esc(brief.get('strategic_assessment','Sin datos suficientes para assessment.'))}</div>
  </div>

  <!-- KPI CARDS -->
  <div class="section">
    <div class="grid grid-4" style="margin-top:24px;">
      <div class="card">
        <div class="card-label">Score Político</div>
        <div class="card-value" style="color:{_color_nivel(nivel)};">{score}/100</div>
        <div class="card-meta">Nivel: {_esc(nivel)}</div>
      </div>
      <div class="card">
        <div class="card-label">Convergencias</div>
        <div class="card-value">{len(convergencias)}</div>
        <div class="card-meta">Factores agrupados</div>
      </div>
      <div class="card">
        <div class="card-label">Anomalías</div>
        <div class="card-value">{len(anomalias)}</div>
        <div class="card-meta">{'>'}2σ del baseline</div>
      </div>
      <div class="card">
        <div class="card-label">Silencios Institucionales</div>
        <div class="card-value">{len(silencios)}</div>
        <div class="card-meta">Actores con cobertura atípica</div>
      </div>
    </div>
  </div>

  <!-- CONVERGENCIAS -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">▸ Convergencias detectadas</div>
      <div class="section-subtitle">3+ factores moviéndose en misma dirección con delta ≥8 puntos</div>
    </div>
    {_render_convergencias(convergencias)}
  </div>

  <!-- ANOMALÍAS -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">▸ Anomalías Estadísticas</div>
      <div class="section-subtitle">Factores con desviación significativa del baseline histórico (z-score ≥ 2σ)</div>
    </div>
    {_render_anomalias(anomalias)}
  </div>

  <!-- INDICATORS & WARNINGS -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">▸ Indicators & Warnings (I&W)</div>
      <div class="section-subtitle">Doctrina clásica de inteligencia estratégica · Indicadores observables predictivos</div>
    </div>
    {_render_iw(iw)}
  </div>

  <!-- SILENCIOS -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">▸ Silencios Institucionales</div>
      <div class="section-subtitle">Actores con cobertura anormalmente baja respecto a promedio histórico</div>
    </div>
    {_render_silencios(silencios)}
  </div>

  <!-- STAKEHOLDER MOVEMENT -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">▸ Stakeholder Movement Map</div>
      <div class="section-subtitle">Cambios significativos de actividad mediática esta semana (±40%)</div>
    </div>
    {_render_stakeholder_movement(sm)}
  </div>

  <!-- COMPARATIVE BENCHMARK -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">▸ Comparative Benchmark</div>
      <div class="section-subtitle">Score actual vs historia propia (4w/12w) y región andina</div>
    </div>
    {_render_benchmark(bench)}
  </div>

  <!-- STRATEGIC RECOMMENDATION (caja destacada al final) -->
  {_render_recommendation(reco)}

  <!-- FOOTER -->
  <div class="footer-note">
    APURISK Intelligence Platform · {_esc(brief.get('doctrina',''))}<br>
    Ventana baseline: {brief.get('ventana_baseline_dias', 28)} días · Producto analítico OSINT<br>
    <a class="back-link" href="/dashboard">← Volver al Dashboard</a>
  </div>

</div>
</body>
</html>"""


def _render_convergencias(convergencias):
    if not convergencias:
        return '<div class="empty">Sin convergencias significativas en la ventana de análisis. Los factores de riesgo se mueven en patrones individuales no sistémicos.</div>'
    out = []
    for c in convergencias:
        dir_label = "ALZA" if c["direccion"] == "alza" else "BAJA"
        css_class = "crit" if c["direccion"] == "alza" and c["delta_promedio"] >= 12 else \
                    "high" if c["direccion"] == "alza" else "low"
        delta = c["delta_promedio"]
        factores_html = "".join(
            f'<div style="font-size:12px; color:var(--txt-2); padding:2px 0;">'
            f'• <strong style="color:var(--txt-1);">{_esc(f["nombre"])}</strong> '
            f'<span style="font-family:monospace; color:var(--{css_class});">{f["delta"]:+.1f}</span>'
            f'</div>'
            for f in c["factores"][:5]
        )
        out.append(f'''
        <div class="threat-card {css_class}" style="margin-bottom:14px;">
          <h3>↗ Convergencia al {_esc(dir_label.lower())}: {c['n_factores']} factores · delta promedio {delta:+.1f}pts</h3>
          <p>{_esc(c['interpretacion'])}</p>
          <div style="margin-top:12px; padding-top:12px; border-top:1px solid var(--bg-3);">
            <div style="font-size:10px; color:var(--txt-2); text-transform:uppercase; letter-spacing:1.5px; margin-bottom:8px;">Factores involucrados</div>
            {factores_html}
          </div>
        </div>
        ''')
    return "".join(out)


def _render_anomalias(anomalias):
    if not anomalias:
        return '<div class="empty">Sin anomalías estadísticas. Los factores se mantienen dentro del rango histórico típico.</div>'
    out = []
    for a in anomalias[:5]:
        z = a["z_score"]
        css = "crit" if abs(z) >= 3 else "high" if abs(z) >= 2 else "med"
        sign = "↑" if a["direccion"] == "alza" else "↓"
        out.append(f'''
        <div class="threat-card {css}" style="margin-bottom:12px;">
          <h3>{sign} {_esc(a['nombre'])} · z = {z:+.2f}σ</h3>
          <p>{_esc(a['interpretacion'])}</p>
          <div class="meta">Score actual: {a['score_actual']:.1f} · Media histórica: {a['media_historica']:.1f} · σ: {a['stdev_historica']:.2f}</div>
        </div>
        ''')
    return "".join(out)


def _render_iw(iw):
    if not iw:
        return '<div class="empty">Sin escenarios I&W disponibles.</div>'
    out = []
    for eid, e in iw.items():
        nivel = e.get("nivel_alerta", "BAJO")
        color = _color_nivel(nivel)
        pct = e.get("porcentaje_activacion", 0)
        indicadores_html = "".join(
            f'<div class="indicator {"active" if ind["estado"]=="activo" else "latent"}">'
            f'{_esc(ind["texto"])}'
            f'</div>'
            for ind in e.get("indicadores", [])
        )
        out.append(f'''
        <div class="scenario">
          <div class="scenario-head">
            <div class="scenario-name">{_esc(e['nombre'])}</div>
            <div class="scenario-pill" style="background:{color}33; color:{color};">
              {_esc(nivel)} · {e['n_activos']}/{e['n_total']} ({pct:.0f}%)
            </div>
          </div>
          <div class="scenario-bar"><div class="scenario-bar-fill" style="width:{pct}%;"></div></div>
          {indicadores_html}
        </div>
        ''')
    return "".join(out)


def _render_silencios(silencios):
    if not silencios:
        return '<div class="empty">Sin silencios institucionales detectados. Actores clave con cobertura normal.</div>'
    out = ['<div class="card" style="padding:8px 24px;">']
    for s in silencios[:7]:
        ratio = s["ratio"]
        out.append(f'''
        <div class="stakeholder-row">
          <div>
            <div class="stakeholder-name">{_esc(s['actor'])}</div>
            <div style="font-size:11px; color:var(--txt-2); margin-top:3px;">
              {s['menciones_periodo']} menciones · promedio histórico: {s['promedio_semanal_historico']}/sem
            </div>
          </div>
          <div class="stakeholder-delta down">ratio {ratio:.0%}</div>
        </div>
        ''')
    out.append('</div>')
    return "".join(out)


def _render_stakeholder_movement(sm):
    if not sm or (not sm.get("con_aumento") and not sm.get("con_descenso")):
        return '<div class="empty">Sin movimientos significativos de stakeholders esta semana.</div>'
    aumentos = sm.get("con_aumento", [])
    descensos = sm.get("con_descenso", [])
    aumentos_html = "".join(
        f'<div class="stakeholder-row">'
        f'<div class="stakeholder-name">{_esc(s["actor"])}<div style="font-size:11px; color:var(--txt-2); margin-top:3px;">{s["menciones_periodo"]} vs {s["menciones_anterior"]} previas</div></div>'
        f'<div class="stakeholder-delta up">+{s["cambio_pct"]:.0f}%</div>'
        f'</div>'
        for s in aumentos[:6]
    ) or '<div class="empty" style="padding:12px;">Sin incrementos detectados</div>'
    descensos_html = "".join(
        f'<div class="stakeholder-row">'
        f'<div class="stakeholder-name">{_esc(s["actor"])}<div style="font-size:11px; color:var(--txt-2); margin-top:3px;">{s["menciones_periodo"]} vs {s["menciones_anterior"]} previas</div></div>'
        f'<div class="stakeholder-delta down">{s["cambio_pct"]:.0f}%</div>'
        f'</div>'
        for s in descensos[:6]
    ) or '<div class="empty" style="padding:12px;">Sin descensos detectados</div>'
    return f'''
    <div class="grid grid-2">
      <div class="card" style="padding:8px 24px;">
        <div class="card-label" style="padding-top:12px;">↑ Incremento de Actividad</div>
        {aumentos_html}
      </div>
      <div class="card" style="padding:8px 24px;">
        <div class="card-label" style="padding-top:12px;">↓ Descenso de Actividad</div>
        {descensos_html}
      </div>
    </div>
    '''


def _render_benchmark(bench):
    if not bench:
        return '<div class="empty">Sin datos de benchmark disponibles.</div>'
    contexto = bench.get("contexto_regional", {})
    score_actual = bench.get("score_actual", 0)
    interp = bench.get("interpretacion", "")
    p4 = bench.get("promedio_4_semanas")
    p12 = bench.get("promedio_12_semanas")
    d4 = bench.get("delta_vs_4w")
    d12 = bench.get("delta_vs_12w")
    pos = bench.get("posicion_historica") or "—"
    contexto_html = "".join(
        f'<tr><td>{_esc(k)}</td><td class="numeric">{v}</td></tr>'
        for k, v in contexto.items() if k != "nota" and isinstance(v, (int, float))
    )
    return f'''
    <div class="card">
      <div style="font-size:13px; color:var(--txt-1); line-height:1.6; margin-bottom:20px;">{_esc(interp)}</div>
      <div class="grid grid-2">
        <div>
          <table class="bench">
            <thead><tr><th>Métrica propia</th><th style="text-align:right;">Valor</th></tr></thead>
            <tbody>
              <tr><td>Score actual</td><td class="numeric" style="text-align:right;">{score_actual}</td></tr>
              <tr><td>Promedio 4 semanas</td><td class="numeric" style="text-align:right;">{p4 if p4 is not None else "—"}</td></tr>
              <tr><td>Promedio 12 semanas</td><td class="numeric" style="text-align:right;">{p12 if p12 is not None else "—"}</td></tr>
              <tr><td>Δ vs 4w</td><td class="numeric" style="text-align:right; color:{('var(--crit)' if (d4 or 0)>5 else 'var(--low)' if (d4 or 0)<-3 else 'var(--txt-0)')};">{d4:+.1f}{"" if d4 is None else ""}</td></tr>
              <tr><td>Δ vs 12w</td><td class="numeric" style="text-align:right;">{d12:+.1f}{"" if d12 is None else ""}</td></tr>
              <tr><td>Posición histórica</td><td class="numeric" style="text-align:right;">{_esc(pos)}</td></tr>
            </tbody>
          </table>
        </div>
        <div>
          <table class="bench">
            <thead><tr><th>Contexto Regional Andino</th><th style="text-align:right;">Score</th></tr></thead>
            <tbody>{contexto_html}</tbody>
          </table>
          <div style="font-size:10px; color:var(--txt-3); margin-top:12px; font-style:italic; line-height:1.4;">
            {_esc(contexto.get("nota", ""))}
          </div>
        </div>
      </div>
    </div>
    '''


def _render_recommendation(reco):
    if not reco:
        return ""
    return f'''
    <div class="recommendation">
      <div class="recommendation-label">▸ Strategic Recommendation</div>
      <div class="recommendation-action">{_esc(reco.get('accion_priorizada', '—'))}</div>
      <dl class="recommendation-meta">
        <div>
          <dt>Horizonte</dt>
          <dd>{_esc(reco.get('horizonte', '—'))}</dd>
        </div>
        <div>
          <dt>Responsable sugerido</dt>
          <dd>{_esc(reco.get('responsable_sugerido', '—'))}</dd>
        </div>
        <div>
          <dt>Costo de no actuar</dt>
          <dd>{_esc(reco.get('costo_no_actuar', '—'))}</dd>
        </div>
        <div>
          <dt>Racional analítico</dt>
          <dd>{_esc(reco.get('racional', '—'))}</dd>
        </div>
      </dl>
    </div>
    '''
