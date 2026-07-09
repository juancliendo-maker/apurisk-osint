# Reporte de Riesgo Político 24h (AP24) — Checklist "Listo para Cliente"

Verificación rápida ANTES de generar y enviar un AP24 a un cliente. El producto
es on-demand (se genera cuando se necesita), pero su **calidad** depende de
condiciones vivas del sistema. Recorrer esta lista toma ~2 minutos.

---

## 1. API de Anthropic operativa (narrativa completa)

- [ ] Smoke test: abrir `/admin/ap24/test-api` → debe responder `{"ok": true, ...}`.
- [ ] Si responde `ok: false`: revisar que `ANTHROPIC_API_KEY` esté configurada en
      Render (Environment) y con saldo. **Sin API válida el reporte sale en modo
      RESPALDO (degradado, sin narrativa) — NO apto para cliente.**

## 2. Datos frescos + snapshot OSINT (contenido real)

- [ ] El pipeline de ingesta corrió recientemente (hay artículos de las últimas 24h).
- [ ] Existe un snapshot OSINT reciente (si nunca corrió el análisis, AP24 da
      error "Sin snapshot OSINT disponible" y no genera PDF).
- [ ] Señal en el propio PDF: el recuadro **INTEGRIDAD DE DATOS** muestra un conteo
      razonable de artículos y una "Última actualización de datos (snapshot)" reciente.

## 3. Modo operativo (sin marca de calibración)

- [ ] `AP24_MODO_CALIBRACION = 0` (config) → el PDF NO debe decir
      "VERSIÓN DE CALIBRACIÓN". En portada/estado debe verse **OPERATIVO**.
      (El producto aprobado arranca en operativo; si se ve la marca, revisar el
      parámetro en config.)

## 4. Generar y revisar el PDF antes de enviar

- [ ] Generar un AP24 desde `/admin/reportes` (corre en segundo plano; se descarga
      con autenticación de admin).
- [ ] Portada: título **"Reporte de Riesgo Político"**, logo THALOS completo,
      fondo navy limpio.
- [ ] Las 5 secciones presentes: **SÍNTESIS DEL DÍA**, **RIESGO POLÍTICO / Las
      últimas 24 horas en desarrollo**, **CONEXIONES Y CONTEXTO**, **HECHOS
      CITADOS**, **NOTA DE MATERIAL**.
- [ ] **HECHOS CITADOS**: idealmente ≥10 hechos, con enlaces limpios (título
      clicable, sin URLs crudas pegadas al texto).
- [ ] Subtítulos de los bloques del día en negrita.
- [ ] Página de contexto: **Actores Políticos en Riesgo** arriba + velocímetro +
      tablero de métricas, juntos; nota metodológica de pesos bajo el velocímetro.
- [ ] Sin texto sobrante ni encabezados duplicados; sin superposición en el grid.

## 5. Tabla de Actores (opcional pero recomendable)

- [ ] Si se quiere la tabla poblada: el analista cargó actores en el admin y los
      vinculó a temas. Si no hay, la tabla muestra la nota honesta "Sin actores en
      riesgo significativo en el período" (válido, pero vacío).

---

### Parámetros AP24 vigentes (referencia)

| Parámetro | Valor | |
|---|---|---|
| `AP24_MODELO` | `claude-sonnet-4-6` | modelo de la API |
| `AP24_MAX_TOKENS` | `3000` | tokens de salida |
| `AP24_TOP_N_ARTICULOS` | `150` | artículos enviados (para ≥10 hechos) |
| `AP24_ACTORES_TOP_N` | `6` | filas de la tabla de actores |
| `AP24_TIMEOUT_S` | `120` | timeout por intento de API |
| `AP24_MODO_CALIBRACION` | `0` | 0 = operativo · 1 = marca de calibración |
| `AP24_PROMPT_MAESTRO` | `v4` | prompt maestro vigente |

Todos son editables en config sin re-desplegar (toman efecto en el siguiente
reporte). Para volver a modo calibración temporalmente: `AP24_MODO_CALIBRACION=1`.
