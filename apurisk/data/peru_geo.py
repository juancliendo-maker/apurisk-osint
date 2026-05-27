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

# Provincias / ciudades importantes con coordenadas más precisas.
# IMPORTANTE: cuando una alerta de prensa menciona solo la ciudad
# (ej. "Paro en Chiclayo") sin la región, buscar_coords primero intenta
# matchear acá. Por eso es crítico tener todas las capitales departamentales
# y ciudades relevantes en cobertura mediática nacional.
PERU_LUGARES_CLAVE = {
    # ── ZONAS MINERAS ──
    "Las Bambas":          (-14.0900, -72.3500),
    "Cotabambas":          (-13.7500, -72.3300),
    "Espinar":             (-14.7900, -71.4100),
    "Tía María":           (-17.0260, -71.7480),
    "Tia Maria":           (-17.0260, -71.7480),
    "Conga":               (-7.0000, -78.4000),
    "Antamina":            (-9.5500, -77.0500),
    "Coroccohuayco":       (-14.7500, -71.3800),
    "Valle de Tambo":      (-17.0260, -71.7480),
    "Yanacocha":           (-7.0000, -78.5000),
    "Cerro Verde":         (-16.5300, -71.6000),
    "Toquepala":           (-17.2330, -70.6000),
    "Cuajone":             (-17.0500, -70.7000),

    # ── LIMA (distritos y zonas) ──
    "SJL":                 (-12.0090, -76.9820),
    "San Juan de Lurigancho": (-12.0090, -76.9820),
    "SMP":                 (-12.0000, -77.0700),
    "San Martín de Porres": (-12.0000, -77.0700),
    "San Martin de Porres": (-12.0000, -77.0700),
    "Villa El Salvador":   (-12.2130, -76.9380),
    "Villa María del Triunfo": (-12.1620, -76.9320),
    "Ate":                 (-12.0280, -76.9230),
    "Ate-Vitarte":         (-12.0280, -76.9230),
    "Comas":               (-11.9300, -77.0600),
    "Carabayllo":          (-11.8300, -77.0400),
    "Puente Piedra":       (-11.8650, -77.0680),
    "Surco":               (-12.1480, -76.9920),
    "Miraflores":          (-12.1190, -77.0290),
    "San Isidro":          (-12.0970, -77.0360),
    "Barranco":            (-12.1450, -77.0220),
    "Mi Perú":             (-11.8540, -77.1300),
    "Ventanilla":          (-11.8740, -77.1490),

    # ── CAPITALES DEPARTAMENTALES (faltantes en la lista de departamentos) ──
    "Trujillo":            (-8.1100, -79.0290),
    "Chiclayo":            (-6.7700, -79.8400),
    "Huancayo":            (-12.0660, -75.2050),
    "Tarapoto":            (-6.4870, -76.3640),
    "Moyobamba":            (-6.0340, -76.9700),
    "Pucallpa":            (-8.3790, -74.5535),
    "Iquitos":             (-3.7437, -73.2516),
    "Juliaca":             (-15.5000, -70.1330),
    "Chimbote":            (-9.0800, -78.5780),
    "Huaraz":              (-9.5290, -77.5290),
    "Abancay":             (-13.6390, -72.8810),
    "Cerro de Pasco":      (-10.6800, -76.2560),
    "Puerto Maldonado":    (-12.5933, -69.1840),
    "Pisco":               (-13.7100, -76.2050),
    "Nazca":               (-14.8290, -74.9270),
    "Chincha":             (-13.4040, -76.1330),
    "Mollendo":            (-17.0290, -72.0140),
    "Camaná":              (-16.6280, -72.7150),
    "Camana":              (-16.6280, -72.7150),
    "Tingo María":         (-9.2960, -75.9970),
    "Tingo Maria":         (-9.2960, -75.9970),

    # ── PIURA (zona del paro agrario) ──
    "Sullana":             (-4.9020, -80.6850),
    "Sechura":             (-5.5550, -80.8190),
    "Tambo Grande":        (-4.9265, -80.3346),
    "Tambogrande":         (-4.9265, -80.3346),
    "Cura Mori":           (-5.3250, -80.6200),
    "Catacaos":            (-5.2700, -80.6800),
    "La Unión":            (-5.3900, -80.7400),
    "La Union":            (-5.3900, -80.7400),
    "Las Lomas":           (-4.6500, -80.2700),
    "Marcavelica":         (-4.8520, -80.7430),
    "Ignacio Escudero":    (-4.8470, -80.8650),
    "El Tallán":           (-5.4420, -80.6090),
    "El Tallan":           (-5.4420, -80.6090),
    "Cristo Nos Valga":    (-5.5040, -80.5960),
    "Talara":              (-4.5770, -81.2710),
    "Paita":               (-5.0860, -81.1140),

    # ── LAMBAYEQUE ──
    "Lambayeque ciudad":   (-6.7060, -79.9020),
    "Ferreñafe":           (-6.6360, -79.7900),
    "Ferrenafe":           (-6.6360, -79.7900),
    "Monsefú":             (-6.8700, -79.8700),
    "Monsefu":             (-6.8700, -79.8700),

    # ── LA LIBERTAD ──
    "Chepén":              (-7.2200, -79.4280),
    "Chepen":              (-7.2200, -79.4280),
    "Pacasmayo":           (-7.4040, -79.5710),
    "Otuzco":              (-7.9020, -78.5760),
    "Huamachuco":          (-7.8170, -78.0440),

    # ── SAN MARTÍN (zona del paro selva) ──
    "Rioja":               (-6.0570, -77.1670),
    "Nueva Cajamarca":     (-5.9420, -77.3070),
    "Tocache":             (-8.1840, -76.5180),
    "Uchiza":              (-8.4530, -76.4660),
    "Juanjuí":              (-7.1750, -76.7280),
    "Juanjui":             (-7.1750, -76.7280),

    # ── PUNO (zona del paro y conflictos ambientales) ──
    "Ayaviri":             (-14.8750, -70.5870),
    "Melgar":              (-14.8750, -70.5870),
    "Llallimayo":          (-15.0500, -70.7000),
    "Azángaro":            (-14.9090, -70.1900),
    "Azangaro":            (-14.9090, -70.1900),

    # ── ÁNCASH ──
    "Casma":               (-9.4730, -78.3030),
    "Huarmey":             (-10.0700, -78.1530),

    # ── JUNÍN ──
    "Tarma":               (-11.4180, -75.6900),
    "La Merced":           (-11.0530, -75.3300),
    "Satipo":              (-11.2530, -74.6360),
    "Chanchamayo":         (-11.0530, -75.3300),
    "Pichanaki":           (-10.9180, -74.8650),

    # ── APURÍMAC ──
    "Andahuaylas":         (-13.6580, -73.3870),
    "Curahuasi":           (-13.5500, -72.7300),
    "Limatambo":           (-13.4570, -72.4250),

    # ── CAJAMARCA ──
    "Jaén":                (-5.7080, -78.8080),
    "Jaen":                (-5.7080, -78.8080),
    "Cutervo":             (-6.3760, -78.8160),
    "Bambamarca":          (-6.6800, -78.5230),

    # ── HUÁNUCO ──
    "Huariaca":            (-10.4440, -76.1870),
    "Leoncio Prado":       (-9.2960, -75.9970),
    "Aucayacu":            (-8.9150, -76.0940),
    "Yanacancha":          (-10.6220, -76.1760),

    # ── AMAZONAS ──
    "Bagua":               (-5.6400, -78.5350),
    "Chachapoyas":         (-6.2310, -77.8690),
    "Condorcanqui":        (-4.7660, -78.1370),
    "Nieva":               (-4.7660, -78.1370),
    "Luya":                (-6.1030, -77.8920),
    "Lamud":               (-6.1030, -77.8920),

    # ── HUANCAVELICA ──
    "Tayacaja":            (-12.2500, -74.7000),
    "Colcabamba":          (-12.4280, -74.6850),
    "Huanta":              (-12.9430, -74.2470),

    # ── AYACUCHO ──
    "Huamanga":            (-13.1590, -74.2230),

    # ── ZONAS ESTRATÉGICAS / CUENCAS ──
    "VRAEM":               (-12.5000, -73.7000),
    "Marañón":             (-5.5000, -75.5000),
    "Maranon":             (-5.5000, -75.5000),
    "Cotabambas - Las Bambas": (-14.0900, -72.3500),
    "Cordillera del Cóndor": (-3.5000, -78.5000),
    "Cordillera del Condor": (-3.5000, -78.5000),

    # ── CALLAO (puerto) ──
    "Callao puerto":       (-12.0500, -77.1300),
    "Aeropuerto Jorge Chávez": (-12.0220, -77.1140),
    "Aeropuerto Jorge Chavez": (-12.0220, -77.1140),

    # ── HISTÓRICOS PROVINCIALES ÚTILES (cobertura mediática) ──
    "Sicaya":              (-12.0660, -75.2680),
    "Concepción":          (-11.9210, -75.3140),
    "Concepcion":          (-11.9210, -75.3140),
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
