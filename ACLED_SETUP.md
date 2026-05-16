# ACLED API · Guía de Activación para APURISK

ACLED (Armed Conflict Location & Event Data Project) provee datos georreferenciados
de eventos políticos verificados. Es el gold standard usado por ONU, Banco Mundial,
ICRC, embajadas y consultoras globales de riesgo.

APURISK ya tiene el integrador construido en `apurisk/collectors/acled.py`. Solo
necesitas obtener una API key y configurarla en Render.

---

## 1. Registro y obtención de API key

### Paso 1 — Crea cuenta de investigador

Ve a https://developer.acleddata.com/ y haz click en **"Register"**.

Datos a completar:
- **Email institucional** (Yahoo personal está bien para empezar)
- **Nombre completo**: Juan Carlos Liendo
- **Organización**: APURISK / Consultoría OSINT independiente
- **País**: Peru
- **Propósito del uso**: "Monitoring of political risk events in Peru for OSINT
  research and analysis. Non-commercial academic use."

> **IMPORTANTE**: Si vas a monetizar APURISK (planes pagos), declara uso comercial.
> El tier comercial requiere licencia paga ($500-$5,000/año según volumen).
> Para empezar con clientes piloto gratis, el tier académico es suficiente.

### Paso 2 — Espera aprobación (24-48h)

ACLED revisa cada solicitud manualmente. Recibirás un email con la API key cuando
aprueben. Lo típico es 1-2 días hábiles.

### Paso 3 — Guarda credenciales

Al recibir aprobación tendrás:
- **API Key**: cadena alfanumérica larga (ej. `xxxxxxxxxxxxxxxxxxxxxxxxxxxx`)
- **Email registrado**: el mismo que usaste para registrarte

---

## 2. Configuración en Render

### Paso 1 — Entra al dashboard de Render

https://dashboard.render.com → `apurisk-osint`

### Paso 2 — Agrega variables de entorno

Ve a **Environment** (menú lateral) → **Add Environment Variable**.

Agrega DOS variables:

| Key | Value | Notas |
|---|---|---|
| `ACLED_API_KEY` | (la key que recibiste) | Mantener secreta |
| `ACLED_EMAIL` | juancliendo@yahoo.com | Email registrado |

Click **Save Changes**. Render redesplegará automáticamente con las nuevas vars.

### Paso 3 — Fuerza un ciclo y valida

Espera 3-5 min al redeploy. Luego abre:

```
https://apurisk-osint.onrender.com/api/refresh
```

Cuando termine, revisa **Logs** de Render — debes ver:

```
  [acled] N eventos recibidos (ventana 14d)
  · ACLED · Eventos georreferenciados: N eventos georreferenciados
```

Donde `N` típicamente será 20-100 eventos en una ventana de 14 días para Perú.

---

## 3. Verificación en el dashboard

Abre `https://apurisk-osint.onrender.com/dashboard` con `Cmd+Shift+R`:

- **Pestaña 📍 ACLED Eventos** — listado con tipo, ubicación, actores, fatalidades.
- **Pestaña 🗺️ Mapa Geográfico** — los markers ACLED tendrán coords exactas (no
  centroides departamentales como otros markers).

Los eventos ACLED reales son verificables: cada uno cita las fuentes periodísticas
originales que los validaron.

---

## 4. Filtrar tipos de eventos (opcional)

Por defecto el collector trae todos los event_types. Si quieres filtrar (ej.
solo protests y violence), edita `apurisk/config.yaml`:

```yaml
acled_event_types:
  - "Protests"
  - "Riots"
  - "Violence against civilians"
  - "Battles"
```

Tipos disponibles en ACLED:
- `Protests` — manifestaciones (incluye `Peaceful protest`, `Protest with intervention`)
- `Riots` — disturbios (`Violent demonstration`, `Mob violence`)
- `Violence against civilians` — ataques a civiles (incluye desapariciones, asesinatos)
- `Battles` — enfrentamientos armados
- `Explosions/Remote violence` — explosivos, IED, drones, artillería
- `Strategic developments` — desarrollos no violentos (arrestos, acuerdos, cambios)

---

## 5. Costos y plan de escalado

**Tier académico (gratis)** — el que activarás ahora:
- Hasta 500 requests/día
- Datos con lag de ~7 días
- Uso no comercial
- Atribución requerida ("Source: ACLED")

**Tier Pro (pagado, futuro)** — cuando tengas clientes corporativos:
- Real-time (lag de <24h)
- Más requests
- Uso comercial
- Acceso al ACLED Dashboard interno con filtros avanzados
- Contacto: https://acleddata.com/access-acled-data/

Mi recomendación: arranca con el tier académico. Cuando llegues a 3-5 clientes
pagando, evalúa upgrading al tier Pro como costo de servicio.

---

## 6. Troubleshooting

**El collector dice "ACLED_API_KEY no configurada"**
→ Las env vars no se guardaron. Revisa que estén en *Environment* de Render
  (no en *Build Command*).

**HTTP 401 Unauthorized**
→ API key incorrecta. Vuelve a copiarla del email de aprobación.

**HTTP 429 Rate limit**
→ Excediste el límite diario. Espera 24h. Aumenta `acled_ventana_dias` para
  jalar más en cada request y reducir frecuencia.

**0 eventos recibidos**
→ Puede ser que en ese período no haya eventos verificados para Perú. Aumenta
  ventana a 30 días en `config.yaml`: `acled_ventana_dias: 30`.

**El dashboard sigue mostrando datos demo**
→ Olvidaste hacer `api/refresh` después del redeploy. Forza un ciclo manual.

---

## 7. Atribución requerida (compliance ACLED)

Si vas a publicar reportes con datos ACLED, INCLUYE esta atribución:

> *"Event data sourced from ACLED (Armed Conflict Location & Event Data Project).
> ACLED is the highest quality, most widely used, real-time data and analysis
> source on political violence and protest around the world. Visit acleddata.com
> for more information."*

Esto ya viene incluido en cada Article generado por el collector (campo
`source_name`). Para reportes PDF/DOCX, agrégalo al footer.

---

## 8. Próximos pasos

Una vez que ACLED esté activo:

1. **Validar cobertura** — ¿cuántos eventos por departamento? ¿coincide con tu
   percepción de la realidad? Si Apurímac/Madre de Dios están bajos, ajusta
   ventana o consulta el dashboard oficial ACLED.

2. **Usar en reportes ejecutivos** — agregar sección "Eventos verificados ACLED"
   al reporte diario PDF. Los compradores corporativos valoran fuentes
   institucionales verificadas mucho más que clasificación automática de RSS.

3. **Series temporales** — comparar densidad de eventos mes a mes para detectar
   tendencias (e.g., ¿hay más bloqueos en mayo 2026 vs mayo 2025?).

4. **Cross-validar con tu clasificador RSS** — si tu pestaña Conflictos detecta
   un paro pero ACLED no lo registra, puede ser que sea menor o no tan
   violento. Buena señal para calibrar.
