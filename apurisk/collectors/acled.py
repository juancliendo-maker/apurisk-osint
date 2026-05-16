"""Recolector ACLED — eventos georreferenciados de conflicto político.

ACLED (Armed Conflict Location & Event Data) es la referencia mundial para
datos georreferenciados de eventos políticos violentos y no violentos.
Cada evento incluye latitud/longitud exactas, fecha, actores, tipo,
fatalities y fuentes verificadas.

Cobertura para Perú:
  - Protestas y manifestaciones (CGTP, comunidades, regionales)
  - Disturbios y violencia política
  - Bloqueos de carreteras (corredor minero, Panamericana)
  - Enfrentamientos con fuerzas del orden (PNP, FFAA)
  - Violencia electoral
  - Actividad de grupos armados (Sendero Luminoso, narcos VRAEM)
  - Conflictos socioambientales con componente violento

API: https://apidocs.acleddata.com/
Registro: https://developer.acleddata.com/

Free tier: uso académico/no comercial sin costo. Aprobación 1-2 días.
Pro tier: uso comercial, contactar a ACLED para licencia.

Variables de entorno (REQUERIDAS para activar):
  ACLED_API_KEY - API key generada al registrarse
  ACLED_EMAIL   - email registrado en ACLED

Si no están configuradas, el collector usa datos demo georreferenciados.
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from .base import BaseCollector, Article


# Mapeo de event_type ACLED → categoría descriptiva interna
EVENT_TYPE_DESC = {
    "Protests": "Protesta",
    "Riots": "Disturbio",
    "Violence against civilians": "Violencia contra civiles",
    "Battles": "Enfrentamiento armado",
    "Explosions/Remote violence": "Explosión/Violencia remota",
    "Strategic developments": "Desarrollo estratégico",
}

# Mapeo de event_type → criticidad estimada
EVENT_TYPE_CRITICIDAD = {
    "Protests": "media",
    "Riots": "alta",
    "Violence against civilians": "alta",
    "Battles": "alta",
    "Explosions/Remote violence": "alta",
    "Strategic developments": "media",
}

# Mapeo del campo admin1 (departamento) ACLED → nombre estándar con tildes
DEPARTAMENTOS_ACLED = {
    "Amazonas": "Amazonas",
    "Ancash": "Áncash",
    "Áncash": "Áncash",
    "Apurimac": "Apurímac",
    "Apurímac": "Apurímac",
    "Arequipa": "Arequipa",
    "Ayacucho": "Ayacucho",
    "Cajamarca": "Cajamarca",
    "Callao": "Callao",
    "Cusco": "Cusco",
    "Huancavelica": "Huancavelica",
    "Huanuco": "Huánuco",
    "Huánuco": "Huánuco",
    "Ica": "Ica",
    "Junin": "Junín",
    "Junín": "Junín",
    "La Libertad": "La Libertad",
    "Lambayeque": "Lambayeque",
    "Lima": "Lima",
    "Loreto": "Loreto",
    "Madre de Dios": "Madre de Dios",
    "Moquegua": "Moquegua",
    "Pasco": "Pasco",
    "Piura": "Piura",
    "Puno": "Puno",
    "San Martin": "San Martín",
    "San Martín": "San Martín",
    "Tacna": "Tacna",
    "Tumbes": "Tumbes",
    "Ucayali": "Ucayali",
}


class ACLEDCollector(BaseCollector):
    source_id = "acled_peru"
    source_name = "ACLED · Eventos georreferenciados"
    category = "estado"

    def __init__(self, config: dict, demo: bool = True):
        super().__init__(config, demo=demo)
        # Credenciales (env vars tienen prioridad sobre config.yaml)
        self.api_key = (
            os.getenv("ACLED_API_KEY")
            or config.get("acled_api_key")
        )
        self.email = (
            os.getenv("ACLED_EMAIL")
            or config.get("acled_email")
        )
        self.country = config.get("acled_country", "Peru")
        self.ventana_dias = int(config.get("acled_ventana_dias", 14))
        self.limit = int(config.get("acled_limit", 500))
        # Si está vacío o None, ACLED devuelve todos los event_types
        self.event_types = config.get("acled_event_types", None)

    def collect(self) -> list[Article]:
        if self.demo:
            return self._demo_articles()
        if not (self.api_key and self.email):
            print("  [info] acled: ACLED_API_KEY/ACLED_EMAIL no configurados → fallback demo")
            return self._demo_articles()
        return self._fetch_real()

    def _fetch_real(self) -> list[Article]:
        try:
            import requests
        except ImportError:
            print("  [warn] acled: requests no instalado")
            return self._demo_articles()

        url = "https://api.acleddata.com/acled/read"
        hoy = datetime.now(timezone.utc).date()
        desde = hoy - timedelta(days=self.ventana_dias)
        date_filter = f"{desde.isoformat()}|{hoy.isoformat()}"

        params = {
            "key": self.api_key,
            "email": self.email,
            "country": self.country,
            "event_date": date_filter,
            "event_date_where": "BETWEEN",
            "limit": self.limit,
            "format": "json",
        }
        if self.event_types:
            if isinstance(self.event_types, list):
                params["event_type"] = ":OR:".join(self.event_types)
            else:
                params["event_type"] = str(self.event_types)

        try:
            r = requests.get(url, params=params, timeout=30,
                              headers={"User-Agent": "APURISK-OSINT/1.0"})
            if r.status_code != 200:
                print(f"  [warn] acled HTTP {r.status_code}: {r.text[:200]}")
                return self._demo_articles()
            data = r.json()
            if not data.get("success"):
                err = data.get("error", "respuesta sin success")
                print(f"  [warn] acled: {err}")
                return self._demo_articles()
            events = data.get("data", [])
            print(f"  [acled] {len(events)} eventos recibidos (ventana {self.ventana_dias}d)")
            out = [self._evento_a_article(ev) for ev in events]
            # Filtrar eventos sin coordenadas válidas o sin información mínima
            out = [a for a in out if a is not None]
            return out
        except Exception as e:
            print(f"  [warn] acled excepción: {e}")
            return self._demo_articles()

    def _evento_a_article(self, ev: dict) -> Article | None:
        """Convierte un evento ACLED a Article normalizado.

        Devuelve None si el evento no tiene información mínima utilizable.
        """
        event_type = ev.get("event_type", "Strategic developments")
        sub_event = ev.get("sub_event_type", "")
        evt_desc = EVENT_TYPE_DESC.get(event_type, event_type)
        criticidad = EVENT_TYPE_CRITICIDAD.get(event_type, "media")

        # Elevar criticidad si hay fatalities
        try:
            fatalities = int(ev.get("fatalities", "0"))
        except (ValueError, TypeError):
            fatalities = 0
        if fatalities > 0:
            criticidad = "alta"
        if fatalities >= 5:
            # 5+ fatalities = evento extremadamente crítico
            criticidad = "alta"

        admin1 = ev.get("admin1", "")
        region = DEPARTAMENTOS_ACLED.get(admin1, admin1) if admin1 else None
        location = ev.get("location", "") or ""
        admin2 = ev.get("admin2", "")

        # Construir título: tipo + ubicación específica
        ubicacion_partes = [p for p in [location, admin2, region] if p]
        # Dedupe preservando orden, max 2 elementos
        ubicacion_str = ", ".join(list(dict.fromkeys(ubicacion_partes))[:2])
        if not ubicacion_str:
            ubicacion_str = "Perú"
        title = f"{evt_desc} en {ubicacion_str}"
        if sub_event and sub_event != event_type:
            title = f"{evt_desc} · {sub_event} · {ubicacion_str}"
        if fatalities > 0:
            title += f" ({fatalities} fallecidos)"

        # Resumen desde el campo notes de ACLED
        notes = ev.get("notes", "") or ""
        summary = notes[:600]

        # URL: ACLED no tiene URL directa por evento, pero la fuente sí
        source_acled = ev.get("source", "")
        # Si source contiene URLs, intentar extraer la primera
        url = ""
        if source_acled:
            import re
            url_match = re.search(r"https?://\S+", source_acled)
            if url_match:
                url = url_match.group(0).rstrip(".,;)")
        if not url:
            # Dashboard público de ACLED como fallback
            url = "https://acleddata.com/dashboard/#/dashboard"

        # Fecha del evento (ACLED usa YYYY-MM-DD)
        event_date = ev.get("event_date", "")
        published = f"{event_date}T12:00:00-05:00" if event_date else ""

        # Coordenadas
        lat = None
        lng = None
        try:
            lat = float(ev.get("latitude", ""))
            lng = float(ev.get("longitude", ""))
        except (ValueError, TypeError):
            pass

        if not (lat and lng):
            # Sin coordenadas no tiene valor para el mapa; descartamos
            return None

        # Actores
        actor1 = ev.get("actor1", "") or ""
        actor2 = ev.get("actor2", "") or ""

        # Source label compacto
        src_label = source_acled.split(";")[0].strip()[:35] if source_acled else "ACLED"

        return Article(
            source_id=self.source_id,
            source_name=f"ACLED · {src_label}",
            category=self.category,
            title=title,
            summary=summary,
            url=url,
            published=published,
            region=region,
            criticidad=criticidad,
            raw={
                "data_id": ev.get("data_id"),
                "event_type": event_type,
                "sub_event_type": sub_event,
                "actor1": actor1,
                "actor2": actor2,
                "admin1": admin1,
                "admin2": admin2,
                "location": location,
                "latitude": lat,
                "longitude": lng,
                "fatalities": fatalities,
                "source_acled": source_acled,
                "notes_full": notes,
                "from_acled": True,
                "tipo_descripcion": evt_desc,
                "tags": ev.get("tags", ""),
            },
        )

    def _demo_articles(self) -> list[Article]:
        """Eventos demo georreferenciados con coords reales del Perú.

        Útil para validar la pestaña Mapa Geográfico antes de tener
        la API key activa.
        """
        ahora = datetime.now(timezone(timedelta(hours=-5)))

        DEMO = [
            {
                "event_type": "Protests",
                "sub_event_type": "Peaceful protest",
                "title": "Marcha CGTP por Día del Trabajador",
                "summary": "Movilización masiva de la CGTP en Plaza Dos de Mayo de Lima. Demandas: incremento RMV, pensiones, seguridad laboral, formalidad.",
                "region": "Lima",
                "location": "Plaza Dos de Mayo, Lima",
                "lat": -12.0464, "lng": -77.0428,
                "actor1": "CGTP",
                "criticidad": "media",
                "fatalities": 0,
                "horas_antiguedad": 18,
            },
            {
                "event_type": "Protests",
                "sub_event_type": "Protest with intervention",
                "title": "Bloqueo del corredor minero por comunidades campesinas",
                "summary": "Comunidades de Cotabambas, Apurímac, bloquean el corredor minero del sur exigiendo cumplimiento de acuerdos con MMG (Las Bambas). Enfrentamientos con DINOES.",
                "region": "Apurímac",
                "location": "Challhuahuacho, Cotabambas",
                "lat": -13.85, "lng": -72.27,
                "actor1": "Comunidades campesinas de Cotabambas",
                "actor2": "DINOES (PNP)",
                "criticidad": "alta",
                "fatalities": 0,
                "horas_antiguedad": 36,
            },
            {
                "event_type": "Riots",
                "sub_event_type": "Violent demonstration",
                "title": "Disturbios en marcha estudiantil en Cusco",
                "summary": "Estudiantes universitarios se enfrentan con PNP en marcha contra recorte presupuestal de UNSAAC. Daños a mobiliario urbano.",
                "region": "Cusco",
                "location": "Plaza San Francisco, Cusco",
                "lat": -13.5168, "lng": -71.9785,
                "actor1": "Estudiantes UNSAAC",
                "actor2": "PNP Cusco",
                "criticidad": "alta",
                "fatalities": 0,
                "horas_antiguedad": 24,
            },
            {
                "event_type": "Violence against civilians",
                "sub_event_type": "Attack",
                "title": "Ataque a comunero por presunta minería ilegal en Madre de Dios",
                "summary": "Ataque armado a comunero líder anti-minería ilegal en La Pampa, Madre de Dios. Reportes de hostigamiento a defensores ambientales en zona deforestada.",
                "region": "Madre de Dios",
                "location": "La Pampa, Tambopata",
                "lat": -12.7,
                "lng": -69.7,
                "actor1": "Grupos vinculados a minería ilegal",
                "actor2": "Líder comunal",
                "criticidad": "alta",
                "fatalities": 0,
                "horas_antiguedad": 48,
            },
            {
                "event_type": "Battles",
                "sub_event_type": "Armed clash",
                "title": "Enfrentamiento FFAA con remanentes terroristas en VRAEM",
                "summary": "Patrulla del Comando Especial VRAEM reporta intercambio de fuego con presuntos remanentes de Sendero Luminoso ligados al narcotráfico en Pichari, Cusco.",
                "region": "Cusco",
                "location": "Pichari, La Convención",
                "lat": -12.93, "lng": -73.78,
                "actor1": "FFAA del Perú",
                "actor2": "Remanentes SL-narcotráfico VRAEM",
                "criticidad": "alta",
                "fatalities": 1,
                "horas_antiguedad": 12,
            },
            {
                "event_type": "Protests",
                "sub_event_type": "Peaceful protest",
                "title": "Movilización pesquera artesanal en Chimbote",
                "summary": "Pescadores artesanales paralizan actividades en Chimbote por veda de anchoveta y demandan subsidios al sector. Marcha pacífica.",
                "region": "Áncash",
                "location": "Chimbote, Santa",
                "lat": -9.0743, "lng": -78.5938,
                "actor1": "Federación de Pescadores Artesanales",
                "criticidad": "media",
                "fatalities": 0,
                "horas_antiguedad": 30,
            },
            {
                "event_type": "Strategic developments",
                "sub_event_type": "Arrests",
                "title": "Operativo PNP-Fiscalía contra contrabando frontera Tacna-Arica",
                "summary": "Detención de 8 presuntos contrabandistas en Santa Rosa, Tacna. Decomiso de mercadería boliviano-chilena valorada en S/2 millones.",
                "region": "Tacna",
                "location": "Santa Rosa, frontera Chile",
                "lat": -18.18, "lng": -70.32,
                "actor1": "PNP-Fiscalía Tacna",
                "actor2": "Red de contrabandistas",
                "criticidad": "media",
                "fatalities": 0,
                "horas_antiguedad": 60,
            },
            {
                "event_type": "Strategic developments",
                "sub_event_type": "Arrests",
                "title": "Captura de migrantes irregulares en Tumbes",
                "summary": "Migración detiene a 35 ciudadanos venezolanos sin documentación en frontera con Ecuador. Investigación por tráfico de personas.",
                "region": "Tumbes",
                "location": "Aguas Verdes, Zarumilla",
                "lat": -3.49, "lng": -80.25,
                "actor1": "Migraciones Perú",
                "actor2": "Migrantes irregulares",
                "criticidad": "media",
                "fatalities": 0,
                "horas_antiguedad": 24,
            },
        ]

        out = []
        for d in DEMO:
            published_dt = ahora - timedelta(hours=d["horas_antiguedad"])
            out.append(Article(
                source_id=self.source_id,
                source_name="ACLED · demo georreferenciado",
                category=self.category,
                title=d["title"],
                summary=d["summary"],
                url="https://acleddata.com/dashboard/#/dashboard",
                published=published_dt.isoformat(timespec="seconds"),
                region=d["region"],
                criticidad=d["criticidad"],
                raw={
                    "event_type": d["event_type"],
                    "sub_event_type": d["sub_event_type"],
                    "actor1": d.get("actor1", ""),
                    "actor2": d.get("actor2", ""),
                    "location": d["location"],
                    "latitude": d["lat"],
                    "longitude": d["lng"],
                    "fatalities": d["fatalities"],
                    "from_acled": True,
                    "is_demo": True,
                    "tipo_descripcion": EVENT_TYPE_DESC.get(d["event_type"], d["event_type"]),
                },
            ))
        return out
