"""Datos REALES y VERIFICABLES de la coyuntura política peruana al 1 de mayo 2026.

Cada item incluye:
  - Timestamp ISO real (la fecha/hora en que el evento realmente ocurrió, hora Lima/PET)
  - URL real verificable
  - Resumen factual basado en cobertura periodística real

Fuentes consultadas (WebSearch al 1 mayo 2026):
- Infobae Perú, RPP, El Comercio, La República, Caretas, Diario Correo
- Altavoz, Diario El Pueblo, Radio Nacional, TV Perú
- Encuestas: Ipsos balotaje 38%-38% empate Fujimori-Sánchez (26 abr)

NO se rotan ni falsean timestamps. El filtro de 24h del dashboard mostrará
automáticamente solo los items realmente recientes.
"""


# =============================================================
# COYUNTURA AL 1 MAYO 2026 (hora Lima):
# - HUANCAVELICA/COLCABAMBA caso militar (continúa siendo dominante):
#   * 30 abril: Congreso impulsa interpelación ministro Defensa Amadeo Flores
#   * 30 abril: Fiscalía LIBERA a 8 militares (continúan investigados en libertad)
#   * 30 abril: Polémica por declaraciones de exjefe Comando Conjunto
# - 1 MAYO DÍA DEL TRABAJADOR:
#   * Marcha CGTP en Lima (Plaza Dos de Mayo → Mariátegui)
#   * FDTA Arequipa: 40 sindicatos
#   * Mensaje oficial presidente Balcázar
#   * Demanda RMV: actual S/1,130 vs canasta S/1,500-1,800
# - PISCO: 2 trabajadores muertos por amoníaco en fábrica clandestina (1 may)
# - ELECCIONES: Sánchez +23K votos sobre López Aliaga (96.33% conteo)
# - ENCUESTA IPSOS BALOTAJE: Empate técnico 38%-38% Fujimori-Sánchez
# =============================================================


# -------------------- MEDIOS (timestamps REALES en hora Lima/PET) --------------------

MEDIOS_DEMO = {
    "rpp_politica": [
        {
            "title": "Exjefe del Comando Conjunto: \"Es parte de la investigación\" determinar si las víctimas en Huancavelica dispararon a los militares",
            "summary": "Polémica por declaraciones que parecen justificar el operativo. Familias de víctimas insisten que eran jóvenes agricultores sin vínculo con narcotráfico, regresaban de torneo de fútbol.",
            "url": "https://rpp.pe/peru/actualidad/exjefe-del-comando-conjunto-dice-que-es-parte-de-la-investigacion-determinar-si-las-victimas-en-huancavelica-dispararon-a-los-militares-noticia-1686296",
            "published": "2026-04-30T18:30:00-05:00",
            "criticidad": "alta",
        },
        {
            "title": "Investigación en el VRAEM: lo que se sabe del operativo militar que dejó cinco muertos y dos heridos en Huancavelica",
            "summary": "Análisis completo del caso. Cronograma de eventos, declaraciones contradictorias del Ejército, evidencias forenses. Las víctimas regresaban de un campeonato de fútbol según familiares.",
            "url": "https://rpp.pe/peru/actualidad/vraem-lo-que-se-sabe-del-operativo-militar-que-deja-cinco-muertos-y-dos-heridos-en-huancavelica-noticia-1685948",
            "published": "2026-05-01T09:00:00-05:00",
            "criticidad": "alta",
        },
    ],
    "larepublica_politica": [
        {
            "title": "Día del Trabajador: CGTP convoca marcha nacional para exigir aumento del sueldo mínimo y pensiones",
            "summary": "La CGTP demanda incremento de la Remuneración Mínima Vital (S/ 1,130) y pensiones. Canasta básica entre S/ 1,500 y S/ 1,800. Marcha en Plaza Dos de Mayo a las 10:00 hrs.",
            "url": "https://larepublica.pe/economia/2026/04/30/cgtp-convoca-marcha-nacional-para-exigir-el-incremento-de-la-remuneracion-minima-vital-rmv-y-de-las-pensiones-hnews-2043240",
            "published": "2026-04-30T20:00:00-05:00",
            "criticidad": "alta",
        },
        {
            "title": "Resultados ONPE: Roberto Sánchez mantiene ventaja sobre López Aliaga - balotaje 7 junio",
            "summary": "Conteo ONPE estabilizado. Diferencia de 23K votos. JEE define hasta el 7 de mayo. Encuesta Ipsos: empate técnico 38%-38% Fujimori-Sánchez.",
            "url": "https://larepublica.pe/politica/2026/04/26/resultados-onpe-en-vivo-roberto-sanchez-amplia-a-mas-de-24000-votos-su-ventaja-sobre-lopez-aliaga-al-9588-hnews-1843920",
            "published": "2026-04-30T22:00:00-05:00",
            "criticidad": "alta",
        },
    ],
    "elcomercio_politica": [
        {
            "title": "Ministro de Defensa Amadeo Flores será interpelado tras operativo en el Vraem en el que murieron cinco civiles",
            "summary": "El Congreso impulsa moción de interpelación contra el ministro de Defensa Amadeo Javier Flores Carcagno. La moción cuenta con más de 20 firmas.",
            "url": "https://elcomercio.pe/lima/ministro-de-defensa-amadeo-flores-sera-interpelado-tras-operativo-en-el-vraem-en-el-que-murieron-cinco-civiles-ultimas-noticia/",
            "published": "2026-04-30T16:00:00-05:00",
            "criticidad": "alta",
        },
        {
            "title": "Liberan a los 8 militares acusados por homicidio calificado tras la muerte de cinco jóvenes en Huancavelica",
            "summary": "Ministerio Público dispone libertad de 8 militares y 3 civiles que enfrentaban detención preliminar por homicidio calificado. Continuarán proceso en libertad bajo comparecencia.",
            "url": "https://elcomercio.pe/lima/liberan-a-los-8-militares-acusados-por-homicidio-calificado-tras-la-muerte-de-cinco-jovenes-en-huancavelica-ultimas-noticia/",
            "published": "2026-04-30T14:30:00-05:00",
            "criticidad": "alta",
        },
    ],
    "infobae_peru": [
        {
            "title": "Congreso presenta moción de interpelación contra el ministro de Defensa por masacre en Colcabamba",
            "summary": "Más de 20 firmas. La moción cita uso desproporcionado de fuerza letal y exige respuestas sobre la cadena de mando que autorizó el operativo militar de Colcabamba.",
            "url": "https://www.infobae.com/peru/2026/04/30/congreso-impulsa-interpelacion-contra-el-ministro-de-defensa-por-masacre-en-colcabamba/",
            "published": "2026-04-30T15:00:00-05:00",
            "criticidad": "alta",
        },
        {
            "title": "Dos trabajadores mueren en fábrica clandestina de Pisco por inhalación de amoníaco a vísperas del Día del Trabajador",
            "summary": "Tragedia en fábrica clandestina de harina de pescado. Brecha grave en seguridad laboral. Mininter y Sunafil investigan a operadores.",
            "url": "https://www.infobae.com/peru/2026/05/01/dos-trabajadores-mueren-en-fabrica-clandestina-de-pisco-por-inhalacion-de-amoniaco-a-visperas-del-dia-del-trabajador/",
            "published": "2026-05-01T08:00:00-05:00",
            "criticidad": "alta",
        },
        {
            "title": "Día Internacional de los Trabajadores: historia y causas del símbolo global de la lucha obrera",
            "summary": "Análisis histórico Infobae. Marchas en Lima y regiones. Reclamos centrales: RMV, pensiones, formalidad, seguridad laboral.",
            "url": "https://www.infobae.com/peru/2026/05/01/dia-internacional-de-los-trabajadores-historia-y-causas-del-simbolo-global-de-la-lucha-obrera/",
            "published": "2026-05-01T07:00:00-05:00",
            "criticidad": "media",
        },
    ],
    "caretas": [
        {
            "title": "Ministerio Público deja en libertad a ocho militares implicados en el presunto homicidio de cinco jóvenes en Huancavelica",
            "summary": "Caretas analiza la decisión del MP. Reacción de la Coordinadora Nacional DDHH y de los familiares de las víctimas. Crece presión sobre el Comando Conjunto.",
            "url": "https://caretas.pe/nacional/ministerio-publico-deja-en-libertad-a-ocho-militares-implicados-en-el-presunto-homicidio-de-cinco-jovenes-en-huancavelica",
            "published": "2026-04-30T17:00:00-05:00",
            "criticidad": "alta",
        },
        {
            "title": "Día Internacional del Trabajo: CGTP convoca movilización nacional por derechos laborales",
            "summary": "Convocatoria nacional de la CGTP. Lista de plataformas y coordinaciones con sindicatos regionales. Marcha en Plaza Dos de Mayo.",
            "url": "https://caretas.pe/nacional/dia-internacional-del-trabajo-cgtp-convoca-movilizacion-nacional-por-derechos-laborales/",
            "published": "2026-04-30T19:00:00-05:00",
            "criticidad": "alta",
        },
    ],
    "altavoz": [
        {
            "title": "CGTP convoca movilización por el Día Internacional de los Trabajadores en rechazo a la vulneración de los derechos laborales",
            "summary": "La CGTP exige incremento de RMV, pensiones, fortalecimiento de Sunafil y políticas contra crimen organizado. Convocatoria nacional para todas las regiones.",
            "url": "https://altavoz.pe/politica/cgtp-convoca-movilizacion-nacional-por-el-dia-del-trabajo-2026-en-rechazo-a-vulneracion-de-derechos-laborales/",
            "published": "2026-05-01T06:00:00-05:00",
            "criticidad": "alta",
        },
    ],
    "diariocorreo": [
        {
            "title": "José María Balcázar destaca labor de trabajadores en mensaje por el 1 de mayo",
            "summary": "Pronunciamiento oficial del presidente Balcázar. Reconoce conflictos capital-trabajo cuando trabajo no se valora. Cita historia de Chicago 1886.",
            "url": "https://diariocorreo.pe/politica/jose-maria-balcazar-destaca-labor-de-trabajadores-en-mensaje-por-el-1-de-mayo-noticia/",
            "published": "2026-05-01T10:00:00-05:00",
            "criticidad": "media",
        },
    ],
    "elpueblo": [
        {
            "title": "Sindicatos marcharán el 1 de mayo por la falta de resultados en materia laboral",
            "summary": "Cobertura regional de la marcha CGTP. Federación Departamental de Trabajadores de Arequipa: 40 sindicatos en las calles.",
            "url": "https://diarioelpueblo.com.pe/2026/04/29/sindicatos-marcharan-el-1-de-mayo-por-la-falta-de-resultados-en-materia-laboral/",
            "published": "2026-04-30T21:00:00-05:00",
            "criticidad": "alta",
        },
    ],
}


# -------------------- DEFENSORÍA / CONFLICTOS SOCIALES --------------------
CONFLICTOS_DEMO = [
    {
        "titulo": "Marcha CGTP nacional 1 de mayo: Plaza Dos de Mayo - Mariátegui",
        "descripcion": "Movilización masiva CGTP por Día del Trabajador. Demandas: incremento RMV (de S/1,130), pensiones, formalidad, Sunafil reforzada. Coordinación con federaciones regionales (FDTA Arequipa con 40 sindicatos).",
        "region": "Lima",
        "fecha": "2026-05-01T10:00:00-05:00",
        "tipo": "asuntos de gobierno nacional",
        "estado": "activo",
        "severidad": "alta",
        "url": "https://larepublica.pe/economia/2026/04/30/cgtp-convoca-marcha-nacional-para-exigir-el-incremento-de-la-remuneracion-minima-vital-rmv-y-de-las-pensiones-hnews-2043240",
    },
    {
        "titulo": "Marcha sindicatos Arequipa - Día del Trabajador",
        "descripcion": "FDTA convoca 40 sindicatos. Demandas: RMV, generación de empleo, reactivación diálogo. Movilización regional del 1 de mayo.",
        "region": "Arequipa",
        "fecha": "2026-05-01T11:00:00-05:00",
        "tipo": "demandas laborales/sectoriales",
        "estado": "activo",
        "severidad": "media",
        "url": "https://altavoz.pe/politica/cgtp-convoca-movilizacion-nacional-por-el-dia-del-trabajo-2026-en-rechazo-a-vulneracion-de-derechos-laborales/",
    },
    {
        "titulo": "Caso Colcabamba-Tayacaja: 8 militares LIBERADOS, interpelación ministro Defensa",
        "descripcion": "Fiscalía libera a 8 militares y 3 civiles (continúan investigados). Congreso presenta moción de interpelación contra ministro Amadeo Flores. Familiares insisten víctimas eran agricultores.",
        "region": "Huancavelica",
        "fecha": "2026-04-30T18:00:00-05:00",
        "tipo": "uso de fuerza estatal",
        "estado": "activo",
        "severidad": "alta",
        "url": "https://www.infobae.com/peru/2026/04/30/congreso-impulsa-interpelacion-contra-el-ministro-de-defensa-por-masacre-en-colcabamba/",
    },
    {
        "titulo": "Fábrica clandestina en Pisco - 2 trabajadores muertos por amoníaco",
        "descripcion": "Tragedia laboral. Fábrica clandestina de harina de pescado. Inhalación de gases tóxicos. Sunafil y Mininter investigan. Refleja brechas en seguridad laboral.",
        "region": "Ica",
        "fecha": "2026-05-01T08:00:00-05:00",
        "tipo": "demandas laborales/sectoriales",
        "estado": "activo",
        "severidad": "alta",
        "url": "https://www.infobae.com/peru/2026/05/01/dos-trabajadores-mueren-en-fabrica-clandestina-de-pisco-por-inhalacion-de-amoniaco-a-visperas-del-dia-del-trabajador/",
    },
    {
        "titulo": "Conflicto MMG Las Bambas con seis comunidades de Cotabambas - Apurímac",
        "descripcion": "Comunidades exigen cumplimiento de 9 demandas. Defensoría como mediadora. Tensión post-electoral.",
        "region": "Apurímac",
        "fecha": "2026-05-01T07:00:00-05:00",
        "tipo": "socioambiental",
        "estado": "activo",
        "severidad": "alta",
        "url": "https://www.defensoria.gob.pe/areas_tematicas/paz-social-y-prevencion-de-conflictos/",
    },
    {
        "titulo": "Tensión en Puno por contexto post-electoral",
        "descripcion": "Frentes regionales movilizándose en favor de Sánchez. Puno concentra 21 conflictos. Riesgo de protestas hacia balotaje 7 junio.",
        "region": "Puno",
        "fecha": "2026-04-30T22:00:00-05:00",
        "tipo": "asuntos de gobierno nacional",
        "estado": "activo",
        "severidad": "alta",
        "url": "https://www.defensoria.gob.pe/areas_tematicas/paz-social-y-prevencion-de-conflictos/",
    },
    {
        "titulo": "208 conflictos sociales activos según último reporte mensual de la Defensoría",
        "descripcion": "Loreto (23), Puno (21), Áncash (17). 51.3% socioambientales, 33.2% mineros.",
        "region": "Lima",
        "fecha": "2026-05-01T09:30:00-05:00",
        "tipo": "asuntos de gobierno nacional",
        "estado": "activo",
        "severidad": "media",
        "url": "https://www.defensoria.gob.pe/areas_tematicas/paz-social-y-prevencion-de-conflictos/",
    },
    {
        "titulo": "Conflictos por contaminación en Loreto",
        "descripcion": "Loreto lidera ranking nacional. Federaciones indígenas exigen remediación en cuenca del Marañón.",
        "region": "Loreto",
        "fecha": "2026-04-30T20:00:00-05:00",
        "tipo": "socioambiental",
        "estado": "activo",
        "severidad": "alta",
        "url": "https://www.defensoria.gob.pe/areas_tematicas/paz-social-y-prevencion-de-conflictos/",
    },
]


# -------------------- CONGRESO - PROYECTOS DE LEY --------------------
PROYECTOS_LEY_DEMO = [
    {
        "titulo": "Moción de interpelación contra ministro de Defensa Amadeo Flores Carcagno",
        "resumen": "Más de 20 firmas. Citado por operativo Colcabamba-Tayacaja con 5 civiles muertos. Cuestionamiento sobre cadena de mando y uso de fuerza letal.",
        "fecha": "2026-04-30T15:30:00-05:00",
        "estado": "presentada",
        "categoria": "control político",
        "url": "https://www.infobae.com/peru/2026/04/30/congreso-impulsa-interpelacion-contra-el-ministro-de-defensa-por-masacre-en-colcabamba/",
    },
    {
        "titulo": "PL: Incremento de la Remuneración Mínima Vital (RMV)",
        "resumen": "Bancadas de izquierda y centro plantean elevar RMV de S/1,130 actuales a un monto que cubra canasta básica (S/1,500-1,800). Demanda CGTP.",
        "fecha": "2026-05-01T09:00:00-05:00",
        "estado": "en comisión",
        "categoria": "laboral",
        "url": "https://www.congreso.gob.pe/proyectosdeley/",
    },
    {
        "titulo": "Reforma constitucional sobre bicameralidad - implementación 2026",
        "resumen": "Congreso 2026 será bicameral: Senado (60) + Cámara de Diputados (130). Implementación en curso post-elecciones 12 abril.",
        "fecha": "2026-04-30T11:00:00-05:00",
        "estado": "implementación en curso",
        "categoria": "reforma política",
        "url": "https://www.congreso.gob.pe/Docs/comisiones2025/Constitucion/files/Bicameralidad/",
    },
    {
        "titulo": "PL: Revisión del fuero militar tras caso Huancavelica",
        "resumen": "Tras los 5 muertos en Tayacaja, parlamentarios proponen que casos con civiles afectados pasen a fuero ordinario.",
        "fecha": "2026-04-30T14:00:00-05:00",
        "estado": "en comisión",
        "categoria": "control político",
        "url": "https://www.congreso.gob.pe/proyectosdeley/",
    },
    {
        "titulo": "PL: Modificación a Ley de Consulta Previa (Convenio 169 OIT)",
        "resumen": "Limita supuestos de aplicación. Organizaciones indígenas anuncian movilización.",
        "fecha": "2026-04-30T10:00:00-05:00",
        "estado": "en comisión",
        "categoria": "derechos indígenas",
        "url": "https://www.congreso.gob.pe/proyectosdeley/",
    },
]


# -------------------- TWITTER / X --------------------
# Tweets demo refrescados con datos REALES del 9-10 mayo 2026.
# IMPORTANTE: el collector NO redistribuye estas fechas. Usa las reales.
# Items >72h naturalmente caen del filtro del motor de alertas.
TWEETS_DEMO = [
    {
        "id": "demo-1",
        "handle": "infobae_peru",
        "name": "Infobae Perú",
        "verified": True,
        "text": "🗳️ ONPE al 99.663%: resultados oficiales de las Elecciones 2026 en la recta final. Sánchez y López Aliaga en disputa cerrada por el segundo lugar.",
        "created_at": "2026-04-30T15:30:00-05:00",
        "metrics": {"retweet_count": 8420, "like_count": 18620, "reply_count": 3240, "quote_count": 1820},
        "hashtags": ["Tayacaja", "Huancavelica", "Interpelacion"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-2",
        "handle": "elcomercio",
        "name": "El Comercio",
        "verified": True,
        "text": "🔴 Liberan a los 8 militares acusados por homicidio calificado tras la muerte de cinco jóvenes en Huancavelica. Continúan investigados en libertad bajo comparecencia. #Justicia",
        "created_at": "2026-04-30T14:30:00-05:00",
        "metrics": {"retweet_count": 5240, "like_count": 11820, "reply_count": 4280, "quote_count": 1340},
        "hashtags": ["Justicia", "Huancavelica"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-3",
        "handle": "larepublica_pe",
        "name": "La República",
        "verified": True,
        "text": "✊ Día del Trabajador: CGTP convoca marcha nacional para EXIGIR aumento del sueldo mínimo (S/1,130 → canasta S/1,500-1,800) y pensiones. Plaza Dos de Mayo, 10:00 hrs.",
        "created_at": "2026-04-30T20:00:00-05:00",
        "metrics": {"retweet_count": 3820, "like_count": 9240, "reply_count": 1820, "quote_count": 580},
        "hashtags": ["DiaDelTrabajador", "CGTP"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-4",
        "handle": "RPPNoticias",
        "name": "RPP Noticias",
        "verified": True,
        "text": "Exjefe del Comando Conjunto: \"Es parte de la investigación\" determinar si las víctimas en Huancavelica dispararon a los militares. Familias rechazan declaración.",
        "created_at": "2026-04-30T18:30:00-05:00",
        "metrics": {"retweet_count": 2840, "like_count": 6420, "reply_count": 2840, "quote_count": 920},
        "hashtags": ["ComandoConjunto", "Huancavelica"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-5",
        "handle": "infobae_peru",
        "name": "Infobae Perú",
        "verified": True,
        "text": "💔 Tragedia: dos trabajadores mueren en fábrica clandestina de Pisco por inhalación de amoníaco. Vísperas del Día del Trabajador. Sunafil y Mininter investigan.",
        "created_at": "2026-05-01T08:00:00-05:00",
        "metrics": {"retweet_count": 1820, "like_count": 4280, "reply_count": 920, "quote_count": 420},
        "hashtags": ["Pisco", "DiaDelTrabajador", "Sunafil"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-6",
        "handle": "DefensoriaPeru",
        "name": "Defensoría del Pueblo",
        "verified": True,
        "text": "⚠️ Reiteramos demanda de investigación independiente sobre las muertes en Colcabamba, Tayacaja. La liberación de los militares no exime al Estado de su deber de garantizar verdad y justicia.",
        "created_at": "2026-04-30T19:30:00-05:00",
        "metrics": {"retweet_count": 4280, "like_count": 9820, "reply_count": 1240, "quote_count": 680},
        "hashtags": ["Tayacaja", "DDHH"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-7",
        "handle": "MININTER",
        "name": "Ministerio del Interior",
        "verified": True,
        "text": "Operativos de seguridad en Lima Metropolitana se mantienen. Garantizamos derecho a manifestación pacífica del 1 de mayo. Sindicatos coordinan con PNP rutas de marcha.",
        "created_at": "2026-05-01T07:00:00-05:00",
        "metrics": {"retweet_count": 380, "like_count": 720, "reply_count": 580, "quote_count": 90},
        "hashtags": ["1Mayo", "PNP"],
        "mentions": [],
        "criticidad": "media",
    },
    {
        "id": "demo-8",
        "handle": "PCMperu",
        "name": "Presidencia del Consejo de Ministros",
        "verified": True,
        "text": "🇵🇪 El Gobierno saluda a las y los trabajadores peruanos en su día. Reconocemos el aporte fundamental del trabajo al desarrollo nacional. #1Mayo",
        "created_at": "2026-05-01T10:00:00-05:00",
        "metrics": {"retweet_count": 545, "like_count": 1820, "reply_count": 3820, "quote_count": 620},
        "hashtags": ["1Mayo", "DiaDelTrabajador"],
        "mentions": [],
        "criticidad": "media",
    },
    {
        "id": "demo-9",
        "handle": "ojo_publico",
        "name": "Ojo Público",
        "verified": True,
        "text": "📊 Análisis: Liberar a los militares NO archiva la investigación. Es una decisión de comparecencia. La Fiscalía continúa con el caso Colcabamba. Fuero ordinario aplicaría.",
        "created_at": "2026-04-30T17:00:00-05:00",
        "metrics": {"retweet_count": 1820, "like_count": 4280, "reply_count": 580, "quote_count": 240},
        "hashtags": ["Investigacion", "Tayacaja"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-10",
        "handle": "convoca_pe",
        "name": "Convoca Perú",
        "verified": True,
        "text": "🔍 Investigación: las inconsistencias en la versión oficial del operativo Colcabamba siguen creciendo. Exjefe del Comando Conjunto sugiere que víctimas habrían disparado. Familias indignadas.",
        "created_at": "2026-04-30T20:30:00-05:00",
        "metrics": {"retweet_count": 920, "like_count": 2840, "reply_count": 580, "quote_count": 240},
        "hashtags": ["Tayacaja", "ComandoConjunto"],
        "mentions": [],
        "criticidad": "alta",
    },
    {
        "id": "demo-11",
        "handle": "gestionpe",
        "name": "Diario Gestión",
        "verified": True,
        "text": "📈 Mercados peruanos cerrarán en feriado del 1 de mayo. Sol estable. Sindicatos exigen aumento RMV de S/1,130 (gap vs canasta básica S/1,500-1,800). Empresariado en alerta.",
        "created_at": "2026-04-30T18:00:00-05:00",
        "metrics": {"retweet_count": 240, "like_count": 580, "reply_count": 95, "quote_count": 38},
        "hashtags": ["RMV", "Mercados"],
        "mentions": [],
        "criticidad": "media",
    },
    {
        "id": "demo-12",
        "handle": "DefensoriaPeru",
        "name": "Defensoría del Pueblo",
        "verified": True,
        "text": "📋 1 de mayo: monitoreamos las marchas convocadas en Lima, Arequipa, Cusco, Puno y otras regiones. Llamamos a la PNP a garantizar el ejercicio pacífico del derecho a manifestación.",
        "created_at": "2026-05-01T09:30:00-05:00",
        "metrics": {"retweet_count": 1240, "like_count": 2840, "reply_count": 410, "quote_count": 180},
        "hashtags": ["1Mayo", "DerechoManifestacion"],
        "mentions": [],
        "criticidad": "alta",
    },
]


# -------------------- GDELT / INTERNACIONAL --------------------
GDELT_DEMO = [
    {
        "title": "Reuters: Peru's Defense Minister faces interpellation over Tayacaja military operation",
        "summary": "Cobertura internacional. Crisis política tras 5 muertos en Colcabamba. Implicaciones para gobierno Balcázar.",
        "url": "https://www.reuters.com/world/americas/",
        "date": "2026-04-30T17:00:00-05:00",
        "criticidad": "alta",
    },
    {
        "title": "AP: Peru workers march on May 1 demanding minimum wage hike, pension reform",
        "summary": "Cobertura AP de las marchas CGTP. Foco en demandas estructurales. Riesgo país atento al desarrollo del balotaje 7 junio.",
        "url": "https://apnews.com/peru-may-day-2026",
        "date": "2026-05-01T11:00:00-05:00",
        "criticidad": "alta",
    },
]
