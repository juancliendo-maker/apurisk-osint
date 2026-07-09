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
-- Rangos DUALES: el porcentaje SUGIERE una banda (primario/secundario), pero NO
-- decide solo — el peso del actor corrige la lectura final. Por eso bandas bajas
-- ofrecen dos niveles (ej. 0-3% verde O amarillo según actor). nivel_secundario
-- y color_secundario_hex son NULL cuando la banda es de color único.
CREATE TABLE IF NOT EXISTS config_umbrales_semaforo (
    id                    INTEGER PRIMARY KEY,
    rango_min             REAL NOT NULL,
    rango_max             REAL NOT NULL,
    nivel_sugerido        TEXT NOT NULL,   -- nivel primario de la banda
    color_hex             TEXT,            -- color primario
    nivel_secundario      TEXT,            -- nivel alternativo si el actor corrige (NULL = banda única)
    color_secundario_hex  TEXT,            -- color alternativo (NULL = banda única)
    pais                  TEXT NOT NULL DEFAULT 'PE',
    activo                INTEGER NOT NULL DEFAULT 1,
    UNIQUE(rango_min, rango_max, pais)
);

-- Activadores de rojo automático (editables por país)
-- tipo: 'absoluto'    → dispara ROJO por sí mismo, sin importar el contexto
--       'condicional' → dispara ROJO solo si el contexto lo confirma (depende del
--                       motor de inteligencia evaluar afectación real)
CREATE TABLE IF NOT EXISTS config_activadores_rojo (
    id          INTEGER PRIMARY KEY,
    pais        TEXT NOT NULL DEFAULT 'PE',
    descripcion TEXT NOT NULL,
    tipo        TEXT NOT NULL DEFAULT 'condicional',  -- 'absoluto' | 'condicional'
    activo      INTEGER NOT NULL DEFAULT 1,
    orden       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(pais, descripcion)
);

CREATE INDEX IF NOT EXISTS idx_activadores_pais ON config_activadores_rojo(pais, activo);

-- Piso estructural de gravedad por tema (editable por analista, por país)
-- El analista fija un mínimo de gravedad estructural para un tema (0-100).
-- El eje Y de la Matriz B del semáforo = max(piso_estructural, impacto_base).
-- piso=0 → la gravedad estructural la define solo el impacto base del tema.
CREATE TABLE IF NOT EXISTS config_piso_estructural (
    id              INTEGER PRIMARY KEY,
    pais            TEXT NOT NULL DEFAULT 'PE',
    tema            TEXT NOT NULL,
    piso            REAL NOT NULL DEFAULT 0,    -- 0-100
    notas           TEXT,
    actualizado_en  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(pais, tema)
);

CREATE INDEX IF NOT EXISTS idx_piso_pais ON config_piso_estructural(pais);

-- Auditoría de cambios de calibración del semáforo (pisos, umbrales, coeficientes)
-- Estructura idéntica a config_fuentes_log para consistencia.
CREATE TABLE IF NOT EXISTS config_semaforo_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campo       TEXT NOT NULL,          -- 'piso:electoral' | 'umbral_x' | 'coef_actividad' …
    valor_anterior  TEXT,
    valor_nuevo     TEXT,
    usuario     TEXT NOT NULL,
    motivo      TEXT,
    cambiado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_semaforo_log_campo ON config_semaforo_log(campo);
CREATE INDEX IF NOT EXISTS idx_semaforo_log_usuario ON config_semaforo_log(usuario);

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

-- ============================================================
-- REGISTRO DE ACTORES (Fase C — base de poder instalada)
-- ============================================================
-- Capas 1 (nivel estratégico) y 2 (capacidad efectiva).
-- Capas 3 (CVO) y 4 (Dinámica) se añaden en tareas posteriores.

-- Actores relevantes para el análisis de riesgo político
CREATE TABLE IF NOT EXISTS config_actores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pais                TEXT NOT NULL DEFAULT 'PE',
    nombre              TEXT NOT NULL,
    tipo                TEXT NOT NULL DEFAULT 'formal',
                        -- 'formal' | 'fáctico' | 'territorial' | 'informal'
    nivel               TEXT NOT NULL DEFAULT 'IV',   -- I-VIII
    nivel_base          REAL NOT NULL DEFAULT 60,
    nivel_base_manual   INTEGER NOT NULL DEFAULT 0,   -- 1 = ajuste manual, no pisa propagación
    -- Capa 2: 6 criterios 1-5. Decisión/recursos/articulación pesan doble.
    crit_decision       INTEGER NOT NULL DEFAULT 3,
    crit_recursos       INTEGER NOT NULL DEFAULT 3,
    crit_articulacion   INTEGER NOT NULL DEFAULT 3,
    crit_legitimidad    INTEGER NOT NULL DEFAULT 3,
    crit_resiliencia    INTEGER NOT NULL DEFAULT 3,
    crit_proyeccion     INTEGER NOT NULL DEFAULT 3,
    -- Calculados y guardados; se recalculan en cada edición
    capacidad_efectiva  REAL NOT NULL DEFAULT 0.5,
    peso_calculado      REAL NOT NULL DEFAULT 0,
    territorio          TEXT NOT NULL DEFAULT 'nacional',
    alias               TEXT,    -- variantes del nombre en prensa, separadas por comas
    activo              INTEGER NOT NULL DEFAULT 1,
    notas_analista      TEXT,
    creado_en           TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_actores_pais ON config_actores(pais, activo);
CREATE INDEX IF NOT EXISTS idx_actores_nivel ON config_actores(nivel);
CREATE INDEX IF NOT EXISTS idx_actores_peso ON config_actores(peso_calculado DESC);

-- Relación actor ↔ tema de riesgo (muchos-a-muchos)
-- Un actor puede influir en varios temas; un tema puede tener varios actores.
-- La agregación de pesos por tema se define en la siguiente tarea.
CREATE TABLE IF NOT EXISTS config_actor_temas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    INTEGER NOT NULL,
    tema        TEXT NOT NULL,
    pais        TEXT NOT NULL DEFAULT 'PE',
    UNIQUE(actor_id, tema),
    FOREIGN KEY(actor_id) REFERENCES config_actores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_actor_temas_actor ON config_actor_temas(actor_id);
CREATE INDEX IF NOT EXISTS idx_actor_temas_tema ON config_actor_temas(tema);

-- Auditoría de cambios en actores
CREATE TABLE IF NOT EXISTS config_actores_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id        INTEGER,
    actor_nombre    TEXT,
    campo           TEXT NOT NULL,
    valor_anterior  TEXT,
    valor_nuevo     TEXT,
    usuario         TEXT NOT NULL,
    motivo          TEXT,
    cambiado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_actores_log_actor ON config_actores_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_actores_log_usuario ON config_actores_log(usuario);

-- ============================================================
-- PROYECCIÓN — Puntos de quiebre (Capa de futuro)
-- ============================================================
-- Eventos futuros conocidos que la tendencia mediática no puede prever
-- (ej. cambio de gobierno). Afectan SOLO la Proyección B (gravedad/riesgo).
-- La Proyección A (actividad) es tendencia pura y no los lee.

CREATE TABLE IF NOT EXISTS config_puntos_quiebre (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL,
    fecha           TEXT NOT NULL,           -- ISO 'YYYY-MM-DD': cuándo ocurre
    pais            TEXT NOT NULL DEFAULT 'PE',
    activo          INTEGER NOT NULL DEFAULT 1,
    notas           TEXT,
    creado_en       TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_quiebre_pais ON config_puntos_quiebre(pais, activo);
CREATE INDEX IF NOT EXISTS idx_quiebre_fecha ON config_puntos_quiebre(fecha);

-- Efectos de un quiebre por tema (una fila por tema afectado)
CREATE TABLE IF NOT EXISTS config_quiebre_efectos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    quiebre_id  INTEGER NOT NULL,
    tema        TEXT NOT NULL,
    direccion   TEXT NOT NULL DEFAULT 'no_toca',   -- 'sube' | 'baja' | 'no_toca'
    intensidad  TEXT NOT NULL DEFAULT 'moderado',  -- 'leve' | 'moderado' | 'fuerte'
    duracion    TEXT NOT NULL DEFAULT 'permanente',-- 'permanente' | 'temporal'
    UNIQUE(quiebre_id, tema),
    FOREIGN KEY(quiebre_id) REFERENCES config_puntos_quiebre(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quiebre_efectos_q ON config_quiebre_efectos(quiebre_id);
CREATE INDEX IF NOT EXISTS idx_quiebre_efectos_tema ON config_quiebre_efectos(tema);

-- Auditoría de cambios en puntos de quiebre
CREATE TABLE IF NOT EXISTS config_quiebre_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quiebre_id      INTEGER,
    quiebre_nombre  TEXT,
    campo           TEXT NOT NULL,
    valor_anterior  TEXT,
    valor_nuevo     TEXT,
    usuario         TEXT NOT NULL,
    motivo          TEXT,
    cambiado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_quiebre_log_q ON config_quiebre_log(quiebre_id);
CREATE INDEX IF NOT EXISTS idx_quiebre_log_usuario ON config_quiebre_log(usuario);

-- ============================================================
-- MOTOR DE INTELIGENCIA — análisis de 7 pasos por tema (Reportes A/B)
-- ============================================================
-- Reporte A = pasos automáticos (2,3,7), foto situacional sin criterio.
-- Reporte B = A + pasos de criterio del analista (1,4,5,6).
-- Cada guardado = una versión fechada (trazabilidad de la lectura del tema).
-- cliente_id es un HOOK para el enfoque por cliente futuro (NULL por ahora).

CREATE TABLE IF NOT EXISTS config_analisis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tema            TEXT NOT NULL,
    pais            TEXT NOT NULL DEFAULT 'PE',
    cliente_id      INTEGER,                 -- hook futuro (enfoque por cliente); NULL = por tema
    version         INTEGER NOT NULL,        -- incremental por (tema, pais, cliente_id)
    fecha           TEXT NOT NULL DEFAULT (datetime('now')),
    usuario         TEXT NOT NULL,
    -- Pasos de CRITERIO (Etapa 2; columnas creadas ya para estabilidad del esquema)
    paso1_escenario     TEXT,    -- (1) Escenario estructural · criterio · solo B
    paso4_organizacion  TEXT,    -- (4) Organización de actores · criterio · solo B
    paso5_sustancia     TEXT,    -- (5) Filtro: sustancia confirmada/editada · solo B
    paso5_ruido         TEXT,    -- (5) Filtro: ruido SEPARADO y conservado · solo B
    paso6_impacto       TEXT,    -- (6) Impacto · criterio · solo B
    snapshot_auto       TEXT,    -- JSON con los pasos automáticos 2,3,7 al momento de guardar
    creado_en       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analisis_tema ON config_analisis(tema, pais, version);
CREATE INDEX IF NOT EXISTS idx_analisis_cliente ON config_analisis(cliente_id);

-- Auditoría de cambios del motor de inteligencia
CREATE TABLE IF NOT EXISTS config_analisis_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    analisis_id     INTEGER,
    tema            TEXT,
    campo           TEXT NOT NULL,
    valor_anterior  TEXT,
    valor_nuevo     TEXT,
    usuario         TEXT NOT NULL,
    motivo          TEXT,
    cambiado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analisis_log_tema ON config_analisis_log(tema);
CREATE INDEX IF NOT EXISTS idx_analisis_log_usuario ON config_analisis_log(usuario);

-- ============================================================
-- GENERADOR DE REPORTES (Etapa 3, Fase 3) — solicitudes y archivos
-- ============================================================
-- Registro de reportes solicitados/generados. En Fase 3-1 solo se guarda la
-- solicitud (estado 'generando'); el PDF real lo produce Fase 3-2.
CREATE TABLE IF NOT EXISTS reportes_generados (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_generacion  TEXT NOT NULL DEFAULT (datetime('now')),
    tipo              TEXT NOT NULL,   -- reporte_a_automatico | reporte_a_manual | reporte_b_tema | reporte_b_caso
    tema              TEXT,            -- si reporte_b_tema
    caso              TEXT,            -- si reporte_b_caso
    rango_datos       TEXT NOT NULL DEFAULT '7d',   -- 24h | 7d | 15d | 30d
    ruta_archivo      TEXT,            -- nombre de archivo PDF (relativo a reportes/)
    tamano_kb         INTEGER,
    estado            TEXT NOT NULL DEFAULT 'generando',  -- generando | completado | error
    nota              TEXT,
    snapshot_id_datos INTEGER,
    usuario_solicito  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reportes_fecha ON reportes_generados(fecha_generacion DESC);
CREATE INDEX IF NOT EXISTS idx_reportes_estado ON reportes_generados(estado);
CREATE INDEX IF NOT EXISTS idx_reportes_tipo ON reportes_generados(tipo);
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

    # ── Umbrales del semáforo (rangos DUALES — el % sugiere, el actor corrige) ──
    # Bandas bajas ofrecen dos niveles; el motor de inteligencia elige según el
    # peso del actor. Bandas altas (≥20%) son de color único.
    *[("INSERT OR IGNORE INTO config_umbrales_semaforo "
       "(rango_min, rango_max, nivel_sugerido, color_hex, "
       "nivel_secundario, color_secundario_hex, pais, activo) "
       f"VALUES ({rmin}, {rmax}, '{n1}', '{c1}', {n2}, {c2}, 'PE', 1)", [])
      for rmin, rmax, n1, c1, n2, c2 in [
          (0,   3,  "VERDE",         "#2ecc71", "'AMARILLO'", "'#f1c40f'"),
          (4,   9,  "AMARILLO",      "#f1c40f", "'NARANJA'",  "'#e67e22'"),
          (10,  19, "NARANJA_ALTO",  "#e67e22", "NULL",       "NULL"),
          (20,  30, "ROJO_PROBABLE", "#e74c3c", "NULL",       "NULL"),
          (30, 100, "ROJO",          "#c0392b", "NULL",       "NULL"),
      ]],

    # ── Activadores de rojo automático — Perú ─────────────────────────────────
    # tipo 'absoluto'    → dispara ROJO por sí mismo
    #      'condicional' → dispara ROJO solo si el contexto lo confirma
    *[("INSERT OR IGNORE INTO config_activadores_rojo "
       f"(pais, descripcion, tipo, activo, orden) VALUES ('PE', '{desc}', '{tipo}', 1, {orden})", [])
      for orden, (tipo, desc) in enumerate([
          ("absoluto",    "Renuncia o cierre del Ejecutivo (presidente, premier o ministros clave)"),
          ("absoluto",    "Manifiesto, comunicado o posicionamiento público de las FFAA o PNP"),
          ("absoluto",    "Censura, interpelación, moción de vacancia o comisión investigadora activada en el Congreso"),
          ("absoluto",    "Investigación formal abierta por Fiscalía, Contraloría, PJ o Procuraduría General"),
          ("condicional", "Paro, bloqueo o movilización anunciada formalmente por gremio, sindicato, frente regional o comunidad"),
          ("condicional", "Adhesión pública de gobernador regional, alcalde provincial o líder territorial al conflicto"),
          ("condicional", "Medios nacionales de referencia instalando escándalo en portada o agenda principal por ≥48h"),
          ("condicional", "Señal de preocupación de embajada, organismo internacional o agencia calificadora"),
          ("condicional", "Afectación directa a inversión, operación, seguridad, reputación o continuidad de autoridad competente"),
          ("condicional", "Paso documentado de indignación digital a acción física, legal, política o administrativa"),
      ], start=1)],

    # ── Parámetro: tipo de fórmula del semáforo (MULTIPLICATIVA, no aditiva) ───
    # VC × PA × CE × IA × V — un factor en 0 colapsa el resultado a 0.
    # Los 'peso' de config_formula_semaforo actúan como EXPONENTES de cada factor,
    # no como sumandos. Documentado aquí para que el motor (tarea 2) lo respete.
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('FORMULA_SEMAFORO_TIPO', 'multiplicativa', 'string', "
        "'Fórmula del semáforo: VC x PA x CE x IA x V. Pesos = exponentes. "
        "Un factor en 0 colapsa el resultado.', 'GLOBAL')", []
    ),

    # ── Piso estructural por tema — seed en 0 (el analista lo edita) ───────────
    # Y de la Matriz B = max(piso, impacto_base). Con piso=0, Y = impacto_base.
    *[("INSERT OR IGNORE INTO config_piso_estructural (pais, tema, piso, notas) "
       f"VALUES ('PE', '{tema}', 0, 'seed inicial — sin piso definido')", [])
      for tema in [
          "estabilidad_gobierno", "corrupcion", "conflictos_sociales",
          "seguridad", "polarizacion", "riesgo_regulatorio",
          "economico_inversion", "electoral",
      ]],

    # ── Parámetros editables de la Matriz B del semáforo ──────────────────────
    # Umbrales de cuadrante (líneas divisorias y cómputo de n_graves_activos)
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SEMAFORO_UMBRAL_ACTIVIDAD_X', '25', 'float', "
        "'Matriz B: umbral del eje X (actividad) que separa silencioso/activo', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SEMAFORO_UMBRAL_GRAVEDAD_Y', '65', 'float', "
        "'Matriz B: umbral del eje Y (gravedad estructural) que separa menor/grave', 'GLOBAL')", []
    ),
    # Coeficientes del Score Global B (provisionales, calibrables sin tocar código)
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SCORE_B_COEF_ACTIVIDAD', '8', 'float', "
        "'Score B: máximo agravante por actividad del tema más grave (puntos)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SCORE_B_COEF_SIMULTANEIDAD', '3.5', 'float', "
        "'Score B: agravante por cada tema grave-y-activo adicional (puntos)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SCORE_B_BONUS_MAX', '15', 'float', "
        "'Score B: tope del agravante total sobre Y_max (puntos)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SEMAFORO_X_MAX_VIZ', '0', 'float', "
        "'Matriz B: tope visible del eje X. 0 = dinámico (máximo real + margen). "
        ">0 fija la escala para comparar semanas. No altera los datos.', 'GLOBAL')", []
    ),

    # ── PA por tema — parámetros de la fórmula de peso de actores ────────────
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('UMBRAL_ACTOR_FUERTE', '70', 'float', "
        "'PA por tema: peso mínimo (0-100) para que un actor cuente como fuerte y genere bonus', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('FACTOR_AGRAVANTE', '3', 'float', "
        "'PA por tema: puntos extra por cada actor fuerte adicional al primero', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('TOPE_BONUS', '10', 'float', "
        "'PA por tema: tope máximo del bonus acumulado por actores fuertes adicionales', 'GLOBAL')", []
    ),

    # ── Matriz B: urgencia por velocidad de cambio + Score "temperatura del momento" ──
    # El color del globo codifica URGENCIA (velocidad 7d), no gravedad. Un tema grave
    # escalando fuerte = urgente (rojo); grave pero quieto = importante (gris).
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SEMAFORO_VELOCIDAD_URGENTE', '30', 'float', "
        "'Matriz B: salto de actividad en 7d (pts) para marcar un tema grave como URGENTE (rojo)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SEMAFORO_VELOCIDAD_PRIORITARIO', '10', 'float', "
        "'Matriz B: salto de actividad en 7d (pts) para marcar un tema grave como PRIORITARIO (ámbar)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SCORE_B_PISO_GRAVEDAD', '65', 'float', "
        "'Score B: piso del score cuando todo está grave pero quieto (temperatura base 0-100)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SCORE_B_URGENCIA_REF', '50', 'float', "
        "'Score B: agregado de urgencia (vel_max + simultaneidad) que mapea a urgencia plena (1.0)', 'GLOBAL')", []
    ),

    # ── Urgencia combinada (gravedad + actividad + velocidad) con intensidad graduada ──
    # Clasificación: APAGADO (Y<65) · LATENTE (Y≥65, act<act_prio) · PRIORITARIO (ámbar)
    # · URGENTE (Y≥65, act≥act_urgente, rojo graduado por índice).
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SEMAFORO_ACTIVIDAD_URGENTE', '10', 'float', "
        "'Matriz B: actividad mínima (% vol.) para marcar un tema grave como URGENTE (rojo)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SEMAFORO_ACTIVIDAD_PRIORITARIO', '5', 'float', "
        "'Matriz B: actividad mínima (% vol.) para marcar un tema grave como PRIORITARIO (ámbar)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('URGENCIA_PESO_GRAVEDAD', '0.6', 'float', "
        "'Índice de urgencia: peso de la gravedad (Y/100). La gravedad manda', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('URGENCIA_PESO_ACTIVIDAD', '0.25', 'float', "
        "'Índice de urgencia: peso de la actividad normalizada (act/act_ref)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('URGENCIA_PESO_VELOCIDAD', '0.15', 'float', "
        "'Índice de urgencia: peso de la velocidad positiva normalizada (vel/vel_ref)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('URGENCIA_ACT_REF', '15', 'float', "
        "'Índice de urgencia: actividad de referencia que normaliza a 1.0 (act_norm = min(1, act/ref))', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('URGENCIA_VEL_REF', '5', 'float', "
        "'Índice de urgencia: velocidad de referencia que normaliza a 1.0 (vel_norm = min(1, vel/ref))', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('SCORE_B_COEF_SIM_IDX', '0.03', 'float', "
        "'Score B: aporte de urgencia por cada tema URGENTE adicional (sobre el índice máximo)', 'GLOBAL')", []
    ),

    # ── Capa 4 Dinámica — umbrales y factor de divergencia (editables) ────────
    # trayectoria_base = suma de 7 señales (-14..+14). Etiqueta por umbral:
    #   ASCENSO (≥ umbral_ascenso) · ESTABLE (entre) · DECLIVE (≤ umbral_declive).
    # trayectoria_en_tema = base + divergencia(-1/0/+1) × factor_div.
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('TRAYECTORIA_UMBRAL_ASCENSO', '6', 'float', "
        "'Dinámica: trayectoria mínima para etiquetar al actor como ASCENSO (rango -14..+14)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('TRAYECTORIA_UMBRAL_DECLIVE', '-6', 'float', "
        "'Dinámica: trayectoria máxima para etiquetar al actor como DECLIVE (rango -14..+14)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('TRAYECTORIA_FACTOR_DIV', '3', 'float', "
        "'Dinámica: puntos que suma/resta una divergencia por tema (±1) a la trayectoria base', 'GLOBAL')", []
    ),

    # ── Proyección — amortiguación de tendencia (A) y mecánica de quiebres (B) ──
    # A: nivel_base(H) = actividad_now + velocidad × FACTOR(H);
    #    FACTOR = ALCANCE × Σ pesos (1.0 a 30d, +DECAY_60D a 60d, +DECAY_90D a 90d).
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PROY_VEL_ALCANCE', '2.0', 'float', "
        "'Proyección A: puntos por unidad de velocidad 7d a 30 días (factor base de la tendencia)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PROY_DECAY_60D', '0.5', 'float', "
        "'Proyección A: peso marginal de la velocidad en el bloque 30-60d (decae a la mitad)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PROY_DECAY_90D', '0.25', 'float', "
        "'Proyección A: peso marginal de la velocidad en el bloque 60-90d (decae otra mitad)', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('PROY_HORIZONTES', '15,30', 'string', "
        "'Proyección: horizontes en días (lista separada por comas) para las tablas A y B', 'GLOBAL')", []
    ),
    # B: gravedad_actual + Σ efectos de quiebre. Intensidad → puntos.
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('QUIEBRE_PTS_LEVE', '8', 'float', "
        "'Proyección B: puntos que sube/baja un tema por un quiebre de intensidad leve', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('QUIEBRE_PTS_MODERADO', '18', 'float', "
        "'Proyección B: puntos que sube/baja un tema por un quiebre de intensidad moderada', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('QUIEBRE_PTS_FUERTE', '30', 'float', "
        "'Proyección B: puntos que sube/baja un tema por un quiebre de intensidad fuerte', 'GLOBAL')", []
    ),
    (
        "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
        "VALUES ('QUIEBRE_DILUCION_DIAS', '30', 'float', "
        "'Proyección B: días en que un efecto TEMPORAL se diluye linealmente hasta 0 tras la fecha del quiebre', 'GLOBAL')", []
    ),

    # ── Valores base por nivel estratégico (I-VIII) — editables desde panel ──
    # Propagación automática: cambiar un valor aquí actualiza todos los actores
    # de ese nivel que NO tengan nivel_base_manual=1.
    *[("INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
       f"VALUES ('ACTOR_NIVEL_{codigo}_BASE', '{valor}', 'float', "
       f"'Valor base del Nivel {codigo} ({nombre}) para el peso del actor', 'GLOBAL')", [])
      for codigo, valor, nombre in [
          ("I",    "95", "Estructural"),
          ("II",   "85", "Sistémico"),
          ("III",  "72", "Determinante"),
          ("IV",   "60", "Relevante"),
          ("V",    "48", "Incidente"),
          ("VI",   "36", "Emergente"),
          ("VII",  "24", "Latente"),
          ("VIII", "12", "Periférico"),
      ]],
]


_MIGRACIONES = [
    # Fase B cierre: añadir peso_analista a instancias existentes (idempotente)
    "ALTER TABLE config_fuentes ADD COLUMN peso_analista REAL NOT NULL DEFAULT 1.0",
    # Alias de actores para emparejamiento con entidades detectadas en noticias
    "ALTER TABLE config_actores ADD COLUMN alias TEXT",
    # Capa 3 CVO — Índice de Activación Estratégica por actor-tema
    # Voluntad: interes_directo pesa doble; Oportunidad: ventana_coyuntural pesa doble.
    # Default 3 = neutro en escala 1-5. indice_activacion se recalcula al editar.
    "ALTER TABLE config_actor_temas ADD COLUMN interes_directo       INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE config_actor_temas ADD COLUMN postura_declarada     INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE config_actor_temas ADD COLUMN antecedente_accion    INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE config_actor_temas ADD COLUMN ventana_coyuntural    INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE config_actor_temas ADD COLUMN ausencia_contrapesos  INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE config_actor_temas ADD COLUMN recursos_movilizables INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE config_actor_temas ADD COLUMN indice_activacion     REAL",
    # Capa 4 Dinámica — trayectoria de poder del actor (7 señales, escala -2 a +2)
    # trayectoria_base = suma de las 7 (rango -14 a +14). Se calcula en vivo, no se almacena.
    "ALTER TABLE config_actores ADD COLUMN din_alianzas        INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE config_actores ADD COLUMN din_financiamiento  INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE config_actores ADD COLUMN din_territorio      INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE config_actores ADD COLUMN din_instituciones   INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE config_actores ADD COLUMN din_internacional   INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE config_actores ADD COLUMN din_relevo_lideres  INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE config_actores ADD COLUMN din_adaptacion      INTEGER NOT NULL DEFAULT 0",
    # Capa 4 — divergencia dinámica por tema (-1 retrocede / 0 neutro / +1 avanza)
    # trayectoria_en_tema = trayectoria_base + divergencia_dinamica × FACTOR_DIV
    "ALTER TABLE config_actor_temas ADD COLUMN divergencia_dinamica INTEGER NOT NULL DEFAULT 0",
    # Recorte del horizonte de proyección a 30 días. Una proyección lineal a 60-90d
    # no es defendible. Recorta SOLO si el valor sigue en el default original — respeta
    # cualquier ajuste manual posterior del analista en calibración.
    "UPDATE config_parametros SET valor='15,30' "
    "WHERE clave='PROY_HORIZONTES' AND valor='15,30,60,90'",
]


_AP24_PROMPT_MAESTRO_V1 = (
    "Eres el redactor de inteligencia de THALOS Strategic Intelligence. "
    "Redactas el \"Análisis Político — Últimas 24 horas\" para altos decisores "
    "(CEO, autoridades). Registro: español formal de inteligencia estratégica, "
    "sobrio, preciso, sin adjetivación innecesaria.\n\n"
    "REGLAS ABSOLUTAS:\n"
    "1. Usa ÚNICAMENTE los hechos provistos en el material adjunto. No uses "
    "conocimiento externo. No inventes hechos, cifras, declaraciones ni "
    "conexiones que el material no soporte.\n"
    "2. Al citar un hecho concreto, indica la fuente entre paréntesis.\n"
    "3. NO hagas proyecciones, escenarios, hipótesis ni recomendaciones. Tu "
    "función es DESCRIBIR y CONTEXTUALIZAR el día, no juzgarlo.\n"
    "4. Separa la sustancia del ruido: prioriza lo que altera el equilibrio "
    "político-institucional sobre lo anecdótico. No desaparezcas el ruido: si "
    "algo es ruidoso pero menor, puedes decirlo en una línea.\n"
    "5. Si el material de un tema es escaso, dilo con honestidad.\n\n"
    "ESTRUCTURA DE SALIDA (usa estos encabezados exactos):\n"
    "SÍNTESIS DEL DÍA — un párrafo (5-7 líneas) con la lectura integral.\n"
    "DESARROLLOS PRINCIPALES — 3 a 5 bloques breves, cada uno: qué ocurrió, qué "
    "actores involucra, en qué contexto se inscribe (según el material).\n"
    "CONEXIONES Y CONTEXTO — un párrafo que hile los desarrollos entre sí y con "
    "las métricas de riesgo provistas (qué temas concentran actividad).\n"
    "NOTA DE MATERIAL — una línea: volumen y límites del material del día."
)

# v2 — Calibración editorial del Coronel tras la primera corrida real. Endurece
# la ATRIBUCIÓN (24h de noticias no concluyen un contexto: se denuncia/se atribuye,
# no se afirma), impone terminología institucional neutra ("periodo de gobierno
# 2021-2026", nunca "gobierno de Boluarte"), exige citar la URL exacta provista sin
# inventarla, prohíbe formato enriquecido, y añade la sección HECHOS CITADOS (lista
# numerada que el PDF parsea para construir su tabla de referencias, mismo orden) y
# renombra DESARROLLOS PRINCIPALES → LAS ÚLTIMAS 24 HORAS EN DESARROLLO.
_AP24_PROMPT_MAESTRO_V2 = (
    "Eres el redactor de inteligencia de THALOS Strategic Intelligence.\n"
    "Redactas el \"Análisis Político — Últimas 24 horas\" para altos decisores.\n"
    "Registro: briefing de inteligencia en español formal. Frases concretas y\n"
    "directas. Evita adjetivos calificativos salvo que el hecho los exija.\n"
    "No es periodismo: es inteligencia.\n\n"
    "CONTENIDO:\n"
    "1. Usa ÚNICAMENTE los hechos provistos. Prohibido conocimiento externo\n"
    "   o afirmaciones no soportadas por el material.\n"
    "2. ATRIBUCIÓN OBLIGATORIA. 24 horas de noticias no permiten concluir un\n"
    "   contexto político. Nunca afirmes categóricamente lo que un titular\n"
    "   denuncia. Formula: \"las noticias de las últimas 24 horas denuncian\n"
    "   que...\", \"según [fuente]...\". Prohibido: \"corrupción institucional\n"
    "   en el MEF\". Correcto: \"las noticias de las últimas 24 horas denuncian\n"
    "   presunta corrupción en el MEF (Fuente — URL)\".\n"
    "3. TERMINOLOGÍA INSTITUCIONAL NEUTRA. Nombres oficiales de cargos e\n"
    "   instituciones, no etiquetas editoriales de medios. Regla explícita:\n"
    "   \"el periodo de gobierno 2021-2026\", nunca \"gobierno de Boluarte\".\n"
    "4. Cada desarrollo cita fuente y URL exactamente como fueron provistas.\n"
    "   Nunca inventes ni modifiques una URL; sin URL, cita solo la fuente.\n"
    "5. PROHIBIDO: proyecciones, hipótesis, recomendaciones. Describe y\n"
    "   contextualiza, no juzgues.\n"
    "6. Separa sustancia de ruido; lo ruidoso pero menor, en una línea.\n"
    "7. Si el material es escaso, dilo.\n\n"
    "FORMA:\n"
    "8. TEXTO PLANO ESTRICTO: sin asteriscos, almohadillas, viñetas, negritas\n"
    "   simuladas ni comillas tipográficas. Citas textuales solo si son\n"
    "   imprescindibles, con comillas rectas. Sin emojis.\n"
    "9. Párrafos compactos, sin líneas en blanco superfluas.\n\n"
    "ESTRUCTURA (encabezados exactos):\n"
    "SÍNTESIS DEL DÍA — un párrafo de 5-7 líneas.\n"
    "LAS ÚLTIMAS 24 HORAS EN DESARROLLO — 3-5 bloques: qué ocurrió, actores,\n"
    "contexto según material; cierre con (Fuente — URL).\n"
    "CONEXIONES Y CONTEXTO — un párrafo que hile desarrollos y métricas.\n"
    "HECHOS CITADOS — lista numerada, mismo orden de prioridad del reporte:\n"
    "[n]. [título] | [fuente] | [URL]. Solo hechos realmente citados.\n"
    "NOTA DE MATERIAL — una línea: volumen y límites del material."
)

# v3 — 2da corrida real. Las URLs YA NO van en el prompt ni en el material: el
# enlace lo arma el código sobre el título/fuente al renderizar (evita la ristra
# de caracteres cruda que confundía el texto). HECHOS CITADOS exige AL MENOS 10
# hechos (o todos los disponibles). El resto de la doctrina v2 se mantiene.
_AP24_PROMPT_MAESTRO_V3 = (
    "Eres el redactor de inteligencia de THALOS Strategic Intelligence.\n"
    "Redactas el \"Reporte de Riesgo Político — Últimas 24 horas\" para altos\n"
    "decisores. Registro: briefing de inteligencia en español formal. Frases\n"
    "concretas y directas. Evita adjetivos calificativos salvo que el hecho los\n"
    "exija. No es periodismo: es inteligencia.\n\n"
    "CONTENIDO:\n"
    "1. Usa ÚNICAMENTE los hechos provistos. Prohibido conocimiento externo\n"
    "   o afirmaciones no soportadas por el material.\n"
    "2. ATRIBUCIÓN OBLIGATORIA. 24 horas de noticias no permiten concluir un\n"
    "   contexto político. Nunca afirmes categóricamente lo que un titular\n"
    "   denuncia. Formula: \"las noticias de las últimas 24 horas denuncian\n"
    "   que...\", \"según [fuente]...\". Prohibido: \"corrupción institucional\n"
    "   en el MEF\". Correcto: \"las noticias de las últimas 24 horas denuncian\n"
    "   presunta corrupción en el MEF (según [fuente])\".\n"
    "3. TERMINOLOGÍA INSTITUCIONAL NEUTRA. Nombres oficiales de cargos e\n"
    "   instituciones, no etiquetas editoriales de medios. Regla explícita:\n"
    "   \"el periodo de gobierno 2021-2026\", nunca \"gobierno de Boluarte\".\n"
    "4. Cada desarrollo cita la FUENTE (nombre del medio) al mencionar un hecho.\n"
    "   NO escribas direcciones web ni URLs: el enlace lo agrega el sistema sobre\n"
    "   el título o la fuente. Escribe solo el nombre del medio entre paréntesis.\n"
    "5. PROHIBIDO: proyecciones, hipótesis, recomendaciones. Describe y\n"
    "   contextualiza, no juzgues.\n"
    "6. Separa sustancia de ruido; lo ruidoso pero menor, en una línea.\n"
    "7. Si el material es escaso, dilo.\n\n"
    "FORMA:\n"
    "8. TEXTO PLANO ESTRICTO: sin asteriscos, almohadillas, viñetas, negritas\n"
    "   simuladas ni comillas tipográficas. Citas textuales solo si son\n"
    "   imprescindibles, con comillas rectas. Sin emojis. Sin direcciones web.\n"
    "9. Párrafos compactos, sin líneas en blanco superfluas.\n\n"
    "ESTRUCTURA (encabezados exactos, cada uno en su propia línea):\n"
    "SÍNTESIS DEL DÍA — un párrafo de 5-7 líneas.\n"
    "LAS ÚLTIMAS 24 HORAS EN DESARROLLO — 3-5 bloques: qué ocurrió, actores,\n"
    "contexto según material; cierre con (según [fuente]).\n"
    "CONEXIONES Y CONTEXTO — un párrafo que hile desarrollos y métricas.\n"
    "HECHOS CITADOS — Lista numerada de AL MENOS 10 hechos del día (o todos los\n"
    "disponibles si el material es menor), en orden de prioridad. Formato por\n"
    "línea: [n]. [título] | [fuente]. Solo hechos del material. Sin URLs.\n"
    "NOTA DE MATERIAL — una línea: volumen y límites del material (si hubo menos\n"
    "de 10 hechos relevantes, dilo aquí)."
)

# v4 — Ajustes finales. Cada bloque de "LAS ÚLTIMAS 24 HORAS EN DESARROLLO" abre
# con su subtítulo temático en línea propia, prefijado con «» » (marcador que el
# render detecta y pone en negrita). Resto de la doctrina v3 intacta. Se construye
# a partir de v3 cambiando solo la línea de estructura de esa sección.
_AP24_PROMPT_MAESTRO_V4 = _AP24_PROMPT_MAESTRO_V3.replace(
    "LAS ÚLTIMAS 24 HORAS EN DESARROLLO — 3-5 bloques: qué ocurrió, actores,\n"
    "contexto según material; cierre con (según [fuente]).\n",
    "LAS ÚLTIMAS 24 HORAS EN DESARROLLO — 3 a 5 bloques. Cada bloque ABRE con su\n"
    "subtítulo temático en una línea propia, prefijada con «» » (ejemplo:\n"
    "» Transición política y conformación del gabinete). En el párrafo\n"
    "siguiente: qué ocurrió, actores, contexto según material; cierre con\n"
    "(según [fuente]). Usa el prefijo » SOLO en la línea del subtítulo.\n",
)

# Parámetros del Análisis Político 24h (Fase 3-3c). Editables por config.
_AP24_PARAMS = [
    ("AP24_MODELO", "claude-sonnet-4-6", "string",
     "Análisis Político 24h: modelo de la API de Anthropic (editable)"),
    ("AP24_MAX_TOKENS", "3000", "int",
     "Análisis Político 24h: máximo de tokens de salida"),
    ("AP24_TOP_N_ARTICULOS", "150", "int",
     "Análisis Político 24h: máximo de artículos 24h enviados (control de costo; "
     "≥150 para que el material alcance para ≥10 hechos citados)"),
    ("AP24_MODO_CALIBRACION", "1", "int",
     "Análisis Político 24h: 1 = marca de calibración visible en el PDF; 0 = operativo"),
    ("AP24_TIMEOUT_S", "120", "int",
     "Análisis Político 24h: timeout (segundos) por intento de la llamada a la API "
     "(la generación de ~3000 tokens tarda 40-90s; el default global de 30s es corto)"),
    ("REPORTES_WATCHDOG_MIN", "10", "int",
     "Reportes: minutos tras los cuales una entry en 'generando' se marca 'error' "
     "(anti-huérfanas: proceso muerto a mitad de generación)"),
    ("AP24_PROMPT_MAESTRO", _AP24_PROMPT_MAESTRO_V4, "string",
     "Análisis Político 24h: system prompt maestro (doctrina THALOS, editable)"),
]


# Reportes dummy para poblar la interfaz en Fase 3-1 (solo si la tabla está vacía).
_REPORTES_DUMMY = [
    "INSERT INTO reportes_generados "
    "(fecha_generacion, tipo, tema, caso, rango_datos, ruta_archivo, tamano_kb, estado, usuario_solicito) "
    "VALUES ('2026-07-02 09:15:00', 'reporte_a_manual', NULL, NULL, '24h', "
    "'2026-07-02_0915_ReporteA_OSINT_24h.pdf', 245, 'completado', 'seed')",
    "INSERT INTO reportes_generados "
    "(fecha_generacion, tipo, tema, caso, rango_datos, ruta_archivo, tamano_kb, estado, usuario_solicito) "
    "VALUES ('2026-07-01 14:30:00', 'reporte_b_tema', 'estabilidad_gobierno', NULL, '7d', "
    "'2026-07-01_1430_ReporteB_estabilidad_gobierno_7d.pdf', 312, 'completado', 'seed')",
    "INSERT INTO reportes_generados "
    "(fecha_generacion, tipo, tema, caso, rango_datos, ruta_archivo, tamano_kb, estado, usuario_solicito) "
    "VALUES ('2026-06-30 06:00:00', 'reporte_a_automatico', NULL, NULL, '24h', "
    "'2026-06-30_0600_ReporteA_OSINT_24h.pdf', 218, 'completado', 'scheduler')",
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
            # Parámetros AP24 (idempotente; no pisa ediciones del analista)
            for clave, valor, tipo, desc in _AP24_PARAMS:
                conn.execute(
                    "INSERT OR IGNORE INTO config_parametros (clave, valor, tipo, descripcion, pais) "
                    "VALUES (?,?,?,?, 'GLOBAL')", (clave, valor, tipo, desc),
                )
            # AP24 v2: UPDATE explícito del VALOR VIVO del prompt maestro (el
            # INSERT OR IGNORE de arriba nunca pisa un valor existente; en prod
            # el valor vivo es v1). Guardado en el texto v1 exacto para respetar
            # cualquier edición manual del analista (mismo criterio idempotente
            # que las migraciones). Una vez en v2, deja de coincidir → no-op.
            try:
                conn.execute(
                    "UPDATE config_parametros SET valor=? "
                    "WHERE clave='AP24_PROMPT_MAESTRO' AND valor=?",
                    (_AP24_PROMPT_MAESTRO_V2, _AP24_PROMPT_MAESTRO_V1),
                )
            except sqlite3.OperationalError:
                pass
            # AP24 v3: UPDATE del valor vivo v2 → v3 (URLs fuera del prompt, ≥10
            # hechos). Encadenado tras v1→v2, así un DB en v1 llega a v3 en una
            # sola pasada. Guardado en el texto v2 exacto (respeta ediciones del
            # analista; una vez en v3 deja de coincidir → no-op).
            try:
                conn.execute(
                    "UPDATE config_parametros SET valor=? "
                    "WHERE clave='AP24_PROMPT_MAESTRO' AND valor=?",
                    (_AP24_PROMPT_MAESTRO_V3, _AP24_PROMPT_MAESTRO_V2),
                )
                # AP24 v4: UPDATE del valor vivo v3 → v4 (subtítulos de bloque con
                # marcador «» »). Encadenado tras v2→v3; guardado en el texto v3
                # exacto (respeta ediciones del analista; en v4 deja de coincidir).
                conn.execute(
                    "UPDATE config_parametros SET valor=? "
                    "WHERE clave='AP24_PROMPT_MAESTRO' AND valor=?",
                    (_AP24_PROMPT_MAESTRO_V4, _AP24_PROMPT_MAESTRO_V3),
                )
                # TOP_N: subir el default vivo 120 → 150 (para ≥10 hechos). Solo
                # si sigue en el default original (respeta ajuste del analista).
                conn.execute(
                    "UPDATE config_parametros SET valor='150' "
                    "WHERE clave='AP24_TOP_N_ARTICULOS' AND valor='120'",
                )
            except sqlite3.OperationalError:
                pass
            # Seed dummy de reportes (solo si la tabla está vacía) — para que la
            # interfaz de Fase 3-1 no se vea vacía. Se genera una sola vez.
            try:
                vacio = conn.execute("SELECT COUNT(*) FROM reportes_generados").fetchone()[0] == 0
                if vacio:
                    for sql in _REPORTES_DUMMY:
                        conn.execute(sql)
            except sqlite3.OperationalError:
                pass
            conn.commit()
    except Exception as e:
        print(f"[admin_tables] Error inicializando tablas admin: {e}")
