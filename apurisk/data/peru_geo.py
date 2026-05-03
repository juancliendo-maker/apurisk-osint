"""Coordenadas (lat, lng) de los 24 departamentos + provincias clave del Perú.

Centroide aproximado para visualización en mapa.
"""

PERU_DEPARTAMENTOS = {
    "Amazonas":      (-5.0750, -78.0750),
    "Áncash":        (-9.5290, -77.5290),
    "Ancash":        (-9.5290, -77.5290),
    "Apurímac":      (-13.6390, -72.8810),
    "Apurimac":      (-13.6390, -72.8810),
    "Arequipa":      (-15.8400, -73.0000),
    "Ayacucho":      (-13.1590, -74.2230),
    "Cajamarca":     (-7.1620, -78.5120),
    "Callao":        (-12.0500, -77.1300),
    "Cusco":         (-13.5320, -71.9670),
    "Huancavelica":  (-12.7860, -74.9750),
    "Huánuco":       (-9.9300, -76.2400),
    "Huanuco":       (-9.9300, -76.2400),
    "Ica":           (-14.0680, -75.7290),
    "Junín":         (-12.0660, -75.2050),
    "Junin":         (-12.0660, -75.2050),
    "La Libertad":   (-8.1100, -78.0100),
    "Lambayeque":    (-6.7700, -79.8400),
    "Lima":          (-12.0464, -77.0428),
    "Loreto":        (-3.7490, -73.2530),
    "Madre de Dios": (-12.5933, -69.1840),
    "Moquegua":      (-17.1950, -70.9350),
    "Pasco":         (-10.6800, -76.2560),
    "Piura":         (-5.1945, -80.6328),
    "Puno":          (-15.8400, -70.0220),
    "San Martín":    (-6.5039, -76.3735),
    "San Martin":    (-6.5039, -76.3735),
    "Tacna":         (-18.0146, -70.2536),
    "Tumbes":        (-3.5660, -80.4520),
    "Ucayali":       (-9.9300, -72.7000),
}

# Provincias / ciudades importantes con coordenadas más precisas
PERU_LUGARES_CLAVE = {
    "Las Bambas":          (-14.0900, -72.3500),  # Cotabambas, Apurímac
    "Cotabambas":          (-13.7500, -72.3300),
    "Espinar":             (-14.7900, -71.4100),
    "Tía María":           (-17.0260, -71.7480),  # Islay, Arequipa
    "Tia Maria":           (-17.0260, -71.7480),
    "Conga":               (-7.0000, -78.4000),
    "Antamina":            (-9.5500, -77.0500),
    "Coroccohuayco":       (-14.7500, -71.3800),
    "Valle de Tambo":      (-17.0260, -71.7480),
    "SJL":                 (-12.0090, -76.9820),  # San Juan de Lurigancho
    "San Juan de Lurigancho": (-12.0090, -76.9820),
    "SMP":                 (-12.0000, -77.0700),  # San Martín de Porres
    "Sicaya":              (-12.0660, -75.2680),
    "Concepción":          (-11.9210, -75.3140),
    "Marañón":              (-5.5000, -75.5000),  # cuenca aproximada
    "Cotabambas - Las Bambas": (-14.0900, -72.3500),
    # Huancavelica
    "Tayacaja":               (-12.2500, -74.7000),
    "Colcabamba":             (-12.4280, -74.6850),
    "VRAEM":                  (-12.5000, -73.7000),
    "Huanta":                 (-12.9430, -74.2470),
}


def buscar_coords(texto: str) -> tuple[float, float] | None:
    """Devuelve (lat, lng) si encuentra alguna referencia geográfica conocida.

    Usa word boundaries para evitar falsos positivos (ej. 'panamericana' → 'Ica').
    """
    if not texto:
        return None
    import re
    txt = texto.lower()
    # primero lugares específicos (más precisos, multipalabra)
    for lugar, coords in PERU_LUGARES_CLAVE.items():
        if re.search(r"\b" + re.escape(lugar.lower()) + r"\b", txt):
            return coords
    # luego departamentos con word boundary
    for dep, coords in PERU_DEPARTAMENTOS.items():
        if re.search(r"\b" + re.escape(dep.lower()) + r"\b", txt):
            return coords
    return None
