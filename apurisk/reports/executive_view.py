"""APURISK Executive Home — Vista premium C-level.

Renderiza el Executive Brief (producido por executive_synthesis.py) como
una página HTML standalone con estética Stratfor/Palantir/Recorded Future.

Paleta:
  - Fondo: slate-900 (#0f172a) — navy intelligence
  - Texto principal: slate-100 (#f1f5f9)
  - Acentos: azul eléctrico (#3b82f6) para interactividad
  - Estados semáforo: verde (#22c55e), ámbar (#f59e0b), naranja (#f97316),
    rojo (#ef4444), morado (#a855f7) para escenarios prospectivos
  - Tipografía: system-ui sans-serif minimalista

Filosofía visual:
  - Densidad informativa controlada (no sobrecargar)
  - Jerarquización por tamaño y peso, no por color saturado
  - Cero pestañas, scroll vertical único
  - Cards con borde sutil y left-accent de color por nivel
"""
from __future__ import annotations
from html import escape as _esc


# =====================================================================
# PALETA CENTRAL (CSS variables)
# =====================================================================
CSS_PALETTE = """
:root {
  /* Fondo y superficies */
  --bg-0: #0f172a;          /* slate-900 - fondo principal */
  --bg-1: #1e293b;          /* slate-800 - cards */
  --bg-2: #334155;          /* slate-700 - hover/border subtle */
  --bg-3: #475569;          /* slate-600 - dividers */

  /* Texto */
  --txt-0: #f8fafc;         /* slate-50 - títulos */
  --txt-1: #cbd5e1;         /* slate-300 - body */
  --txt-2: #94a3b8;         /* slate-400 - secundario */
  --txt-3: #64748b;         /* slate-500 - terciario */

  /* Acentos */
  --accent: #3b82f6;        /* blue-500 - interactividad */
  --accent-soft: rgba(59,130,246,0.15);
  --prospectivo: #a855f7;   /* purple-500 - escenarios */
  --prospectivo-soft: rgba(168,85,247,0.12);

  /* Estados semáforo */
  --estable: #22c55e;       /* green-500 */
  --estable-soft: rgba(34,197,94,0.12);
  --bajo: #84cc16;          /* lime-500 - verde-amarillo */
  --moderado: #f59e0b;      /* amber-500 */
  --moderado-soft: rgba(245,158,11,0.12);
  --elevado: #f97316;       /* orange-500 */
  --elevado-soft: rgba(249,115,22,0.12);
  --critico: #ef4444;       /* red-500 */
  --critico-soft: rgba(239,68,68,0.15);

  /* Espaciado */
  --gap-xs: 6px;
  --gap-sm: 12px;
  --gap-md: 20px;
  --gap-lg: 32px;
  --gap-xl: 56px;
}
"""

# Mapeo: token de color de status_nacional → CSS variable
COLOR_MAP = {
    "verde": "var(--estable)",
    "verde-amarillo": "var(--bajo)",
    "ambar": "var(--moderado)",
    "naranja": "var(--elevado)",
    "rojo": "var(--critico)",
}
COLOR_SOFT_MAP = {
    "verde": "var(--estable-soft)",
    "verde-amarillo": "rgba(132,204,22,0.12)",
    "ambar": "var(--moderado-soft)",
    "naranja": "var(--elevado-soft)",
    "rojo": "var(--critico-soft)",
}


def _color(token: str, soft: bool = False) -> str:
    """Token de color del JSON → CSS variable."""
    table = COLOR_SOFT_MAP if soft else COLOR_MAP
    return table.get(token or "ambar", "var(--moderado)")


def _fmt_num(v, decimals: int = 1) -> str:
    """Número formateado con N decimales, tolerante a None."""
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v) -> str:
    """Porcentaje 0-100, tolerante a None."""
    if v is None:
        return "—"
    try:
        return f"{float(v):.0f}%"
    except (TypeError, ValueError):
        return str(v)


# =====================================================================
# SECCIÓN 1: HEADER
# =====================================================================
def _render_header(brief: dict) -> str:
    generado = _esc(str(brief.get("generado_en", "")))
    valido = _esc(str(brief.get("valido_hasta", "")))
    llm_modo = _esc(str(brief.get("llm_modo", "")))
    return f"""
    <header class="exec-header">
      <div class="brand">
        <div class="brand-mark">◆</div>
        <div class="brand-text">
          <div class="brand-name">APURISK</div>
          <div class="brand-tagline">Intelligence Platform · Perú</div>
        </div>
      </div>
      <div class="header-meta">
        <div class="meta-block">
          <div class="meta-label">Brief generado</div>
          <div class="meta-value">{generado}</div>
        </div>
        <div class="meta-block">
          <div class="meta-label">Próxima actualización</div>
          <div class="meta-value">{valido}</div>
        </div>
        <div class="meta-block">
          <div class="meta-label">Motor</div>
          <div class="meta-value" title="{llm_modo}">{llm_modo[:40]}</div>
        </div>
        <div class="header-actions">
          <button class="btn btn-primary" onclick="regenerarBrief()">
            ↻ Regenerar
          </button>
          <a href="/dashboard" class="btn btn-secondary">Vista Analyst</a>
        </div>
      </div>
    </header>
    """


# =====================================================================
# SECCIÓN 2: STATUS NACIONAL (5 métricas)
# =====================================================================
def _render_status_bar(status: dict) -> str:
    def _card(item: dict, destacado: bool = False) -> str:
        color = _color(item.get("color", "ambar"))
        score = _fmt_num(item.get("score"), 1)
        nombre = _esc(str(item.get("nombre", "")))
        etiqueta = _esc(str(item.get("etiqueta", "")))
        sublabel = _esc(str(item.get("sublabel", "")))
        cls = "status-card status-card--hero" if destacado else "status-card"
        return f"""
        <div class="{cls}" style="--card-accent: {color};">
          <div class="status-card__label">{nombre}</div>
          <div class="status-card__score">{score}</div>
          <div class="status-card__tag" style="color: {color};">{etiqueta}</div>
          <div class="status-card__sub">{sublabel}</div>
        </div>
        """

    # Tendencia país tiene formato distinto
    tp = status.get("tendencia_pais", {}) or {}
    tp_color = _color(tp.get("color", "ambar"))
    tp_arrow = _esc(str(tp.get("arrow", "→")))
    tp_etiqueta = _esc(str(tp.get("etiqueta", "")))
    tp_delta = _fmt_num(tp.get("delta", 0), 1)
    if tp.get("delta", 0) >= 0:
        tp_delta = "+" + tp_delta
    tendencia_card = f"""
    <div class="status-card status-card--trend" style="--card-accent: {tp_color};">
      <div class="status-card__label">{_esc(str(tp.get('nombre', '')))}</div>
      <div class="status-card__trend-arrow" style="color: {tp_color};">{tp_arrow}</div>
      <div class="status-card__tag" style="color: {tp_color};">{tp_etiqueta}</div>
      <div class="status-card__sub">Δ {tp_delta} pts</div>
    </div>
    """

    return f"""
    <section class="status-bar">
      <div class="section-header">
        <h2 class="section-title">Executive Status — Perú</h2>
        <div class="section-sub">Riesgo operacional consolidado · ventana 7 días</div>
      </div>
      <div class="status-grid">
        {_card(status.get("operacional_nacional", {}), destacado=True)}
        {_card(status.get("minero", {}))}
        {_card(status.get("corredor_sur", {}))}
        {_card(status.get("criminal", {}))}
        {tendencia_card}
      </div>
    </section>
    """


# =====================================================================
# SECCIÓN 3: EXECUTIVE INSIGHT (hero memo)
# =====================================================================
def _render_executive_insight(insight: dict) -> str:
    if not insight or not insight.get("insight"):
        return ""
    texto = _esc(str(insight.get("insight", "")))
    categorias = insight.get("categorias_detectadas", []) or []
    fuente_llm = insight.get("fuente_llm", False)
    badge_motor = ""
    if fuente_llm:
        badge_motor = '<span class="badge badge--llm">Análisis IA</span>'
    chips_cat = " ".join(
        f'<span class="chip">{_esc(str(c))}</span>'
        for c in categorias[:4]
    )
    return f"""
    <section class="executive-insight">
      <div class="insight-rail"></div>
      <div class="insight-body">
        <div class="insight-header">
          <div class="insight-label">EXECUTIVE INSIGHT · SEMANAL</div>
          {badge_motor}
        </div>
        <p class="insight-text">{texto}</p>
        <div class="insight-footer">{chips_cat}</div>
      </div>
    </section>
    """


# =====================================================================
# SECCIÓN 4: EXECUTIVE THREAT PANEL (top 5 amenazas)
# =====================================================================
def _render_threat_panel(amenazas: list) -> str:
    if not amenazas:
        return ""
    nivel_color_map = {
        "CRÍTICA": "var(--critico)", "ALTO": "var(--elevado)",
        "MEDIO": "var(--moderado)", "BAJO": "var(--bajo)",
    }

    cards = ""
    for i, a in enumerate(amenazas[:5], 1):
        nivel = str(a.get("nivel", ""))
        color = nivel_color_map.get(nivel, "var(--moderado)")
        score = _fmt_num(a.get("score"), 1)
        prob = _fmt_pct(a.get("probabilidad"))
        imp = _fmt_num(a.get("impacto"), 0)
        tend = str(a.get("tendencia", "→"))
        narrativa = _esc(str(a.get("narrativa", "")))
        conv = a.get("en_convergencia", False)
        conv_badge = ('<span class="badge badge--conv">⇄ En convergencia</span>'
                       if conv else "")
        impl_chips = " ".join(
            f'<span class="chip chip--impl">{_esc(str(c))}</span>'
            for c in a.get("implicancias_categorias", [])[:5]
        )
        cards += f"""
        <article class="threat-card" style="--threat-color: {color};">
          <div class="threat-rank">#{i}</div>
          <div class="threat-content">
            <div class="threat-head">
              <h3 class="threat-name">{_esc(str(a.get("nombre", "")))}</h3>
              <div class="threat-meta">
                <span class="threat-tag" style="color: {color};">{_esc(nivel)}</span>
                <span class="threat-tag-sub">{_esc(str(a.get("categoria", "")))}</span>
                {conv_badge}
              </div>
            </div>
            <p class="threat-narrative">{narrativa}</p>
            <div class="threat-metrics">
              <div class="metric"><span class="metric-label">SCORE</span><span class="metric-value">{score}</span></div>
              <div class="metric"><span class="metric-label">PROB</span><span class="metric-value">{prob}</span></div>
              <div class="metric"><span class="metric-label">IMPACTO</span><span class="metric-value">{imp}</span></div>
              <div class="metric"><span class="metric-label">TENDENCIA</span><span class="metric-value">{tend}</span></div>
            </div>
            <div class="threat-impl">{impl_chips}</div>
          </div>
        </article>
        """

    return f"""
    <section class="threat-panel">
      <div class="section-header">
        <h2 class="section-title">Threat Panel — Amenazas Prioritarias</h2>
        <div class="section-sub">Top 5 vectores con mayor exposición operacional</div>
      </div>
      <div class="threat-list">{cards}</div>
    </section>
    """


# =====================================================================
# SECCIÓN 5: CRITICAL ALERTS (stream)
# =====================================================================
def _render_critical_alerts(alerts: list) -> str:
    if not alerts:
        return ""
    rows = ""
    nivel_color = {"CRÍTICA": "var(--critico)", "ALTA": "var(--elevado)"}
    for a in alerts[:8]:
        nivel = str(a.get("nivel", ""))
        color = nivel_color.get(nivel, "var(--moderado)")
        hours = a.get("hours_ago")
        if hours is not None:
            try:
                h = float(hours)
                if h < 1:
                    edad = f"{int(h*60)}m"
                elif h < 24:
                    edad = f"{int(h)}h"
                else:
                    edad = f"{int(h/24)}d"
            except (TypeError, ValueError):
                edad = "—"
        else:
            edad = "—"

        titulo = _esc(str(a.get("titulo", "")))[:140]
        cat = _esc(str(a.get("categoria", "")))
        regla = _esc(str(a.get("regla", "")))
        fuente = _esc(str(a.get("fuente", "")))
        por_que = _esc(str(a.get("por_que_importa", "")))
        url = str(a.get("url", ""))
        link = (f'<a href="{_esc(url)}" target="_blank" rel="noopener" class="alert-link">↗</a>'
                if url else "")

        rows += f"""
        <div class="alert-row" style="--alert-color: {color};">
          <div class="alert-meta">
            <span class="alert-nivel" style="color: {color};">{_esc(nivel)}</span>
            <span class="alert-edad">{edad}</span>
          </div>
          <div class="alert-body">
            <div class="alert-title">{titulo} {link}</div>
            <div class="alert-detail">
              <span class="alert-cat">{cat}</span>
              <span class="alert-fuente">{fuente}</span>
              <span class="alert-regla">{regla}</span>
            </div>
            <div class="alert-importa">⚡ {por_que}</div>
          </div>
        </div>
        """

    return f"""
    <section class="critical-alerts">
      <div class="section-header">
        <h2 class="section-title">Critical Alerts — Stream Operacional</h2>
        <div class="section-sub">Eventos accionables · solo nivel CRÍTICA / ALTA</div>
      </div>
      <div class="alerts-stream">{rows}</div>
    </section>
    """


# =====================================================================
# SECCIÓN 6: HOTSPOT MAP (Leaflet)
# =====================================================================
# Configuración geográfica estratégica del territorio peruano
# Polígonos aproximados de zonas críticas (lat, lon)
ZONAS_ESTRATEGICAS = [
    {
        "id": "corredor_sur_minero",
        "nombre": "Corredor Sur Minero",
        "descripcion": "Apurímac · Cusco · Espinar — Las Bambas, MMG, Glencore",
        "color": "#f97316",
        "opacity": 0.18,
        "coords": [
            [-13.5, -73.2], [-13.0, -71.5], [-14.0, -71.2],
            [-15.0, -71.5], [-15.2, -72.5], [-14.5, -73.5],
        ],
    },
    {
        "id": "vraem",
        "nombre": "VRAEM",
        "descripcion": "Valle de los ríos Apurímac, Ene y Mantaro — narcoterrorismo",
        "color": "#ef4444",
        "opacity": 0.22,
        "coords": [
            [-12.0, -74.5], [-11.7, -73.5], [-12.2, -73.0],
            [-13.2, -73.3], [-13.5, -74.2], [-12.8, -74.8],
        ],
    },
    {
        "id": "madre_dios",
        "nombre": "Madre de Dios — Minería Ilegal",
        "descripcion": "Tambopata · Inambari · La Pampa — actividad ilícita aurífera",
        "color": "#f59e0b",
        "opacity": 0.18,
        "coords": [
            [-12.0, -70.5], [-11.5, -69.0], [-12.8, -68.5],
            [-13.5, -69.5], [-13.3, -70.8],
        ],
    },
    {
        "id": "frontera_norte",
        "nombre": "Frontera Norte (Tumbes-Ecuador)",
        "descripcion": "Migración irregular · contrabando · narcotráfico",
        "color": "#f59e0b",
        "opacity": 0.15,
        "coords": [
            [-3.4, -80.5], [-3.5, -79.8], [-4.2, -79.5],
            [-4.5, -80.2], [-4.0, -80.8],
        ],
    },
    {
        "id": "puno_altiplano",
        "nombre": "Puno Altiplano",
        "descripcion": "Conflictividad ambiental · cuenca Llallimayo · bloqueos",
        "color": "#ef4444",
        "opacity": 0.15,
        "coords": [
            [-14.8, -71.0], [-14.5, -69.5], [-15.5, -69.0],
            [-16.5, -69.5], [-16.0, -70.8],
        ],
    },
]

# Corredores logísticos críticos (polylines)
CORREDORES = [
    {
        "id": "panamericana_norte",
        "nombre": "Panamericana Norte",
        "descripcion": "Lima → Trujillo → Chiclayo → Piura → Tumbes",
        "color": "#fbbf24",
        "coords": [
            [-12.0464, -77.0428], [-9.93, -76.24], [-8.11, -79.03],
            [-6.77, -79.84], [-5.19, -80.63], [-3.57, -80.45],
        ],
    },
    {
        "id": "panamericana_sur",
        "nombre": "Panamericana Sur",
        "descripcion": "Lima → Ica → Arequipa → Tacna",
        "color": "#fbbf24",
        "coords": [
            [-12.0464, -77.0428], [-13.42, -76.13], [-14.07, -75.73],
            [-16.40, -71.54], [-17.65, -71.34], [-18.01, -70.25],
        ],
    },
    {
        "id": "carretera_central",
        "nombre": "Carretera Central",
        "descripcion": "Lima → Huancayo → Pucallpa",
        "color": "#fbbf24",
        "coords": [
            [-12.0464, -77.0428], [-11.50, -75.95], [-12.07, -75.21],
            [-10.55, -74.92], [-8.38, -74.55],
        ],
    },
    {
        "id": "corredor_cobre",
        "nombre": "Corredor del Cobre",
        "descripcion": "Cusco → Espinar → Las Bambas → Matarani",
        "color": "#fb923c",
        "coords": [
            [-13.532, -71.967], [-14.79, -71.41], [-13.95, -72.85],
            [-16.40, -71.54], [-17.00, -72.10],
        ],
    },
]

# Emoji + estilo por tipo de hotspot
ICONOS_HOTSPOT = {
    "corredor_logistico": {"emoji": "🚧", "color": "#f97316", "label": "Bloqueo logístico"},
    "mineria_ilegal":     {"emoji": "⚠",  "color": "#f59e0b", "label": "Minería ilegal"},
    "conflicto_social":   {"emoji": "🔥", "color": "#ef4444", "label": "Conflicto social"},
    "violencia":          {"emoji": "🚨", "color": "#dc2626", "label": "Violencia / criminalidad"},
    "frontera":           {"emoji": "🌐", "color": "#f59e0b", "label": "Frontera / migración"},
}


def _render_hotspot_map(hotspots: list) -> str:
    if not hotspots:
        hotspots = []
    # Aplanar eventos con coords válidas (puede estar vacío)
    markers_data = []
    for h in hotspots:
        tipo = h.get("tipo", "")
        cfg = ICONOS_HOTSPOT.get(tipo, {"emoji": "●", "color": "#3b82f6",
                                          "label": h.get("label", "")})
        for ev in h.get("eventos", []):
            lat, lon = ev.get("lat"), ev.get("lon")
            if lat is None or lon is None:
                continue
            try:
                markers_data.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "tipo": tipo,
                    "emoji": cfg["emoji"],
                    "color": cfg["color"],
                    "label": cfg["label"],
                    "titulo": str(ev.get("titulo", ""))[:180],
                    "lugar": str(ev.get("lugar", "")),
                    "fuente": str(ev.get("fuente", "")),
                })
            except (TypeError, ValueError):
                continue

    # Leyenda enriquecida — hotspots activos + capas estratégicas
    leyenda_hotspots = ""
    for h in hotspots:
        tipo = h.get("tipo", "")
        cfg = ICONOS_HOTSPOT.get(tipo, {"emoji": "●", "color": "#3b82f6", "label": ""})
        leyenda_hotspots += f"""
        <div class="legend-item">
          <span class="legend-icon" style="background: {cfg["color"]}22; border-color: {cfg["color"]};">
            {cfg["emoji"]}
          </span>
          <span class="legend-label">{_esc(str(h.get("label", "")))}</span>
          <span class="legend-count">{h.get("n_eventos", 0)}</span>
        </div>
        """

    leyenda_zonas = ""
    for z in ZONAS_ESTRATEGICAS:
        leyenda_zonas += f"""
        <div class="legend-item legend-item--zone">
          <span class="legend-band" style="background: {z["color"]};"></span>
          <div class="legend-zone-meta">
            <div class="legend-zone-name">{_esc(z["nombre"])}</div>
            <div class="legend-zone-desc">{_esc(z["descripcion"])}</div>
          </div>
        </div>
        """

    leyenda_corredores = ""
    for c in CORREDORES:
        leyenda_corredores += f"""
        <div class="legend-item legend-item--route">
          <span class="legend-line" style="background: {c["color"]};"></span>
          <div class="legend-zone-meta">
            <div class="legend-zone-name">{_esc(c["nombre"])}</div>
            <div class="legend-zone-desc">{_esc(c["descripcion"])}</div>
          </div>
        </div>
        """

    import json as _json
    markers_json = _json.dumps(markers_data, ensure_ascii=False)
    zonas_json = _json.dumps(ZONAS_ESTRATEGICAS, ensure_ascii=False)
    corredores_json = _json.dumps(CORREDORES, ensure_ascii=False)

    return f"""
    <section class="hotspot-map">
      <div class="section-header">
        <h2 class="section-title">Mapa Operacional — Hotspots & Geografía Estratégica</h2>
        <div class="section-sub">
          {len(markers_data)} eventos activos · {len(ZONAS_ESTRATEGICAS)} zonas estratégicas ·
          {len(CORREDORES)} corredores logísticos críticos
        </div>
      </div>
      <div class="map-container">
        <div id="execMap" class="map-canvas"></div>
        <aside class="map-legend">
          <div class="legend-title">Hotspots activos</div>
          {leyenda_hotspots if leyenda_hotspots else '<div class="legend-empty">Sin eventos georreferenciados esta ventana.</div>'}
          <div class="legend-divider"></div>
          <div class="legend-title">Zonas estratégicas</div>
          {leyenda_zonas}
          <div class="legend-divider"></div>
          <div class="legend-title">Corredores logísticos</div>
          {leyenda_corredores}
        </aside>
      </div>
      <script>
        (function() {{
          if (typeof L === 'undefined') {{
            console.error('Leaflet no cargado');
            return;
          }}
          const markers = {markers_json};
          const zonas = {zonas_json};
          const corredores = {corredores_json};

          const map = L.map('execMap', {{
            center: [-10.5, -75.5],
            zoom: 5,
            zoomControl: true,
            attributionControl: false,
            preferCanvas: false,
          }});

          // Tile base oscuro CartoDB dark_nolabels (sin labels = más limpio)
          const tileDark = L.tileLayer(
            'https://{{s}}.basemaps.cartocdn.com/dark_nolabels/{{z}}/{{x}}/{{y}}{{r}}.png',
            {{
              maxZoom: 13,
              minZoom: 4,
              subdomains: 'abcd',
            }}
          );
          tileDark.addTo(map);

          // Capa de labels SOBRE las zonas (sutiles, para no saturar)
          const tileLabels = L.tileLayer(
            'https://{{s}}.basemaps.cartocdn.com/dark_only_labels/{{z}}/{{x}}/{{y}}{{r}}.png',
            {{ maxZoom: 13, opacity: 0.75 }}
          );

          // ========== ZONAS ESTRATÉGICAS (polígonos) ==========
          const layerZonas = L.featureGroup();
          zonas.forEach(z => {{
            const poly = L.polygon(z.coords, {{
              color: z.color,
              fillColor: z.color,
              fillOpacity: z.opacity,
              weight: 1.5,
              opacity: 0.65,
              dashArray: '4,4',
            }});
            poly.bindTooltip(
              `<strong>${{z.nombre}}</strong><br><span style="font-size:10px;">${{z.descripcion}}</span>`,
              {{ direction: 'center', className: 'apurisk-tip', sticky: false }}
            );
            poly.addTo(layerZonas);
          }});
          layerZonas.addTo(map);

          // ========== CORREDORES LOGÍSTICOS (polylines) ==========
          const layerCorredores = L.featureGroup();
          corredores.forEach(c => {{
            const line = L.polyline(c.coords, {{
              color: c.color,
              weight: 3,
              opacity: 0.75,
              dashArray: '8,4',
            }});
            line.bindTooltip(
              `<strong>${{c.nombre}}</strong><br><span style="font-size:10px;">${{c.descripcion}}</span>`,
              {{ direction: 'auto', className: 'apurisk-tip', sticky: true }}
            );
            line.addTo(layerCorredores);
          }});
          layerCorredores.addTo(map);

          // Agregar labels encima
          tileLabels.addTo(map);

          // ========== MARKERS DE EVENTOS (DivIcon con emoji) ==========
          const layerMarkers = L.featureGroup();
          markers.forEach(m => {{
            const divIcon = L.divIcon({{
              className: 'apurisk-marker',
              html: `<div class="marker-bubble" style="background:${{m.color}}; box-shadow:0 0 0 3px ${{m.color}}33;">
                       <span class="marker-emoji">${{m.emoji}}</span>
                     </div>`,
              iconSize: [32, 32],
              iconAnchor: [16, 16],
              popupAnchor: [0, -18],
            }});
            const mk = L.marker([m.lat, m.lon], {{ icon: divIcon }});
            mk.bindPopup(
              `<div class="apurisk-popup">
                <div class="pp-label" style="color:${{m.color}};">${{m.emoji}} ${{m.label}}</div>
                <div class="pp-title">${{m.titulo}}</div>
                <div class="pp-meta">📍 ${{m.lugar}}<br>📰 ${{m.fuente}}</div>
              </div>`,
              {{ maxWidth: 300, className: 'apurisk-popup-wrapper' }}
            );
            mk.addTo(layerMarkers);
          }});
          layerMarkers.addTo(map);

          // ========== Control de capas ==========
          L.control.layers(
            null,
            {{
              '🚨 Hotspots activos': layerMarkers,
              '⬛ Zonas estratégicas': layerZonas,
              '🛣 Corredores logísticos': layerCorredores,
            }},
            {{ position: 'bottomleft', collapsed: false }}
          ).addTo(map);

          // Si hay markers, ajustar vista para incluir todos
          if (markers.length > 0) {{
            const bounds = layerMarkers.getBounds().pad(0.15);
            map.fitBounds(bounds, {{ maxZoom: 7 }});
          }}
        }})();
      </script>
    </section>
    """


# =====================================================================
# SECCIÓN 7: IMPLICANCIAS OPERACIONALES (grid 2x3)
# =====================================================================
def _render_implicancias(impl: dict) -> str:
    if not impl:
        return ""
    estado_color = {
        "ESTABLE": "var(--estable)",
        "MONITOREO": "var(--bajo)",
        "ATENCIÓN": "var(--moderado)",
        "ALERTA": "var(--elevado)",
    }
    estado_soft = {
        "ESTABLE": "var(--estable-soft)",
        "MONITOREO": "rgba(132,204,22,0.10)",
        "ATENCIÓN": "var(--moderado-soft)",
        "ALERTA": "var(--elevado-soft)",
    }
    icons = {
        "logistica": "🛣",
        "esg": "🌱",
        "regulatorio": "⚖",
        "reputacional": "📣",
        "fuerza_laboral": "👥",
        "continuidad": "⚙",
    }
    cards = ""
    for key in ["logistica", "esg", "regulatorio", "reputacional",
                 "fuerza_laboral", "continuidad"]:
        data = impl.get(key, {})
        if not data:
            continue
        estado = str(data.get("estado", "ESTABLE"))
        color = estado_color.get(estado, "var(--moderado)")
        soft = estado_soft.get(estado, "var(--moderado-soft)")
        label = _esc(str(data.get("label", "")))
        narrativa = _esc(str(data.get("narrativa", "")))
        n = data.get("n_amenazas", 0)
        icon = icons.get(key, "•")
        cards += f"""
        <div class="impl-card" style="--impl-color: {color}; --impl-soft: {soft};">
          <div class="impl-head">
            <span class="impl-icon">{icon}</span>
            <div>
              <div class="impl-label">{label}</div>
              <div class="impl-estado" style="color: {color};">{_esc(estado)} · {n} amenaza{'s' if n != 1 else ''}</div>
            </div>
          </div>
          <p class="impl-narrative">{narrativa}</p>
        </div>
        """
    return f"""
    <section class="implicancias">
      <div class="section-header">
        <h2 class="section-title">Implicancias Operacionales</h2>
        <div class="section-sub">Cómo afecta el entorno actual a las 6 dimensiones críticas del negocio</div>
      </div>
      <div class="impl-grid">{cards}</div>
    </section>
    """


# =====================================================================
# SECCIÓN 8: OUTLOOK 30 DÍAS (3 escenarios)
# =====================================================================
def _render_outlook(outlook: dict) -> str:
    escenarios = outlook.get("escenarios", []) or []
    if not escenarios:
        return ""
    cols = ""
    for esc in escenarios:
        prob = int(esc.get("probabilidad_pct", 0))
        color = _color(esc.get("color", "ambar"))
        soft = _color(esc.get("color", "ambar"), soft=True)
        label = _esc(str(esc.get("label", "")))
        narrativa = _esc(str(esc.get("narrativa", "")))
        indicadores = "".join(
            f'<li>{_esc(str(ind))}</li>'
            for ind in esc.get("indicadores_tempranos", [])[:5]
        )
        cols += f"""
        <article class="outlook-col" style="--outlook-color: {color}; --outlook-soft: {soft};">
          <div class="outlook-prob">
            <div class="prob-num">{prob}<span class="prob-pct">%</span></div>
            <div class="prob-bar"><div class="prob-fill" style="width: {prob}%;"></div></div>
          </div>
          <h3 class="outlook-label">{label}</h3>
          <p class="outlook-narrative">{narrativa}</p>
          <div class="outlook-indicadores-title">Indicadores tempranos</div>
          <ul class="outlook-indicadores">{indicadores}</ul>
        </article>
        """

    metodologia = _esc(str(outlook.get("metodologia", "")))
    return f"""
    <section class="outlook">
      <div class="section-header">
        <h2 class="section-title">Outlook — Próximos 30 días</h2>
        <div class="section-sub">{metodologia}</div>
      </div>
      <div class="outlook-grid">{cols}</div>
    </section>
    """


# =====================================================================
# CSS COMPLETO
# =====================================================================
def _css() -> str:
    return CSS_PALETTE + """
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg-0);
  color: var(--txt-1);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.exec-container {
  max-width: 1440px;
  margin: 0 auto;
  padding: 0 var(--gap-md);
}

/* ===== HEADER ===== */
.exec-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--gap-md) 0;
  border-bottom: 1px solid var(--bg-2);
  margin-bottom: var(--gap-lg);
}
.brand {
  display: flex;
  align-items: center;
  gap: var(--gap-sm);
}
.brand-mark {
  font-size: 28px;
  color: var(--accent);
  line-height: 1;
}
.brand-name {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: var(--txt-0);
}
.brand-tagline {
  font-size: 11px;
  color: var(--txt-3);
  letter-spacing: 1.5px;
  text-transform: uppercase;
}
.header-meta {
  display: flex;
  align-items: center;
  gap: var(--gap-md);
}
.meta-block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.meta-label {
  font-size: 9px;
  color: var(--txt-3);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 2px;
}
.meta-value {
  font-size: 11px;
  color: var(--txt-1);
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-actions {
  display: flex;
  gap: 8px;
}
.btn {
  padding: 7px 14px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
  display: inline-block;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s ease;
  letter-spacing: 0.3px;
}
.btn-primary {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}
.btn-primary:hover { background: #2563eb; border-color: #2563eb; }
.btn-secondary {
  background: transparent;
  color: var(--txt-2);
  border-color: var(--bg-3);
}
.btn-secondary:hover { color: var(--txt-0); border-color: var(--txt-3); }

/* ===== SECTION HEADER ===== */
section { margin-bottom: var(--gap-xl); }
.section-header {
  margin-bottom: var(--gap-md);
  padding-left: 4px;
}
.section-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--txt-0);
  text-transform: uppercase;
  letter-spacing: 2px;
  margin: 0 0 4px 0;
}
.section-sub {
  font-size: 12px;
  color: var(--txt-3);
}

/* ===== STATUS BAR ===== */
.status-grid {
  display: grid;
  grid-template-columns: 1.5fr repeat(4, 1fr);
  gap: var(--gap-sm);
}
.status-card {
  background: var(--bg-1);
  border: 1px solid var(--bg-2);
  border-left: 3px solid var(--card-accent);
  border-radius: 6px;
  padding: var(--gap-md);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 130px;
}
.status-card--hero { background: linear-gradient(135deg, var(--bg-1), rgba(59,130,246,0.04)); }
.status-card__label {
  font-size: 10px;
  color: var(--txt-3);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  font-weight: 600;
}
.status-card__score {
  font-size: 42px;
  font-weight: 700;
  color: var(--txt-0);
  line-height: 1.1;
  margin: 8px 0 4px;
  font-variant-numeric: tabular-nums;
}
.status-card__trend-arrow {
  font-size: 38px;
  font-weight: 700;
  line-height: 1.1;
  margin: 8px 0 4px;
}
.status-card__tag {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
}
.status-card__sub {
  font-size: 11px;
  color: var(--txt-2);
  margin-top: 6px;
}
.status-card--trend { text-align: left; }

/* ===== EXECUTIVE INSIGHT ===== */
.executive-insight {
  display: flex;
  background: linear-gradient(135deg, var(--bg-1), rgba(168,85,247,0.05));
  border: 1px solid var(--bg-2);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: var(--gap-xl);
}
.insight-rail {
  width: 5px;
  background: linear-gradient(to bottom, var(--accent), var(--prospectivo));
  flex-shrink: 0;
}
.insight-body {
  padding: var(--gap-md) var(--gap-lg);
  flex: 1;
}
.insight-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--gap-sm);
}
.insight-label {
  font-size: 10px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 2.5px;
  font-weight: 700;
}
.insight-text {
  font-size: 15px;
  line-height: 1.75;
  color: var(--txt-0);
  margin: 0;
  font-weight: 400;
}
.insight-footer {
  margin-top: var(--gap-md);
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* ===== CHIPS Y BADGES ===== */
.chip, .badge {
  display: inline-block;
  font-size: 10px;
  padding: 3px 8px;
  border-radius: 3px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.chip {
  background: var(--bg-2);
  color: var(--txt-2);
  text-transform: uppercase;
}
.chip--impl {
  background: rgba(59,130,246,0.10);
  color: var(--accent);
}
.badge--llm {
  background: var(--accent-soft);
  color: var(--accent);
}
.badge--conv {
  background: var(--prospectivo-soft);
  color: var(--prospectivo);
}

/* ===== THREAT PANEL ===== */
.threat-list {
  display: flex;
  flex-direction: column;
  gap: var(--gap-md);
}
.threat-card {
  display: flex;
  gap: var(--gap-md);
  background: var(--bg-1);
  border: 1px solid var(--bg-2);
  border-left: 4px solid var(--threat-color);
  border-radius: 6px;
  padding: var(--gap-md);
}
.threat-rank {
  flex-shrink: 0;
  font-size: 36px;
  font-weight: 700;
  color: var(--txt-3);
  font-variant-numeric: tabular-nums;
  width: 60px;
  text-align: center;
  line-height: 1;
  padding-top: 8px;
}
.threat-content { flex: 1; }
.threat-head { margin-bottom: 8px; }
.threat-name {
  font-size: 18px;
  font-weight: 700;
  color: var(--txt-0);
  margin: 0 0 6px;
}
.threat-meta {
  display: flex;
  gap: var(--gap-sm);
  align-items: center;
  flex-wrap: wrap;
}
.threat-tag {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
}
.threat-tag-sub {
  font-size: 11px;
  color: var(--txt-3);
}
.threat-narrative {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--txt-1);
  margin: var(--gap-sm) 0;
}
.threat-metrics {
  display: flex;
  gap: var(--gap-lg);
  padding: 10px 0;
  border-top: 1px solid var(--bg-2);
  margin-top: var(--gap-sm);
}
.metric { display: flex; flex-direction: column; align-items: flex-start; }
.metric-label {
  font-size: 9px;
  color: var(--txt-3);
  letter-spacing: 1px;
  text-transform: uppercase;
}
.metric-value {
  font-size: 14px;
  font-weight: 700;
  color: var(--txt-0);
  font-variant-numeric: tabular-nums;
}
.threat-impl {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 6px;
}

/* ===== CRITICAL ALERTS ===== */
.alerts-stream {
  display: flex;
  flex-direction: column;
  background: var(--bg-1);
  border: 1px solid var(--bg-2);
  border-radius: 6px;
  overflow: hidden;
}
.alert-row {
  display: flex;
  gap: var(--gap-md);
  padding: var(--gap-sm) var(--gap-md);
  border-bottom: 1px solid var(--bg-2);
  border-left: 3px solid var(--alert-color);
  transition: background 0.15s ease;
}
.alert-row:last-child { border-bottom: none; }
.alert-row:hover { background: rgba(255,255,255,0.02); }
.alert-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 80px;
  flex-shrink: 0;
}
.alert-nivel {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1px;
}
.alert-edad {
  font-size: 11px;
  color: var(--txt-3);
  font-family: ui-monospace, monospace;
}
.alert-body { flex: 1; }
.alert-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--txt-0);
  margin-bottom: 4px;
  line-height: 1.5;
}
.alert-link {
  color: var(--accent);
  text-decoration: none;
  font-weight: 700;
  margin-left: 4px;
}
.alert-link:hover { color: #60a5fa; }
.alert-detail {
  display: flex;
  gap: var(--gap-sm);
  font-size: 11px;
  color: var(--txt-3);
  margin-bottom: 4px;
}
.alert-cat::after, .alert-fuente::after {
  content: " ·";
  margin-left: 2px;
  color: var(--bg-3);
}
.alert-importa {
  font-size: 12px;
  color: var(--txt-2);
  font-style: italic;
}

/* ===== HOTSPOT MAP ===== */
.map-container {
  display: grid;
  grid-template-columns: 1fr 280px;
  gap: 0;
  background: var(--bg-1);
  border: 1px solid var(--bg-2);
  border-radius: 6px;
  overflow: hidden;
  height: 560px;
}
.map-canvas {
  width: 100%;
  height: 100%;
  background: #0a1626;
  position: relative;
}
.map-legend {
  padding: var(--gap-md);
  background: var(--bg-1);
  border-left: 1px solid var(--bg-2);
  overflow-y: auto;
  font-size: 12px;
}
.legend-title {
  font-size: 10px;
  color: var(--txt-3);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  font-weight: 700;
  margin: 0 0 8px 0;
}
.legend-divider {
  height: 1px;
  background: var(--bg-2);
  margin: 14px 0;
}
.legend-empty {
  font-size: 11px;
  color: var(--txt-3);
  font-style: italic;
  padding: 8px 0;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  font-size: 12px;
}
.legend-item--zone, .legend-item--route {
  align-items: flex-start;
  padding: 7px 0;
}
.legend-icon {
  width: 26px;
  height: 26px;
  border: 1.5px solid;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}
.legend-band {
  width: 18px;
  height: 18px;
  border-radius: 3px;
  opacity: 0.55;
  flex-shrink: 0;
  margin-top: 2px;
}
.legend-line {
  width: 18px;
  height: 3px;
  border-radius: 2px;
  flex-shrink: 0;
  margin-top: 10px;
}
.legend-label {
  flex: 1;
  color: var(--txt-1);
}
.legend-count {
  background: var(--bg-2);
  color: var(--txt-1);
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.legend-zone-meta { flex: 1; min-width: 0; }
.legend-zone-name {
  font-size: 12px;
  color: var(--txt-0);
  font-weight: 600;
  line-height: 1.3;
}
.legend-zone-desc {
  font-size: 10px;
  color: var(--txt-3);
  line-height: 1.4;
  margin-top: 2px;
}

/* ===== LEAFLET PERSONALIZADO ===== */
/* Markers de evento con burbuja + emoji */
.apurisk-marker {
  background: transparent;
  border: none;
}
.marker-bubble {
  width: 30px;
  height: 30px;
  border-radius: 50% 50% 50% 0;
  transform: rotate(-45deg);
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid rgba(255,255,255,0.85);
  cursor: pointer;
  transition: transform 0.15s ease;
}
.marker-bubble:hover {
  transform: rotate(-45deg) scale(1.18);
  z-index: 1000;
}
.marker-emoji {
  transform: rotate(45deg);
  font-size: 14px;
  line-height: 1;
}

/* Popups */
.apurisk-popup-wrapper .leaflet-popup-content-wrapper {
  background: #1e293b;
  color: #f1f5f9;
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.55);
  border: 1px solid #334155;
}
.apurisk-popup-wrapper .leaflet-popup-tip {
  background: #1e293b;
  border: 1px solid #334155;
}
.apurisk-popup-wrapper .leaflet-popup-close-button {
  color: #94a3b8;
}
.apurisk-popup {
  padding: 4px 6px;
  min-width: 200px;
  max-width: 280px;
}
.pp-label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  margin-bottom: 6px;
}
.pp-title {
  font-size: 13px;
  font-weight: 600;
  color: #f8fafc;
  margin-bottom: 8px;
  line-height: 1.45;
}
.pp-meta {
  font-size: 10.5px;
  color: #94a3b8;
  line-height: 1.6;
  border-top: 1px solid #334155;
  padding-top: 6px;
}

/* Tooltip de zonas y corredores */
.apurisk-tip {
  background: #1e293b !important;
  color: #f1f5f9 !important;
  border: 1px solid #334155 !important;
  border-radius: 4px !important;
  font-size: 11px !important;
  padding: 6px 10px !important;
  box-shadow: 0 2px 8px rgba(0,0,0,0.5) !important;
}
.apurisk-tip::before { border-top-color: #1e293b !important; }

/* Control de capas */
.leaflet-control-layers {
  background: rgba(15,23,42,0.92) !important;
  color: #cbd5e1 !important;
  border: 1px solid #334155 !important;
  border-radius: 6px !important;
  font-size: 11px !important;
  padding: 8px 10px !important;
  box-shadow: 0 2px 12px rgba(0,0,0,0.6) !important;
}
.leaflet-control-layers label {
  display: flex !important;
  align-items: center !important;
  gap: 6px !important;
  padding: 3px 0 !important;
  color: #cbd5e1 !important;
}
.leaflet-control-layers-overlays {
  display: flex !important;
  flex-direction: column !important;
  gap: 2px !important;
}

/* Zoom control */
.leaflet-control-zoom a {
  background: #1e293b !important;
  color: #cbd5e1 !important;
  border: 1px solid #334155 !important;
}
.leaflet-control-zoom a:hover {
  background: #334155 !important;
  color: #f8fafc !important;
}

/* ===== IMPLICANCIAS ===== */
.impl-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--gap-md);
}
.impl-card {
  background: var(--bg-1);
  border: 1px solid var(--bg-2);
  border-top: 3px solid var(--impl-color);
  border-radius: 6px;
  padding: var(--gap-md);
}
.impl-head {
  display: flex;
  gap: var(--gap-sm);
  align-items: center;
  margin-bottom: var(--gap-sm);
}
.impl-icon {
  font-size: 24px;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--impl-soft);
  border-radius: 6px;
}
.impl-label {
  font-size: 14px;
  font-weight: 700;
  color: var(--txt-0);
}
.impl-estado {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
}
.impl-narrative {
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--txt-1);
  margin: 0;
}

/* ===== OUTLOOK ===== */
.outlook-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--gap-md);
}
.outlook-col {
  background: var(--bg-1);
  border: 1px solid var(--bg-2);
  border-radius: 6px;
  padding: var(--gap-md);
  position: relative;
  overflow: hidden;
}
.outlook-col::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: var(--outlook-color);
}
.outlook-prob { margin-bottom: var(--gap-sm); }
.prob-num {
  font-size: 44px;
  font-weight: 700;
  color: var(--outlook-color);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.prob-pct {
  font-size: 18px;
  color: var(--txt-3);
}
.prob-bar {
  margin-top: 8px;
  height: 4px;
  background: var(--bg-2);
  border-radius: 2px;
  overflow: hidden;
}
.prob-fill {
  height: 100%;
  background: var(--outlook-color);
  transition: width 0.6s ease;
}
.outlook-label {
  font-size: 11px;
  color: var(--txt-3);
  text-transform: uppercase;
  letter-spacing: 2px;
  font-weight: 700;
  margin: var(--gap-sm) 0 6px;
}
.outlook-narrative {
  font-size: 13px;
  line-height: 1.65;
  color: var(--txt-1);
  margin: 0 0 var(--gap-md);
}
.outlook-indicadores-title {
  font-size: 10px;
  color: var(--txt-3);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  font-weight: 600;
  margin-bottom: 6px;
}
.outlook-indicadores {
  margin: 0;
  padding-left: 18px;
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--txt-2);
}
.outlook-indicadores li { margin-bottom: 3px; }

/* ===== FOOTER ===== */
.exec-footer {
  border-top: 1px solid var(--bg-2);
  padding: var(--gap-md) 0;
  margin-top: var(--gap-xl);
  font-size: 11px;
  color: var(--txt-3);
  text-align: center;
  letter-spacing: 0.5px;
}

/* ===== RESPONSIVE ===== */
@media (max-width: 1100px) {
  .status-grid { grid-template-columns: 1fr 1fr; }
  .impl-grid, .outlook-grid { grid-template-columns: 1fr; }
  .map-container { grid-template-columns: 1fr; height: auto; }
  .map-canvas { height: 360px; }
}
@media (max-width: 700px) {
  .exec-header { flex-direction: column; gap: var(--gap-sm); align-items: flex-start; }
  .header-meta { flex-wrap: wrap; }
  .threat-card { flex-direction: column; }
  .threat-rank { width: auto; padding-top: 0; }
  .threat-metrics { flex-wrap: wrap; gap: var(--gap-md); }
}
"""


# =====================================================================
# RENDER PRINCIPAL
# =====================================================================
def render_executive_home(brief: dict) -> str:
    """Renderiza el Executive Home completo como HTML standalone.

    Args:
        brief: dict producido por sintetizar_executive_brief().

    Returns:
        HTML completo con CSS embebido + Leaflet desde CDN.
    """
    status = brief.get("status_nacional", {}) or {}
    insight = brief.get("executive_insight", {}) or {}
    amenazas = brief.get("amenazas_prioritarias", []) or []
    alerts = brief.get("critical_alerts", []) or []
    hotspots = brief.get("hotspots", []) or []
    impl = brief.get("implicancias_operacionales", {}) or {}
    outlook = brief.get("outlook_30d", {}) or {}

    # Errores parciales (si los hubo)
    errores_html = ""
    errores = brief.get("_errores_bloques", {})
    if errores:
        items = "".join(
            f"<li><strong>{_esc(str(k))}</strong>: {_esc(str(v))}</li>"
            for k, v in errores.items()
        )
        errores_html = f"""
        <div class="errores-banner">
          ⚠ Bloques con errores parciales: <ul>{items}</ul>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>APURISK Intelligence — Executive Brief</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="">
<style>{_css()}</style>
</head>
<body>
<div class="exec-container">
  {_render_header(brief)}
  {errores_html}
  {_render_status_bar(status)}
  {_render_executive_insight(insight)}
  {_render_threat_panel(amenazas)}
  {_render_critical_alerts(alerts)}
  {_render_hotspot_map(hotspots)}
  {_render_implicancias(impl)}
  {_render_outlook(outlook)}
  <footer class="exec-footer">
    APURISK Intelligence Platform · OSINT Strategic Risk Monitoring · Perú
  </footer>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
<script>
async function regenerarBrief() {{
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '↻ Regenerando...';
  try {{
    await fetch('/api/executive/brief/regenerar', {{ method: 'POST' }});
    location.reload();
  }} catch (e) {{
    alert('Error regenerando: ' + e.message);
    btn.disabled = false;
    btn.textContent = '↻ Regenerar';
  }}
}}
</script>
</body>
</html>"""
