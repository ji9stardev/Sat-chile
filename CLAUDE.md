# SAT-Chile · Reglas del sistema de diseño (integración con Figma)

Guía para traducir diseños de Figma (vía MCP) al frontend de SAT-Chile. **No hay framework ni build**:
la PWA es HTML + CSS + JavaScript nativo (módulos ES), servida tal cual por FastAPI.
Todo texto de interfaz va en **español de Chile** y trata al usuario de «usted».

## 1. Estructura del proyecto

```
src/presentation/pwa/          ← todo el frontend (servido en "/" por FastAPI)
├── index.html                   las 4 vistas, el diálogo de lugar y todos los íconos SVG en línea
├── styles.css                   ÚNICA hoja de estilos: tokens (:root) + componentes
├── app.js                       orquestación: navegación por hash, eventos, refrescos
├── js/
│   ├── config.js                tokens de nivel en JS (LEVELS), textos y constantes
│   ├── ui.js                    render de la interfaz (crea nodos DOM con el helper el())
│   ├── map.js                   Leaflet: capas, refugios, «elegir en el mapa»
│   ├── api.js · geo.js · push.js · storage.js
├── sw.js                        Service Worker: precache, datos offline, teselas
├── manifest.json · icons/       íconos PWA (PNG generados + icon.svg)
└── vendor/                      leaflet/ y fonts/ (Atkinson Hyperlegible), sin CDN
src/presentation/api/main.py   monta la PWA, CSP y cabeceras de caché
```

Vistas = `<section id="view-{inicio|monitoreo|seguro|reportar}" class="view">` en `index.html`,
alternadas por `location.hash` (`app.js` → `showView`). No hay router ni componentes de framework.

## 2. Tokens de diseño

Definidos como **variables CSS en `:root`** de `src/presentation/pwa/styles.css`. No hay Style
Dictionary ni transformación: los valores de Figma se copian a mano en estas variables.

```css
:root {
  --bg: #F6F5F1;  --surface: #FFFFFF;  --surface-2: #F0EEE8;
  --border: #E2E0D9;  --border-strong: #D4D1C8;
  --text: #15171A;  --muted: #4A4F57;  --muted-2: #3A3F46;
  --accent: #1F4E8C;  --accent-ink: #163A69;  --accent-soft: #DCE6F3;  --focus: #1F4E8C;
  /* niveles de riesgo */
  --verde: #1E7F4F;  --amarillo: #F2C230;  --naranja: #E8772A;  --rojo: #C62828;  --sin-datos: #5A5F66;
  /* cauces */
  --river: #1F6FB2;  --quebrada: #C2410C;
  /* tema dinámico según el nivel */
  --level: var(--sin-datos);  --on-level: #FFFFFF;  --level-chip: rgba(255,255,255,.18);
  --radius: 18px;  --tabbar-h: 78px;
}
body[data-level="AMARILLO"] { --level: var(--amarillo); --on-level: #15171A; --level-chip: rgba(0,0,0,.12); }
```

- **El nivel tematiza la página**: `ui.applyLevel()` pone `body[data-level]`, y `--level` / `--on-level`
  cambian. Sobre amarillo y naranja el texto es oscuro (`#15171A`), sobre verde y rojo es blanco.
- **Duplicado en JS**: los colores de nivel y de cauces también están en `js/config.js`
  (`LEVELS`, `WATERCOURSE_KINDS`) porque Leaflet los necesita como valores. Si cambia un color
  en Figma, **actualícelo en ambos lugares**. Lo mismo para `#F6F5F1` en `manifest.json` y el
  `<meta name="theme-color">`.
- **Tipografía**: una sola familia, Atkinson Hyperlegible 400/700, autoalojada en
  `vendor/fonts/`. Cuerpo de 18px/1.4. Escala usada: 13–15 (secundario), 16–18 (cuerpo),
  20–22 (títulos), 32–48 (cifras grandes, `.big-number`). **Mínimo 15px de texto** (13px sólo
  para ejes o escalas).
- **Espaciado y radios**: valores en px directos, sin escala nombrada. Huecos de 6/8/10/12/14 px,
  relleno de tarjeta de 18px, radios de 12–14 (chips), 16 (filas), 18–20 (tarjetas, botones),
  24 (hero) y 999px (píldoras). Use estos mismos valores en lugar de inventar nuevos.

## 3. Componentes

No hay librería de componentes ni Storybook. Un «componente» es **una clase CSS en
`styles.css` + marcado en `index.html` o un nodo creado en `ui.js`**. Los principales:

| Componente | Clase(s) | Dónde se usa |
|---|---|---|
| Tarjeta | `.card` (fondo `--surface`, radio 20, relleno 18) | todas las vistas |
| Tarjeta de nivel | `.hero` + `data-threat` | Inicio |
| Botón principal | `.btn-primary` (alto mínimo 64px, `--accent`) | acciones principales |
| Botón secundario | `.btn-secondary`, `.btn-small`, `.btn-inline` | acciones de apoyo |
| Fila de lista | `.rows > .row` con `.row-text`, `.row-value` y `.level-pill[data-level]` | Monitoreo, refugios |
| Píldora de nivel | `.level-pill[data-level="VERDE|AMARILLO|NARANJA|ROJO"]` | filas de alerta |
| Medidor | `.gauge` (segmentos `.seg-*` + `.marker`), `.meter` | lluvia |
| Barra de pestañas | `.tabbar` | navegación inferior fija |
| Aviso emergente | `.toast` (lo crea `ui.toast()`) | mensajes cortos |
| Diálogo | `<dialog class="place-dialog">` | cambiar de ubicación |

Los nodos dinámicos se crean con el helper de `ui.js` / `map.js`, nunca con `innerHTML` y texto
del servidor (por seguridad y por la CSP):

```js
function row(name, detail, level, value = null) {
  const li = el('li', null, 'row');
  const text = el('span', null, 'row-text');
  text.append(el('strong', name), el('span', detail));
  li.append(text);
  if (level) {
    const pill = el('span', LEVELS[level]?.word ?? level, 'level-pill');
    pill.dataset.level = level;
    li.append(pill);
  }
  return li;
}
```

El estado visual se expresa con **atributos `data-*`** (`data-level`, `data-threat`, `data-state`,
`data-new`) y `hidden`, no con clases modificadoras.

## 4. Frameworks y librerías

- **UI**: JavaScript nativo (ES modules con `<script type="module" src="/app.js">`). Sin React,
  Vue, TypeScript, bundler, npm ni paso de build: los archivos se editan y se sirven tal cual.
- **CSS**: CSS plano en un archivo, sin preprocesador, Tailwind, CSS Modules ni CSS-in-JS.
- **Mapa**: Leaflet copiado en `vendor/leaflet/` (global `window.L`), con teselas de OpenStreetMap.
- **Backend**: FastAPI sirve la PWA (`_mount_pwa` en `api/main.py`). `index.html` y `sw.js` se
  sirven con `Cache-Control: no-cache`.

**Al convertir un diseño de Figma**: si `get_design_context` devuelve React o Tailwind, tómelo sólo
como referencia y **reescríbalo como HTML semántico + clases en `styles.css`**, usando
variables `var(--…)` en vez de colores literales.

## 5. Recursos (assets)

- Todo se sirve desde el mismo origen. **No se usan CDN**: la CSP de `api/main.py` sólo permite
  `'self'`, más las teselas de `tile.openstreetmap.org` y el iframe de `embed.windy.com`.
  Las imágenes o fuentes nuevas deben ir a `src/presentation/pwa/` (p. ej. `vendor/` o `icons/`).
- **Offline**: cada archivo estático nuevo debe agregarse a la lista de precache de `sw.js`, y
  hay que **subir `VERSION`** (`'v2.10.0'` → siguiente) para que los celulares lo descarguen.
- Optimización: fuentes en woff2 precargadas (`<link rel="preload">`), íconos SVG en línea, PNG
  de la PWA generados por `scripts/generate_icons.py` (sin dependencias externas), y GZip en el
  servidor. No hay pipeline de imágenes: exporte desde Figma en SVG o WebP ya optimizados.

## 6. Íconos

- **Íconos de interfaz**: SVG **en línea** en `index.html`, al estilo de Lucide/Feather: rejilla de 24,
  `fill="none" stroke="currentColor"`, `stroke-width` de 2 a 2.4, extremos redondeados y
  `aria-hidden="true"`. Heredan el color del texto. Exporte desde Figma con ese mismo formato:

  ```html
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>
  ```

- **Íconos por amenaza** en la tarjeta de nivel: un SVG por amenaza con la clase `ico ico-{AMENAZA}`
  (`ALUVION`, `DESLIZAMIENTO`, `DESBORDE`, `TORMENTA`, `NONE`, `SIN_DATOS`). El CSS muestra sólo el
  que coincide con `.hero[data-threat]`. Un ícono de amenaza nuevo sigue esa convención de nombre.
- **Íconos de la app**: `icons/icon.svg` (fuente) y los PNG 192, 512, maskable 512, apple-touch y
  badge-72, generados con `python scripts/generate_icons.py`.
- **Marcadores de mapa**: `L.circleMarker` o `L.divIcon` con colores de `config.js`. Los puntos
  seguros oficiales son círculos y los refugios son cuadrados.

## 7. Estilos y diseño responsivo

- **Metodología**: clases semánticas planas en kebab-case (`.hero-title`, `.row-text`), sin
  BEM estricto. Las secciones de `styles.css` se separan con comentarios
  `/* ------------- nombre */`. Agregue estilos nuevos en la sección que corresponda.
- **Globales**: `box-sizing: border-box`, foco visible (`:focus-visible`, 3px `--focus`),
  `[hidden] { display:none !important }`, clases de ayuda `.muted`, `.small`, `.visually-hidden`
  y `.skip-link`.
- **Responsivo, pensado primero para el celular y en una columna**: `main` y `.topbar` tienen
  `max-width: 560px` centrado. Hay un único breakpoint (`@media (min-width: 600px)`) para la barra
  de pestañas, y se respetan las zonas seguras (`env(safe-area-inset-*)`). Las cuadrículas internas
  usan `grid-template-columns: repeat(n, minmax(0, 1fr))`. Diseñe en Figma con marcos de 360–400px.
- **Accesibilidad, reglas de la propia hoja**:
  - Texto de 15px o más.
  - Botones de al menos 48px de alto (los principales, 64px).
  - **El color nunca es la única señal**: cada nivel lleva también una palabra.
  - Se respeta `prefers-reduced-motion`.
- **Sólo tema claro** (`color-scheme: light`). No hay modo oscuro.

## 8. Lista de verificación para implementar un diseño de Figma

1. Los colores salen de `var(--…)`; si aparece uno nuevo, agréguelo a `:root` (y a `config.js` si
   lo usa el mapa o JavaScript).
2. Reutilice `.card`, `.row`, `.btn-primary`, `.level-pill` y los demás componentes antes de crear clases.
3. El marcado estático va en `index.html` y lo dinámico se crea con `el()` en `ui.js`. Nada de `innerHTML`
   con datos del servidor.
4. Íconos como SVG en línea con `currentColor`.
5. Archivo estático nuevo: agréguelo al precache de `sw.js` y suba `VERSION`.
6. Revise el resultado a 360px de ancho y con `body[data-level]` en los cuatro niveles.
7. Revise `node --check` en los JS tocados y corra `pytest` si cambió algo del backend.
