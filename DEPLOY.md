# APURISK 1.0 — Guía de despliegue 24/7

**Objetivo:** tener una URL pública (ej. `https://apurisk-osint.onrender.com`) que muestre el dashboard con datos siempre frescos, generando reportes on-demand sin tu intervención manual.

---

## Opción A — Render.com (la más fácil, ~5 minutos)

Render.com hospeda Python web apps gratis con HTTPS automático y dominio incluido. Es la opción recomendada si no quieres administrar servidores.

### Paso 1 — Subir el código a GitHub

```bash
# En la carpeta del proyecto
cd "PLATAFORMA OSINT APURISK 1.0 PERU"

# Inicializar git si aún no está
git init
git add .
git commit -m "APURISK 1.0 — initial deploy"

# Crear el repo en GitHub.com (botón "New repository") y luego:
git remote add origin https://github.com/TU_USUARIO/apurisk-osint.git
git branch -M main
git push -u origin main
```

> El repo puede ser **privado**. Render se conecta vía OAuth y lee aunque sea privado.

### Paso 2 — Crear el servicio en Render

1. Crea una cuenta gratis en https://render.com (puedes loguearte con GitHub)
2. En el Dashboard click **"New +"** → **"Blueprint"**
3. Selecciona el repo `apurisk-osint` que acabas de subir
4. Render detecta automáticamente el archivo `render.yaml` y propone crear el servicio
5. Click **"Apply"**

Render automáticamente:
- Instala las dependencias (`requirements-server.txt`)
- Lanza el servidor con `uvicorn apurisk.server:app`
- Asigna disco persistente de 1 GB para `output/` (snapshots, archivo SQLite, reportes)
- Te da una URL pública con HTTPS, por ejemplo: `https://apurisk-osint.onrender.com`

### Paso 3 — (Opcional) Activar Twitter / X live

Para que el módulo de Twitter traiga tweets reales:

1. En Render Dashboard → tu servicio → **"Environment"**
2. Click **"Add Environment Variable"**
3. Key: `TWITTER_BEARER_TOKEN` · Value: tu Bearer Token de https://developer.x.com
4. Click **"Save Changes"** (Render reinicia el servicio)

Si no agregas este token, Twitter funciona en modo demo (datos sintéticos). Las demás fuentes (RPP, La República, Defensoría, GDELT...) funcionan sin token.

### Paso 4 — Probar la URL

Abre `https://TU-SERVICIO.onrender.com` y verás:

- **`/`** → Dashboard interactivo (10 pestañas: Mapa Riesgos, Mapa Geográfico, Alertas, Reporte 24h, Twitter, Conflictos, Legislativo, Entidades, Tendencias, Descargas, Monitoreo)
- **`/api/status`** → Estado del scheduler y métricas
- **`/api/reporte/ejecutivo/pdf`** → Descarga reporte ejecutivo PDF
- **`/api/reporte/ejecutivo/docx`** → Descarga reporte ejecutivo Word
- **`/api/reporte/24h/html`** → Reporte 24h imprimible
- **`/api/reporte/alertas/docx`** → Alertas inmediatas Word
- **`/api/reporte/semanal/pdf`** → Reporte semanal con tendencias
- **`/api/buscar?keyword=Huancavelica`** → Búsqueda en archivo histórico
- **`/api/buscar?tipo=persistentes&min_dias=3`** → Casos persistentes
- **`/api/refresh`** → Forzar re-ejecución del pipeline ahora

### Plan free vs starter

- **Free** ($0): el servicio se duerme tras 15 min sin tráfico. Cuando alguien lo abra de nuevo, despierta en ~30 segundos. Datos persisten en el disco.
- **Starter** ($7/mes): siempre encendido, sin sleep. Recomendado para uso operacional real.

Cambias de plan en cualquier momento desde el Dashboard de Render.

---

## Opción B — Docker (cualquier servidor)

Si tienes un VPS propio (DigitalOcean, AWS Lightsail, etc.) o quieres correr local:

```bash
# Build de la imagen
docker build -t apurisk-osint .

# Correr — modo tiempo real (cada 30 min)
docker run -d \
  --name apurisk \
  -p 8080:8080 \
  -e TWITTER_BEARER_TOKEN="AAAA..." \
  -v $(pwd)/output:/app/output \
  --restart unless-stopped \
  apurisk-osint

# Ver logs
docker logs -f apurisk

# La plataforma estará en http://TU-SERVIDOR:8080
```

Para HTTPS y dominio personalizado, recomiendo poner Caddy o Nginx delante:

```bash
# docker-compose.yml ejemplo con Caddy
# (Caddy obtiene certificados Let's Encrypt automáticamente)
```

---

## Opción C — VPS con systemd (control total)

```bash
# En tu VPS (Ubuntu/Debian)
git clone https://github.com/TU_USUARIO/apurisk-osint.git /opt/apurisk
cd /opt/apurisk
python3 -m venv venv
./venv/bin/pip install -r requirements-server.txt

# Crear servicio systemd
sudo tee /etc/systemd/system/apurisk.service > /dev/null <<'EOF'
[Unit]
Description=APURISK 1.0 - OSINT Riesgos Políticos Perú
After=network.target

[Service]
Type=simple
User=apurisk
WorkingDirectory=/opt/apurisk
Environment="TWITTER_BEARER_TOKEN=AAAA..."
Environment="REFRESH_SECONDS=1800"
ExecStart=/opt/apurisk/venv/bin/uvicorn apurisk.server:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now apurisk
sudo systemctl status apurisk
```

Luego configura Nginx + Let's Encrypt para HTTPS y dominio personalizado.

---

## Endpoints de la API (resumen)

Una vez desplegado, tu plataforma expone:

| URL | Qué devuelve |
|---|---|
| `/` o `/dashboard` | Dashboard HTML completo (visual) |
| `/api/status` | JSON con estado del scheduler, archivo, último snapshot |
| `/api/snapshot` | JSON crudo del snapshot más reciente |
| `/api/refresh` | Forza nueva ejecución del pipeline ahora |
| `/api/reporte/ejecutivo/pdf` | **Reporte ejecutivo PDF** (≤3 páginas, foco tendencias) |
| `/api/reporte/ejecutivo/docx` | Reporte ejecutivo Word |
| `/api/reporte/24h/html` | Reporte 24h imprimible |
| `/api/reporte/24h/docx` | Reporte 24h Word |
| `/api/reporte/alertas/html` | Feed de alertas HTML |
| `/api/reporte/alertas/docx` | Alertas Word |
| `/api/reporte/diario/pdf` | Reporte diario detallado PDF |
| `/api/reporte/semanal/pdf` | Reporte semanal con tendencias PDF |
| `/api/buscar?keyword=X` | Búsqueda en archivo histórico (artículos) |
| `/api/buscar?tipo=alertas&nivel=CRÍTICA` | Buscar alertas |
| `/api/buscar?tipo=persistentes&min_dias=3` | Casos persistentes |
| `/api/buscar?tipo=score&dias=30` | Serie temporal del score global |
| `/healthz` | Health check |

Estos endpoints permiten **integrar APURISK con otras herramientas**: Slack, Power BI, Google Sheets, dashboards corporativos, o cualquier sistema que consuma JSON.

---

## Comandos útiles tras el deploy

```bash
# Ver estado desde curl
curl https://TU-SERVICIO.onrender.com/api/status

# Descargar reporte ejecutivo PDF
curl -O https://TU-SERVICIO.onrender.com/api/reporte/ejecutivo/pdf

# Buscar histórico
curl "https://TU-SERVICIO.onrender.com/api/buscar?keyword=Huancavelica&limit=10"

# Forzar refresh manual
curl https://TU-SERVICIO.onrender.com/api/refresh
```

---

## Costos estimados

| Opción | Costo mensual | Características |
|---|---|---|
| Render Free | $0 | Sleep tras 15 min idle. OK para uso ocasional |
| Render Starter | $7 | Siempre activo, dominio gratis, HTTPS, 1 GB disco |
| Railway Pro | $5+ | Similar a Render |
| Fly.io | $0-5 | Free tier generoso; bueno técnicamente |
| DigitalOcean Droplet | $6 | VPS con control total, requiere admin sysop |
| AWS Lightsail | $5 | Similar a DigitalOcean |

**Recomendación:** empieza con **Render Free** para validar que todo funciona. Si lo usas operativamente, sube a **Starter ($7)** para evitar sleeps.
