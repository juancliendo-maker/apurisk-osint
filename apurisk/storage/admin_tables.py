"""APURISK · storage/admin_tables — Esquema de tablas de configuración editable.

Fase A: crea las tablas (vacías). La lectura en Fase A proviene de config.yaml.
Fase B: las tablas se pueblan desde el panel y el pipeline las consulta via ConfigLoader.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path


_ADMIN_SCHEMA = """
-- Fuentes RSS / web configurables (reemplazará rss_media.py hardcodeado en Fase B)
CREATE TABLE IF NOT EXISTS config_fuentes (
    id              INTEGER PRIMARY KEY,
    nombre          TEXT NOT NULL,
    url_feed        TEXT,
    tipo            TEXT NOT NULL DEFAULT 'rss',      -- 'rss' | 'web' | 'manual'
    pais            TEXT NOT NULL DEFAULT 'PE',
    calidad         REAL NOT NULL DEFAULT 1.0,
    peso_analista   REAL NOT NULL DEFAULT 1.0,        -- multiplicador manual del analista (0.1–2.0)
    activo          INTEGER NOT NULL DEFAULT 1,
    categoria       TEXT,
    notas           TEXT,
    creado_en       TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Factores de riesgo P×I configurables (reemplazará lista en risk_matrix.py en Fase B)
CREATE TABLE IF NOT EXISTS config_factores (
    id              INTEGER PRIMARY KEY,
    factor_id       TEXT UNIQUE NOT NULL,
    nombre          TEXT NOT NULL,
    categoria       TEXT NOT NULL,
    pais            TEXT NOT NULL DEFAULT 'PE',
    impacto_base    INTEGER NOT NULL DEFAULT 60,
    prob_base       INTEGER NOT NULL DEFAULT 30,
    activo          INTEGER NOT NULL DEFAULT 1,
    orden           INTEGER NOT NULL DEFAULT 0
);

-- Keywords por factor (reemplazará listas inline en risk_matrix.py en Fase B)
CREATE TABLE IF NOT EXISTS config_keywords (
    id          INTEGER PRIMARY KEY,
    factor_id   TEXT NOT NULL,
    tipo        TEXT NOT NULL,      -- 'fuerte' | 'contexto' | 'negacion'
    keyword     TEXT NOT NULL,
    pais        TEXT NOT NULL DEFAULT 'PE',
    activo      INTEGER NOT NULL DEFAULT 1
);

-- Reglas de alerta configurables (reemplazará alerts.py hardcodeado en Fase B)
CREATE TABLE IF NOT EXISTS config_alertas_reglas (
    id              INTEGER PRIMARY KEY,
    regla_id        TEXT UNIQUE NOT NULL,
    nombre          TEXT NOT NULL,
    nivel           TEXT NOT NULL,      -- 'CRÍTICA' | 'ALTA' | 'MEDIA'
    factor_id       TEXT,
    umbral_score    REAL NOT NULL DEFAULT 0,
    activo          INTEGER NOT NULL DEFAULT 1,
    pais            TEXT NOT NULL DEFAULT 'PE'
);

-- Perfiles de país (para expansión andina Fase C)
CREATE TABLE IF NOT EXISTS config_paises (
    codigo          TEXT PRIMARY KEY,   -- ISO 3166-1 alpha-2
    nombre          TEXT NOT NULL,
    activo          INTEGER NOT NULL DEFAULT 0,
    config_json     TEXT                -- JSON con overrides específicos de país
);

-- Parámetros globales del motor (reemplazará constantes en risk_matrix.py en Fase B)
CREATE TABLE IF NOT EXISTS config_parametros (
    clave       TEXT PRIMARY KEY,
    valor       TEXT NOT NULL,
    tipo        TEXT NOT NULL DEFAULT 'float',   -- 'float' | 'int' | 'string' | 'json'
    descripcion TEXT,
    pais        TEXT NOT NULL DEFAULT 'GLOBAL'
);

-- Auditoría de cambios de calidad/peso de fuentes (OBLIGATORIO per diseño)
CREATE TABLE IF NOT EXISTS config_fuentes_log (
    id              INTEGER PRIMARY KEY,
    fuente_id       INTEGER,
    campo           TEXT NOT NULL,          -- 'calidad' | 'peso_analista' | 'activo'
    valor_anterior  TEXT,
    valor_nuevo     TEXT,
    usuario         TEXT NOT NULL,
    motivo          TEXT,
    cambiado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fuentes_log_fuente ON config_fuentes_log(fuente_id);
CREATE INDEX IF NOT EXISTS idx_fuentes_log_campo ON config_fuentes_log(campo);
CREATE INDEX IF NOT EXISTS idx_fuentes_log_usuario ON config_fuentes_log(usuario);

-- Ingesta manual de URLs (Fase B Item 4)
-- El analista pega una URL; el pipeline la procesa en el siguiente ciclo.
-- procesada=0 → pendiente; procesada=1 → ya participó en el análisis.
-- Contador de trigger B2: si ingestas_hoy > TRIGGER_B2, mostrar aviso de migración.
CREATE TABLE IF NOT EXISTS ingestas_manuales (
    id              INTEGER PRIMARY KEY,
    url             TEXT NOT NULL,
    titulo          TEXT,
    resumen         TEXT,
    fuente          TEXT,
    categoria       TEXT NOT NULL DEFAULT 'medios',
    published       TEXT NOT NULL,          -- ISO 8601 hora Lima (PET)
    procesada       INTEGER NOT NULL DEFAULT 0,
    procesada_en    TEXT,
    ingresada_por   TEXT NOT NULL,
    ingresada_en    TEXT NOT NULL DEFAULT (datetime('now')),
    pais            TEXT NOT NULL DEFAULT 'PE'
);

CREATE INDEX IF NOT EXISTS idx_ingestas_procesada ON ingestas_manuales(procesada);
CREATE INDEX IF NOT EXISTS idx_ingestas_ingresada ON ingestas_manuales(ingresada_en);

-- ============================================================
-- MOTOR DE ANÁLISIS DUAL (Fase C — estructuras de datos)
-- ============================================================
-- Dos motores sobre el mismo flujo de noticias:
--   · osint       — volumen / reputación / tendencias
--   · inteligencia — análisis estratégico / semáforo

-- Factores de las dos fórmulas de puntaje OSINT
-- tipo_puntaje: 'sustancia' | 'ruido'
CREATE TABLE IF NOT EXISTS config_factores_formula (
    id              INTEGER PRIMARY KEY,
    motor           TEXT NOT NULL DEFAULT 'osint',  -- 'osint' | 'inteligencia'
    tipo_puntaje    TEXT NOT NULL,                  -- 'sustancia' | 'ruido'
    nombre_factor   TEXT NOT NULL,
    descripcion     TEXT,
    peso            REAL NOT NULL DEFAULT 1.0,
    pais            TEXT NOT NULL DEFAULT 'PE',
    activo          INTEGER NOT NULL DEFAULT 1,
    UNIQUE(motor, tipo_puntaje, nombre_factor, pais)
);

-- Factores de la fórmula del semáforo de riesgo (motor inteligencia)
-- factor: 'VC' | 'PA' | 'CE' | 'IA' | 'V'
CREATE TABLE IF NOT EXISTS config_formula_semaforo (
    id      INTEGER PRIMARY KEY,
    factor  TEXT NOT NULL,   -- código corto: VC / PA / CE / IA / V
    nombre  TEXT NOT NULL,
    peso    REAL NOT NULL DEFAULT 1.0,
    pais    TEXT NOT NULL DEFAULT 'PE',
    activo  INTEGER NOT NULL DEFAULT 1,
    UNIQUE(factor, pais)
);

-- Umbrales de clasificación del semáforo (guía visual)
CREATE TABLE IF NOT EXISTS config_umbrales_semaforo (
    id              INTEGER PRIMARY KEY,
    rango_min       REAL NOT NULL,
    rango_max       REAL NOT NULL,
    nivel_sugerido  TEXT NOT NULL,   -- 'VERDE' | 'AMARILLO' | 'NARANJA' | 'ROJO_PROBABLE' | 'ROJO'
    color_hex       TEXT,
    pais            TEXT NOT NULL DEFAULT 'PE',
    activo          INTEGER NOT NULL DEFAULT 1,
    UNIQUE(rango_min, rango_max, pais)
);

-- Activadores de rojo automático (editables por país)
CREATE TABLE IF NOT EXISTS config_activadores_rojo (
    id          INTEGER PRIMARY KEY,
    pais        TEXT NOT NULL DEFAULT 'PE',
    descripcion TEXT NOT NULL,
    activo      INTEGER NOT NULL DEFAULT 1,
    orden       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(pais, descripcion)
);

CREATE INDEX IF NOT EXISTS idx_activadores_pais ON config_activadores_rojo(pais, activo);

-- Resultados de análisis por artículo y por motor
-- Una sola tabla con campo 'motor' (extensible sin ALTER TABLE).
-- Unicidad: (articulo_id, motor) — un resultado por motor por artículo.
-- articulo_id referencia articulos.id en archive.db (misma BD vía attach o mismo archivo).
-- resultado_json: blob JSON con el output específico de cada motor.
CREATE TABLE IF NOT EXISTS resultados_analisis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    articulo_id     INTEGER NOT NULL,   -- FK a articulos.id
    motor           TEXT NOT NULL,      -- 'osint' | 'inteligencia'
    pais            TEXT NOT NULL DEFAULT 'PE',
    score_sustancia REAL,               -- OSINT: puntaje de sustancia 0-100
    score_ruido     REAL,               -- OSINT: puntaje de ruido 0-100
    score_semaforo  REAL,               -- Inteligencia: puntaje fórmula semáforo
    nivel_semaforo  TEXT,               -- Inteligencia: VERDE/AMARILLO/NARANJA/ROJO_PROBABLE/ROJO
    activador_rojo  INTEGER DEFAULT 0,  -- 1 si disparó activador automático
    resultado_json  TEXT,               -- JSON con detalle completo del análisis
    procesado_en    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(articulo_id, motor)
);

CREATE INDEX IF NOT EXISTS idx_resultados_articulo ON resultados_analisis(articulo_id);
CREATE INDEX IF NOT EXISTS idx_resultados_motor ON resultados_analisis(motor, pais);
CREATE INDEX IF NOT EXISTS idx_resultados_nivel ON resultados_analisis(nivel_semaforo);
"""

_DATOS_INICIALES = [
    ("INSERT OR IGNORE INTO config_paises (codigo, nombre, activo) VALUES ('PE', 'Perú', 1)", []),
    ("INSERT OR IGNORE INTO config_paises (codigo, nombre, activo) VALUES ('CO', 'Colombia', 0)", []),
    ("INSERT OR IGNORE INTO config_paises (codigo, nombre, activo) VALUES ('EC', 'Ecuador', 0)", []),
    ("INSERT OR IGNORE INTO config_paises (codigo, nombre, activo) VALUES ('BO', 'Bolivia', 0)", []),
    ("INSERT OR IGNORE INTO config_paises (codigo, nombre, activo) VALUES ('CL', 'Chile', 0)", []),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion) "
        "VALUES ('DECAY_HALF_LIFE_H', '36', 'float', 'Vida media del decay de evidencia en horas')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion) "
        "VALUES ('LOG_COEFICIENTE', '32', 'float', 'Coeficiente logarítmico de probabilidad')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PESO_H24', '0.30', 'float', 'Peso del horizonte 24h en score v2', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PESO_H7D', '0.30', 'float', 'Peso del horizonte 7d en score v2', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PESO_H30D', '0.25', 'float', 'Peso del horizonte 30d en score v2', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PESO_H90D', '0.15', 'float', 'Peso del horizonte 90d en score v2', 'GLOBAL')", []
    ),

    # ── Factores fórmula OSINT — puntaje de SUSTANCIA ─────────────────────────
    *[("INSERT OR IGNORE INTO config_factores_formula "
       "(motor, tipo_puntaje, nombre_factor, descripcion, peso, pais, activo) "
       f"VALUES ('osint', 'sustancia', '{nombre}', '{desc}', 1.0, 'PE', 1)", [])
      for nombre, desc in [
          ("relevancia_tematica",     "Qué tan directamente toca el tema político/institucional de interés"),
          ("peso_del_actor",          "Relevancia política del actor principal mencionado"),
          ("alcance_real",            "Cobertura efectiva estimada (audiencia real, no seguidores)"),
          ("capacidad_escalamiento",  "Potencial de que el evento escale a nivel nacional o sectorial"),
          ("territorialidad",         "Presencia o impacto en territorio con operaciones o intereses"),
          ("conexion_institucional",  "Vinculación con institución formal (Congreso, Fiscalía, FFAA, etc.)"),
          ("intensidad_narrativa",    "Tono, urgencia y carga emocional del relato periodístico"),
      ]],

    # ── Factores fórmula OSINT — puntaje de RUIDO ─────────────────────────────
    *[("INSERT OR IGNORE INTO config_factores_formula "
       "(motor, tipo_puntaje, nombre_factor, descripcion, peso, pais, activo) "
       f"VALUES ('osint', 'ruido', '{nombre}', '{desc}', 1.0, 'PE', 1)", [])
      for nombre, desc in [
          ("repeticion",              "Misma información publicada múltiples veces sin aporte nuevo"),
          ("anonimato",               "Fuente anónima o no verificable como origen principal"),
          ("automatizacion_probable", "Indicios de publicación automatizada o bot (ritmo, lenguaje, timing)"),
          ("baja_originalidad",       "Contenido copiado, parafraseado o sin valor agregado editorial"),
          ("baja_interaccion_autentica", "Engagement bajo o sospechosamente uniforme para el alcance declarado"),
          ("patron_coordinado",       "Múltiples cuentas/fuentes amplificando el mismo mensaje simultáneamente"),
      ]],

    # ── Factores fórmula SEMÁFORO (motor inteligencia) ────────────────────────
    *[("INSERT OR IGNORE INTO config_formula_semaforo "
       "(factor, nombre, peso, pais, activo) "
       f"VALUES ('{cod}', '{nombre}', 1.0, 'PE', 1)", [])
      for cod, nombre in [
          ("VC", "Vulnerabilidad del contexto institucional"),
          ("PA", "Posición del actor principal en la cadena de poder"),
          ("CE", "Capacidad de escalamiento del evento"),
          ("IA", "Intensidad y amplitud de la acción política"),
          ("V",  "Velocidad de propagación y adopción mediática"),
      ]],

    # ── Umbrales del semáforo ──────────────────────────────────────────────────
    *[("INSERT OR IGNORE INTO config_umbrales_semaforo "
       "(rango_min, rango_max, nivel_sugerido, color_hex, pais, activo) "
       f"VALUES ({rmin}, {rmax}, '{nivel}', '{color}', 'PE', 1)", [])
      for rmin, rmax, nivel, color in [
          (0,   3,  "VERDE",         "#2ecc71"),
          (3,   9,  "AMARILLO",      "#f1c40f"),
          (10,  19, "NARANJA",       "#e67e22"),
          (20,  30, "ROJO_PROBABLE", "#e74c3c"),
          (30, 100, "ROJO",          "#c0392b"),
      ]],

    # ── Activadores de rojo automático — Perú ─────────────────────────────────
    *[("INSERT OR IGNORE INTO config_activadores_rojo "
       f"(pais, descripcion, activo, orden) VALUES ('PE', '{desc}', 1, {orden})", [])
      for orden, desc in enumerate([
          "Renuncia o cierre del Ejecutivo (presidente, premier o ministros clave)",
          "Manifiesto, comunicado o posicionamiento público de las FFAA o PNP",
          "Censura, interpelación, moción de vacancia o comisión investigadora activada en el Congreso",
          "Investigación formal abierta por Fiscalía, Contraloría, PJ o Procuraduría General",
          "Paro, bloqueo o movilización anunciada formalmente por gremio, sindicato, frente regional o comunidad",
          "Adhesión pública de gobernador regional, alcalde provincial o líder territorial al conflicto",
          "Medios nacionales de referencia instalando escándalo en portada o agenda principal por ≥48h",
          "Señal de preocupación de embajada, organismo internacional o agencia calificadora",
          "Afectación directa a inversión, operación, seguridad, reputación o continuidad de autoridad competente",
          "Paso documentado de indignación digital a acción física, legal, política o administrativa",
      ], start=1)],
]


_MIGRACIONES = [
    # Fase B cierre: añadir peso_analista a instancias existentes (idempotente)
    "ALTER TABLE config_fuentes ADD COLUMN peso_analista REAL NOT NULL DEFAULT 1.0",
]


def inicializar_admin_tables(db_path: str) -> None:
    """Crea las tablas de configuración admin en la BD existente e inserta datos iniciales."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(_ADMIN_SCHEMA)
            for sql, _ in _DATOS_INICIALES:
                conn.execute(sql)
            # Migraciones idempotentes: ignoran "duplicate column" de SQLite
            for mig in _MIGRACIONES:
                try:
                    conn.execute(mig)
                except sqlite3.OperationalError:
                    pass  # columna ya existe
            conn.commit()
    except Exception as e:
        print(f"[admin_tables] Error inicializando tablas admin: {e}")
