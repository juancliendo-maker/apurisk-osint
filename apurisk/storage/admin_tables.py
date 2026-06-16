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
]


def inicializar_admin_tables(db_path: str) -> None:
    """Crea las tablas de configuración admin en la BD existente e inserta datos iniciales."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(_ADMIN_SCHEMA)
            for sql, _ in _DATOS_INICIALES:
                conn.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[admin_tables] Error inicializando tablas admin: {e}")
