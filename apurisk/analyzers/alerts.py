"""Motor de alertas inmediatas: reglas que disparan flags de criticidad.

Cada alerta incluye:
  - id, nivel (CRÍTICA | ALTA | MEDIA), titular, descripción
  - timestamp, antigüedad (horas)
  - regla disparada
  - URLs / fuentes
  - acción recomendada
"""
from __future__ import annotations
from datetime import datetime


REGLAS = [
    {
        "id": "VACANCIA_ACTIVADA",
        "nivel": "CRÍTICA",
        "patrones": ["moción de vacancia", "mocion de vacancia", "firmas para vacancia", "vacancia presidencial", "destitución", "destitucion", "destituido", "destituida", "censura presidencial"],
        # Descartar referencias HISTÓRICAS a vacancias previas (Castillo, Vizcarra, PPK, etc.)
        # y notas retrospectivas que no son evento actual.
        "patrones_negacion": [
            "pedro castillo", "vizcarra", "ppk", "kuczynski",
            "ex presidente", "expresidente", "ex-presidente",
            "anterior vacancia", "histórica vacancia", "historica vacancia",
            "complot contra", "denuncia contra pedro",
            "fue destituido", "fue destituida",
            "destituido en", "destituida en",
            "destituido el", "destituida el",
        ],
        "categoria": "Estabilidad gubernamental",
        "accion": "Activar protocolo de comunicación de crisis. Briefing a stakeholders en 2h.",
    },
    {
        "id": "RENUNCIA_MINISTRO",
        "nivel": "CRÍTICA",
        "patrones": ["renuncia ministro", "renuncia mininter", "renuncia titular", "presentó su renuncia", "presento su renuncia", "renuncia indeclinable"],
        "categoria": "Estabilidad gubernamental",
        "accion": "Mapear sucesor probable. Evaluar continuidad de políticas sectoriales.",
    },
    {
        # Crisis específica del Tribunal Constitucional.
        "id": "CRISIS_TRIBUNAL_CONSTITUCIONAL",
        "nivel": "CRÍTICA",
        "patrones": [
            # ── Tribunal Constitucional ──
            # Forma "X renuncia A LA presidencia DE Y" (la que usa la prensa real)
            "renuncia a la presidencia del tribunal constitucional",
            "renuncia a la presidencia del tc",
            "renuncia a presidencia del tribunal constitucional",
            "renuncia a presidencia del tc",
            "renunció a la presidencia del tribunal constitucional",
            "renuncio a la presidencia del tribunal constitucional",
            "renunció a la presidencia del tc",
            "renuncio a la presidencia del tc",
            "renunció a presidencia del tribunal constitucional",
            "renuncio a presidencia del tribunal constitucional",
            "renunció a presidencia del tc",
            "renuncio a presidencia del tc",
            # Forma "X DEJA la presidencia"
            "deja la presidencia del tribunal constitucional",
            "deja la presidencia del tc",
            "dejó la presidencia del tribunal constitucional",
            "dejo la presidencia del tribunal constitucional",
            "dejó la presidencia del tc",
            "dejo la presidencia del tc",
            # Forma "X ABANDONA la presidencia"
            "abandona la presidencia del tribunal constitucional",
            "abandona la presidencia del tc",
            "abandonó la presidencia del tribunal constitucional",
            "abandono la presidencia del tribunal constitucional",
            # Renuncia al cargo
            "renuncia al cargo de presidente del tribunal constitucional",
            "renuncia al cargo de presidenta del tribunal constitucional",
            "renuncia al cargo de presidente del tc",
            "renuncia al cargo de presidenta del tc",
            # Renuncia irrevocable
            "renuncia irrevocable presidente del tc",
            "renuncia irrevocable presidenta del tc",
            "renuncia irrevocable presidente del tribunal constitucional",
            "renuncia irrevocable presidenta del tribunal constitucional",
            # Magistrados del TC (variantes verbales)
            "magistrado del tc renuncia",
            "magistrada del tc renuncia",
            "magistrado del tribunal constitucional renuncia",
            "magistrada del tribunal constitucional renuncia",
            "magistrados del tribunal constitucional renuncian",
            "renuncia magistrado del tribunal constitucional",
            "renuncia magistrada del tribunal constitucional",
            "renuncia magistrado del tc",
            "renuncia magistrada del tc",
            # Crisis activa
            "crisis en el tribunal constitucional",
            "crisis del tribunal constitucional",
            "crisis en el tc",
            "crisis del tc",
            "vacancia tribunal constitucional",
            "destitución magistrado",
            "destitucion magistrado",
            "remoción magistrado",
            "remocion magistrado",
            # Apoyo perdido / votación interna (señales de crisis)
            "perder apoyo de magistrados",
            "perdió apoyo de magistrados",
            "perdio apoyo de magistrados",
            "rechazo del pleno del tc",
            "rechazo del pleno del tribunal constitucional",
        ],
        "patrones_negacion": [
            "ex magistrado", "ex magistrada", "exmagistrado", "exmagistrada",
            "ex presidente del tc", "expresidente del tc",
            "anterior renuncia", "anterior crisis", "previa renuncia",
            "histórica renuncia", "historica renuncia",
            "absuelve magistrado", "absuelven magistrado",
            "fallo del tc del año pasado", "fallo del tc del ano pasado",
            "tribunal constitucional de chile",
            "tribunal constitucional de bolivia",
            "tribunal constitucional de colombia",
            "tribunal constitucional de ecuador",
            "tribunal constitucional español", "tribunal constitucional espanol",
        ],
        "categoria": "Riesgo regulatorio",
        "accion": "Evaluar impacto en certidumbre constitucional. Revisar agenda del TC próximos 30 días sobre sectores expuestos (minería, tributario, ambiental).",
    },
    {
        # Crisis específica del Poder Judicial / Corte Suprema.
        "id": "CRISIS_PODER_JUDICIAL",
        "nivel": "CRÍTICA",
        "patrones": [
            "renuncia a la presidencia del poder judicial",
            "renuncia a la presidencia de la corte suprema",
            "renunció a la presidencia del poder judicial",
            "renuncio a la presidencia del poder judicial",
            "renunció a la presidencia de la corte suprema",
            "renuncio a la presidencia de la corte suprema",
            "deja la presidencia del poder judicial",
            "dejó la presidencia del poder judicial",
            "dejo la presidencia del poder judicial",
            "deja la presidencia de la corte suprema",
            "dejó la presidencia de la corte suprema",
            "dejo la presidencia de la corte suprema",
            "renuncia presidente del poder judicial",
            "renuncia presidenta del poder judicial",
            "renuncia presidente de la corte suprema",
            "renuncia presidenta de la corte suprema",
            "juez supremo renuncia",
            "jueza suprema renuncia",
            "destitución de juez supremo",
            "destitucion de juez supremo",
            "remoción jueces supremos",
            "remocion jueces supremos",
            "crisis del poder judicial",
            "crisis en el poder judicial",
            "crisis en la corte suprema",
            "huelga del poder judicial",
            "paro nacional del poder judicial",
            "paro nacional del pj",
        ],
        "patrones_negacion": [
            "ex presidente del poder judicial", "expresidente del poder judicial",
            "ex juez supremo", "exjuez supremo",
            "histórica renuncia", "historica renuncia",
            "poder judicial de chile", "poder judicial de bolivia",
            "corte suprema de chile", "corte suprema de argentina",
            "corte suprema de eeuu", "corte suprema de estados unidos",
        ],
        "categoria": "Riesgo regulatorio",
        "accion": "Evaluar impacto en amparos empresariales pendientes y procesos por delitos económicos. Revisar resoluciones críticas del próximo mes en Sala Suprema Constitucional.",
    },
    {
        # Crisis en órganos de control horizontal: JNJ, Contraloría,
        # Defensoría, Fiscalía de la Nación.
        "id": "CRISIS_ORGANOS_CONTROL",
        "nivel": "CRÍTICA",
        "patrones": [
            # JNJ
            "renuncia a la presidencia de la jnj",
            "renunció a la presidencia de la jnj",
            "renuncio a la presidencia de la jnj",
            "deja la presidencia de la jnj",
            "dejó la presidencia de la jnj",
            "renuncia presidente de la jnj",
            "renuncia presidenta de la jnj",
            "renuncia a la junta nacional de justicia",
            "miembro de la jnj renuncia",
            "miembros de la jnj renuncian",
            "renuncia miembro de la jnj",
            "crisis en la junta nacional de justicia",
            "crisis en la jnj",
            "remoción miembros jnj", "remocion miembros jnj",
            "remoción de la jnj", "remocion de la jnj",
            "pacto mafioso jnj",
            "pacto mafioso de la jnj",
            # Contraloría
            "renuncia a la contraloría general",
            "renuncia a la contraloria general",
            "renuncia contralor general", "renuncia contralora general",
            "renunció contralor general", "renuncio contralor general",
            "renunció contralora general", "renuncio contralora general",
            "deja la contraloría", "dejó la contraloría", "dejo la contraloria",
            "destitución contralor general", "destitucion contralor general",
            "destitución contralora general", "destitucion contralora general",
            "crisis en la contraloría", "crisis en la contraloria",
            # Defensoría del Pueblo
            "renuncia a la defensoría del pueblo",
            "renuncia a la defensoria del pueblo",
            "renuncia defensor del pueblo", "renuncia defensora del pueblo",
            "renunció defensor del pueblo", "renuncio defensor del pueblo",
            "destitución defensor del pueblo", "destitucion defensor del pueblo",
            "crisis en la defensoría", "crisis en la defensoria",
            # Fiscalía de la Nación
            "renuncia a la fiscalía de la nación",
            "renuncia a la fiscalia de la nacion",
            "renuncia fiscal de la nación", "renuncia fiscal de la nacion",
            "renunció fiscal de la nación", "renuncio fiscal de la nacion",
            "destitución fiscal de la nación", "destitucion fiscal de la nacion",
            "deja la fiscalía de la nación", "dejó la fiscalía de la nación",
            "dejo la fiscalia de la nacion",
            "crisis en el ministerio público", "crisis en el ministerio publico",
            "huelga del ministerio público", "huelga del ministerio publico",
        ],
        "patrones_negacion": [
            "ex contralor", "excontralor",
            "ex defensor", "exdefensor",
            "ex fiscal de la nación", "ex fiscal de la nacion",
            "anterior contralor", "anterior defensor",
            "fiscalía de chile", "fiscalia de chile",
            "fiscalía de bolivia", "fiscalia de bolivia",
            "contraloría de chile", "contraloria de chile",
            "defensoría de méxico", "defensoria de mexico",
        ],
        "categoria": "Riesgo regulatorio",
        "accion": "Evaluar impacto en auditorías de megaproyectos públicos, mediación en conflictos sociales, persecución delitos económicos. Revisar agenda próximas 4 semanas.",
    },
    {
        "id": "BLOQUEO_VIA_NACIONAL",
        "nivel": "CRÍTICA",
        "patrones": [
            "panamericana", "interoceánica", "interoceanica", "corredor minero",
            "bloqueo de", "bloquean", "vías bloqueadas", "vias bloqueadas",
            "toma de carretera", "toma de carreteras",
            "toma de la vía", "toma de la via",
            "toma de vía", "toma de via",
            "vía tomada", "via tomada",
            "carretera tomada", "carretera bloqueada",
            "paso bloqueado", "tránsito interrumpido",
            "ruta del cobre", "carretera central",
        ],
        # Descartar accidentes de tránsito (no son bloqueos políticos)
        # y movilizaciones POLÍTICAS/ELECTORALES (esas van a PROCESO_ELECTORAL).
        "patrones_negacion": [
            # Accidentes de tránsito
            "choque frontal", "choque entre", "accidente de tránsito",
            "accidente de transito", "accidente vehicular",
            "volcadura", "volcamiento", "carambola",
            "atropello", "atropellado",
            "minivan", "auto se despistó", "auto se despisto",
            "vehículo se despistó", "vehiculo se despisto",
            "heridos en accidente", "fallecidos en accidente",
            "obras en la carretera", "obras viales",
            "fluidez vehicular", "fluidez del tránsito",
            # Movilizaciones políticas/electorales específicas en actor
            # (no genéricas para evitar over-blocking de paros reales)
            "contra keiko", "contra fujimori",
            "contra castillo", "contra boluarte", "contra dina boluarte",
            "contra el congreso", "contra la jnj",
            "contra el gobierno", "contra el presidente",
            "contra el ejecutivo", "contra la presidenta",
            "marcha electoral", "marcha política", "marcha politica",
            "marcha ciudadana", "marcha por la democracia",
            "movilización política", "movilizacion politica",
            "movilización electoral", "movilizacion electoral",
            "plantón frente al congreso", "planton frente al congreso",
            "plantón frente al palacio", "planton frente al palacio",
            "plantón frente al jne", "planton frente al jne",
            # Indicadores electorales explícitos
            "segunda vuelta", "balotaje", "campaña electoral",
            "candidato presidencial",
        ],
        "categoria": "Conflictos sociales",
        "accion": "Alertar operaciones logísticas y mineras en zona afectada.",
    },
    {
        "id": "BLOQUEO_FLUVIAL",
        "nivel": "CRÍTICA",
        "patrones": [
            "bloqueo fluvial", "bloqueo del río", "bloqueo del rio",
            "toma del río", "toma del rio", "toma de río", "toma de rio",
            "río ramos", "rio ramos", "río marañón", "rio maranon",
            "río ucayali", "rio ucayali", "río amazonas", "rio amazonas",
            "río napo", "rio napo", "río huallaga", "rio huallaga",
            "vía fluvial bloqueada", "embarcaciones detenidas",
            "comunidades indígenas bloquean río",
            "comunidad bloquea río", "comunidad bloquea rio",
        ],
        "categoria": "Conflictos sociales",
        "accion": "Alertar operaciones extractivas y logística fluvial. Coordinar con Defensoría.",
    },
    {
        "id": "SICARIATO_HOMICIDIO_ORGANIZADO",
        "nivel": "CRÍTICA",
        "patrones": [
            # Sicariato genérico
            "sicariato", "sicario", "sicarios",
            "asesinato a sueldo", "asesinato por encargo",
            "ajuste de cuentas", "ajusticiado",
            "balacera", "tiroteo", "balacera deja muerto",
            # Extorsión / crimen organizado urbano
            "cobro de cupos", "cobro de cupo", "cobro por cupo",
            "extorsión transportistas", "extorsion transportistas",
            "asesinan a empresario", "asesinan a dirigente",
            "tren de aragua perú", "tren de aragua peru",
            "los pulpos", "los malditos",
            "matan a balazos", "abaten a balazos",
            "ola de asesinatos", "ola de violencia",
            # Asesinato / muerte de policías y militares (CRÍTICO para Perú)
            "muere policía", "muere policia", "muere efectivo policial",
            "policía muerto", "policia muerto", "asesinan policía",
            "asesinan policia", "abaten policía", "abaten policia",
            "policía abatido", "policia abatido", "policía caído",
            "matan a policía", "matan a policia",
            "ataque a policía", "atentado contra policía",
            "atentado contra patrulla", "patrulla atacada",
            "atentado a comisaría",
            "suboficial pnp", "sub oficial pnp",
            "suboficial muerto", "sub oficial muerto",
            "soldado muerto", "militar muerto",
            "muere militar", "abaten militar", "asesinan militar",
            "fallece efectivo", "efectivo policial fallecido",
            "ffaa abatido", "ffaa muerto",
        ],
        "categoria": "Seguridad",
        "accion": "Alertar seguridad de personal en zona. Coordinar con PNP/FECOR. Evaluar reforzar protocolos.",
    },
    {
        "id": "TOMA_UNIVERSITARIA",
        "nivel": "ALTA",
        "patrones": [
            "toma de universidad", "toma de la universidad",
            "toma de universidades", "ocupación universitaria",
            "ocupacion universitaria",
            "estudiantes toman universidad", "estudiantes toman la universidad",
            "alumnos toman universidad", "alumnos toman la universidad",
            "universitarios toman", "universidad tomada",
            "ocupan universidad", "ocupan campus",
            "tomar local universitario",
            # Universidades específicas frecuentemente afectadas
            "unmsm tomada", "san marcos tomada", "tomada san marcos",
            "uni tomada", "unfv tomada", "villarreal tomada",
            "unsa tomada", "unsaac tomada", "unp tomada",
            "callao tomada", "uncp tomada",
            "huelga estudiantil", "paro estudiantil",
            "manifestación estudiantil", "movilización estudiantil",
            "encadenados en universidad",
            "asamblea universitaria toma",
            # Detonantes habituales
            "rector renuncia", "destituyen rector", "asamblea destituye rector",
            "estudiantes exigen rector", "elecciones universidad",
            "reforma universitaria",
        ],
        "categoria": "Conflictos sociales",
        "accion": "Monitorear desarrollo y posibles bloqueos viales asociados. Coordinar con MININTER y autoridades académicas.",
    },
    {
        "id": "ASESINATOS_VIOLENCIA_CRITICA",
        "nivel": "CRÍTICA",
        "patrones": [
            # Asesinatos generales (alta prioridad)
            "asesinan", "asesinaron", "asesinato",
            "acribillado", "acribillada", "acribillaron", "acribillan",
            "muertos a balazos", "muerto a balazos",
            "muertos a tiros", "muerto a tiros",
            "asesinado a balazos", "asesinado a tiros",
            "matan a tiros", "matan de un balazo", "matan a balazos",
            "mueren a balazos", "mueren en ataque",
            "atacan a tiros", "disparan a quemarropa",
            "encuentran cadáver", "encuentran cadaver",
            "encuentran cuerpo sin vida", "hallan cadáver", "hallan cadaver",
            "cadáver encontrado", "cadaver encontrado",
            "asesinato múltiple", "asesinato multiple",
            "mueren en ataque", "mueren en balacera",
            "doble asesinato", "triple asesinato",
            "cuatro muertos", "cinco muertos", "seis muertos",
            "varios muertos", "múltiples muertos",
            "deja muertos", "deja víctimas mortales",
            # Lugares de ola criminal
            "trujillo asesinato", "asesinato en trujillo",
            "asesinato en lima", "asesinato en chiclayo",
            "asesinato en piura", "asesinato en chimbote",
            "asesinato en huancayo", "asesinato en arequipa",
            # Modalidades
            "feminicidio", "infanticidio",
            "asalto con muerte", "robo termina en muerte",
            "comerciante asesinado", "empresario asesinado",
            "transportista asesinado", "minero asesinado",
            "abogado asesinado", "periodista asesinado",
            "regidor asesinado", "alcalde asesinado",
            "dirigente asesinado", "líder asesinado",
            # Genérico
            "homicidio", "feminicidio", "parricidio",
            "violencia letal", "muerte violenta",
        ],
        "categoria": "Seguridad",
        "accion": "Verificar exposición de personal en zona. Coordinar con PNP. Plan de seguridad activable.",
    },
    {
        "id": "NARCOTRAFICO_OPERATIVO",
        "nivel": "CRÍTICA",
        "patrones": [
            # Narcotráfico explícito
            "narcotráfico", "narcotrafico",
            "narcotraficante", "narcotraficantes",
            "narcoterrorismo", "narcoterrorista", "narcoterroristas",
            # Decomisos y operativos
            "incauta droga", "incautan droga", "incautación de droga",
            "incautacion de droga", "decomiso de droga", "decomisan droga",
            "decomisa cocaína", "decomisa cocaina", "incautación cocaína",
            "cocaína incautada", "cocaina incautada",
            "kilos de cocaína", "kilos de cocaina",
            "toneladas de droga", "kilos de droga",
            "pasta básica de cocaína", "pasta basica de cocaina", "pbc decomiso",
            "operativo antidroga", "operativo dirandro", "dirandro decomisa",
            # VRAEM y zonas críticas
            "vraem", "vrae ", "valle de los ríos apurímac",
            "valle de los rios apurimac",
            "operativo vraem", "patrulla vraem",
            "remanentes terroristas", "remanentes sl",
            "sendero luminoso vraem",
            "ataque vraem", "emboscada vraem",
            "pichari", "kimbiri",  # localidades VRAEM
            # Carteles
            "cártel oro", "cartel oro",
            "carteles mexicanos", "carteles colombianos",
            "rutas del oro", "lavado de oro",
            "ruta del oro ilegal", "ruta del oro",
            "narco-minería", "narcominería", "narco mineria",
            "narco-mineria", "narcotráfico minería",
            # Encuentros e incautaciones generales
            "encuentran droga", "encuentra droga",
            "hallan droga", "hallaron droga",
            "tonelada de pbc", "tonelada de cocaína",
            "tonelada de cocaina", "toneladas de cocaína",
            "kilos de pbc", "kilos de oro ilegal",
        ],
        "categoria": "Seguridad",
        "accion": "Coordinar con FFAA/PNP/DEA. Verificar seguridad operativa en zonas críticas. Briefing de actualización.",
    },
    {
        "id": "CONFLICTO_EXTRACTIVO",
        "nivel": "ALTA",
        "patrones": ["las bambas", "antamina", "tía maría", "tia maria", "coroccohuayco", "espinar", "conga", "comunidades campesinas", "cotabambas"],
        "categoria": "Conflictos sociales",
        "accion": "Notificar empresa minera y autoridad regional. Activar mesa técnica.",
    },
    {
        "id": "PARO_REGIONAL",
        "nivel": "ALTA",
        "patrones": ["paro regional", "paro indefinido", "paro agrario", "paralización", "paralizacion", "frente de defensa", "movilización", "movilizacion"],
        # Descartar movilizaciones POLÍTICAS/ELECTORALES específicas
        # (esas van a PROCESO_ELECTORAL). Negaciones específicas en el
        # actor político para NO over-blockear paros operacionales reales.
        "patrones_negacion": [
            # Marchas contra actores políticos específicos
            "contra keiko", "contra fujimori",
            "contra castillo", "contra boluarte", "contra dina boluarte",
            "contra el congreso", "contra la jnj",
            "contra el gobierno", "contra el presidente",
            "contra el ejecutivo", "contra la presidenta",
            # Marchas explícitamente electorales/políticas
            "marcha electoral", "movilización electoral", "movilizacion electoral",
            "marcha política", "marcha politica",
            "movilización política", "movilizacion politica",
            "marcha por la democracia", "marcha ciudadana",
            "plantón frente al congreso", "planton frente al congreso",
            "plantón frente al palacio", "planton frente al palacio",
            "plantón frente al jne", "planton frente al jne",
            "plantón frente al tc", "planton frente al tc",
            # Indicadores electorales explícitos
            "segunda vuelta", "balotaje",
            "campaña electoral", "campana electoral",
            "candidato presidencial",
        ],
        "categoria": "Conflictos sociales",
        "accion": "Monitoreo cada 4h. Activar contacto con autoridades regionales.",
    },
    {
        "id": "RIESGO_PAIS",
        "nivel": "ALTA",
        "patrones": ["riesgo país", "riesgo pais", "embig", "sol se deprecia", "fuga de capitales", "calificadora", "fitch", "moody", "s&p", "soberano"],
        "categoria": "Económico",
        "accion": "Alertar a clientes con exposición FX. Recomendar coberturas.",
    },
    {
        "id": "BCR_ALERTA",
        "nivel": "ALTA",
        "patrones": ["bcr advierte", "bcr alerta", "velarde", "tasa de referencia", "incertidumbre política", "incertidumbre politica"],
        "categoria": "Económico",
        "accion": "Análisis de impacto sobre portafolio. Recomendar reposicionamiento.",
    },
    {
        "id": "INVESTIGACION_FORMAL",
        "nivel": "ALTA",
        "patrones": ["formaliza denuncia", "allanamiento", "imputado", "fiscalía dispone", "fiscalia dispone", "denuncia constitucional", "investigación preparatoria"],
        "categoria": "Corrupción",
        "accion": "Verificar exposición reputacional de stakeholders.",
    },
    {
        "id": "ATAQUE_VIOLENCIA",
        "nivel": "CRÍTICA",
        "patrones": ["ataque a comisaría", "ataque a comisaria", "explosivos", "policías heridos", "policias heridos", "atentado", "asesinato", "homicidio", "extorsión", "extorsion"],
        "categoria": "Seguridad",
        "accion": "Activar protocolo de seguridad para personal en zona.",
    },
    {
        "id": "REFORMA_INSTITUCIONAL",
        "nivel": "ALTA",
        "patrones": ["bicameralidad", "reforma electoral", "valla electoral", "consulta popular", "modifica constitución", "modifica constitucion", "reforma constitucional"],
        "categoria": "Riesgo regulatorio",
        "accion": "Briefing legal-político sobre alcance de la reforma.",
    },
    {
        "id": "PROCESO_ELECTORAL",
        "nivel": "ALTA",
        "patrones": ["actas observadas", "actas cuestionadas", "votos observados",
                     "segunda vuelta", "jee", "onpe definirá", "onpe definira",
                     "conteo electoral", "conteo de votos", "reconteo de votos",
                     "reconteo de actas", "reconteo manual", "votos en disputa",
                     "impugnación de actas", "impugnacion de actas",
                     "irregularidades en el conteo", "padrón cuestionado",
                     "fraude electoral"],
        "categoria": "Riesgo regulatorio",
        "accion": "Monitoreo intenso del cierre electoral. Reportes cada 6h hasta resolución JEE.",
    },
    {
        "id": "CONFLICTIVIDAD_ELECTORAL",
        "nivel": "ALTA",
        "patrones": ["desconocer las elecciones", "desconocer los resultados",
                     "desconocer el resultado", "no reconocer los resultados",
                     "no reconocer el resultado", "amenaza de movilización electoral",
                     "movilización postelectoral", "movilizacion postelectoral",
                     "tensión postelectoral", "tension postelectoral",
                     "conflictividad electoral", "amenaza con movilizarse",
                     "llamado a las calles", "llaman a desconocer"],
        "categoria": "Estabilidad gubernamental",
        "accion": "Monitoreo de escalada postelectoral. Mapear convocatorias de movilización y actores.",
    },
    {
        "id": "AUDIOS_FILTRADOS",
        "nivel": "ALTA",
        "patrones": ["audios revelan", "audios filtrados", "audios donde", "panel revela", "filtración", "filtracion", "reuniones secretas"],
        "categoria": "Corrupción",
        "accion": "Mapear menciones y evaluar exposición.",
    },
    {
        "id": "INSEGURIDAD_LIMA",
        "nivel": "ALTA",
        "patrones": ["sjl", "smp", "san juan de lurigancho", "estado de emergencia", "crimen organizado", "marchas vecinales"],
        "categoria": "Seguridad",
        "accion": "Coordinar con seguridad corporativa. Mapear oficinas en zonas afectadas.",
    },
    {
        "id": "MAGNICIDIO",
        "nivel": "CRÍTICA",
        "patrones": ["magnicidio", "atentado contra", "atentado al candidato",
                     "asesinato candidato", "asesinato de candidato", "asesinaron al",
                     "intento de asesinato", "tentativa de homicidio candidato",
                     "balacera mitin", "ataque a mitin", "atentado a mitin",
                     "amenaza de muerte candidato", "ataque al líder", "ataque al lider"],
        "categoria": "Violencia electoral",
        "accion": "ALERTA NACIONAL. Activar protocolo de emergencia. Briefing inmediato a stakeholders. Verificar exposición de personal protegido.",
    },
    {
        "id": "ELECCIONES_CUESTIONADAS",
        "nivel": "CRÍTICA",
        "patrones": ["elecciones nulas", "elecciones inválidas", "elecciones invalidas",
                     "elecciones cuestionadas", "elecciones anuladas", "anulación electoral",
                     "anulacion electoral", "fraude electoral", "votos cuestionados",
                     "impugnación electoral", "impugnacion electoral",
                     "actas falsificadas", "actas adulteradas",
                     "padrón cuestionado", "padron cuestionado",
                     "no reconocer resultados", "rechaza resultados"],
        "categoria": "Estabilidad gubernamental",
        "accion": "Activar protocolo de crisis institucional. Briefing legal-político inmediato. Monitoreo intensivo cada 1h hasta resolución.",
    },
    {
        "id": "CORRUPCION_SISTEMICA",
        "nivel": "CRÍTICA",
        "patrones": ["lava jato", "odebrecht", "729 delitos", "67 congresistas", "lavado de activos",
                     "organización criminal", "organizacion criminal", "denuncia constitucional",
                     "captura del estado", "ministerio público investigado", "ministerio publico investigado"],
        "categoria": "Corrupción",
        "accion": "Briefing legal sobre exposición. Activar comité de crisis reputacional. Mapear stakeholders mencionados.",
    },
    {
        "id": "INTERVENCION_FFAA",
        "nivel": "CRÍTICA",
        "patrones": ["fuerzas armadas en las calles", "militares en las calles", "militares combatan",
                     "decreto supremo militarización", "decreto supremo militarizacion",
                     "comando conjunto despliega", "operación militar conjunta", "operacion militar conjunta",
                     "estado de emergencia ejecutivo", "ffaa intervienen"],
        "categoria": "Militar / Seguridad",
        "accion": "ALERTA INSTITUCIONAL. Evaluar regresión democrática. Briefing geopolítico inmediato.",
    },
    {
        "id": "TENSION_FRONTERIZA_CHILE",
        "nivel": "CRÍTICA",
        "patrones": ["frontera con chile", "muro fronterizo", "escudo fronterizo", "zanja fronteriza",
                     "tacna emergencia", "tacna estado de emergencia", "kast frontera",
                     "agentes en la frontera", "100 agentes frontera", "militares peruanos frontera"],
        "categoria": "Seguridad nacional",
        "accion": "Notificar operaciones logísticas Tacna-Arica. Riesgo de escalamiento bilateral.",
    },
    {
        "id": "TENSION_FRONTERIZA_OTRA",
        "nivel": "ALTA",
        "patrones": ["frontera con ecuador", "frontera con bolivia", "frontera con brasil",
                     "tumbes incidente", "puno bolivia", "incidente fronterizo", "patrullaje binacional"],
        "categoria": "Seguridad nacional",
        "accion": "Monitoreo regional. Verificar canales diplomáticos.",
    },
    {
        "id": "CRISIS_MIGRATORIA",
        "nivel": "ALTA",
        "patrones": ["expulsión migrantes", "expulsion migrantes", "tren de aragua", "ingreso irregular",
                     "deportación masiva", "deportacion masiva", "venezolanos expulsados",
                     "regularización migratoria", "regularizacion migratoria"],
        "categoria": "Social / Seguridad",
        "accion": "Análisis de impacto laboral y social. Coordinar con áreas de RRHH y compliance.",
    },
    {
        "id": "RUPTURA_DIPLOMATICA",
        "nivel": "CRÍTICA",
        "patrones": ["ruptura diplomática", "ruptura diplomatica", "persona non grata",
                     "expulsión embajador", "expulsion embajador", "retira embajador", "rompe relaciones",
                     "congelar relaciones", "asilo betssy", "sheinbaum"],
        "categoria": "Diplomacia / Geopolítica",
        "accion": "Briefing geopolítico crítico. Evaluar exposición comercial bilateral. Activar protocolo país.",
    },
    {
        "id": "TENSION_DIPLOMATICA",
        "nivel": "ALTA",
        "patrones": ["diálogo discreto", "dialogo discreto", "reconciliación bilateral", "reconciliacion bilateral",
                     "tensión bilateral", "tension bilateral", "embajador en consultas",
                     "cancillería convoca", "cancilleria convoca", "embajada resguardada"],
        "categoria": "Diplomacia / Geopolítica",
        "accion": "Monitoreo del canal diplomático. Reportar al equipo de relaciones internacionales.",
    },
]


def _texto(a) -> str:
    return ((a.title or "") + " " + (a.summary or "")).lower()


def detectar_alertas(articulos: list, conflictos: list, ventana_horas: int = 72) -> list[dict]:
    """Recorre artículos+conflictos y emite alertas si aplican reglas, dentro de la ventana.

    ventana_horas controla la antigüedad máxima permitida. Por defecto 72h
    para capturar todo el ciclo informativo reciente.

    BLINDAJES contra falsos positivos:
      1. Filtro temporal estricto: items con hours_ago > ventana_horas se descartan
      2. Items marcados is_demo=True NO disparan alertas CRÍTICAS automáticamente
         (solo MEDIA o ALTA si la regla así lo dispone). Esto evita que tweets
         demo del sample_data permanezcan como críticos permanentemente.
      3. Verificación de timestamp válido (no None, no infinito)
    """
    out: list[dict] = []
    todos = list(articulos) + list(conflictos)
    for a in todos:
        hours = a.hours_ago()
        if hours == float("inf") or hours > ventana_horas or hours < 0:
            continue
        # Verificar si es item demo (no debe disparar alertas críticas eternas)
        is_demo = False
        try:
            raw = getattr(a, "raw", {}) or {}
            is_demo = bool(raw.get("is_demo", False))
        except Exception:
            is_demo = False

        text = _texto(a)
        for r in REGLAS:
            if any(p in text for p in r["patrones"]):
                # Anti-falsos positivos: si la regla tiene patrones_negacion
                # y alguno matchea el texto, descartar este match.
                negaciones = r.get("patrones_negacion", [])
                if negaciones and any(neg in text for neg in negaciones):
                    continue
                nivel = r["nivel"]
                # Items demo: rebajar nivel CRÍTICA → ALTA, ALTA → MEDIA
                if is_demo:
                    if nivel == "CRÍTICA":
                        nivel = "ALTA"
                    elif nivel == "ALTA":
                        nivel = "MEDIA"
                else:
                    # bonificación de severidad si la criticidad del item REAL es alta
                    if a.criticidad == "alta" and nivel == "ALTA":
                        nivel = "CRÍTICA"
                out.append({
                    "alert_id": f"{r['id']}_{abs(hash(a.title)) % 10000}",
                    "regla": r["id"],
                    "nivel": nivel,
                    "categoria": r["categoria"],
                    "titulo": a.title,
                    "resumen": a.summary,
                    "fuente": a.source_name,
                    "url": a.url,
                    "region": a.region,
                    "hours_ago": round(hours, 1),
                    "timestamp": a.published,
                    "accion": r["accion"],
                    "ventana_24h": hours <= 24,
                    "is_demo": is_demo,
                })
                break  # una sola regla por ítem, la primera que matchee
    # ordenar: críticas primero, luego por antigüedad ascendente
    nivel_rank = {"CRÍTICA": 0, "ALTA": 1, "MEDIA": 2}
    out.sort(key=lambda x: (nivel_rank.get(x["nivel"], 9), x["hours_ago"]))
    return out
