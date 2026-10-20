# SAT-Maule · Sistema de Alerta Temprana para la Región del Maule

PWA instalable que indica a cada vecino del Maule **su** nivel de riesgo de crecida o inundación
según su ubicación GPS, le muestra qué hacer y dónde está el punto seguro más cercano, y le avisa
por notificación push aunque tenga la pantalla bloqueada.

> **Importante.** SAT-Maule **complementa, no reemplaza,** las alertas oficiales de SENAPRED ni el
> Sistema de Alerta de Emergencia (SAE) de los celulares. Antes de usarlo con la comunidad hay que
> reemplazar los datos geográficos de ejemplo por cartografía oficial
> (ver [Datos de referencia](#datos-de-referencia-obligatorio-antes-de-producción)) y calibrar los
> umbrales con eventos históricos de la región.

---

## Índice

1. [Cómo funciona](#cómo-funciona)
2. [Arquitectura](#arquitectura)
3. [Ejecución local](#ejecución-local)
4. [MongoDB Atlas gratis](#mongodb-atlas-gratis)
5. [Desplegar gratis en Render](#desplegar-gratis-en-render)
6. [Desplegar gratis en Koyeb](#desplegar-gratis-en-koyeb)
7. [Monitor de zonas y cron externo](#monitor-de-zonas-y-cron-externo)
8. [Notificaciones en Android e iPhone](#notificaciones-en-android-e-iphone)
9. [Datos de referencia](#datos-de-referencia-obligatorio-antes-de-producción)
10. [API](#api)
11. [Procesamiento con PySpark (opcional)](#procesamiento-con-pyspark-opcional)
12. [Seguridad y privacidad](#seguridad-y-privacidad)
13. [Limitaciones conocidas](#limitaciones-conocidas)
14. [Créditos y licencias de datos](#créditos-y-licencias-de-datos)

---

## Cómo funciona

| Pieza | Qué hace |
|---|---|
| **Motor de consenso** | Pondera la lluvia pronosticada por 3 modelos globales (ECMWF IFS 0.25°, DWD ICON, NOAA GFS) vía Open-Meteo. Si los modelos discrepan, baja un nivel para evitar falsas alarmas. |
| **Filtro hidrológico** | Si la isoterma 0 °C está bajo 1850 m (precipita nieve en la cordillera) o el suelo está seco (< 0,28 m³/m³), cada condición **baja un nivel** el riesgo de desborde (no lo anula: con mucha lluvia el agua escurre igual). La app muestra el motivo junto a los mm. Si llega a VERDE emite un *Reporte de Tranquilidad* o un *Aviso de Tormenta Eléctrica sin riesgo de desborde*. |
| **Alertas de SENAPRED** | Sólo fija su nivel la alerta amarilla o roja de lluvia, crecida, aluvión o tormenta que **nombra su comuna**. Las alertas para toda la región se informan sin cambiar el color de su punto (salvo que falte el pronóstico propio), y las de otras amenazas (incendios, volcanes, emergencias sanitarias) aparecen sólo en la tarjeta de SENAPRED. |
| **Cauces cercanos** | Al tomar la ubicación, lista y dibuja los ríos, esteros, quebradas y canales a menos de 3 km (red hidrográfica completa del Maule, ~5.200 tramos de OpenStreetMap). |
| **Exposición personal** | Usa el cauce que más influye (distancia ponderada por tipo: un canal de riego pesa menos que un río) y la altura del usuario sobre él. En la ribera el nivel sube; en una terraza alta, baja. |
| **Aluvión** | Estar cerca de una quebrada **no** genera alerta por sí solo. Se avisa cuando, además, la isoterma 0 °C está sobre 2.200 m y la lluvia de 24 h (la mayor entre la pronosticada y la ya caída) llega a 15 / 30 / 50 mm (AMARILLO / NARANJA / ROJO). Un chubasco de 10 mm/h o más sube un nivel. Umbrales heurísticos a calibrar con SERNAGEOMIN. |
| **Cobertura nacional** | Al activar la ubicación en cualquier punto de Chile identifica la **comuna y región** (límites de OpenStreetMap) y usa los umbrales de lluvia de su macrozona (Norte Grande, Norte Chico, Centro, Sur, Austral). |
| **Deslizamientos** | Mide la pendiente del terreno alrededor del usuario con el modelo de elevación (8 puntos a 120 m). Bajo 15° no hay alerta; de 15° a 30° el nivel de lluvia baja uno; sobre 30° se aplica completo. |
| **Caudal del río** | Pronóstico GloFAS (Copernicus) del río más cercano a menos de 3 km: caudal de hoy, máximo de los próximos 7 días y tendencia. |
| **Reportes vecinales** | Con 3 vecinos distintos reportando a menos de 2 km en 3 horas, se emite una alerta ciudadana (AMARILLO o NARANJA; nunca ROJO por sí sola). |
| **Monitor de zonas** | Cada 30 min evalúa las zonas, crea o escala alertas de desborde, de aluvión (sólo zonas con quebradas a menos de 7 km del centro) y de tormenta eléctrica, y las despacha por Web Push. El aviso de aluvión sólo llega a quien registró una ubicación a menos de 1 km de una quebrada. |
| **Tormenta eléctrica** | Con CAPE ≥ 800 J/kg y algo de lluvia (≥ 1 mm) se emite aviso AMARILLO aunque la lluvia total sea baja. |
| **Anti-spam** | Cooldown por nivel (ROJO 1 h, NARANJA 3 h, AMARILLO 12 h: sin recordatorios nocturnos por un aviso preventivo); un aumento de nivel se avisa siempre. |
| **PWA offline** | Guarda zonas, puntos seguros, último diagnóstico y teléfonos de emergencia. Los reportes hechos sin señal se envían al volver la conexión. |

## Arquitectura

Clean Architecture / DDD: las dependencias apuntan siempre hacia el dominio.

```
src/
├── domain/            Reglas puras (sin I/O): entidades, value objects, servicios
│   ├── entities/        Alert, GeoZone, Watercourse, SafePoint, CitizenReport, Subscriber, Telemetry
│   ├── value_objects/   Coordinates/GeoJSON, RiskLevel, ThreatType, pronósticos, notificaciones
│   ├── services/        ConsensusEngine, ExposureAssessment, CrowdValidation, CooldownPolicy
│   └── repositories/    Puertos (ABC / Protocol) hacia la infraestructura
├── application/       Casos de uso: evaluate_gps_risk, process_crowdsourcing, dispatch_alerts,
│                      monitor_zones, register_subscriber, get_zones_status, get_map_layers
├── infrastructure/    Adaptadores: MongoDB (Motor), Open-Meteo (httpx), Web Push (pywebpush), PySpark
└── presentation/
    ├── api/           FastAPI: routers, composición de dependencias (container.py)
    └── pwa/           Frontend: index.html, app.js + js/, sw.js, manifest.json, íconos, Leaflet
```

La API y la PWA se sirven desde **el mismo origen**: no hace falta configurar CORS y el
Service Worker controla todo el sitio.

**Teorema CAP:** MongoDB está configurado para priorizar disponibilidad (escrituras `w=1`,
lecturas desde secundarios si cae el primario, reintentos). Si una fuente falla, el diagnóstico
GPS se entrega igual marcado como `PARCIAL`. Detalle en
[`mongo_client.py`](src/infrastructure/database/mongo_client.py).

## Ejecución local

Requisitos: Python 3.11+ y una base MongoDB (Atlas gratis o local).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env                                     # y completar SAT_MONGO_URI
python -m src.infrastructure.notifications.generate_vapid   # pegar ambas claves en .env
python -c "import secrets; print(secrets.token_urlsafe(32))" # → SAT_REPORTER_HASH_SECRET

python scripts/import_chile.py                           # comunas y cauces de las 16 regiones → data/chile/ (30-90 min, se puede reanudar)
python scripts/import_osm_waterways.py                   # alternativa: sólo el Maule (el repo ya trae data/maule_waterways.geojson)
python -m src.infrastructure.database.seed                # zonas, ~5.200 cauces y puntos de ejemplo
uvicorn src.presentation.api.main:app --reload
```

**Demostración sin MongoDB ni configuración** (pronóstico real, datos en memoria, Web Push real):

```bash
python scripts/demo.py              # abre la app y el panel http://localhost:8000/demo
python scripts/demo.py --simulado   # lluvia simulada desde el panel, para ensayar alertas
```

- PWA: <http://localhost:8000>
- Documentación interactiva de la API: <http://localhost:8000/docs>

> La geolocalización, el Service Worker y Web Push sólo funcionan en **HTTPS** o en `localhost`.
> Para probar en un celular dentro de la red local use un túnel HTTPS (p. ej. `cloudflared`) o
> despliegue en Render/Koyeb, que entregan HTTPS automáticamente.

Pruebas y tipado estricto:

```bash
pytest            # dominio, casos de uso, adaptadores y API
mypy src          # --strict
```

Con Docker:

```bash
docker build -t sat-maule .
docker run --env-file .env -p 8000:8000 sat-maule
```

## MongoDB Atlas gratis

1. Cree una cuenta en <https://www.mongodb.com/cloud/atlas> y un clúster **M0 (Free)**.
   Elija la región más cercana a su hosting (p. ej. AWS `sa-east-1` São Paulo, o la misma región
   de Render/Koyeb).
2. **Database Access** → *Add New Database User*: usuario y contraseña con rol
   *Read and write to any database*.
3. **Network Access** → *Add IP Address*. Render y Koyeb (plan gratuito) no tienen IP fija, así
   que normalmente se usa `0.0.0.0/0`. Compénselo con una contraseña larga y aleatoria.
4. **Connect → Drivers** → copie la cadena `mongodb+srv://...`, reemplace `<password>` y
   úsela como `SAT_MONGO_URI`.
5. Cargue los datos iniciales una vez, desde su computador:

   ```bash
   SAT_MONGO_URI="mongodb+srv://..." python -m src.infrastructure.database.seed
   ```

Los índices (`2dsphere` para consultas geoespaciales y TTL para expirar telemetría y registros
antiguos) se crean solos al iniciar la API.

## Desplegar gratis en Render

1. Suba el proyecto a un repositorio de GitHub (el `.gitignore` ya excluye `.env`).
2. En <https://render.com>: **New → Web Service** → conecte el repositorio.
   Render detecta el `Dockerfile` automáticamente. Elija el plan **Free**.
3. **Environment**: cargue las variables de [`.env.example`](.env.example):
   `SAT_MONGO_URI`, `SAT_VAPID_PRIVATE_KEY`, `SAT_VAPID_PUBLIC_KEY`, `SAT_VAPID_SUBJECT`,
   `SAT_REPORTER_HASH_SECRET`, `SAT_CRON_TOKEN` y `SAT_MONITOR_INTERVAL_MIN=0`
   (ver [monitor](#monitor-de-zonas-y-cron-externo)).
4. **Settings → Health Check Path**: `/api/health`.
5. Despliegue. La app queda en `https://<su-servicio>.onrender.com`.

> **Plan gratuito de Render:** al momento de escribir esto, el servicio se *duerme* tras unos
> 15 minutos sin tráfico y tarda cerca de un minuto en despertar. Para un sistema de alertas eso
> es inaceptable sin el cron externo de la sección siguiente, que además lo mantiene despierto.
> Revise las condiciones vigentes del plan (horas mensuales incluidas, límites) en el sitio de Render.

## Desplegar gratis en Koyeb

1. En <https://www.koyeb.com>: **Create Service → GitHub** → seleccione el repositorio.
2. **Builder:** *Dockerfile*. **Instance:** la opción gratuita disponible.
3. **Exposed port:** `8000` (Koyeb entrega `PORT`, que el contenedor respeta).
4. **Health check:** HTTP en `/api/health`.
5. **Environment variables:** las mismas que en Render.
6. Despliegue. La app queda en `https://<app>-<org>.koyeb.app`.

> Las condiciones del plan gratuito de Koyeb (instancias, suspensión por inactividad, regiones)
> cambian con frecuencia: verifíquelas antes de depender de él. Si el servicio se suspende por
> inactividad, use también el cron externo.

## Monitor de zonas y cron externo

El monitor evalúa todas las zonas, crea o escala alertas y las despacha. Hay dos modos; use
**sólo uno** a la vez para evitar alertas duplicadas:

| Modo | Cuándo | Configuración |
|---|---|---|
| **En proceso** | Hosting que nunca se duerme (VPS, plan pagado) | `SAT_MONITOR_INTERVAL_MIN=30` |
| **Cron externo** | Hosting gratuito que se duerme | `SAT_MONITOR_INTERVAL_MIN=0` + `SAT_CRON_TOKEN` |

**cron-job.org (gratis):** cree un *cronjob* cada 10–15 minutos con:

- URL: `https://<su-app>/api/internal/monitor`
- Método: `POST`
- Cabecera: `X-Cron-Token: <valor de SAT_CRON_TOKEN>`
- Timeout: el máximo permitido (la primera llamada puede despertar al servidor)

**Alternativa con GitHub Actions** (`.github/workflows/monitor.yml`). Las ejecuciones programadas
de GitHub pueden retrasarse varios minutos, así que es mejor como respaldo:

```yaml
name: monitor-sat-maule
on:
  schedule: [{ cron: "*/15 * * * *" }]
  workflow_dispatch:
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -fsS -m 120 -X POST "${{ secrets.SAT_URL }}/api/internal/monitor" \
               -H "X-Cron-Token: ${{ secrets.SAT_CRON_TOKEN }}"
```

## Notificaciones en Android e iPhone

| Plataforma | Requisitos |
|---|---|
| **Android** (Chrome, Edge, Firefox, Samsung Internet) | Funciona desde el navegador. Para la mejor experiencia: menú → *Instalar app* (o el botón **Instalar app** de la PWA). |
| **iPhone / iPad** (iOS 16.4 o superior) | Web Push **sólo** funciona si la app está instalada: Safari → *Compartir* → *Agregar a pantalla de inicio*, abrirla desde el ícono y tocar **Activar protección**. La PWA muestra esta instrucción automáticamente. |

- Las alertas ROJO y NARANJA quedan fijas en pantalla hasta que el usuario las toca y vibran con
  un patrón largo. iOS no permite personalizar la vibración.
- Si cambian las claves VAPID, las suscripciones anteriores dejan de funcionar y cada usuario
  debe volver a tocar **Activar protección**.
- Sin `SAT_VAPID_*` la app funciona igual, pero no envía alertas: `/api/health` informa
  `"web_push": false`.

## Datos de referencia (obligatorio antes de producción)

[`seed.py`](src/infrastructure/database/seed.py) carga 5 zonas piloto: Vilches, Talca, Cumpeo,
Achibueno y Licantén. Todo ese contenido es **de ejemplo**:

- Las zonas son cuadrados de unos 13 km alrededor de cada localidad.
- Los cauces son trazos simplificados de 2–3 puntos (`geometry_source = "referencial"`).
- Los puntos seguros se llaman `[DEMO]` y **no son lugares oficiales**.

Reemplácelos por:

- **Cauces:** red hidrográfica de la DGA / Banco Nacional de Aguas o IDE Chile (GeoJSON
  `LineString`, `geometry_source` con la fuente).
- **Zonas:** áreas inundables de SENAPRED, planes reguladores comunales o estudios de riesgo.
- **Puntos seguros:** catastro oficial municipal / SENAPRED.

Calibre además los umbrales de lluvia (`ConsensusConfig`) con el job histórico de PySpark.

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/gps-check?lat=&lon=` | Diagnóstico personalizado: nivel, exposición, cauce cercano, recomendaciones y puntos seguros |
| `POST` | `/api/subscribe` | Registra la suscripción Push y la ubicación base del vecino (idempotente) |
| `POST` | `/api/report-water-level` | Reporte vecinal de crecida (regla de 3 vecinos en 2 km; 1 reporte cada 10 min por dispositivo) |
| `GET` | `/api/zones` | Estado de las zonas, ordenadas por riesgo (`?name=Talca` filtra) |
| `GET` | `/api/map` | Capas del mapa (zonas, cauces, puntos seguros) en pares `[lat, lon]` |
| `GET` | `/api/push/public-key` | Clave pública VAPID para el navegador |
| `POST` | `/api/internal/monitor` | Ejecuta el monitor (requiere `X-Cron-Token`) |
| `GET` | `/api/health` | Estado del servicio y sus dependencias |

Documentación OpenAPI completa en `/docs`.

## Procesamiento con PySpark (opcional)

Requiere Java 17+ (`JAVA_HOME`) y `pip install -e ".[spark]"`. En Windows además `winutils.exe`.

```bash
# Umbrales críticos de inundación por estación (ventanas de 6, 24 y 72 h)
python -m src.infrastructure.spark.historical_batch --input data/maule.parquet --output out/umbrales

# Consolidación de telemetría en tiempo real hacia MongoDB
python -m src.infrastructure.spark.realtime_stream --source data/stream_in \
       --thresholds out/umbrales/parquet --checkpoint chk/realtime
```

## Umbrales por macrozona

Los umbrales están en [`regional_thresholds.py`](src/domain/services/regional_thresholds.py)
(lluvia en 24 h para AMARILLO / NARANJA / ROJO):

| Macrozona | Regiones | Desborde | Aluvión / deslizamiento | Isoterma mín. aluvión |
|---|---|---|---|---|
| Norte Grande | Arica y Parinacota – Atacama | 10 / 20 / 35 mm | 8 / 15 / 25 mm | 3.000 m |
| Norte Chico | Coquimbo | 15 / 30 / 50 mm | 10 / 20 / 35 mm | 2.700 m |
| Centro | Valparaíso – Maule | 20 / 40 / 70 mm | 15 / 30 / 50 mm | 2.200 m |
| Sur | Ñuble – Los Ríos | 35 / 60 / 100 mm | 25 / 45 / 70 mm | 1.600 m |
| Austral | Los Lagos – Magallanes | 40 / 70 / 110 mm | 30 / 55 / 80 mm | 1.200 m |

> Son **valores heurísticos iniciales** escalados desde el Maule. Deben calibrarse con eventos
> históricos de cada macrozona (SERNAGEOMIN, DMC, DGA) antes de usarse con la comunidad.

## Seguridad y privacidad

- **Reportes vecinales:** el identificador del dispositivo se anonimiza con HMAC-SHA256
  (`SAT_REPORTER_HASH_SECRET`). No se guardan nombres, teléfonos ni correos.
- **Ubicaciones:** la ubicación base de cada suscriptor se guarda sólo para enviarle alertas
  cercanas. Informe esto a los usuarios según la Ley 19.628 sobre protección de la vida privada.
- **Cabeceras de seguridad:** CSP estricta (scripts sólo del propio sitio; iframes sólo de Windy),
  `nosniff`, `Referrer-Policy` y `Permissions-Policy`.
- **Interfaz:** ningún dato del servidor se inserta como HTML (`textContent`).
- **Leaflet:** se incluye localmente (v1.9.4, verificado contra su hash SRI oficial).
- **Contenedor:** se ejecuta con un usuario sin privilegios.

## Limitaciones conocidas

- **Efectividad no medida.** La efectividad real debe medirse comparando con eventos históricos
  del Maule; el código no garantiza ninguna tasa de acierto.
- **Pendiente aproximada.** El DEM de ~90 m suaviza los relieves: subestima taludes y cortes
  cortos. Es un tamizaje, no reemplaza los mapas de peligro de SERNAGEOMIN.
- **Monitor nacional pendiente.** El diagnóstico GPS cubre todo Chile, pero el monitor de 30 min
  y las alertas push automáticas aún recorren sólo las zonas piloto del Maule.
- **Elevación aproximada.** El modelo de elevación (Copernicus, ~90 m) da alturas sobre el cauce
  sólo orientativas.
- **Hosting gratuito.** Puede dormirse; sin cron externo las alertas se retrasan.
- **Un solo monitor.** El monitor en proceso asume una sola instancia; no escale a varias
  réplicas sin moverlo a un job único.
- **Uso no comercial.** Open-Meteo es gratuito para uso no comercial; un despliegue
  institucional de alto volumen puede requerir su plan de pago.
- **Teselas del mapa.** Las teselas de `tile.openstreetmap.org` están sujetas a su
  [política de uso](https://operations.osmfoundation.org/policies/tiles/): sirven para tráfico
  bajo o moderado. Con muchos usuarios, contrate un proveedor de teselas.
- **Red hidrográfica.** La de OpenStreetMap es la más completa disponible como dato abierto, pero
  no es cartografía oficial: puede omitir cauces o tener trazados imprecisos. Para producción,
  contrástela con la red de la DGA o de IDE Chile.

## Créditos y licencias de datos

- Pronósticos: [Open-Meteo](https://open-meteo.com) (CC BY 4.0) con modelos de ECMWF, DWD y NOAA.
- Caudales: Copernicus Emergency Management Service – GloFAS (CC BY 4.0).
- Elevación: Copernicus DEM GLO-90.
- Mapa y red hidrográfica (ríos, esteros, quebradas, canales): © OpenStreetMap contributors (ODbL 1.0).
- Windy.com: mapa embebido (lluvia, tormentas, ráfagas, CAPE y pronóstico del punto) y, con `SAT_WINDY_POINT_API_KEY`, estudio numérico por modelos (GFS, ICON) en `GET /api/windy-study`.
- [Leaflet](https://leafletjs.com) (BSD-2-Clause).
