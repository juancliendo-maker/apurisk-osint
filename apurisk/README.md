# APURISK 1.0 — Plataforma OSINT de Riesgos Políticos del Perú

Plataforma de inteligencia visual que automatiza el ciclo
**recolección → análisis → matriz de riesgo → alertas → reportes** sobre
fuentes abiertas peruanas e internacionales y **opinión pública en Twitter/X**
para apoyar el trabajo de **consultoría política**.

## Capacidades

### Recolección OSINT
- **Medios peruanos** (RSS): RPP, La República, El Comercio, Gestión, IDL-Reporteros
- **Estado**: Defensoría del Pueblo (conflictos sociales), Congreso (proyectos de ley)
- **Twitter / X**: API v2 `/tweets/search/recent` con queries configurables (vacancia, paros, corrupción, riesgo país)
- **Internacional**: GDELT (eventos sobre Perú)

### Análisis
- **Sentimiento** en español (lexicón, reemplazable por `pysentimiento`/BETO)
- **Entidades**: instituciones, partidos, regiones, empresas en zona de riesgo
- **Temas**: estabilidad, conflictos, regulatorio, polarización, corrupción, seguridad, electoral, económico
- **Matriz de Riesgo P×I**: 10 factores con probabilidad, impacto, score `√(P·I)`, nivel CRÍTICO/ALTO/MEDIO/BAJO, tendencia ↑/↓/→
- **Motor de alertas**: 9 reglas que disparan alertas inmediatas con acción recomendada
- **Twitter analytics**: hashtags, virales, engagement total, reach estimado, sentimiento por feed
- **Geocoding**: cada alerta y conflicto se asocia a coordenadas reales del Perú (24 departamentos + lugares clave: Las Bambas, Tía María, Coroccohuayco, Espinar, etc.)
- **Score global** ponderado, alineado con la matriz, alertas críticas y sentimiento

### Dashboard del analista
Single-file HTML con **8 tabs**:
1. **Mapa de Riesgos** — Matriz P×I (bubble) + Treemap por categoría + Cards visuales con barras Prob/Impacto (sin tablas)
2. **Mapa Geográfico** — Leaflet + OpenStreetMap, marcadores georreferenciados color-coded por severidad, popup con resumen y enlace a fuente
3. **Alertas Inmediatas** — feed crítico con triggers y acciones recomendadas
4. **Reporte 24h** — síntesis ejecutiva + headlines de las últimas 24h
5. **Twitter / X** — feed live, virales, hashtags trending, métricas de engagement
6. **Conflictos** — cards visuales por evento (no tabla)
7. **Legislativo** — cards de proyectos de ley con enlace al expediente
8. **Entidades** — barras visuales de instituciones, partidos, empresas, regiones

### Factores monitoreados (15)
1. Vacancia presidencial · 2. Censura/interpelación al Gabinete · 3. Renuncia ministro clave
4. Bloqueos en zonas extractivas · 5. Paros regionales · 6. Reforma electoral regresiva
7. Regulación sectorial restrictiva · 8. Investigaciones por corrupción · 9. **Corrupción sistémica de altos cargos**
10. Deterioro seguridad ciudadana · 11. Presión sobre estabilidad económica
12. **Intervención FFAA en orden interno** · 13. **Tensiones fronterizas** · 14. **Crisis migratoria**
15. **Tensiones diplomáticas** (con México, Chile, etc.)

### Reportes generados (8 formatos)
1. **Dashboard interactivo** (HTML) — interfaz analista intel-grade con 9 tabs
2. **Reporte Diario PDF** — síntesis ejecutiva del día con score, factores, alertas, headlines (URLs clickables)
3. **Reporte Semanal PDF** — agregado de últimos 7 días con tendencias y top eventos
4. **Reporte 24h** (HTML imprimible + DOCX) — briefing matutino
5. **Alertas Inmediatas** (HTML + DOCX) — feed crítico con triggers, acciones y URLs
6. **Reporte ejecutivo completo** (DOCX) — secciones, tablas y entidades
7. **Snapshot JSON** — todos los datos para encadenar con BI
8. **Centro de Descargas** integrado en el dashboard (pestaña 📥 Descargas)

**Cada referencia (artículo, alerta, tweet, conflicto, PL) tiene URL clicable a la fuente original.**

## Estructura

```
apurisk/
├── config.yaml               fuentes, queries Twitter, pesos, parámetros
├── main.py                   orquestador (--watch para tiempo real)
├── requirements.txt
├── collectors/
│   ├── base.py · rss_media · defensoria · gdelt · congreso
│   └── twitter.py            ★ X API v2 + bearer token
├── analyzers/
│   ├── sentiment · entities · topics · risk_score
│   ├── risk_matrix.py        matriz Probabilidad × Impacto
│   ├── alerts.py             motor de reglas de alertas
│   └── twitter_analysis.py   ★ hashtags, virales, engagement
├── reports/
│   ├── html_dashboard.py     ★ con Leaflet + treemap + factor cards
│   ├── reporte_24h.py        reporte 24h HTML + DOCX
│   ├── alertas_report.py     alertas inmediatas HTML + DOCX
│   └── docx_report.py        reporte ejecutivo clásico
├── data/
│   ├── sample_data.py        ★ incluye TWEETS_DEMO realistas
│   └── peru_geo.py           ★ coords de 24 deps + lugares clave
└── output/                   reportes generados (timestamped)
```

## Uso

```bash
cd apurisk
pip install -r requirements.txt

# Modo en vivo con monitoreo cada 30 minutos (default):
export TWITTER_BEARER_TOKEN="tu_bearer_token_de_x_api"
python -m apurisk.main --live

# Cadencia personalizada (ej. cada 10 minutos):
python -m apurisk.main --live --watch 600

# Corrida única (sin loop):
python -m apurisk.main --live --once
```

### Monitoreo automático cada 30 minutos

A partir de esta versión, **el modo live ejecuta el ciclo completo cada 30
minutos** (`--watch 1800` por default). Cada ciclo:

1. Recolecta datos frescos de las 10 fuentes
2. Re-analiza matriz P×I, alertas y twitter
3. Regenera todos los HTML/DOCX con timestamp nuevo
4. Actualiza `dashboard_latest.html` (siempre apunta al snapshot más reciente)

**El dashboard HTML hace auto-refresco en el navegador** vía meta-refresh
(1800s) sincronizado con la cadencia del backend. Además incluye:

- **Cuenta atrás visible** en el header (`MM:SS` hasta próxima recarga)
- **Barra de progreso** del intervalo de actualización
- **Botón "⟳ Actualizar"** para forzar recarga manual
- **Banner de frescura** que se torna amarillo si los datos pasan de stale
- **Pestaña "⟳ Monitoreo"** con estado del sistema, salud de cada fuente,
  configuración de reglas, y comando para reactivar el watch loop

Para servir el dashboard a múltiples usuarios:

```bash
# Terminal 1 - corre el ciclo de actualización cada 30 min
python -m apurisk.main --live --watch 1800

# Terminal 2 - sirve la carpeta output
python -m http.server 8080 --directory output
# Luego abrir: http://localhost:8080/dashboard_latest.html
```

### Datos reales actualizados

Los datos de fallback (modo demo) son **datos reales y verificables** de la
coyuntura peruana al 25 de abril de 2026: post-elecciones generales del 12-13 abr,
con Keiko Fujimori (17.06%) en 1°, disputa entre Sánchez y López Aliaga por el 2°,
JEE resolviendo actas observadas, presidente interino José María Balcázar, riesgo
país en 129 pb, 208 conflictos sociales activos según Defensoría. Cada item
incluye **URL real** (rpp.pe, elcomercio.pe, larepublica.pe, gestion.pe,
infobae.com, defensoria.gob.pe, congreso.gob.pe, etc.) y **timestamp ISO 8601**.

### Modo Live: cómo funciona

Al ejecutar con `--live`:
1. Cada collector intenta conectar a su fuente real con retry/backoff
2. Headers User-Agent, Accept-Language `es-PE`, manejo de gzip/redirects
3. Parser `feedparser` con fallback a XML estándar (RSS 2.0 + Atom)
4. Limpieza de HTML en summaries, normalización de timestamps a ISO 8601
5. Detección automática de criticidad por keywords del título
6. **Si una fuente falla, cae automáticamente al modo demo para esa fuente**
   (no rompe el pipeline completo)
7. Twitter usa `TWITTER_BEARER_TOKEN` del entorno o `config.yaml`

## ⚠️ Seguridad: NO compartir credenciales

**La API de X (Twitter) NO acepta usuario/password.** Sólo autentica con **Bearer Token**.

**Pasos seguros para activar Twitter live:**

1. Cambia tu password de X si la has compartido en algún chat o lugar inseguro
2. Activa autenticación en dos factores
3. Ve a [developer.x.com](https://developer.x.com) con tu cuenta
4. Crea un Project + App, habilita el endpoint `tweets/search/recent`
5. Genera tu Bearer Token (cadena que empieza con `AAAA...`)
6. En tu terminal local:
   ```bash
   export TWITTER_BEARER_TOKEN="AAAA...tu_token_aqui"
   python -m apurisk.main --live
   ```

El Bearer Token se puede revocar y regenerar en cualquier momento sin afectar el password
de tu cuenta. Mantén el token solo en tu máquina (en un `.env` o variable de entorno) —
**nunca lo compartas en chat, código en repos públicos ni screenshots**.

## API Twitter / X

Para activar opinión pública en tiempo real:

1. Obtén un Bearer Token en [developer.x.com](https://developer.x.com)
2. Exporta:
   ```bash
   export TWITTER_BEARER_TOKEN="AAAA..."
   ```
3. Personaliza queries en `config.yaml` → `twitter_queries`
4. Ejecuta con `--live`

Si no hay token, el sistema cae automáticamente al modo demo con tweets sintéticos
realistas peruanos (Andina, RPP, La República, IDL, Gestión, Defensoría, IPSOS, etc.).

## Mapa geográfico

Usa **Leaflet** + tiles de **OpenStreetMap** (sin API key requerida). Cada marker:
- Color por severidad (rojo crítica, naranja alta, ámbar media)
- Tamaño proporcional a la criticidad
- Popup con título, resumen, región y **enlace a la fuente original**
- Auto-fit del bounds del mapa a los puntos detectados

El módulo `data/peru_geo.py` incluye centroides de los 24 departamentos del Perú
y coordenadas precisas de lugares clave: Las Bambas, Tía María, Coroccohuayco,
Espinar, Antamina, Conga, SJL, SMP, Cotabambas, Valle de Tambo, etc.

## Visualización de riesgos sin tablas

- **Matriz P×I**: bubble chart con cuadrantes (Chart.js)
- **Treemap**: por categoría de riesgo (Chart.js + chartjs-chart-treemap)
- **Factor cards**: cada factor es una tarjeta con barras de probabilidad e
  impacto, score destacado, evidencias clicables a fuentes
- **Entidades**: barras horizontales por menciones (no tabla)
- **Conflictos**: cards visuales por evento con badge de severidad

## Reglas de alertas

`VACANCIA_ACTIVADA`, `RENUNCIA_MINISTRO`, `BLOQUEO_VIA_NACIONAL`, `PARO_REGIONAL`,
`RIESGO_PAIS`, `INVESTIGACION_FORMAL`, `ATAQUE_VIOLENCIA`, `REFORMA_INSTITUCIONAL`,
`AUDIOS_FILTRADOS` — cada una con nivel CRÍTICA/ALTA y acción recomendada.

## Roadmap

- Persistencia SQLite/PostgreSQL para series temporales
- Sentimiento con `pysentimiento`/BETO en español
- NER con spaCy `es_core_news_lg`
- Notificaciones push (email/Slack/Telegram) cuando se gatillen alertas críticas
- Backend FastAPI + frontend React/Streamlit con auto-refresh
- Mapa choropleth del Perú con riesgo agregado por departamento
- Integración encuestas (IPSOS/Datum/Vox Populi) para opinión electoral
- Detección de bots y operaciones coordinadas en X
- Exportación a Power BI / Tableau
