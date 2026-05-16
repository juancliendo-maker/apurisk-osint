"""Dashboard HTML premium para analista OSINT — APURISK 1.0.

Single-file HTML self-contained con:
  - Chart.js (gráficos) + chartjs-chart-treemap (treemap de riesgos)
  - Leaflet (mapa real con OpenStreetMap)
  - Cards visuales en lugar de tablas
  - Ticker live, alertas, twitter feed
  - Cada referencia con URL clickable
"""
from __future__ import annotations
from datetime import datetime, timedelta
import json
import html as _html
from collections import Counter
from pathlib import Path

from ..data.peru_geo import buscar_coords
from ..utils.timezone_pe import now_pe, now_pe_iso, fmt_pe, fmt_pe_full, parse_to_pe


def _esc(s: str | None) -> str:
    return _html.escape(s or "", quote=True)


def _fmt_hours(h):
    if h is None or h == float("inf"):
        return "—"
    if h < 1:
        return f"hace {int(h * 60)}m"
    if h < 24:
        return f"hace {h:.1f}h"
    return f"hace {h/24:.1f}d"


_MESES = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "set", 10: "oct", 11: "nov", 12: "dic",
}


def _fmt_datetime(iso_str: str | None) -> str:
    """Formatea ISO 8601 a 'DD mes HH:MM PET' (hora de Lima)."""
    return fmt_pe(iso_str, with_tz=True)


def _short_url(url: str, max_len: int = 60) -> str:
    """Recorta una URL para visualización."""
    if not url:
        return ""
    import re
    m = re.match(r"https?://([^/]+)(/.*)?", url)
    if not m:
        return url[:max_len] + ("…" if len(url) > max_len else "")
    host = m.group(1)
    path = m.group(2) or ""
    if len(path) > max_len - len(host):
        path = path[: max_len - len(host) - 1] + "…"
    return f"{host}{path}"


def _format_metric(n: int) -> str:
    if n is None:
        return "0"
    if n >= 1000000:
        return f"{n/1000000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


CSS = """
:root {
  --bg-0:#0a0e1a; --bg-1:#0f172a; --bg-2:#1e293b; --bg-3:#334155;
  --txt-0:#f1f5f9; --txt-1:#cbd5e1; --txt-2:#94a3b8; --txt-3:#64748b;
  --accent:#38bdf8; --accent-2:#a78bfa;
  --critico:#ef4444; --alto:#f97316; --medio:#f59e0b; --bajo:#22c55e;
  --grid:#1e293b;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif; background: var(--bg-0); color: var(--txt-0); font-size: 14px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.app-header { display:flex; align-items:center; justify-content:space-between; padding: 14px 28px; border-bottom: 1px solid var(--bg-3); background: linear-gradient(180deg,#0c1220 0%, var(--bg-1) 100%); position:sticky; top:0; z-index:1000;}
.brand { display:flex; align-items:center; gap:14px;}
.brand .logo { width:38px; height:38px; border-radius:8px; background: linear-gradient(135deg, var(--accent), var(--accent-2)); display:flex; align-items:center; justify-content:center; font-weight:800; color:#0a0e1a; font-size:18px;}
.brand .name { font-weight:700; letter-spacing:.5px; font-size:16px;}
.brand .sub { font-size:11px; color:var(--txt-2); letter-spacing:1px; text-transform:uppercase;}
.live-status { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--txt-2);}
.live-dot { width:8px; height:8px; border-radius:50%; background:var(--bajo); box-shadow:0 0 8px var(--bajo); animation: pulse 2s infinite;}
@keyframes pulse { 0%,100% { opacity:1;} 50% { opacity:.4;} }

/* Refresh module */
.refresh-mod {
  display:flex; align-items:center; gap:14px; background: var(--bg-2);
  border: 1px solid var(--bg-3); border-radius: 8px; padding: 8px 14px;
  font-size: 12px;
}
.refresh-mod .label { color: var(--txt-2); text-transform: uppercase; letter-spacing: 1px; font-size: 10px;}
.refresh-mod .value { font-weight: 600; font-family: -apple-system, sans-serif;}
.refresh-mod .countdown { font-family: ui-monospace, "SF Mono", monospace; color: var(--accent); font-weight: 700; min-width: 64px; display:inline-block; text-align:center;}
.refresh-mod .countdown.warning { color: var(--medio);}
.refresh-mod .countdown.critical { color: var(--critico); animation: pulse 1s infinite;}
.refresh-mod button {
  background: var(--accent); color: var(--bg-0); border: none;
  padding: 6px 14px; border-radius: 6px; font-weight: 700; font-size: 12px;
  cursor: pointer; letter-spacing: .5px; text-transform: uppercase;
  transition: background .15s;
}
.refresh-mod button:hover { background: var(--accent-2); }
.refresh-mod button:disabled { background: var(--bg-3); color: var(--txt-2); cursor: not-allowed;}
.refresh-mod .sep { width: 1px; height: 24px; background: var(--bg-3);}
.refresh-mod .progress {
  width: 90px; height: 6px; background: var(--bg-3); border-radius: 3px; overflow: hidden;
}
.refresh-mod .progress > div {
  height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2));
  width: 100%; transition: width 1s linear;
}

/* Downloads */
.download-section { background: var(--bg-1); border: 1px solid var(--bg-3); border-radius: 8px; padding: 14px 18px; margin-bottom: 14px;}
.download-section h4 { font-size: 13px; color: var(--txt-0); margin-bottom: 12px; display:flex; align-items:center; gap: 8px; font-weight:600;}
.download-section h4 .count-badge { background: var(--bg-3); color: var(--txt-1); padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 4px;}
.download-item { display:flex; justify-content: space-between; align-items: center; padding: 10px 12px; background: var(--bg-2); border-radius: 6px; margin-bottom: 6px; gap: 12px;}
.download-item .dl-name { font-weight: 600; font-size: 13px; }
.download-item .dl-name a { color: var(--txt-0); text-decoration: none;}
.download-item .dl-name a:hover { color: var(--accent);}
.download-item .dl-meta { color: var(--txt-2); font-size: 11px; margin-top: 2px;}
.download-item .dl-btn {
  background: var(--accent); color: var(--bg-0); border: none;
  padding: 6px 14px; border-radius: 6px; font-weight: 700; font-size: 12px;
  text-decoration: none; letter-spacing: .5px; text-transform: uppercase;
  white-space: nowrap; transition: opacity .15s;
}
.download-item .dl-btn:hover { opacity: 0.85; text-decoration: none;}

/* Botón GRANDE para "Generar AHORA" */
.dl-btn-instant {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center;
  color: white; padding: 14px 12px; border-radius: 8px;
  font-weight: 700; font-size: 13px; text-decoration: none;
  letter-spacing: 0.3px; line-height: 1.3;
  transition: transform .15s, opacity .15s;
  box-shadow: 0 2px 4px rgba(0,0,0,0.15);
  min-height: 64px;
}
.dl-btn-instant small {
  display: block; font-size: 10px; font-weight: 400;
  opacity: 0.85; margin-top: 4px; letter-spacing: 0.5px;
}
.dl-btn-instant:hover {
  transform: translateY(-2px); text-decoration: none;
  box-shadow: 0 4px 8px rgba(0,0,0,0.25);
  opacity: 0.95;
}

/* Last update banner inside top of content */
.fresh-banner {
  background: linear-gradient(90deg, rgba(34,197,94,0.15), transparent);
  border-left: 3px solid var(--bajo);
  padding: 10px 18px; border-radius: 6px; margin-bottom: 14px;
  display:flex; align-items:center; gap: 14px; font-size: 13px;
}
.fresh-banner.stale { border-left-color: var(--medio); background: linear-gradient(90deg, rgba(245,158,11,0.15), transparent);}
.fresh-banner .icon { font-size: 18px;}
.fresh-banner strong { color: var(--txt-0);}
.fresh-banner .meta { color: var(--txt-2); margin-left: auto; font-size: 12px;}

.ticker { background: var(--bg-2); border-bottom: 1px solid var(--bg-3); overflow:hidden; height:36px; display:flex; align-items:center;}
.ticker .label { background: var(--critico); color:#fff; padding: 6px 14px; font-size:11px; font-weight:700; letter-spacing:1.5px; flex-shrink:0; margin-right: 14px;}
.ticker-track { display:flex; gap:48px; white-space:nowrap; animation: scroll 80s linear infinite;}
.ticker-item { color:var(--txt-1); font-size:13px;}
.ticker-item .src { color:var(--accent); font-weight:600; margin-right:8px;}
@keyframes scroll { 0% { transform: translateX(0);} 100% { transform: translateX(-50%);} }

.kpi-row { display:grid; grid-template-columns: repeat(5, 1fr); gap: 14px; padding: 18px 28px;}
.kpi { background: var(--bg-1); border: 1px solid var(--bg-3); border-radius: 10px; padding: 14px 18px; position:relative; overflow:hidden;}
.kpi::before { content:""; position:absolute; top:0; left:0; width:3px; height:100%; background: var(--accent);}
.kpi.critico::before { background: var(--critico);}
.kpi.alto::before { background: var(--alto);}
.kpi.medio::before { background: var(--medio);}
.kpi.bajo::before { background: var(--bajo);}
.kpi .lbl { font-size:11px; letter-spacing:1px; color: var(--txt-2); text-transform:uppercase; }
.kpi .val { font-size: 28px; font-weight: 700; margin-top: 6px;}
.kpi .sub { color: var(--txt-2); font-size: 12px; margin-top:4px;}
.nivel-CRÍTICO, .nivel-ALTO-cls, .val-CRÍTICO { color: var(--critico);}
.nivel-ALTO { color: var(--alto);}
.nivel-MEDIO { color: var(--medio);}
.nivel-BAJO { color: var(--bajo);}

.tabs { display:flex; gap:0; padding: 0 28px; border-bottom: 1px solid var(--bg-3); background: var(--bg-0); position:sticky; top: 64px; z-index: 100;}
.tab { padding: 12px 20px; cursor:pointer; color: var(--txt-2); font-weight:500; border-bottom: 2px solid transparent; transition: all .15s; font-size:13px; white-space: nowrap;}
.tab:hover { color: var(--txt-0);}
.tab.active { color: var(--accent); border-bottom-color: var(--accent);}
.tab .count { background: var(--bg-3); color: var(--txt-0); padding: 2px 8px; border-radius: 10px; font-size:11px; margin-left:8px;}
.tab.active .count { background: var(--accent); color: var(--bg-0);}

.content { padding: 18px 28px 60px;}
.tab-panel { display: none;}
.tab-panel.active { display:block;}

.grid { display:grid; gap:14px;}
.grid-12 { grid-template-columns: repeat(12, 1fr);}
.span-3 { grid-column: span 3;} .span-4 { grid-column: span 4;} .span-5 { grid-column: span 5;}
.span-6 { grid-column: span 6;} .span-7 { grid-column: span 7;} .span-8 { grid-column: span 8;} .span-12 { grid-column: span 12;}

.card { background: var(--bg-1); border: 1px solid var(--bg-3); border-radius: 10px; padding: 18px;}
.card h3 { font-size: 11px; letter-spacing: 1.5px; color: var(--txt-2); text-transform: uppercase; margin-bottom: 14px; display:flex; justify-content:space-between; align-items:center;}
.card h3 .badge { background: var(--bg-3); color: var(--txt-1); padding: 2px 8px; border-radius: 4px; font-size:10px; letter-spacing: .5px;}

/* Risk Matrix bubble */
.matrix-canvas-host { position: relative; height: 360px;}

/* Risk Factor Cards (no table) */
.factors-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px;}
.factor-card { background: var(--bg-2); border: 1px solid var(--bg-3); border-radius: 8px; padding: 14px; position: relative; overflow: hidden;}
.factor-card::before { content:""; position: absolute; top:0; left:0; right: 0; height: 3px; background: var(--medio);}
.factor-card.CRÍTICO::before { background: var(--critico);}
.factor-card.ALTO::before { background: var(--alto);}
.factor-card.MEDIO::before { background: var(--medio);}
.factor-card.BAJO::before { background: var(--bajo);}
.factor-head { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom: 10px;}
.factor-head .titulo { font-weight: 600; font-size:13px; line-height:1.3;}
.factor-head .cat { font-size: 10px; color: var(--txt-2); text-transform: uppercase; letter-spacing: 1px; margin-top: 3px;}
.factor-head .score-pill { background: var(--bg-3); border-radius: 6px; padding: 6px 10px; text-align: center; min-width: 60px;}
.factor-head .score-pill .num { font-weight:700; font-size: 18px;}
.factor-head .score-pill .lbl { font-size: 9px; letter-spacing:1px; text-transform:uppercase; color: var(--txt-2);}
.factor-bars { display: grid; grid-template-columns: 50px 1fr 32px; gap: 8px; align-items: center; margin: 6px 0; font-size:11px;}
.factor-bars .bar-label { color: var(--txt-2); text-transform: uppercase; letter-spacing:.5px;}
.bar { height: 6px; background: var(--bg-3); border-radius: 3px; overflow: hidden;}
.bar > div { height: 100%; border-radius: 3px;}
.bar.prob > div { background: linear-gradient(90deg, var(--accent), var(--accent-2));}
.bar.imp > div { background: linear-gradient(90deg, var(--medio), var(--alto));}
.factor-meta { display:flex; gap:10px; font-size:11px; color: var(--txt-2); margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--bg-3);}
.factor-meta .tendencia.up { color: var(--critico);}
.factor-meta .tendencia.down { color: var(--bajo);}
.factor-evidence { margin-top: 8px; font-size: 11px; }
.factor-evidence a { display:inline-block; background: var(--bg-3); padding: 2px 8px; border-radius: 4px; margin: 2px 2px 0 0; color: var(--txt-1);}
.factor-evidence a:hover { background: var(--accent); color: var(--bg-0); text-decoration: none;}

/* Map */
#peru-map { height: 540px; border-radius: 8px;}
.leaflet-container { background: #0a0e1a !important; }

/* Alerts */
.alerta { padding: 14px; border-left: 3px solid var(--medio); background: var(--bg-2); border-radius: 6px; margin-bottom: 10px; }
.alerta.CRÍTICA { border-left-color: var(--critico); background: linear-gradient(90deg, rgba(239,68,68,0.08), var(--bg-2));}
.alerta.ALTA { border-left-color: var(--alto);}
.alerta-head { display:flex; align-items:center; justify-content:space-between; margin-bottom: 8px; flex-wrap: wrap; gap:6px;}
.alerta .nivel-tag { font-size: 10px; font-weight: 700; letter-spacing: 1.5px; padding: 3px 8px; border-radius: 4px;}
.alerta.CRÍTICA .nivel-tag { background: var(--critico); color: #fff;}
.alerta.ALTA .nivel-tag { background: var(--alto); color: #fff;}
.alerta.MEDIA .nivel-tag { background: var(--medio); color: #fff;}
.alerta .meta { color: var(--txt-2); font-size: 12px;}
.alerta .titulo { font-size: 14px; font-weight: 600; margin: 6px 0; line-height: 1.4;}
.alerta .titulo a { color: var(--txt-0);}
.alerta .titulo a:hover { color: var(--accent); text-decoration: none;}
.alerta .resumen { color: var(--txt-1); font-size: 13px; line-height: 1.5;}
.alerta .accion { margin-top: 8px; padding: 8px 10px; background: var(--bg-1); border-radius: 6px; font-size: 12px; color: var(--txt-1); border-left: 2px solid var(--accent);}
.alerta .accion strong { color: var(--accent); letter-spacing:.5px; font-size:11px; text-transform:uppercase; display:block; margin-bottom:2px;}
.alerta .links { margin-top: 8px; display:flex; flex-wrap:wrap; gap: 8px; align-items: center;}
.alerta .links a { font-size: 12px; padding: 3px 10px; background: var(--bg-1); border-radius: 4px; border: 1px solid var(--bg-3);}
.alerta .links a:hover { background: var(--accent); color: var(--bg-0); border-color: var(--accent); text-decoration: none;}
.alerta .links .regla { color: var(--txt-3); font-family: monospace; font-size: 11px;}

/* Fresh / Recent pills */
.fresh-pill { display:inline-block; padding: 2px 8px; border-radius: 10px; font-size: 9px; font-weight: 700; letter-spacing: 1px; margin-left: 6px;}
.fresh-pill.new { background: var(--bajo); color: var(--bg-0); animation: pulse 1.5s infinite;}
.fresh-pill.recent { background: var(--accent); color: var(--bg-0);}

/* Headlines */
.headline { padding: 12px 0; border-bottom: 1px solid var(--bg-3);}
.headline:last-child { border-bottom: none;}
.headline .src { font-size: 11px; color: var(--accent); font-weight: 600; text-transform: uppercase; letter-spacing: 1px;}
.headline .src .crit { padding: 1px 6px; border-radius: 3px; margin-left: 6px; font-size:10px;}
.headline .src .crit.alta { background: var(--critico); color:#fff;}
.headline .src .crit.media { background: var(--medio); color:#fff;}
.headline .src .crit.baja { background: var(--bajo); color:#fff;}
.headline .titulo { font-size: 14px; font-weight: 600; margin-top: 4px; line-height: 1.4;}
.headline .titulo a { color: var(--txt-0);}
.headline .titulo a:hover { color: var(--accent); text-decoration: none;}
.headline .resumen { color: var(--txt-2); font-size: 12.5px; margin-top: 4px; line-height: 1.5;}
.headline .meta { color: var(--txt-3); font-size: 11px; margin-top: 4px;}

/* Twitter feed */
.tweet { padding: 14px; background: var(--bg-2); border-radius: 8px; margin-bottom: 10px; border: 1px solid var(--bg-3);}
.tweet-head { display:flex; gap: 10px; align-items: center; margin-bottom: 8px;}
.tweet-avatar { width: 38px; height: 38px; border-radius: 50%; background: linear-gradient(135deg, #1da1f2, #0c87c5); display:flex; align-items:center; justify-content:center; font-weight: 700; color: white; font-size: 14px;}
.tweet-user .name { font-weight: 600; font-size: 13px; display:flex; align-items:center; gap:4px;}
.tweet-user .name .verified { color: #1da1f2; font-size: 12px;}
.tweet-user .handle { color: var(--txt-2); font-size: 12px;}
.tweet-text { font-size: 13.5px; line-height: 1.5; color: var(--txt-0); margin-bottom: 10px;}
.tweet-text .hashtag { color: var(--accent);}
.tweet-text .mention { color: var(--accent-2);}
.tweet-meta { display:flex; gap: 16px; color: var(--txt-2); font-size: 12px; align-items: center;}
.tweet-meta .metric { display:flex; align-items: center; gap: 4px;}
.tweet-meta .time { margin-left: auto; color: var(--txt-3);}
.tweet-meta a { color: var(--accent);}
.viral-badge { background: linear-gradient(90deg, var(--alto), var(--critico)); color: white; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px; letter-spacing: 1px; margin-left: 8px;}

/* Hashtag pills */
.hashtag-pills { display: flex; flex-wrap: wrap; gap: 6px;}
.hashtag-pill { background: var(--bg-2); border: 1px solid var(--bg-3); padding: 4px 10px; border-radius: 16px; font-size: 12px; display: flex; align-items: center; gap: 6px;}
.hashtag-pill .tag { color: var(--accent); font-weight: 600;}
.hashtag-pill .count { color: var(--txt-2); font-size: 11px;}

/* Conflicts cards */
.conflict-card { background: var(--bg-2); border: 1px solid var(--bg-3); border-radius: 8px; padding: 14px; margin-bottom: 10px; border-left: 3px solid var(--medio);}
.conflict-card.alta { border-left-color: var(--critico);}
.conflict-card.media { border-left-color: var(--medio);}
.conflict-card .head { display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;}
.conflict-card .titulo { font-weight: 600; font-size: 13px;}
.conflict-card .region-tag { background: var(--bg-3); padding: 2px 8px; border-radius: 4px; font-size: 11px; color: var(--txt-1);}
.conflict-card .desc { color: var(--txt-1); font-size: 12.5px; line-height: 1.5;}
.conflict-card .meta { color: var(--txt-2); font-size: 11px; margin-top: 6px; display:flex; justify-content:space-between; align-items:center;}

/* PL cards */
.pl-card { background: var(--bg-2); border: 1px solid var(--bg-3); border-radius: 8px; padding: 14px; margin-bottom: 10px;}
.pl-card .titulo { font-weight: 600; font-size: 13px; margin-bottom: 4px;}
.pl-card .titulo a { color: var(--txt-0);}
.pl-card .titulo a:hover { color: var(--accent);}
.pl-card .meta { display:flex; gap: 8px; font-size: 11px; color: var(--txt-2); margin-top: 6px;}
.pl-card .meta .tag { background: var(--bg-3); padding: 2px 8px; border-radius: 4px;}
.pl-card .resumen { color: var(--txt-1); font-size: 12.5px; line-height: 1.5; margin-top: 6px;}

footer { text-align: center; padding: 24px; color: var(--txt-3); font-size: 11px; border-top: 1px solid var(--bg-3); margin-top: 28px;}

/* Leaflet popup styling */
.leaflet-popup-content-wrapper { background: var(--bg-2) !important; color: var(--txt-0) !important; border-radius: 6px !important;}
.leaflet-popup-content { margin: 12px 14px !important; font-size: 13px !important; line-height: 1.5;}
.leaflet-popup-content a { color: var(--accent); }
.leaflet-popup-tip { background: var(--bg-2) !important;}
.leaflet-popup-content strong { color: var(--txt-0);}
.leaflet-control-attribution { background: rgba(15,23,42,0.7) !important; color: var(--txt-3) !important;}
.leaflet-control-attribution a { color: var(--accent) !important;}
"""


def _factor_card(f: dict) -> str:
    tend_cls = {"↑": "up", "↓": "down", "→": ""}[f["tendencia"]]
    evid_links = ""
    for e in f.get("evidencias", [])[:4]:
        if e.get("url"):
            tooltip = f"{e['title']} ({_short_url(e['url'])})"
            evid_links += (
                f"<a href='{_esc(e['url'])}' target='_blank' rel='noopener' "
                f"title='{_esc(tooltip)}'>{_esc(e['source'])}</a>"
            )
    return f"""
    <div class="factor-card {f['nivel']}">
      <div class="factor-head">
        <div>
          <div class="titulo">{_esc(f['nombre'])}</div>
          <div class="cat">{_esc(f['categoria'])}</div>
        </div>
        <div class="score-pill">
          <div class="num nivel-{f['nivel']}">{f['score']}</div>
          <div class="lbl nivel-{f['nivel']}">{f['nivel']}</div>
        </div>
      </div>
      <div class="factor-bars">
        <span class="bar-label">PROB</span>
        <div class="bar prob"><div style="width:{f['probabilidad']}%"></div></div>
        <span style="text-align:right; font-weight:600;">{f['probabilidad']}</span>
      </div>
      <div class="factor-bars">
        <span class="bar-label">IMP</span>
        <div class="bar imp"><div style="width:{f['impacto']}%"></div></div>
        <span style="text-align:right; font-weight:600;">{f['impacto']}</span>
      </div>
      <div class="factor-meta">
        <span class="tendencia {tend_cls}">{f['tendencia']}</span>
        <span>{f['menciones_24h']} menc. 24h</span>
        <span>(prev: {f['menciones_72h']})</span>
      </div>
      <div class="factor-evidence">{evid_links}</div>
    </div>
    """


def _alerta_html(a: dict) -> str:
    url_link = ""
    if a.get("url"):
        url_link = (
            f"<a href='{_esc(a['url'])}' target='_blank' rel='noopener' title='{_esc(a['url'])}'>"
            f"🔗 {_esc(_short_url(a['url']))}</a>"
        )
    region_tag = f"<span style='color:#cbd5e1;'>📍 {_esc(a['region'])}</span>" if a.get("region") else ""
    titulo_html = _esc(a['titulo'])
    if a.get('url'):
        titulo_html = f"<a href='{_esc(a['url'])}' target='_blank' rel='noopener'>{titulo_html}</a>"
    fecha_iso = a.get("timestamp") or ""
    fecha_legible = _fmt_datetime(fecha_iso)
    return f"""
    <div class="alerta {a['nivel']}">
      <div class="alerta-head">
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
          <span class="nivel-tag">{a['nivel']}</span>
          <span class="meta">{_esc(a['categoria'])}</span>
          {region_tag}
          <span class="meta" title="{_esc(fecha_iso)}">🕒 {fecha_legible} · {_fmt_hours(a['hours_ago'])}</span>
        </div>
      </div>
      <div class="titulo">{titulo_html}</div>
      <div class="resumen">{_esc(a['resumen'])}</div>
      <div class="accion"><strong>ACCIÓN RECOMENDADA</strong> {_esc(a['accion'])}</div>
      <div class="links">
        <span style="color:var(--txt-2); font-size:11px;">Fuente:</span> {url_link}
        <span class="regla">Regla: {_esc(a['regla'])}</span>
      </div>
    </div>
    """


def _headline_html(a) -> str:
    crit_cls = a.criticidad if a.criticidad in ("alta","media","baja") else "media"
    titulo = _esc(a.title)
    if a.url:
        titulo = f"<a href='{_esc(a.url)}' target='_blank' rel='noopener'>{titulo}</a>"
    url_link = ""
    if a.url:
        url_link = (
            f" · <a href='{_esc(a.url)}' target='_blank' rel='noopener' title='{_esc(a.url)}'>"
            f"🔗 {_esc(_short_url(a.url, 50))}</a>"
        )
    fecha_legible = _fmt_datetime(a.published)
    # Badge "RECIENTE" si <2h, "ÚLTIMAS 24H" si <24h
    badge = ""
    h = a.hours_ago()
    if h <= 2:
        badge = "<span class='fresh-pill new'>● AHORA</span>"
    elif h <= 24:
        badge = "<span class='fresh-pill recent'>● 24H</span>"
    return f"""
    <div class="headline">
      <div class="src">{_esc(a.source_name)} <span class="crit {crit_cls}">{crit_cls.upper()}</span> {badge}</div>
      <div class="titulo">{titulo}</div>
      <div class="resumen">{_esc((a.summary or '')[:280])}</div>
      <div class="meta" title="{_esc(a.published or '')}">🕒 {fecha_legible} · {_fmt_hours(a.hours_ago())}{url_link}</div>
    </div>
    """


def _tweet_html(t, viral: bool = False) -> str:
    raw = t.raw or {}
    handle = raw.get("handle", "user")
    name = raw.get("name", handle)
    verified_html = "<span class='verified'>✓</span>" if raw.get("verified") else ""
    metrics = raw.get("metrics", {})

    # render text con hashtags y mentions
    text = _esc(t.summary or t.title)
    import re
    text = re.sub(r"(#\w+)", r"<span class='hashtag'>\1</span>", text)
    text = re.sub(r"(@\w+)", r"<span class='mention'>\1</span>", text)

    avatar_letter = (name[:1] or "?").upper()
    viral_badge = "<span class='viral-badge'>VIRAL</span>" if viral else ""

    return f"""
    <div class="tweet">
      <div class="tweet-head">
        <div class="tweet-avatar">{_esc(avatar_letter)}</div>
        <div class="tweet-user">
          <div class="name">{_esc(name)} {verified_html}{viral_badge}</div>
          <div class="handle">@{_esc(handle)}</div>
        </div>
      </div>
      <div class="tweet-text">{text}</div>
      <div class="tweet-meta">
        <span class="metric">🔁 {_format_metric(metrics.get('retweet_count', 0))}</span>
        <span class="metric">❤ {_format_metric(metrics.get('like_count', 0))}</span>
        <span class="metric">💬 {_format_metric(metrics.get('reply_count', 0))}</span>
        <span class="metric">📌 {_format_metric(metrics.get('quote_count', 0))}</span>
        <span class="time" title="{_esc(t.published or '')}">🕒 {_fmt_datetime(t.published)} · {_fmt_hours(t.hours_ago())} · <a href='{_esc(t.url)}' target='_blank' rel='noopener' title='{_esc(t.url)}'>🔗 ver en X →</a></span>
      </div>
    </div>
    """


def _conflict_card(c) -> str:
    raw = c.raw or {}
    sev = raw.get("severidad", "media")
    region = raw.get("region") or c.region or "—"
    url_link = ""
    if c.url:
        url_link = (
            f"<a href='{_esc(c.url)}' target='_blank' rel='noopener' title='{_esc(c.url)}'>"
            f"🔗 {_esc(_short_url(c.url, 55))}</a>"
        )
    titulo_html = _esc(c.title)
    if c.url:
        titulo_html = f"<a href='{_esc(c.url)}' target='_blank' rel='noopener'>{titulo_html}</a>"
    return f"""
    <div class="conflict-card {sev}">
      <div class="head">
        <div class="titulo">{titulo_html}</div>
        <span class="region-tag">📍 {_esc(region)}</span>
      </div>
      <div class="desc">{_esc(c.summary)}</div>
      <div class="meta">
        <span title="{_esc(c.published or '')}">{_esc(raw.get('tipo','—'))} · severidad <strong class="nivel-{sev.upper() if sev=='alta' else 'MEDIO' if sev=='media' else 'BAJO'}">{sev}</strong> · {_esc(raw.get('estado','—'))} · 🕒 {_fmt_datetime(c.published)} · {_fmt_hours(c.hours_ago())}</span>
        {url_link}
      </div>
    </div>
    """


def _sanitizar_url(url: str) -> str:
    """Corrige URLs de Twitter con IDs ficticios apuntando al perfil.

    Los IDs demo legacy empiezan con '181512'. Para esos, reescribimos a
    https://x.com/USER que es verificable.
    """
    if not url:
        return ""
    import re
    m = re.match(r"https?://(?:twitter|x)\.com/([^/]+)/status/(\d+)", url)
    if m:
        handle, tid = m.group(1), m.group(2)
        # Heurística: si el ID está en el rango de los demos (alrededor de 18151200000xx),
        # asumimos que es ficticio y devolvemos el perfil
        if tid.startswith("181512") and len(tid) <= 16:
            return f"https://x.com/{handle}"
    return url


def _analizar_tendencias_semana(output_dir: str | None) -> dict:
    """Analiza snapshots de últimos 7 días para detectar persistencia y tendencias."""
    if not output_dir:
        return {"snapshots": 0, "score_serie": [], "alertas_persistentes": [], "factores_serie": {}}
    import glob, json
    from collections import defaultdict
    p = Path(output_dir)
    cutoff = now_pe() - timedelta(days=7)
    snaps = []
    for f in sorted(p.glob("apurisk_snapshot_*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            gen = data.get("generado", "")
            try:
                dt = parse_to_pe(gen) or datetime.fromisoformat(gen)
                if dt >= cutoff:
                    snaps.append((dt, data))
            except Exception:
                pass
        except Exception:
            continue

    if not snaps:
        return {"snapshots": 0, "score_serie": [], "alertas_persistentes": [], "factores_serie": {}}

    # Serie temporal del score
    score_serie = []
    for dt, data in snaps:
        score_serie.append({
            "ts": dt.isoformat(timespec="seconds"),
            "label": dt.strftime("%d %b %H:%M"),
            "score": data.get("riesgo", {}).get("global", 0),
            "nivel": data.get("riesgo", {}).get("nivel", "—"),
            "alertas_criticas": len([a for a in data.get("alertas", []) if a["nivel"] == "CRÍTICA"]),
            "alertas_total": len(data.get("alertas", [])),
        })

    # Alertas persistentes — agrupadas por título normalizado
    grupos = defaultdict(list)
    for dt, data in snaps:
        for a in data.get("alertas", []):
            # llave normalizada por título base
            key = a.get("titulo", "")[:90].lower().strip()
            grupos[key].append({
                "ts": dt,
                "alerta": a,
            })
    persistentes = []
    for key, ocurrencias in grupos.items():
        if not key or len(ocurrencias) < 2:
            continue
        last = ocurrencias[-1]["alerta"]
        timestamps = [o["ts"] for o in ocurrencias]
        dias_distintos = len({t.date() for t in timestamps})
        persistentes.append({
            "titulo": last.get("titulo", ""),
            "categoria": last.get("categoria", ""),
            "nivel": last.get("nivel", ""),
            "regla": last.get("regla", ""),
            "fuente": last.get("fuente", ""),
            "url": _sanitizar_url(last.get("url", "")),  # corrige URLs Twitter ficticios legacy
            "ocurrencias": len(ocurrencias),
            "dias_activo": dias_distintos,
            "primera_vez": min(timestamps).isoformat(timespec="seconds"),
            "ultima_vez": max(timestamps).isoformat(timespec="seconds"),
        })
    persistentes.sort(key=lambda x: (-x["dias_activo"], -x["ocurrencias"]))

    # Serie por factor (top 5 factores cómo evolucionan en el período)
    factor_serie = defaultdict(list)
    for dt, data in snaps:
        for f in data.get("matriz_riesgo", [])[:8]:
            factor_serie[f["nombre"]].append({
                "ts": dt.isoformat(timespec="seconds"),
                "label": dt.strftime("%d %b"),
                "score": f["score"],
                "prob": f["probabilidad"],
                "imp": f["impacto"],
            })

    return {
        "snapshots": len(snaps),
        "primer_snap": snaps[0][0].isoformat(timespec="seconds") if snaps else None,
        "ultimo_snap": snaps[-1][0].isoformat(timespec="seconds") if snaps else None,
        "score_serie": score_serie,
        "alertas_persistentes": persistentes[:25],
        "factores_serie": dict(factor_serie),
    }


def _render_tendencias(t: dict) -> str:
    if t["snapshots"] == 0:
        return """
        <div class='card span-12'>
          <h3>📈 Tendencias semanales</h3>
          <div style='padding: 18px; color: var(--txt-2); line-height: 1.7;'>
            <p>Aún no hay suficientes snapshots para análisis de tendencia.</p>
            <p>El sistema necesita acumular varias corridas (mínimo 2-3 días) para detectar
            patrones persistentes. Ejecuta en modo <code>--live --watch 1800</code> para
            que el ciclo cada 30 min vaya acumulando historial en <code>output/</code>.</p>
            <p>En cada nueva ejecución se detectará:</p>
            <ul>
              <li>Alertas que reaparecen (casos persistentes)</li>
              <li>Evolución del score global</li>
              <li>Factores que suben/bajan de severidad</li>
            </ul>
          </div>
        </div>
        """

    # Persistent alerts
    rows_persistentes = ""
    for p in t["alertas_persistentes"]:
        url_link = ""
        if p.get("url"):
            url_link = f"<a href='{_esc(p['url'])}' target='_blank' rel='noopener' title='{_esc(p['url'])}'>🔗 fuente</a>"
        nivel_color = {"CRÍTICA": "var(--critico)", "ALTA": "var(--alto)", "MEDIA": "var(--medio)"}.get(p["nivel"], "var(--accent)")
        rows_persistentes += f"""
        <tr>
          <td><strong style='color:{nivel_color};'>{_esc(p['nivel'])}</strong></td>
          <td><strong>{_esc(p['titulo'])}</strong><br>
              <span style='color:var(--txt-2); font-size:11px;'>{_esc(p['categoria'])} · regla {_esc(p['regla'])}</span></td>
          <td style='text-align:center;'><strong>{p['dias_activo']}</strong> días<br>
              <span style='color:var(--txt-2); font-size:10px;'>{p['ocurrencias']} apariciones</span></td>
          <td style='font-size:11px; color:var(--txt-2);'>
              📅 {_fmt_datetime(p['primera_vez'])}<br>
              🕒 {_fmt_datetime(p['ultima_vez'])}</td>
          <td>{url_link}</td>
        </tr>
        """
    if not rows_persistentes:
        rows_persistentes = "<tr><td colspan='5' style='text-align:center; color:var(--txt-2); padding:14px;'><em>Aún no hay alertas que se hayan repetido. Cuando se acumulen más snapshots aparecerán acá.</em></td></tr>"

    return f"""
    <div class='card span-12' style='background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); margin-bottom: 14px;'>
      <h3>📈 Análisis semanal de tendencias <span class='badge'>{t['snapshots']} snapshots · período: {_fmt_datetime(t['primer_snap'])} → {_fmt_datetime(t['ultimo_snap'])}</span></h3>
      <div style='font-size:13px; color:var(--txt-1); line-height:1.6;'>
        Esta vista analiza los snapshots acumulados de los últimos 7 días para identificar
        <strong>casos persistentes</strong> (alertas que se repiten día tras día) y la
        <strong>evolución del riesgo global</strong>.
      </div>
    </div>

    <div class='card span-7'>
      <h3>📊 Evolución del Score de riesgo · 7 días</h3>
      <div style='height: 280px;'><canvas id='scoreSerieChart'></canvas></div>
    </div>

    <div class='card span-5'>
      <h3>🚨 Volumen de alertas por snapshot</h3>
      <div style='height: 280px;'><canvas id='alertasSerieChart'></canvas></div>
    </div>

    <div class='card span-12'>
      <h3>♻️ Casos persistentes · alertas que se siguen presentando <span class='badge'>{len(t['alertas_persistentes'])} casos</span></h3>
      <table>
        <thead>
          <tr>
            <th>Nivel</th>
            <th>Caso / Categoría</th>
            <th style='text-align:center;'>Persistencia</th>
            <th>Primera y última aparición</th>
            <th>Fuente</th>
          </tr>
        </thead>
        <tbody>{rows_persistentes}</tbody>
      </table>
    </div>

    <div class='card span-12'>
      <h3>📉 Tendencia de los top 5 factores</h3>
      <div style='height: 320px;'><canvas id='factoresSerieChart'></canvas></div>
    </div>
    """


def _scan_descargas(output_dir: str | None) -> dict:
    """Escanea el directorio de output y devuelve los archivos disponibles para descarga."""
    if not output_dir:
        return {"diarios_pdf": [], "semanales_pdf": [], "diarios_docx": [], "ejecutivo_docx": [], "alertas": [], "snapshots": []}
    import os
    p = Path(output_dir)
    if not p.exists():
        return {"diarios_pdf": [], "semanales_pdf": [], "diarios_docx": [], "ejecutivo_docx": [], "alertas": [], "snapshots": []}

    def _info(path):
        try:
            stat = os.stat(path)
            size_kb = stat.st_size / 1024
            mtime = datetime.fromtimestamp(stat.st_mtime)
            return {
                "name": Path(path).name,
                # Cuando el dashboard se sirve desde el endpoint /dashboard del FastAPI server,
                # los archivos viven bajo /output/ (montado como StaticFiles).
                # El path absoluto desde la raíz del server SIEMPRE funciona.
                "path": f"/output/{Path(path).name}",
                "size": f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB",
                "mtime": mtime,
                "mtime_str": _fmt_datetime(mtime.isoformat(timespec='seconds')),
            }
        except Exception:
            return None

    def _list(pattern):
        return sorted(
            [_info(f) for f in p.glob(pattern) if _info(f)],
            key=lambda x: x["mtime"], reverse=True,
        )

    return {
        "diarios_pdf": _list("apurisk_diario_*.pdf"),
        "semanales_pdf": _list("apurisk_semanal_*.pdf"),
        "diarios_docx": _list("apurisk_reporte_24h_*.docx"),
        "diarios_html": _list("apurisk_reporte_24h_*.html"),
        "alertas_pdf": _list("apurisk_alertas_*.pdf"),
        "alertas_docx": _list("apurisk_alertas_*.docx"),
        "alertas_html": _list("apurisk_alertas_*.html"),
        "ejecutivo_docx": _list("apurisk_ejecutivo_*.docx"),
        # NUEVOS: reportes ejecutivos diarios visuales (≤3 páginas, foco tendencias)
        "ejecutivo_diario_pdf": _list("apurisk_ejecutivo_diario_*.pdf"),
        "ejecutivo_diario_docx": _list("apurisk_ejecutivo_diario_*.docx"),
        "snapshots": _list("apurisk_snapshot_*.json"),
        "dashboards": _list("apurisk_dashboard_*.html"),
    }


def _render_descargas(descargas: dict) -> str:
    """HTML del panel de descargas con dos secciones:
       1) GENERAR AHORA — botones que disparan endpoints /api/reporte/...
          (funcionan SIEMPRE, generan el reporte al instante con datos más frescos)
       2) ARCHIVOS YA GENERADOS — listado de los reportes en el output/
    """

    # ===== SECCIÓN 1: Botones "Generar AHORA" (SIEMPRE visibles y funcionales) =====
    instant_html = """
    <div class='download-section' style='background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); border: 1px solid var(--accent); margin-bottom: 18px;'>
      <h4>⚡ Generar reporte AHORA <span class='count-badge' style='background: var(--accent); color: var(--bg-0);'>al instante</span></h4>
      <div style='color: var(--txt-1); font-size: 13px; line-height: 1.6; margin-bottom: 12px;'>
        Estos botones generan el reporte <b>en el momento</b> con los datos más recientes
        del scheduler. El archivo se descarga inmediatamente.
      </div>
      <div style='display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px;'>
        <a href='/api/reporte/ejecutivo/pdf' target='_blank' class='dl-btn-instant' style='background: #DC2626;'>
          📄 Ejecutivo PDF<br><small>≤3 págs · foco tendencias</small>
        </a>
        <a href='/api/reporte/ejecutivo/docx' target='_blank' class='dl-btn-instant' style='background: #1E40AF;'>
          📝 Ejecutivo Word<br><small>≤3 págs · editable</small>
        </a>
        <a href='/api/reporte/24h/html' target='_blank' class='dl-btn-instant' style='background: #38BDF8;'>
          📰 Reporte 24h HTML<br><small>imprimible · web</small>
        </a>
        <a href='/api/reporte/24h/docx' target='_blank' class='dl-btn-instant' style='background: #1E40AF;'>
          📝 Reporte 24h Word<br><small>completo · editable</small>
        </a>
        <a href='/api/reporte/alertas/html' target='_blank' class='dl-btn-instant' style='background: #EA580C;'>
          🚨 Alertas HTML<br><small>feed crítico</small>
        </a>
        <a href='/api/reporte/alertas/docx' target='_blank' class='dl-btn-instant' style='background: #DC2626;'>
          🚨 Alertas Word<br><small>con acciones</small>
        </a>
        <a href='/api/reporte/diario/pdf' target='_blank' class='dl-btn-instant' style='background: #A78BFA;'>
          📊 Diario PDF<br><small>detalle completo</small>
        </a>
        <a href='/api/reporte/semanal/pdf' target='_blank' class='dl-btn-instant' style='background: #A78BFA;'>
          📅 Semanal PDF<br><small>tendencias 7 días</small>
        </a>
      </div>
    </div>
    """

    def _section(titulo, icono, items, fmt, color="var(--accent)"):
        if not items:
            return f"""
            <div class='download-section'>
              <h4>{icono} {titulo} <span class='count-badge' style='background:var(--bg-3); color:var(--txt-2);'>0</span></h4>
              <div style='color:var(--txt-2); font-size:12px; padding: 8px;'><em>El scheduler generará archivos en el siguiente ciclo (~30 min). Mientras tanto, usa los botones <b>"Generar AHORA"</b> arriba.</em></div>
            </div>
            """
        rows = ""
        for it in items[:10]:  # Top 10 más recientes
            rows += f"""
            <div class='download-item'>
              <div>
                <div class='dl-name'><a href='{_esc(it["path"])}' download title='Descargar {_esc(it["name"])}'>{_esc(it["name"])}</a></div>
                <div class='dl-meta'>🕒 {_esc(it["mtime_str"])} · 📦 {_esc(it["size"])} · {fmt}</div>
              </div>
              <a href='{_esc(it["path"])}' download class='dl-btn' style='background: {color};'>⬇ Descargar</a>
            </div>
            """
        return f"""
        <div class='download-section'>
          <h4>{icono} {titulo} <span class='count-badge'>{len(items)}</span></h4>
          {rows}
        </div>
        """

    archivos_html = (
        _section("Ejecutivo Diario PDF (≤3 págs)", "📄", descargas.get("ejecutivo_diario_pdf", []), "PDF", "var(--critico)") +
        _section("Ejecutivo Diario Word (≤3 págs)", "📝", descargas.get("ejecutivo_diario_docx", []), "DOCX", "var(--accent)") +
        _section("Reportes Diarios PDF (detallado)", "📄", descargas["diarios_pdf"], "PDF", "var(--accent-2)") +
        _section("Reportes Semanales PDF", "📅", descargas["semanales_pdf"], "PDF", "var(--accent-2)") +
        _section("Reporte 24h (HTML imprimible)", "📰", descargas["diarios_html"], "HTML", "var(--accent)") +
        _section("Reporte 24h (Word)", "📝", descargas["diarios_docx"], "DOCX") +
        _section("Reporte ejecutivo clásico (Word)", "📋", descargas["ejecutivo_docx"], "DOCX") +
        _section("Alertas Inmediatas (Word)", "🚨", descargas["alertas_docx"], "DOCX", "var(--critico)") +
        _section("Alertas Inmediatas (HTML)", "🚨", descargas["alertas_html"], "HTML", "var(--alto)") +
        _section("Snapshots de datos (JSON)", "📊", descargas["snapshots"], "JSON", "var(--bajo)") +
        _section("Dashboards históricos (HTML)", "📈", descargas["dashboards"], "HTML")
    )
    return instant_html + """
    <div class='download-section'>
      <h4>📂 Archivos generados por el scheduler <span class='count-badge'>histórico</span></h4>
      <div style='color: var(--txt-2); font-size: 12px; margin-bottom: 8px;'>
        Los reportes que se han generado en ciclos anteriores del scheduler (cada 30 min). Click en cualquiera para descargarlo.
      </div>
    </div>
    """ + archivos_html


def _render_fuentes_estado(articulos, conflictos, proyectos, tweets) -> str:
    """Tabla con el estado de cada fuente (cuántos ítems trajo y si está OK)."""
    rows = ""
    fuentes = Counter()
    for a in (articulos or []):
        fuentes[a.source_name] += 1
    for c in (conflictos or []):
        fuentes[c.source_name] += 1
    for p in (proyectos or []):
        fuentes[p.source_name] += 1
    for t in (tweets or []):
        fuentes["Twitter / X"] += 1
    if not fuentes:
        return "<tr><td colspan='3' style='color:var(--txt-2);'>Sin fuentes activas</td></tr>"
    for nombre, n in sorted(fuentes.items(), key=lambda x: -x[1]):
        estado_html = (
            "<span style='color:var(--bajo);'>🟢 OK</span>" if n > 0
            else "<span style='color:var(--medio);'>🟡 Sin datos</span>"
        )
        rows += f"<tr><td>{_esc(nombre)}</td><td style='text-align:right; font-weight:600;'>{n}</td><td>{estado_html}</td></tr>"
    return rows


def _pl_card(p) -> str:
    raw = p.raw or {}
    titulo = _esc(p.title)
    if p.url:
        titulo = f"<a href='{_esc(p.url)}' target='_blank' rel='noopener'>{titulo}</a>"
    url_link = ""
    if p.url:
        url_link = (
            f"<a href='{_esc(p.url)}' target='_blank' rel='noopener' title='{_esc(p.url)}'>"
            f"🔗 {_esc(_short_url(p.url, 55))}</a>"
        )
    return f"""
    <div class="pl-card">
      <div class="titulo">{titulo}</div>
      <div class="resumen">{_esc(p.summary)}</div>
      <div class="meta">
        <span class="tag">{_esc(raw.get('estado','—'))}</span>
        <span class="tag">{_esc(raw.get('categoria','—'))}</span>
        <span title="{_esc(p.published or '')}">🕒 {_fmt_datetime(p.published)} · {_fmt_hours(p.hours_ago())}</span>
        {url_link}
      </div>
    </div>
    """


def generar_dashboard_html(
    output_path: str,
    articulos,
    conflictos,
    proyectos,
    entidades: dict,
    temas: dict,
    riesgo: dict,
    matriz: list,
    alertas: list,
    tweets: list = None,
    twitter_stats: dict = None,
    modo: str = "demo",
    ventana: int = 24,
    refresh_seconds: int = 1800,
    output_dir: str = None,
    acled_events: list = None,
    crimen_items: list = None,
):
    tweets = tweets or []
    twitter_stats = twitter_stats or {}
    acled_events = acled_events or []
    crimen_items = crimen_items or []

    # 24h slice — todos ordenados por fecha desc (más reciente primero)
    articulos_sorted = sorted(articulos, key=lambda a: a.hours_ago())
    conflictos_sorted = sorted(conflictos, key=lambda c: c.hours_ago())
    proyectos_sorted = sorted(proyectos, key=lambda p: p.hours_ago())
    tweets_sorted = sorted(tweets or [], key=lambda t: t.hours_ago())

    art_24 = [a for a in articulos_sorted if a.hours_ago() <= 24]
    conf_24 = [c for c in conflictos_sorted if c.hours_ago() <= 24]

    # ordenar alertas: críticas primero, luego por antigüedad ascendente (más recientes primero)
    nivel_rank = {"CRÍTICA": 0, "ALTA": 1, "MEDIA": 2}
    alertas_sorted = sorted(alertas, key=lambda a: (nivel_rank.get(a.get("nivel", ""), 9), a.get("hours_ago", 999)))
    alertas_24 = [a for a in alertas_sorted if a.get("hours_ago", 999) <= 24]
    alertas_criticas = [a for a in alertas_sorted if a["nivel"] == "CRÍTICA"]

    # Construir markers para el mapa: SOLO alertas y conflictos REALMENTE recientes.
    # Ventana temporal estricta: 48 horas. Esto evita que markers viejos
    # (ej. caso Huancavelica del 30 abril) sigan apareciendo en tiempo real.
    # También excluimos items demo para que no contaminen el mapa.
    MAPA_VENTANA_HORAS = 48
    map_markers = []
    for a in alertas:
        # Filtro temporal estricto
        h_ago = a.get("hours_ago", 999)
        if h_ago is None or h_ago > MAPA_VENTANA_HORAS or h_ago < 0:
            continue
        # Excluir items demo
        if a.get("is_demo", False):
            continue
        coords = None
        if a.get("region"):
            coords = buscar_coords(a["region"])
        if not coords:
            coords = buscar_coords((a.get("titulo") or "") + " " + (a.get("resumen") or ""))
        if coords:
            map_markers.append({
                "lat": coords[0], "lng": coords[1],
                "tipo": "alerta", "nivel": a["nivel"],
                "titulo": a["titulo"], "resumen": a["resumen"],
                "url": a.get("url", ""), "fuente": a["fuente"],
                "categoria": a["categoria"], "region": a.get("region", ""),
                "hours_ago": h_ago,
                "fecha": _fmt_datetime(a.get("timestamp", "")),
                "fecha_iso": a.get("timestamp", ""),
            })
    for c in conflictos:
        # Filtro temporal estricto para conflictos
        try:
            ch = c.hours_ago()
            if ch == float("inf") or ch > MAPA_VENTANA_HORAS or ch < 0:
                continue
        except Exception:
            continue
        # Excluir items demo si la marca está presente
        try:
            raw_marker = getattr(c, "raw", {}) or {}
            if raw_marker.get("is_demo", False):
                continue
        except Exception:
            pass
        raw = c.raw or {}
        region = raw.get("region") or c.region
        coords = buscar_coords(region or "") if region else None
        if not coords:
            coords = buscar_coords(c.title + " " + (c.summary or ""))
        if coords:
            sev = raw.get("severidad", "media")
            map_markers.append({
                "lat": coords[0], "lng": coords[1],
                "tipo": "conflicto",
                "nivel": "CRÍTICA" if sev == "alta" else "ALTA" if sev == "media" else "MEDIA",
                "titulo": c.title, "resumen": c.summary,
                "url": c.url, "fuente": c.source_name,
                "categoria": raw.get("tipo", "conflicto social"),
                "region": region or "",
                "hours_ago": round(ch, 1),
                "fecha": _fmt_datetime(c.published),
                "fecha_iso": c.published or "",
            })

    # ACLED markers — coordenadas exactas reales (lat/lng del evento)
    # ACLED entrega eventos verificados con coordenadas precisas, así que
    # no usamos buscar_coords sino los valores directos del evento.
    # Ventana temporal más amplia (14 días) porque ACLED reporta con lag.
    ACLED_VENTANA_HORAS = 24 * 14
    for ev in acled_events:
        try:
            eh = ev.hours_ago()
            if eh == float("inf") or eh > ACLED_VENTANA_HORAS or eh < 0:
                continue
        except Exception:
            continue
        raw = ev.raw or {}
        lat = raw.get("latitude")
        lng = raw.get("longitude")
        if not (lat and lng):
            continue
        # Nivel basado en criticidad + fatalities
        fatal = raw.get("fatalities", 0) or 0
        if fatal >= 5 or ev.criticidad == "alta":
            nivel = "CRÍTICA"
        elif ev.criticidad == "alta":
            nivel = "ALTA"
        elif ev.criticidad == "media":
            nivel = "ALTA"
        else:
            nivel = "MEDIA"
        evt_type = raw.get("event_type", "Evento ACLED")
        categoria_label = raw.get("tipo_descripcion", evt_type)
        is_demo_acled = raw.get("is_demo", False)
        map_markers.append({
            "lat": float(lat), "lng": float(lng),
            "tipo": "acled",
            "nivel": nivel,
            "titulo": ev.title,
            "resumen": ev.summary,
            "url": ev.url,
            "fuente": ev.source_name,
            "categoria": categoria_label,
            "region": ev.region or raw.get("admin1", ""),
            "hours_ago": round(eh, 1),
            "fecha": _fmt_datetime(ev.published),
            "fecha_iso": ev.published or "",
            "fatalities": fatal,
            "actor1": raw.get("actor1", ""),
            "actor2": raw.get("actor2", ""),
            "is_demo": is_demo_acled,
        })

    # Datos para charts
    matriz_data = [
        {"x": f["probabilidad"], "y": f["impacto"], "r": max(8, f["score"] / 5),
         "label": f["nombre"], "nivel": f["nivel"]}
        for f in matriz
    ]
    color_nivel = {"CRÍTICO": "#ef4444", "ALTO": "#f97316", "MEDIO": "#f59e0b", "BAJO": "#22c55e"}

    # Treemap data: categorías agrupadas
    treemap_data = []
    for f in matriz:
        treemap_data.append({
            "name": f["nombre"],
            "value": float(f["score"]),
            "category": f["categoria"],
            "nivel": f["nivel"],
        })

    # Ticker
    ticker_items = " · ".join(
        f"<span class='ticker-item'><span class='src'>{_esc(a['fuente'])}</span>{_esc(a['titulo'])}</span>"
        for a in alertas[:14]
    ) or f"<span class='ticker-item'>Sin alertas activas en la ventana de monitoreo.</span>"
    ticker = ticker_items + " · " + ticker_items

    # Factor cards
    factors_html = "".join(_factor_card(f) for f in matriz)

    # Alerts — críticas primero, dentro de cada nivel por fecha desc
    alertas_crit_html = "".join(_alerta_html(a) for a in alertas_criticas) or "<div style='color:var(--txt-2);'><em>Sin alertas críticas en la ventana actual.</em></div>"
    alertas_all_html = "".join(_alerta_html(a) for a in alertas_sorted) or "<div style='color:var(--txt-2);'><em>Sin alertas activas.</em></div>"

    # FILTRO ESTRICTO 24h — sólo eventos de últimas 24h, ordenados por más reciente
    headlines_24h = [a for a in articulos_sorted if a.hours_ago() <= 24]
    hl_24h = "".join(_headline_html(a) for a in headlines_24h)
    if not hl_24h:
        hl_24h = "<em style='color:var(--txt-2)'>Sin artículos en las últimas 24 horas.</em>"

    # Tweets últimas 24h
    tweets_para_mostrar = [t for t in tweets_sorted if t.hours_ago() <= 24]
    tweets_html = "".join(_tweet_html(t) for t in tweets_para_mostrar) if tweets_para_mostrar else "<em style='color:var(--txt-2)'>Sin tweets en las últimas 24 horas. Configura TWITTER_BEARER_TOKEN para activar API live.</em>"

    # Conflict cards — ventana 7 DÍAS (los conflictos sociales se desarrollan
    # típicamente en varios días, no en horas). Mantenemos la filtración a 72h
    # de alertas y 48h del mapa, pero la pestaña de Conflictos muestra el
    # cuadro completo de la última semana para que el analista vea el contexto.
    CONF_VENTANA_HORAS = 24 * 7
    conflictos_recientes = [c for c in conflictos_sorted if c.hours_ago() <= CONF_VENTANA_HORAS]
    # Excluir items demo si el pipeline real-time ya devolvió algo
    conflictos_reales = [c for c in conflictos_recientes if not (c.raw or {}).get("is_demo", False)]
    if conflictos_reales:
        conflictos_24h = conflictos_reales
    else:
        # Si no hay reales, mostramos lo que haya (demo) para no dejar pestaña vacía
        conflictos_24h = conflictos_recientes

    # Tweets virales
    virales = twitter_stats.get("virales", [])
    virales_html = ""
    for v in virales:
        viral_text = _esc(v["text"])
        import re
        viral_text = re.sub(r"(#\w+)", r"<span class='hashtag'>\1</span>", viral_text)
        viral_text = re.sub(r"(@\w+)", r"<span class='mention'>\1</span>", viral_text)
        verified_html = "<span class='verified'>✓</span>" if v.get("verified") else ""
        avatar_letter = (v.get("name", "?")[:1]).upper()
        m = v.get("metrics", {})
        virales_html += f"""
        <div class="tweet">
          <div class="tweet-head">
            <div class="tweet-avatar">{_esc(avatar_letter)}</div>
            <div class="tweet-user">
              <div class="name">{_esc(v['name'])} {verified_html}<span class='viral-badge'>VIRAL · {_format_metric(v['engagement'])} eng</span></div>
              <div class="handle">@{_esc(v['handle'])}</div>
            </div>
          </div>
          <div class="tweet-text">{viral_text}</div>
          <div class="tweet-meta">
            <span class="metric">🔁 {_format_metric(m.get('retweet_count', 0))}</span>
            <span class="metric">❤ {_format_metric(m.get('like_count', 0))}</span>
            <span class="time">{_fmt_hours(v.get('hours_ago'))} · <a href='{_esc(v['url'])}' target='_blank' rel='noopener'>ver en X →</a></span>
          </div>
        </div>
        """
    if not virales_html:
        virales_html = "<em style='color:var(--txt-2)'>Sin tweets virales detectados.</em>"

    # Hashtags
    hashtags_html = ""
    for tag, cnt in twitter_stats.get("hashtags", [])[:20]:
        hashtags_html += f"<div class='hashtag-pill'><span class='tag'>#{_esc(tag)}</span><span class='count'>{cnt}</span></div>"
    if not hashtags_html:
        hashtags_html = "<em style='color:var(--txt-2)'>—</em>"

    # Conflict cards — ventana de últimos 7 días, ordenados por más recientes primero
    conflict_cards = "".join(_conflict_card(c) for c in conflictos_24h)
    if not conflict_cards:
        conflict_cards = "<em style='color:var(--txt-2)'>Sin conflictos sociales detectados en la última semana en fuentes RSS monitoreadas.</em>"

    # PL cards — solo legislativo REAL-TIME (excluir demo si hay reales)
    proyectos_reales = [p for p in proyectos_sorted if not (p.raw or {}).get("is_demo", False)]
    if proyectos_reales:
        proyectos_para_mostrar = proyectos_reales
    else:
        proyectos_para_mostrar = proyectos_sorted
    pl_cards = "".join(_pl_card(p) for p in proyectos_para_mostrar)
    if not pl_cards:
        pl_cards = "<em style='color:var(--txt-2)'>Sin actividad legislativa detectada en la última semana en fuentes RSS monitoreadas.</em>"

    # CRIMEN ORGANIZADO cards — agrupados por tipología
    crimen_sorted = sorted(crimen_items, key=lambda c: c.hours_ago()) if crimen_items else []
    crimen_por_tipo = {}
    for it in crimen_sorted:
        tip = (it.raw or {}).get("tipologia", "otros")
        crimen_por_tipo.setdefault(tip, []).append(it)

    tipologias_labels = {
        "narcotrafico": ("🚫 Narcotráfico", "var(--critico)"),
        "mineria_ilegal": ("⛏️ Minería ilegal", "var(--alto)"),
        "tala_ilegal": ("🪓 Tala ilegal", "var(--alto)"),
        "contrabando": ("📦 Contrabando", "var(--medio)"),
        "migracion_irregular": ("🌐 Migración irregular", "var(--medio)"),
        "extorsion_sicariato": ("🔫 Extorsión / Sicariato", "var(--critico)"),
    }

    def _crimen_card(it):
        raw = it.raw or {}
        sev = raw.get("severidad", "media")
        region = raw.get("region") or it.region or "—"
        tip = raw.get("tipologia", "otros")
        label = tipologias_labels.get(tip, (tip, "var(--accent)"))[0]
        url_link = ""
        if it.url:
            url_link = (
                f"<a href='{_esc(it.url)}' target='_blank' rel='noopener' title='{_esc(it.url)}'>"
                f"🔗 {_esc(_short_url(it.url, 55))}</a>"
            )
        titulo_html = _esc(it.title)
        if it.url:
            titulo_html = f"<a href='{_esc(it.url)}' target='_blank' rel='noopener'>{titulo_html}</a>"
        return f"""
        <div class="conflict-card {sev}">
          <div class="head">
            <div class="titulo">{titulo_html}</div>
            <span class="region-tag" style="background:{tipologias_labels.get(tip, (tip, 'var(--accent)'))[1]}33; color:{tipologias_labels.get(tip, (tip, 'var(--accent)'))[1]};">{label}</span>
          </div>
          <div class="desc">{_esc(it.summary)}</div>
          <div class="meta">
            <span title="{_esc(it.published or '')}">📍 {_esc(region)} · severidad <strong>{sev}</strong> · 🕒 {_fmt_datetime(it.published)} · {_fmt_hours(it.hours_ago())}</span>
            {url_link}
          </div>
        </div>
        """

    crimen_html_blocks = ""
    for tip in ("narcotrafico", "mineria_ilegal", "tala_ilegal", "contrabando", "migracion_irregular", "extorsion_sicariato"):
        items = crimen_por_tipo.get(tip, [])
        if not items:
            continue
        label, color = tipologias_labels.get(tip, (tip, "var(--accent)"))
        cards = "".join(_crimen_card(it) for it in items[:10])
        crimen_html_blocks += f"""
        <div class="card span-12" style="border-left: 4px solid {color};">
          <h3 style="color:{color};">{label} <span class="badge">{len(items)}</span></h3>
          {cards}
        </div>
        """
    if not crimen_html_blocks:
        crimen_html_blocks = "<div class='card'><em style='color:var(--txt-2)'>Sin eventos de crimen organizado / migración detectados en las últimas 2 semanas en fuentes RSS monitoreadas.</em></div>"

    # ACLED cards — eventos georreferenciados
    acled_sorted = sorted(acled_events, key=lambda a: a.hours_ago()) if acled_events else []
    def _acled_card(ev):
        raw = ev.raw or {}
        evt_type = raw.get("event_type", "Evento")
        evt_desc = raw.get("tipo_descripcion", evt_type)
        sub_event = raw.get("sub_event_type", "")
        location = raw.get("location") or ev.region or "—"
        actor1 = raw.get("actor1", "")
        actor2 = raw.get("actor2", "")
        fatal = raw.get("fatalities", 0) or 0
        is_demo_ev = raw.get("is_demo", False)
        sev = ev.criticidad

        # Color por event_type
        color_evt = {
            "Protests": "var(--medio)",
            "Riots": "var(--alto)",
            "Violence against civilians": "var(--critico)",
            "Battles": "var(--critico)",
            "Explosions/Remote violence": "var(--critico)",
            "Strategic developments": "var(--accent)",
        }.get(evt_type, "var(--accent)")

        actores_html = ""
        if actor1:
            actores_html = f"<div style='font-size:12px; color:var(--txt-2); margin-top:6px;'>👥 <strong>{_esc(actor1)}</strong>"
            if actor2:
                actores_html += f" ↔ {_esc(actor2)}"
            actores_html += "</div>"

        fatal_html = ""
        if fatal > 0:
            fatal_html = f"<span style='color:var(--critico); font-weight:600;'>☠️ {fatal} fallecidos</span> · "

        url_link = ""
        if ev.url and "acleddata.com" not in ev.url:
            url_link = (
                f"<a href='{_esc(ev.url)}' target='_blank' rel='noopener'>"
                f"🔗 {_esc(_short_url(ev.url, 50))}</a>"
            )
        demo_tag = "<span style='background:var(--bg-3); color:var(--txt-2); padding:2px 8px; border-radius:4px; font-size:10px;'>DEMO</span>" if is_demo_ev else ""
        return f"""
        <div class="conflict-card {sev}" style="border-left: 4px solid {color_evt};">
          <div class="head">
            <div class="titulo">{_esc(ev.title)} {demo_tag}</div>
            <span class="region-tag" style="background:{color_evt}33; color:{color_evt};">{_esc(evt_desc)}</span>
          </div>
          <div class="desc">{_esc(ev.summary[:300])}{'…' if len(ev.summary) > 300 else ''}</div>
          {actores_html}
          <div class="meta">
            <span title="{_esc(ev.published or '')}">{fatal_html}📍 {_esc(location)} · 🕒 {_fmt_datetime(ev.published)} · {_fmt_hours(ev.hours_ago())}</span>
            {url_link}
          </div>
        </div>
        """

    acled_cards = "".join(_acled_card(ev) for ev in acled_sorted[:30])
    if not acled_cards:
        acled_cards = "<em style='color:var(--txt-2)'>Sin eventos ACLED disponibles. Configura las variables ACLED_API_KEY y ACLED_EMAIL en Render para activar datos reales.</em>"

    # Entity cards (no más tabla)
    def _entity_block(items, label, max_n=10):
        if not items:
            return ""
        bars_max = max(v for _, v in items) or 1
        rows = ""
        for k, v in items[:max_n]:
            w = int((v / bars_max) * 100)
            rows += f"""
            <div style='display:grid; grid-template-columns: 130px 1fr 30px; gap:8px; align-items:center; padding: 4px 0; font-size:12.5px;'>
              <span>{_esc(k)}</span>
              <div class='bar' style='height:8px;'><div style='width:{w}%; background:linear-gradient(90deg, var(--accent), var(--accent-2));'></div></div>
              <span style='text-align:right; color: var(--txt-2);'>{v}</span>
            </div>"""
        return rows

    inst_html = _entity_block(entidades.get("instituciones", []), "Instituciones")
    part_html = _entity_block(entidades.get("partidos", []), "Partidos")
    reg_html = _entity_block(entidades.get("regiones", []), "Regiones")
    emp_html = _entity_block(entidades.get("empresas_riesgo", []), "Empresas")

    # KPIs
    kpi_global = riesgo["global"]
    kpi_global_nivel = riesgo["nivel"]
    kpi_alertas_crit = len(alertas_criticas)
    kpi_alertas = len(alertas)
    kpi_articulos_24 = len(art_24)
    kpi_conf_alta = len([c for c in conflictos if (c.raw or {}).get("severidad") == "alta" and (c.raw or {}).get("estado") == "activo"])
    tweets_24 = [t for t in tweets_sorted if t.hours_ago() <= 24]
    kpi_tweets = len(tweets_24)
    kpi_engagement = twitter_stats.get("engagement_total", 0)

    kpi_class = "critico" if kpi_global_nivel == "ALTO" else ("alto" if kpi_global_nivel == "MEDIO" else "bajo")

    refresh_minutos = max(1, int(refresh_seconds / 60))
    _now_pe = now_pe()
    generated_iso = _now_pe.isoformat(timespec="seconds")
    _now_pe_naive_iso = _now_pe.replace(tzinfo=None).isoformat(timespec="seconds")  # para JS Date sin tz

    # Escanear archivos disponibles para descarga
    if not output_dir:
        output_dir = str(Path(output_path).resolve().parent)
    descargas = _scan_descargas(output_dir)
    descargas_html = _render_descargas(descargas)
    total_descargas = sum(len(v) for v in descargas.values() if isinstance(v, list))

    # Análisis de tendencia semanal
    tendencias = _analizar_tendencias_semana(output_dir)
    tendencias_html = _render_tendencias(tendencias)
    persistentes_count = len(tendencias.get("alertas_persistentes", []))

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="refresh" content="{refresh_seconds}" />
<title>APURISK 1.0 — Plataforma OSINT de Riesgos Políticos · Perú</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="" />
<!-- Chart.js v4 (UMD bundle) -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<!-- Treemap plugin compatible con Chart.js v4 -->
<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-treemap@3.1.0/dist/chartjs-chart-treemap.min.js"></script>
<!-- Leaflet -->
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>{CSS}</style>
</head>
<body>

<header class="app-header">
  <div class="brand">
    <div class="logo">A</div>
    <div>
      <div class="name">APURISK 1.0</div>
      <div class="sub">Plataforma OSINT · Riesgos Políticos · Perú</div>
    </div>
  </div>
  <div class="refresh-mod">
    <div>
      <div class="label">Última actualización</div>
      <div class="value" id="lastUpdate" data-iso="{generated_iso}">{_now_pe.strftime('%d %b · %H:%M:%S')} PET</div>
    </div>
    <div class="sep"></div>
    <div>
      <div class="label">Próxima en</div>
      <div class="value"><span class="countdown" id="countdown">--:--</span></div>
    </div>
    <div class="progress"><div id="progressBar"></div></div>
    <div class="sep"></div>
    <button id="refreshBtn" title="Recargar ahora desde el servidor">⟳ Actualizar</button>
  </div>
  <div class="live-status">
    <span class="live-dot"></span>
    <span>EN VIVO · ciclo cada {refresh_minutos} min</span>
    <span style="margin-left:14px; opacity:.6;">Modo: <strong style="color: {'#22c55e' if modo == 'live' else '#f59e0b'};">{modo.upper()}</strong> · Ventana: {ventana}h</span>
    <span style="margin-left:14px; opacity:.7; font-size: 11px;" id="libDiag">cargando librerías…</span>
  </div>
</header>

<div class="ticker">
  <div class="label">⚠ ALERTAS</div>
  <div class="ticker-track">{ticker}</div>
</div>

<div class="kpi-row">
  <div class="kpi {kpi_class}">
    <div class="lbl">Score Riesgo Global</div>
    <div class="val nivel-{kpi_global_nivel}">{kpi_global}</div>
    <div class="sub">Nivel: <strong class="nivel-{kpi_global_nivel}">{kpi_global_nivel}</strong></div>
  </div>
  <div class="kpi critico">
    <div class="lbl">Alertas Críticas</div>
    <div class="val nivel-CRÍTICO">{kpi_alertas_crit}</div>
    <div class="sub">{kpi_alertas} alertas activas en total</div>
  </div>
  <div class="kpi alto">
    <div class="lbl">Conflictos sociales (alta)</div>
    <div class="val nivel-ALTO">{kpi_conf_alta}</div>
    <div class="sub">activos en zonas extractivas / vías</div>
  </div>
  <div class="kpi medio">
    <div class="lbl">Cobertura últimas 24h</div>
    <div class="val">{kpi_articulos_24 + kpi_tweets}</div>
    <div class="sub">{kpi_articulos_24} medios · {kpi_tweets} tweets</div>
  </div>
  <div class="kpi bajo">
    <div class="lbl">Engagement Twitter/X</div>
    <div class="val">{_format_metric(kpi_engagement)}</div>
    <div class="sub">interacciones acumuladas 24h</div>
  </div>
</div>

<nav class="tabs">
  <div class="tab active" data-tab="riesgos">Mapa de Riesgos</div>
  <div class="tab" data-tab="geo">Mapa Geográfico <span class="count">{len(map_markers)}</span></div>
  <div class="tab" data-tab="alertas">Alertas Inmediatas <span class="count">{kpi_alertas_crit}</span></div>
  <div class="tab" data-tab="r24h">Reporte 24h <span class="count">{kpi_articulos_24}</span></div>
  <div class="tab" data-tab="twitter">Twitter / X <span class="count">{kpi_tweets}</span></div>
  <div class="tab" data-tab="conflictos">Conflictos</div>
  <div class="tab" data-tab="legislativo">Legislativo</div>
  <div class="tab" data-tab="crimen">🛡️ Crimen Organizado <span class="count">{len(crimen_items)}</span></div>
  <div class="tab" data-tab="acled">📍 ACLED Eventos <span class="count">{len(acled_events)}</span></div>
  <div class="tab" data-tab="entidades">Entidades</div>
  <div class="tab" data-tab="tendencias">📈 Tendencias 7d <span class="count">{persistentes_count}</span></div>
  <div class="tab" data-tab="descargas">📥 Descargas <span class="count">{total_descargas}</span></div>
  <div class="tab" data-tab="analisis">🔍 Análisis de Caso</div>
  <div class="tab" data-tab="monitoreo">⟳ Monitoreo</div>
</nav>

<div class="content">

  <div class="fresh-banner" id="freshBanner">
    <span class="icon">🟢</span>
    <div>
      <strong>Datos frescos.</strong>
      <span style="color: var(--txt-2);">Snapshot generado a las <span id="freshTime">{_now_pe.strftime('%H:%M:%S')} PET del {_now_pe.strftime('%d %b %Y')}</span>.
      Auto-refresco cada {refresh_minutos} min en modo live.</span>
    </div>
    <span class="meta">Total fuentes: 4 colectores · {sum(1 for x in articulos if x.url)} ítems con URL · {len(map_markers)} markers georreferenciados</span>
  </div>

  <!-- TAB: MAPA DE RIESGOS (visual, no tablas) -->
  <section class="tab-panel active" id="tab-riesgos">
    <div class="grid grid-12">
      <div class="card span-7" id="matrixCard">
        <h3>Matriz Probabilidad × Impacto <span class="badge">{len(matriz)} factores</span></h3>
        <div class="matrix-canvas-host"><canvas id="matrixChart"></canvas></div>
      </div>
      <div class="card span-5" id="treemapCard">
        <h3>Treemap de Factores · tamaño = score <span class="badge">score = √(P·I)</span></h3>
        <div style="height: 400px;"><canvas id="treemapChart"></canvas></div>
        <div style="margin-top: 8px; font-size: 11px; color: var(--txt-2); text-align: center;">
          Cada bloque es un factor de riesgo. <b>Más grande = mayor score</b>. Color = nivel (rojo crítico · naranja alto · ámbar medio · verde bajo).
        </div>
      </div>

      <div class="card span-12">
        <h3>Factores de riesgo · ranking visual <span class="badge">{len([m for m in matriz if m['nivel'] in ('CRÍTICO','ALTO')])} en zona alta/crítica</span></h3>
        <div class="factors-grid">{factors_html}</div>
      </div>
    </div>
  </section>

  <!-- TAB: MAPA GEOGRÁFICO -->
  <section class="tab-panel" id="tab-geo">
    <div class="grid grid-12">
      <div class="card span-12">
        <h3>Geolocalización de alertas y conflictos · ÚLTIMAS 48 HORAS <span class="badge" style="background:{'var(--critico)' if len(map_markers) == 0 else 'var(--accent)'};">{len(map_markers)} {'punto' if len(map_markers) == 1 else 'puntos'} activos</span></h3>
        <div style="font-size: 12px; color: var(--txt-2); margin-bottom: 10px;">
          🕒 Filtro temporal estricto: solo eventos con timestamp dentro de las últimas <b>48 horas</b>.
          Items demo y eventos viejos NO aparecen aquí. Se renueva automáticamente cada 30 min con el scheduler.
        </div>
        {('<div style="background: var(--bg-2); border-left: 3px solid var(--accent); padding: 18px; margin-bottom: 12px; border-radius: 6px;"><strong style="color: var(--accent);">ℹ Sin eventos georreferenciados en las últimas 48 horas.</strong><br><span style="color: var(--txt-2); font-size: 13px;">Cuando ocurra un evento real con ubicación identificable (Apurímac, Tacna, Huancavelica, etc.) en una fuente monitoreada, aparecerá aquí automáticamente en el siguiente ciclo del scheduler.</span></div>' if len(map_markers) == 0 else '')}
        <div id="peru-map"></div>
        <div style="display:flex; gap:18px; margin-top:14px; font-size:12px; color: var(--txt-2); flex-wrap: wrap;">
          <span><span style="display:inline-block; width:12px; height:12px; background:#ef4444; border-radius:50%; margin-right:6px;"></span>Crítica</span>
          <span><span style="display:inline-block; width:12px; height:12px; background:#f97316; border-radius:50%; margin-right:6px;"></span>Alta</span>
          <span><span style="display:inline-block; width:12px; height:12px; background:#f59e0b; border-radius:50%; margin-right:6px;"></span>Media</span>
          <span style="margin-left: auto;">Mapa: OpenStreetMap · Capas: Leaflet · Ventana: 48h</span>
        </div>
      </div>
    </div>
  </section>

  <!-- TAB: ALERTAS -->
  <section class="tab-panel" id="tab-alertas">
    <div class="grid grid-12">
      <div class="card span-12">
        <h3>🚨 Alertas críticas en curso <span class="badge">{kpi_alertas_crit}</span></h3>
        {alertas_crit_html}
      </div>
      <div class="card span-12">
        <h3>Todas las alertas activas <span class="badge">{kpi_alertas}</span></h3>
        {alertas_all_html}
      </div>
    </div>
  </section>

  <!-- TAB: REPORTE 24H -->
  <section class="tab-panel" id="tab-r24h">
    <div class="grid grid-12">
      <div class="card span-12" style="background: linear-gradient(135deg, var(--bg-1), var(--bg-2));">
        <h3>Síntesis ejecutiva · últimas 24 horas</h3>
        <div style="line-height:1.7; font-size:14px;">
          <p>En las últimas <strong>24 horas</strong> se procesaron <strong>{kpi_articulos_24} artículos</strong> de medios y datasets internacionales,
          <strong>{kpi_tweets} tweets</strong> con <strong>{_format_metric(kpi_engagement)}</strong> interacciones,
          se detectaron <strong>{len(alertas_24)} alertas</strong> ({kpi_alertas_crit} críticas) y se monitorean
          <strong>{kpi_conf_alta} conflictos sociales</strong> de severidad alta activos.</p>
          <p>Score global: <strong class="nivel-{kpi_global_nivel}">{kpi_global}/100 · {kpi_global_nivel}</strong>.
          Categoría con mayor presión: <strong>{max(riesgo['categorias'].items(), key=lambda x: x[1])[0].replace('_',' ')}</strong>
          ({max(riesgo['categorias'].values()):.0f}/100).
          Sentimiento agregado: <strong>{riesgo['sentimiento_promedio']}</strong>.</p>
        </div>
      </div>
      <div class="card span-12">
        <h3>Cobertura últimas 24 horas · ordenada por más reciente <span class="badge">{len(headlines_24h)} ítems en 24h</span></h3>
        {hl_24h}
      </div>
    </div>
  </section>

  <!-- TAB: TWITTER -->
  <section class="tab-panel" id="tab-twitter">
    <div class="grid grid-12">
      <div class="card span-4">
        <h3>📊 Stats · Twitter/X</h3>
        <div style="display:grid; gap:10px;">
          <div><div style="color:var(--txt-2); font-size:11px; text-transform:uppercase; letter-spacing:1px;">Tweets 24h</div>
            <div style="font-size:24px; font-weight:700;">{kpi_tweets}</div></div>
          <div><div style="color:var(--txt-2); font-size:11px; text-transform:uppercase; letter-spacing:1px;">Engagement total</div>
            <div style="font-size:24px; font-weight:700;">{_format_metric(kpi_engagement)}</div></div>
          <div><div style="color:var(--txt-2); font-size:11px; text-transform:uppercase; letter-spacing:1px;">Reach estimado</div>
            <div style="font-size:24px; font-weight:700;">{_format_metric(twitter_stats.get('reach_estimado', 0))}</div></div>
          <div><div style="color:var(--txt-2); font-size:11px; text-transform:uppercase; letter-spacing:1px;">Sentimiento</div>
            <div style="font-size:24px; font-weight:700;">{twitter_stats.get('sentimiento_promedio', 0)}</div></div>
        </div>
      </div>
      <div class="card span-8">
        <h3>🔥 Tweets virales <span class="badge">{len(virales)}</span></h3>
        {virales_html}
      </div>

      <div class="card span-12">
        <h3>#️⃣ Hashtags trending</h3>
        <div class="hashtag-pills">{hashtags_html}</div>
      </div>

      <div class="card span-12">
        <h3>Feed Twitter/X · más reciente primero <span class="badge">{kpi_tweets} en últimas 24h · top 25 mostrados</span></h3>
        {tweets_html}
      </div>
    </div>
  </section>

  <!-- TAB: CONFLICTOS (cards) — clasificación REAL-TIME desde RSS de medios -->
  <section class="tab-panel" id="tab-conflictos">
    <div class="card">
      <h3>Conflictos sociales · ÚLTIMOS 7 DÍAS
        <span class="badge" style="background:{'var(--critico)' if len(conflictos_24h) == 0 else 'var(--accent)'};">
          {len(conflictos_24h)} {'evento' if len(conflictos_24h) == 1 else 'eventos'}
        </span>
      </h3>
      <div style="font-size:12px; color:var(--txt-2); margin: 6px 0 14px;">
        🔄 Clasificación automática en tiempo real desde RSS de medios. Detecta paros, huelgas, marchas, bloqueos, protestas y movilizaciones por departamento.
      </div>
      {conflict_cards}
    </div>
  </section>

  <!-- TAB: LEGISLATIVO — clasificación REAL-TIME desde RSS de medios -->
  <section class="tab-panel" id="tab-legislativo">
    <div class="card">
      <h3>Actividad legislativa · ÚLTIMOS 7 DÍAS
        <span class="badge" style="background:{'var(--critico)' if len(proyectos_para_mostrar) == 0 else 'var(--accent)'};">
          {len(proyectos_para_mostrar)} {'item' if len(proyectos_para_mostrar) == 1 else 'items'}
        </span>
      </h3>
      <div style="font-size:12px; color:var(--txt-2); margin: 6px 0 14px;">
        🔄 Clasificación automática en tiempo real desde RSS de medios. Detecta proyectos de ley, mociones, interpelaciones, censuras, dictámenes y reformas constitucionales.
      </div>
      {pl_cards}
    </div>
  </section>

  <!-- TAB: CRIMEN ORGANIZADO Y MIGRACIÓN — clasificación temática REAL-TIME -->
  <section class="tab-panel" id="tab-crimen">
    <div class="card span-12" style="background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); margin-bottom: 14px;">
      <h3 style="margin-bottom: 6px;">🛡️ Crimen Organizado, Tráficos Ilícitos y Migración Irregular <span class="badge">{len(crimen_items)} eventos</span></h3>
      <div style="color: var(--txt-1); font-size: 13px; line-height: 1.6;">
        Clasificación automática en tiempo real de artículos RSS por tipología:
        narcotráfico, minería ilegal, tala ilegal, contrabando, migración irregular,
        extorsión y sicariato. Ventana: últimos 14 días.
      </div>
    </div>
    <div class="grid grid-12">
      {crimen_html_blocks}
    </div>
  </section>

  <!-- TAB: ACLED — eventos georreferenciados de violencia política y protestas -->
  <section class="tab-panel" id="tab-acled">
    <div class="card span-12" style="background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); margin-bottom: 14px;">
      <h3 style="margin-bottom: 6px;">📍 ACLED · Eventos Georreferenciados <span class="badge">{len(acled_events)} eventos</span></h3>
      <div style="color: var(--txt-1); font-size: 13px; line-height: 1.6;">
        <strong>ACLED (Armed Conflict Location & Event Data)</strong> — referencia mundial para
        eventos políticos georreferenciados: protestas, disturbios, batallas, violencia contra
        civiles, explosiones, desarrollos estratégicos. Cada evento incluye coordenadas exactas,
        actores y fuentes verificadas. Ventana: últimos 14 días.
        <br><a href="https://acleddata.com/dashboard" target="_blank" rel="noopener" style="color:var(--accent);">🔗 Ver dashboard oficial ACLED</a>
      </div>
    </div>
    <div class="card">
      {acled_cards}
    </div>
  </section>

  <!-- TAB: TENDENCIAS 7 DÍAS -->
  <section class="tab-panel" id="tab-tendencias">
    <div class="grid grid-12">
      {tendencias_html}
    </div>
  </section>

  <!-- TAB: DESCARGAS -->
  <section class="tab-panel" id="tab-descargas">
    <div class="card span-12" style="background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); margin-bottom: 14px;">
      <h3 style="margin-bottom: 6px;">📥 Centro de descargas <span class="badge">{total_descargas} archivos</span></h3>
      <div style="color: var(--txt-1); font-size: 13px; line-height: 1.6;">
        Reportes generados automáticamente en cada ciclo de monitoreo (cada {refresh_minutos} min en modo live).
        Disponibles en formato <strong>PDF</strong> (diario y semanal), <strong>DOCX</strong> (Word imprimible),
        <strong>HTML</strong> (versión web) y <strong>JSON</strong> (datos crudos para BI).
        Click en cualquier archivo para descargarlo.
      </div>
    </div>

    <div class="grid grid-12">
      <div class="span-12">
        {descargas_html}
      </div>
    </div>

    <div class="card span-12" style="margin-top: 14px;">
      <h3>📦 Cómo distribuir los reportes</h3>
      <div style="line-height: 1.7; font-size: 13px; color: var(--txt-1);">
        <p><strong>Reportes diarios PDF:</strong> ideal para briefing matutino del equipo. Tamaño compacto, contiene
        score global, top 7 factores P×I, alertas críticas, análisis por dimensión y headlines con URLs clickables.</p>

        <p><strong>Reportes semanales PDF:</strong> síntesis ejecutiva del período. Contiene KPIs agregados,
        evolución diaria del score, top eventos críticos del período y factores dominantes. Genera tendencias
        leyendo todos los snapshots JSON acumulados en <code>output/</code>.</p>

        <p><strong>Distribución por email:</strong> los PDFs se pueden adjuntar a un envío automatizado
        (configura un cron o GitHub Action que ejecute el pipeline y mande el PDF al equipo).</p>

        <p><strong>Distribución web:</strong> sirve la carpeta <code>output/</code> con cualquier servidor estático
        (<code>python -m http.server 8080 --directory output</code>) y comparte el enlace al dashboard.
        Los stakeholders verán el dashboard live y pueden descargar cualquier reporte desde esta misma pestaña.</p>
      </div>
    </div>
  </section>

  <!-- TAB: ANÁLISIS DE CASO -->
  <section class="tab-panel" id="tab-analisis">
    <div class="grid grid-12">
      <div class="card span-12" style="background: linear-gradient(135deg, var(--bg-1), var(--bg-2));">
        <h3>🔍 Análisis OSINT de Caso bajo demanda <span class="badge">Plataforma analítica</span></h3>
        <div style="font-size:14px; line-height:1.7; color: var(--txt-1);">
          <p>El módulo de <strong>Análisis de Caso</strong> permite al analista solicitar un estudio
          estructurado de un evento o caso específico de riesgo político para Perú.</p>
          <p>El sistema:</p>
          <ol style="margin-left: 20px; margin-top: 8px;">
            <li>Comprende el caso descrito por el analista</li>
            <li>Identifica actores principales, secundarios, instituciones, regiones y sectores</li>
            <li>Cruza con la base SQLite interna (snapshots, alertas, artículos archivados)</li>
            <li>Analiza las URLs proporcionadas</li>
            <li>Busca cobertura en las ~54 fuentes monitoreadas</li>
            <li>Evalúa el caso en 6 dimensiones: institucional, social, electoral, económico, mediático, seguridad</li>
            <li>Clasifica nivel de riesgo (BAJO/MODERADO/ALTO/CRÍTICO) y tendencia</li>
            <li>Proyecta evolución mediática (24h, 48h, 72h, semana)</li>
            <li>Construye tres escenarios prospectivos (Desescalada / Continuidad / Escalada)</li>
            <li>Identifica alertas tempranas a monitorear</li>
            <li>Evalúa confiabilidad de fuentes</li>
            <li>Genera un <strong>reporte PDF estructurado en 14 secciones</strong></li>
          </ol>
        </div>
        <a href="/analisis" target="_blank" style="display:inline-block; margin-top:18px; background: linear-gradient(90deg, var(--accent), var(--accent-2)); color: var(--bg-0); padding: 14px 28px; border-radius: 8px; font-weight: 700; font-size: 14px; text-decoration: none; letter-spacing: .5px; text-transform: uppercase;">
          📊 Abrir formulario de solicitud →
        </a>
      </div>

      <div class="card span-12">
        <h3>📚 Cómo usar el módulo</h3>
        <div style="font-size:13px; line-height:1.6; color: var(--txt-1);">
          <p><strong>1. Describe el caso</strong> — qué está ocurriendo, dónde y desde cuándo.</p>
          <p><strong>2. Aporta tu hipótesis (opcional)</strong> — qué intuición tienes sobre el caso. El sistema la trata como hipótesis a contrastar, no como verdad confirmada.</p>
          <p><strong>3. Proporciona URLs de referencia</strong> — enlaces específicos a notas que quieres que se analicen prioritariamente (una por línea).</p>
          <p><strong>4. Define periodo y profundidad</strong> — qué ventana temporal cubrir y qué nivel de detalle quieres.</p>
          <p><strong>5. Indica regiones/actores/sectores de interés</strong> — para refinar la búsqueda.</p>
          <p><strong>6. Click "Generar reporte"</strong> — el sistema procesa y descarga un PDF estructurado con conclusión analítica y recomendación específica para el analista.</p>
        </div>
      </div>

      <div class="card span-12" style="border-left: 3px solid var(--accent);">
        <h3>🔐 Reglas de calidad del análisis</h3>
        <div style="font-size:12.5px; line-height:1.6; color: var(--txt-2);">
          <p>El reporte aplica las siguientes reglas:</p>
          <ul style="margin-left: 18px; margin-top: 6px;">
            <li>No inventar información; las afirmaciones provienen de fuentes verificables.</li>
            <li>Distinguir hechos confirmados de hipótesis o rumores.</li>
            <li>Cruzar al menos tres tipos de fuente: oficial, mediática y redes sociales.</li>
            <li>Si una fuente no es confiable, se indica explícitamente.</li>
            <li>Información proveniente de redes sociales sin verificación → se etiqueta como "no confirmada".</li>
            <li>Lenguaje analítico, sobrio y profesional. Sin sesgo partidario.</li>
            <li>Foco exclusivo en riesgo político para el Perú.</li>
          </ul>
        </div>
      </div>
    </div>
  </section>

  <!-- TAB: MONITOREO -->
  <section class="tab-panel" id="tab-monitoreo">
    <div class="grid grid-12">
      <div class="card span-6">
        <h3>⟳ Estado del sistema</h3>
        <div style="display:grid; gap:14px;">
          <div style="display:flex; justify-content:space-between; padding: 8px 0; border-bottom: 1px solid var(--bg-3);">
            <span style="color:var(--txt-2);">Cadencia de actualización</span>
            <strong>{refresh_minutos} min ({refresh_seconds}s)</strong>
          </div>
          <div style="display:flex; justify-content:space-between; padding: 8px 0; border-bottom: 1px solid var(--bg-3);">
            <span style="color:var(--txt-2);">Modo de operación</span>
            <strong style="color:{'var(--bajo)' if modo == 'live' else 'var(--medio)'};">{modo.upper()}</strong>
          </div>
          <div style="display:flex; justify-content:space-between; padding: 8px 0; border-bottom: 1px solid var(--bg-3);">
            <span style="color:var(--txt-2);">Última actualización</span>
            <strong title="{generated_iso}">{_now_pe.strftime('%d %b %Y · %H:%M:%S')} PET</strong>
          </div>
          <div style="display:flex; justify-content:space-between; padding: 8px 0; border-bottom: 1px solid var(--bg-3);">
            <span style="color:var(--txt-2);">Próxima actualización</span>
            <strong id="nextUpdateText" data-iso="{(_now_pe + timedelta(seconds=refresh_seconds)).isoformat(timespec='seconds')}">{(_now_pe + timedelta(seconds=refresh_seconds)).strftime('%d %b · %H:%M:%S')} PET</strong>
          </div>
          <div style="display:flex; justify-content:space-between; padding: 8px 0; border-bottom: 1px solid var(--bg-3);">
            <span style="color:var(--txt-2);">Auto-refresco navegador</span>
            <strong style="color:var(--bajo);">✓ Activo (meta refresh)</strong>
          </div>
          <div style="display:flex; justify-content:space-between; padding: 8px 0;">
            <span style="color:var(--txt-2);">Salud del sistema</span>
            <strong style="color:var(--bajo);">🟢 Operativo</strong>
          </div>
        </div>
        <button id="refreshBtn2" style="background:var(--accent); color:var(--bg-0); border:none; padding:10px 20px; border-radius:6px; font-weight:700; font-size:13px; cursor:pointer; letter-spacing:.5px; text-transform:uppercase; margin-top:14px; width:100%;">⟳ Forzar actualización ahora</button>
      </div>

      <div class="card span-6">
        <h3>📊 Fuentes activas <span class="badge">10 colectores</span></h3>
        <table>
          <thead><tr><th>Fuente</th><th style="text-align:right">Ítems</th><th>Estado</th></tr></thead>
          <tbody>
            {_render_fuentes_estado(articulos, conflictos, proyectos, tweets)}
          </tbody>
        </table>
      </div>

      <div class="card span-12">
        <h3>📋 Configuración de monitoreo</h3>
        <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:14px;">
          <div style="padding: 12px; background: var(--bg-2); border-radius: 6px; border-left: 3px solid var(--accent);">
            <div style="color:var(--txt-2); font-size:11px; text-transform:uppercase; letter-spacing:1px;">Ventana de alertas</div>
            <div style="font-size:18px; font-weight:700; margin-top:4px;">72 horas</div>
            <div style="color:var(--txt-2); font-size:11px; margin-top:4px;">Captura el ciclo informativo completo</div>
          </div>
          <div style="padding: 12px; background: var(--bg-2); border-radius: 6px; border-left: 3px solid var(--accent);">
            <div style="color:var(--txt-2); font-size:11px; text-transform:uppercase; letter-spacing:1px;">Reglas de alerta</div>
            <div style="font-size:18px; font-weight:700; margin-top:4px;">13 reglas</div>
            <div style="color:var(--txt-2); font-size:11px; margin-top:4px;">VACANCIA · PARO · BLOQUEO · CORRUPCIÓN · etc.</div>
          </div>
          <div style="padding: 12px; background: var(--bg-2); border-radius: 6px; border-left: 3px solid var(--accent);">
            <div style="color:var(--txt-2); font-size:11px; text-transform:uppercase; letter-spacing:1px;">Factores P×I</div>
            <div style="font-size:18px; font-weight:700; margin-top:4px;">10 factores</div>
            <div style="color:var(--txt-2); font-size:11px; margin-top:4px;">Estabilidad · Sociales · Regulatorio · Económico</div>
          </div>
        </div>

        <h3 style="margin-top: 24px;">🗺️ Recursos de referencia externos</h3>
        <div style="color: var(--txt-2); font-size: 12.5px; margin-bottom: 10px;">
          Aplicaciones web oficiales con datos georreferenciados o resultados en vivo. Click para abrir directamente en una pestaña nueva.
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px;">
          <a href="https://aplicaciones.mininter.gob.pe/mapadeldelitogeoreferenciado/" target="_blank" rel="noopener"
             style="display: block; padding: 14px; background: var(--bg-2); border-radius: 8px; border-left: 3px solid var(--critico); text-decoration: none; color: var(--txt-0);">
            <div style="font-size: 11px; color: var(--txt-2); text-transform: uppercase; letter-spacing: 1px;">🚨 SEGURIDAD CIUDADANA</div>
            <div style="font-weight: 700; margin-top: 4px;">Mapa del Delito georreferenciado</div>
            <div style="font-size: 12px; color: var(--txt-2); margin-top: 4px;">MININTER · denuncias policiales por delito y zona, cobertura nacional</div>
          </a>
          <a href="https://resultadoelectoral.onpe.gob.pe/" target="_blank" rel="noopener"
             style="display: block; padding: 14px; background: var(--bg-2); border-radius: 8px; border-left: 3px solid var(--accent); text-decoration: none; color: var(--txt-0);">
            <div style="font-size: 11px; color: var(--txt-2); text-transform: uppercase; letter-spacing: 1px;">🗳️ ELECTORAL</div>
            <div style="font-weight: 700; margin-top: 4px;">ONPE - Resultados Electorales en Vivo</div>
            <div style="font-size: 12px; color: var(--txt-2); margin-top: 4px;">Conteo oficial actualizado de las elecciones 2026 y balotaje</div>
          </a>
          <a href="https://www.jne.gob.pe/index.html" target="_blank" rel="noopener"
             style="display: block; padding: 14px; background: var(--bg-2); border-radius: 8px; border-left: 3px solid var(--accent); text-decoration: none; color: var(--txt-0);">
            <div style="font-size: 11px; color: var(--txt-2); text-transform: uppercase; letter-spacing: 1px;">⚖️ ELECTORAL</div>
            <div style="font-weight: 700; margin-top: 4px;">JNE - Resoluciones del Pleno</div>
            <div style="font-size: 12px; color: var(--txt-2); margin-top: 4px;">Resoluciones del Jurado Nacional de Elecciones</div>
          </a>
          <a href="https://www.defensoria.gob.pe/areas_tematicas/paz-social-y-prevencion-de-conflictos/" target="_blank" rel="noopener"
             style="display: block; padding: 14px; background: var(--bg-2); border-radius: 8px; border-left: 3px solid var(--alto); text-decoration: none; color: var(--txt-0);">
            <div style="font-size: 11px; color: var(--txt-2); text-transform: uppercase; letter-spacing: 1px;">🤝 CONFLICTOS SOCIALES</div>
            <div style="font-weight: 700; margin-top: 4px;">Defensoría - Reporte mensual</div>
            <div style="font-size: 12px; color: var(--txt-2); margin-top: 4px;">Conflictos sociales activos por región y tipo</div>
          </a>
        </div>

        <h3 style="margin-top: 24px;">🔄 Cómo activar modo live continuo</h3>
        <pre style="background: var(--bg-0); border: 1px solid var(--bg-3); padding: 14px; border-radius: 6px; font-family: ui-monospace, monospace; font-size: 12.5px; color: var(--txt-1); overflow-x: auto;"><code>export TWITTER_BEARER_TOKEN="tu_bearer_de_x_api"
python -m apurisk.main --live --watch {refresh_seconds}

# El dashboard se regenerará cada {refresh_minutos} min
# El navegador hará auto-refresco del HTML al expirar
# Sirve la carpeta output/ con cualquier servidor estático:
python -m http.server 8080 --directory output
# Luego abre: http://localhost:8080/dashboard_latest.html</code></pre>
      </div>
    </div>
    <script>
      // Conectar el segundo botón de refresh manual
      document.getElementById('refreshBtn2')?.addEventListener('click', () => {{
        const u = new URL(location.href);
        u.searchParams.set('t', Date.now());
        location.href = u.toString();
      }});
    </script>
  </section>

  <!-- TAB: ENTIDADES -->
  <section class="tab-panel" id="tab-entidades">
    <div class="grid grid-12">
      <div class="card span-6"><h3>Top instituciones</h3>{inst_html}</div>
      <div class="card span-6"><h3>Top partidos</h3>{part_html}</div>
      <div class="card span-6"><h3>Empresas en zona de riesgo</h3>{emp_html}</div>
      <div class="card span-6"><h3>Regiones más mencionadas</h3>{reg_html}</div>
    </div>
  </section>

</div>

<footer>
  APURISK 1.0 · Construido como prototipo de consultoría política · Fuentes: medios peruanos, portales del Estado (Defensoría, Congreso, JNE),
  Twitter/X (API v2) y datasets internacionales (GDELT/ACLED) · Mapa: OpenStreetMap · En modo demo se utilizan datos sintéticos representativos.
</footer>

<script>
  // ====== Diagnóstico de librerías cargadas =====
  function checkLibs() {{
    let treemapLoaded = false;
    try {{
      treemapLoaded = !!(window.Chart && window.Chart.registry && window.Chart.registry.getController && window.Chart.registry.getController('treemap'));
    }} catch (e) {{ treemapLoaded = false; }}
    return {{
      chart: typeof window.Chart === 'function',
      treemap: treemapLoaded,
      leaflet: typeof window.L === 'object' && window.L !== null,
    }};
  }}
  window.addEventListener('load', () => {{
    const status = checkLibs();
    console.log('[APURISK] Librerías cargadas:', status);
    const diag = document.getElementById('libDiag');
    if (diag) {{
      const ok = '✓', fail = '✗';
      diag.innerHTML = `Chart: <span style="color:${{status.chart?'#22c55e':'#ef4444'}};">${{status.chart?ok:fail}}</span> · Treemap: <span style="color:${{status.treemap?'#22c55e':'#f59e0b'}};">${{status.treemap?ok:'opcional'}}</span> · Leaflet: <span style="color:${{status.leaflet?'#22c55e':'#ef4444'}};">${{status.leaflet?ok:fail}}</span>`;
    }}
    // Si treemap no cargó, ocultar su card y agrandar la matriz
    if (!status.treemap) {{
      const tc = document.getElementById('treemapCard');
      if (tc) tc.style.display = 'none';
      const mc = document.getElementById('matrixCard');
      if (mc) {{ mc.classList.remove('span-7'); mc.classList.add('span-12'); }}
    }}
  }});

  // ====== Tabs (PRIMERO — para que NUNCA se rompan si algo falla más abajo) =====
  document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    const target = document.getElementById('tab-' + t.dataset.tab);
    if (target) target.classList.add('active');
    // si abre el mapa, invalidate size
    if (t.dataset.tab === 'geo' && window._peruMap) {{
      setTimeout(() => {{
        try {{
          window._peruMap.invalidateSize();
          if (window._peruBounds) window._peruMap.fitBounds(window._peruBounds, {{padding: [40, 40]}});
        }} catch (e) {{ console.error('peru-map resize:', e); }}
      }}, 150);
    }}
    if (t.dataset.tab === 'riesgos') {{
      setTimeout(() => {{
        try {{
          if (window._matrixChart) window._matrixChart.resize();
          if (window._treemapChart) window._treemapChart.resize();
        }} catch (e) {{ console.error('chart resize:', e); }}
      }}, 100);
    }}
  }}));

  // ====== Countdown + auto-refresh (SIN auto-reload - solo display) =====
  (function () {{
    const REFRESH_MS = {refresh_seconds} * 1000;
    const generated = new Date('{generated_iso}');
    // expires se calcula desde el momento en que el navegador CARGA la página, no desde generated
    // Esto evita loops si la página se abre mucho después de generar el HTML
    const pageLoadedAt = new Date();
    const expires = new Date(pageLoadedAt.getTime() + REFRESH_MS);
    const cd = document.getElementById('countdown');
    const pb = document.getElementById('progressBar');
    const banner = document.getElementById('freshBanner');
    if (!cd) return;

    function fmt(ms) {{
      if (ms <= 0) return '00:00';
      const total = Math.floor(ms/1000);
      const m = Math.floor(total/60);
      const s = total % 60;
      return String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
    }}

    function tick() {{
      try {{
        const now = new Date();
        const remain = Math.max(0, expires - now);
        const ageOfData = now - generated;  // antigüedad del SNAPSHOT (no de la pestaña)
        if (cd) cd.textContent = fmt(remain);
        if (pb) {{
          const pct = Math.max(0, Math.min(100, (remain / REFRESH_MS) * 100));
          pb.style.width = pct + '%';
        }}
        if (cd) {{
          cd.classList.remove('warning', 'critical');
          if (remain < 300000 && remain > 0) cd.classList.add('warning');
          if (remain < 60000  && remain > 0) cd.classList.add('critical');
        }}
        // Banner stale si el snapshot tiene >1.5x del intervalo (no recarga, solo avisa)
        if (banner && ageOfData > REFRESH_MS * 1.5) {{
          banner.classList.add('stale');
          const icon = banner.querySelector('.icon');
          const strg = banner.querySelector('strong');
          if (icon) icon.textContent = '🟡';
          if (strg) strg.textContent = `Snapshot generado hace ${{Math.round(ageOfData/60000)}} min.`;
        }}
        // NO LLAMAR location.reload() aquí — el <meta http-equiv="refresh"> ya lo hace
        // de manera relativa al load del navegador, sin riesgo de loop.
      }} catch (e) {{ console.error('tick error:', e); }}
    }}
    tick();
    setInterval(tick, 1000);

    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => {{
      refreshBtn.disabled = true; refreshBtn.textContent = '⟳ Recargando...';
      const u = new URL(location.href);
      u.searchParams.set('t', Date.now());
      // Pequeño delay para que el usuario vea el feedback antes de recargar
      setTimeout(() => {{ location.href = u.toString(); }}, 200);
    }});
  }})();

  // Tabs antiguo (ya no necesario; el handler de tabs está arriba)
  if (false) document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('tab-' + t.dataset.tab).classList.add('active');
    // si abre el mapa, invalidate size (Leaflet necesita esto si el contenedor estaba oculto)
    if (t.dataset.tab === 'geo' && window._peruMap) {{
      setTimeout(() => {{
        window._peruMap.invalidateSize();
        // refit bounds tras hacer visible el mapa
        if (window._peruBounds) window._peruMap.fitBounds(window._peruBounds, {{padding: [40, 40]}});
      }}, 150);
    }}
    // si abre matriz de riesgos, redimensionar charts
    if (t.dataset.tab === 'riesgos') {{
      setTimeout(() => {{
        if (window._matrixChart) window._matrixChart.resize();
        if (window._treemapChart) window._treemapChart.resize();
        if (window._catChart) window._catChart.resize();
        if (window._topicsChart) window._topicsChart.resize();
      }}, 100);
    }}
  }}));

  // ===== Inicialización defensiva: un fallo no debe romper el resto =====
  function safeInit(name, fn) {{
    try {{ fn(); }} catch (e) {{ console.error('[APURISK]', name, 'error:', e); }}
  }}

  // Risk Matrix bubble
  const matrixData = {json.dumps(matriz_data, ensure_ascii=False)};
  const colorByNivel = {json.dumps(color_nivel, ensure_ascii=False)};
  const drawQuadrants = {{
    id: 'quadrants',
    afterDraw(chart) {{
      const {{ ctx, chartArea: {{ left, top, right, bottom }}, scales: {{ x, y }} }} = chart;
      ctx.save();
      ctx.strokeStyle = '#334155'; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
      const xMid = x.getPixelForValue(50); const yMid = y.getPixelForValue(50);
      ctx.beginPath(); ctx.moveTo(xMid, top); ctx.lineTo(xMid, bottom); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(left, yMid); ctx.lineTo(right, yMid); ctx.stroke();
      ctx.restore();
      ctx.fillStyle = '#475569'; ctx.font = '10px sans-serif';
      ctx.fillText('Alto Imp · Baja Prob', left + 6, top + 14);
      ctx.fillText('Alto Imp · Alta Prob (CRÍTICO)', xMid + 6, top + 14);
      ctx.fillText('Bajo Imp · Baja Prob', left + 6, bottom - 6);
      ctx.fillText('Bajo Imp · Alta Prob', xMid + 6, bottom - 6);
    }}
  }};
  if (window.Chart) Chart.register(drawQuadrants);

  safeInit('matrixChart', () => {{ window._matrixChart = new Chart(document.getElementById('matrixChart').getContext('2d'), {{
    type: 'bubble',
    data: {{
      datasets: matrixData.map(d => ({{
        label: d.label,
        data: [{{x: d.x, y: d.y, r: d.r}}],
        backgroundColor: colorByNivel[d.nivel] + 'CC',
        borderColor: colorByNivel[d.nivel], borderWidth: 2,
      }}))
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display:false }},
        tooltip: {{ callbacks: {{ label: (ctx) => `${{ctx.dataset.label}} — Prob ${{ctx.raw.x}} · Imp ${{ctx.raw.y}}` }} }}
      }},
      scales: {{
        x: {{ min: 0, max: 100, title: {{ display: true, text: 'PROBABILIDAD →', color:'#94a3b8', font:{{size:10, weight:600}} }}, grid: {{ color:'#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
        y: {{ min: 0, max: 100, title: {{ display: true, text: 'IMPACTO →', color:'#94a3b8', font:{{size:10, weight:600}} }}, grid: {{ color:'#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
      }}
    }}
  }}); }});

  // Treemap (puede fallar si el plugin no carga; safeInit aísla el error)
  // Diseño limpio: cada factor en su propio rectángulo (sin agrupación de categoría
  // que causaba labels superpuestas). Nombre arriba, score abajo, sin "Σ" confuso.
  const treemapData = {json.dumps(treemap_data, ensure_ascii=False)};
  safeInit('treemapChart', () => {{ window._treemapChart = new Chart(document.getElementById('treemapChart').getContext('2d'), {{
    type: 'treemap',
    data: {{
      datasets: [{{
        tree: treemapData,
        key: 'value',
        // Sin 'groups' — un solo nivel: cada factor es un rectángulo independiente
        backgroundColor: (ctx) => {{
          if (!ctx.raw || !ctx.raw._data) return 'rgba(56,189,248,0.55)';
          const nivel = ctx.raw._data.nivel;
          const c = colorByNivel[nivel] || '#38bdf8';
          // Añadimos transparencia para que el texto blanco resalte mejor
          return c + 'D9';
        }},
        borderColor: '#0a0e1a', borderWidth: 2, spacing: 2,
        labels: {{
          display: true,
          align: 'left',
          position: 'top',
          color: '#ffffff',
          font: {{ size: 11, weight: 'bold', family: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }},
          padding: 6,
          formatter: (ctx) => {{
            if (!ctx.raw || !ctx.raw._data) return '';
            const d = ctx.raw._data;
            // Truncar nombres largos para que entren en el rectángulo
            const nombre = (d.name || '').length > 24 ? (d.name || '').substring(0, 22) + '…' : (d.name || '');
            // Si el rectángulo es muy chiquito (score < 30), solo mostrar score
            if ((d.value || 0) < 30) return `${{d.value || 0}}`;
            return [nombre, `Score: ${{d.value || 0}}  ·  ${{d.nivel || ''}}`];
          }},
          overflow: 'hidden',
        }}
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#0f172a',
          titleColor: '#ffffff', bodyColor: '#cbd5e1',
          borderColor: '#334155', borderWidth: 1,
          padding: 10,
          callbacks: {{
            title: (items) => {{
              if (!items.length) return '';
              const d = items[0].raw._data;
              return d.name || '';
            }},
            label: (ctx) => {{
              const d = ctx.raw._data;
              return [
                `Categoría: ${{d.category || '—'}}`,
                `Nivel: ${{d.nivel || '—'}}`,
                `Score: ${{d.value || 0}}`,
              ];
            }}
          }}
        }}
      }}
    }}
  }}); }});

  // ====== Tendencias semanales =====
  const trendData = {json.dumps(tendencias, ensure_ascii=False, default=str)};
  if (trendData.score_serie && trendData.score_serie.length > 0) {{
    // Gráfico de evolución del score
    safeInit('scoreSerieChart', () => {{ if (document.getElementById('scoreSerieChart')) new Chart(document.getElementById('scoreSerieChart'), {{
      type: 'line',
      data: {{
        labels: trendData.score_serie.map(p => p.label),
        datasets: [{{
          label: 'Score Global',
          data: trendData.score_serie.map(p => p.score),
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56,189,248,0.15)',
          fill: true,
          tension: 0.3,
          pointBackgroundColor: trendData.score_serie.map(p => {{
            if (p.score >= 70) return '#ef4444';
            if (p.score >= 45) return '#f59e0b';
            return '#22c55e';
          }}),
          pointRadius: 5,
          pointHoverRadius: 7,
        }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{ afterLabel: ctx => `Nivel: ${{trendData.score_serie[ctx.dataIndex].nivel}}\\nAlertas: ${{trendData.score_serie[ctx.dataIndex].alertas_total}} (${{trendData.score_serie[ctx.dataIndex].alertas_criticas}} críticas)` }} }}
        }},
        scales: {{
          x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8', font: {{size: 10}} }} }},
          y: {{ min: 0, max: 100, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
        }}
      }}
    }}); }});

    // Gráfico de alertas por snapshot
    safeInit('alertasSerieChart', () => {{ if (document.getElementById('alertasSerieChart')) new Chart(document.getElementById('alertasSerieChart'), {{
      type: 'bar',
      data: {{
        labels: trendData.score_serie.map(p => p.label),
        datasets: [{{
          label: 'Críticas', data: trendData.score_serie.map(p => p.alertas_criticas),
          backgroundColor: '#ef4444', stack: 'a'
        }}, {{
          label: 'Otras', data: trendData.score_serie.map(p => p.alertas_total - p.alertas_criticas),
          backgroundColor: '#f59e0b', stack: 'a'
        }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#cbd5e1', font: {{size: 11}} }} }} }},
        scales: {{
          x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ color: '#94a3b8', font: {{size: 10}} }} }},
          y: {{ stacked: true, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
        }}
      }}
    }}); }});

    // Tendencia de top 5 factores
    safeInit('factoresSerieChart', () => {{
      const factoresKeys = Object.keys(trendData.factores_serie || {{}}).slice(0, 5);
      if (factoresKeys.length === 0) return;
      const elem = document.getElementById('factoresSerieChart');
      if (!elem) return;
      const palette = ['#ef4444','#f97316','#a78bfa','#38bdf8','#22c55e'];
      const datasets = factoresKeys.map((nombre, i) => ({{
        label: nombre.length > 28 ? nombre.substring(0,26) + '…' : nombre,
        data: trendData.factores_serie[nombre].map(p => p.score),
        borderColor: palette[i],
        backgroundColor: palette[i] + '33',
        tension: 0.3, pointRadius: 3,
      }}));
      const labels = trendData.factores_serie[factoresKeys[0]].map(p => p.label);
      new Chart(elem, {{
        type: 'line',
        data: {{ labels: labels, datasets: datasets }},
        options: {{
          responsive: true, maintainAspectRatio: false,
          plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#cbd5e1', font: {{size: 10}} }} }} }},
          scales: {{
            x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8', font: {{size: 10}} }} }},
            y: {{ min: 0, max: 100, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
          }}
        }}
      }});
    }});
  }}

  // Mapa Leaflet (envuelto en safeInit para que un fallo no rompa el resto)
  const mapMarkers = {json.dumps(map_markers, ensure_ascii=False)};
  safeInit('peru-map', () => {{
  if (typeof L === 'undefined') {{
    console.error('Leaflet no cargó - mostrando fallback');
    const cont = document.getElementById('peru-map');
    if (cont) {{
      let html = '<div style="padding:20px; color:var(--txt-1);"><h4 style="color:var(--alto);">⚠ Leaflet no se cargó. Mostrando fallback:</h4>';
      html += '<table style="width:100%; margin-top:14px;"><thead><tr><th>Región</th><th>Nivel</th><th>Título</th><th>Fuente</th></tr></thead><tbody>';
      for (const m of mapMarkers) {{
        const link = m.url ? `<a href="${{m.url}}" target="_blank" rel="noopener">${{m.fuente}}</a>` : m.fuente;
        html += `<tr><td>${{m.region||'—'}}</td><td>${{m.nivel}}</td><td>${{m.titulo.substring(0,60)}}…</td><td>${{link}}</td></tr>`;
      }}
      html += '</tbody></table></div>';
      cont.innerHTML = html;
    }}
    return;
  }}
  const map = L.map('peru-map', {{ zoomControl: true }}).setView([-9.19, -75.0152], 5);
  window._peruMap = map;
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  }}).addTo(map);

  const colorByNivelMap = {{ 'CRÍTICA': '#ef4444', 'ALTA': '#f97316', 'MEDIA': '#f59e0b', 'BAJA': '#22c55e' }};

  // Agrupar markers por coordenada (mismo departamento) para mostrar uno solo con count
  const grouped = {{}};
  mapMarkers.forEach(m => {{
    const key = m.lat.toFixed(3) + ',' + m.lng.toFixed(3);
    if (!grouped[key]) grouped[key] = {{ lat: m.lat, lng: m.lng, region: m.region, items: [] }};
    grouped[key].items.push(m);
  }});

  Object.values(grouped).forEach(g => {{
    // determinar nivel agregado por mayor severidad del grupo
    const orden = ['CRÍTICA', 'ALTA', 'MEDIA', 'BAJA'];
    let nivelGrupo = 'MEDIA';
    for (const lvl of orden) {{
      if (g.items.some(x => x.nivel === lvl)) {{ nivelGrupo = lvl; break; }}
    }}
    const color = colorByNivelMap[nivelGrupo] || '#38bdf8';
    const count = g.items.length;
    const radius = count > 1 ? Math.min(22, 10 + count * 2) : (nivelGrupo === 'CRÍTICA' ? 12 : nivelGrupo === 'ALTA' ? 10 : 8);

    // ÚNICO marker por región — sin jitter, contenido en su zona
    const circle = L.circleMarker([g.lat, g.lng], {{
      radius: radius, color: color, weight: 2,
      fillColor: color, fillOpacity: 0.65,
    }}).addTo(map);

    // Si son múltiples eventos, mostrar count en el centro
    if (count > 1) {{
      const icon = L.divIcon({{
        className: 'marker-count',
        html: `<div style="background:${{color}}; color:#fff; border-radius:50%; width:24px; height:24px; line-height:24px; text-align:center; font-weight:700; font-size:11px; border:2px solid #0a0e1a; box-shadow:0 0 6px rgba(0,0,0,0.5);">${{count}}</div>`,
        iconSize: [24, 24], iconAnchor: [12, 12]
      }});
      L.marker([g.lat, g.lng], {{ icon: icon, interactive: false }}).addTo(map);
    }}

    // Popup con TODOS los eventos del grupo
    const eventos = g.items.map(m => {{
      const u = m.url ? `<br><a href='${{m.url}}' target='_blank' rel='noopener' style='color:#38bdf8;'>🔗 ${{m.fuente}}</a>` : '';
      const f = m.fecha ? `<span style='color:#94a3b8; font-size:10px;'>🕒 ${{m.fecha}}</span>` : '';
      return `<div style="padding: 6px 0; border-top: 1px solid #334155;">
        <div style='font-weight:600; font-size:12px; color:#fff;'>[${{m.nivel}}] ${{m.titulo}}</div>
        <div style='font-size:11px; color:#cbd5e1; margin: 2px 0;'>${{m.resumen.substring(0, 150)}}${{m.resumen.length > 150 ? '…' : ''}}</div>
        ${{f}}${{u}}
      </div>`;
    }}).join('');
    circle.bindPopup(
      `<div style='max-width: 320px; max-height: 380px; overflow-y: auto;'>
        <strong style='font-size:13px;'>📍 ${{g.region || 'Sin región'}}</strong>
        <span style='background:${{color}}; color:#fff; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:700; margin-left:6px;'>${{count}} ${{count === 1 ? 'evento' : 'eventos'}}</span>
        ${{eventos}}
      </div>`,
      {{maxWidth: 360}}
    );
  }});

  // Si hay markers, ajustar bounds para mostrar todos los puntos
  if (mapMarkers.length > 0) {{
    const lats = mapMarkers.map(m => m.lat);
    const lngs = mapMarkers.map(m => m.lng);
    const bounds = L.latLngBounds(
      [Math.min(...lats) - 0.5, Math.min(...lngs) - 0.5],
      [Math.max(...lats) + 0.5, Math.max(...lngs) + 0.5]
    );
    window._peruBounds = bounds;
    map.fitBounds(bounds, {{padding: [40, 40]}});
  }}
  }}); // cierre safeInit('peru-map')
</script>
</body>
</html>
"""
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
