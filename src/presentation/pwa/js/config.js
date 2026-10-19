// Configuración y textos estáticos. Este archivo queda en caché: todo lo de aquí funciona sin conexión.

export const LEVEL_ORDER = Object.freeze(['VERDE', 'AMARILLO', 'NARANJA', 'ROJO']);

export const LEVELS = Object.freeze({
  VERDE: { word: 'VERDE', color: '#1E7F4F' },
  AMARILLO: { word: 'AMARILLO', color: '#F2C230' },
  NARANJA: { word: 'NARANJA', color: '#E8772A' },
  ROJO: { word: 'ROJO', color: '#C62828' },
  SIN_DATOS: { word: 'SIN DATOS', color: '#5A5F66' },
});

export const THREAT_LABELS = Object.freeze({
  ALUVION: 'ALUVIÓN',
  DESLIZAMIENTO: 'DESLIZAMIENTO',
  DESBORDE: 'DESBORDE DE RÍO',
  TORMENTA: 'TORMENTA ELÉCTRICA',
});

// Texto principal de la tarjeta de nivel y pasos cortos, por amenaza y nivel.
export const THREAT_TEXT = Object.freeze({
  ALUVION: {
    AMARILLO: {
      word: 'Atención a la quebrada',
      title: 'Llueve en altura. La quebrada puede crecer rápido.',
      steps: ['Prepare su mochila de emergencia', 'No acampe ni se estacione junto a quebradas', 'Si la quebrada suena distinto, aléjese'],
    },
    NARANJA: {
      word: 'Prepárese para salir',
      title: 'Lluvia fuerte en la cordillera. Puede bajar un aluvión.',
      steps: ['Tenga lista su mochila y documentos', 'Revise su ruta hacia terreno alto', 'Si oye un ruido fuerte, salga de inmediato'],
    },
    ROJO: {
      word: 'Aléjese de la quebrada',
      title: 'Riesgo alto de aluvión. Suba a terreno alto ahora.',
      steps: ['Suba ahora a terreno alto, lejos del cauce', 'No espere ver el barro: llega en minutos', 'No cruce quebradas, vados ni puentes'],
    },
  },
  DESLIZAMIENTO: {
    AMARILLO: {
      word: 'Atención a la ladera',
      title: 'La lluvia puede soltar tierra y piedras en su sector.',
      steps: ['Fíjese en grietas, ruidos o árboles inclinados', 'Evite caminos bajo cortes de cerro', 'Prepare su mochila de emergencia'],
    },
    NARANJA: {
      word: 'Prepárese para salir',
      title: 'Lluvia fuerte sobre terreno empinado. Puede haber deslizamientos.',
      steps: ['Tenga lista su mochila y documentos', 'Aléjese de taludes y laderas', 'Si ve grietas o tierra moviéndose, salga'],
    },
    ROJO: {
      word: 'Aléjese de la ladera',
      title: 'Riesgo alto de deslizamiento. Vaya a terreno plano y firme.',
      steps: ['Salga ahora hacia terreno plano y firme', 'No pase bajo cortes de cerro ni taludes', 'Avise a sus vecinos de la ladera'],
    },
  },
  DESBORDE: {
    AMARILLO: {
      word: 'Esté atento',
      title: 'Se espera lluvia fuerte. El río puede subir.',
      steps: ['Prepare su mochila de emergencia', 'No cruce ríos ni esteros', 'Revise esta app cada hora'],
    },
    NARANJA: {
      word: 'Prepárese para salir',
      title: 'El río podría desbordarse en las próximas horas.',
      steps: ['Tenga lista su mochila y documentos', 'Ayude a vecinos mayores o con niños', 'Si el agua sube, vaya al punto seguro'],
    },
    ROJO: {
      word: 'Evacúe ahora',
      title: 'Riesgo alto de desborde. Salga hacia terreno alto.',
      steps: ['Salga ahora hacia el punto seguro', 'No cruce cauces ni puentes', 'Lleve documentos y remedios'],
    },
  },
  TORMENTA: {
    AMARILLO: {
      word: 'Tormenta eléctrica',
      title: 'Posibles rayos en las próximas horas.',
      steps: ['Evite cerros descubiertos y árboles solos', 'Si oye truenos, entre a una casa o auto', 'Desconecte aparatos eléctricos'],
    },
  },
});

export const CALM_STEPS = Object.freeze(['Conozca su ruta hacia terreno alto', 'Tenga a mano su mochila de emergencia']);

export const NO_DATA = Object.freeze({
  word: 'Sin datos',
  title: 'Active su ubicación para conocer su riesgo.',
  steps: [
    'Toque «Activar protección» y permita el acceso a su ubicación',
    'Si oye un ruido fuerte en una quebrada, aléjese sin esperar una alerta',
  ],
});

// Indicación para el camino al punto seguro, según la amenaza.
export const ROUTE_HINTS = Object.freeze({
  ALUVION: 'Suba hacia terreno alto lateral, nunca quebrada abajo. No cruce el cauce.',
  DESLIZAMIENTO: 'Vaya por terreno plano. No pase bajo laderas ni cortes de camino.',
  DESBORDE: 'Camine alejándose del río. No cruce puentes ni calles con agua.',
  TORMENTA: 'Si hay truenos, espere bajo techo antes de salir.',
});

// Números nacionales de emergencia de Chile.
export const PHONES = Object.freeze([
  { number: '131', who: 'Ambulancia (SAMU)' },
  { number: '132', who: 'Bomberos' },
  { number: '133', who: 'Carabineros' },
  { number: '134', who: 'PDI' },
  { number: '130', who: 'CONAF (incendios)' },
  { number: '137', who: 'Rescate marítimo' },
]);

export const WATERCOURSE_KINDS = Object.freeze({
  RIO: { label: 'Río', color: '#1F6FB2', weight: 5, dash: null },
  ESTERO: { label: 'Estero', color: '#2A8FB0', weight: 3, dash: null },
  QUEBRADA: { label: 'Quebrada (riesgo de aluvión)', color: '#C2410C', weight: 3, dash: '6 5' },
  CANAL: { label: 'Canal de riego', color: '#7C5CC4', weight: 2, dash: null },
});

export const MODEL_NAMES = Object.freeze({
  ecmwf_ifs025: 'ECMWF',
  icon_seamless: 'ICON',
  gfs_seamless: 'GFS',
});

export const SUSCEPTIBILITY = Object.freeze({ BAJA: 'Baja', MEDIA: 'Media', ALTA: 'Alta' });

export const MISSING_SOURCE_LABELS = Object.freeze({
  pronostico: 'pronóstico del tiempo',
  elevacion: 'altura del terreno',
  zonas: 'zonas monitoreadas',
  cauces: 'ríos cercanos',
  alertas: 'alertas vigentes',
  puntos_seguros: 'puntos seguros',
  pendiente: 'pendiente del terreno',
  caudal: 'caudal del río',
  probabilidades: 'probabilidades (ensemble)',
});

export const FLOW_TRENDS = Object.freeze({
  SUBE_FUERTE: 'Sube fuerte',
  SUBE: 'Sube',
  ESTABLE: 'Estable',
  BAJA: 'Baja',
});

// Ráfagas (km/h) para la fila «Viento fuerte». Orientativo: no genera alertas push.
export const WIND_GUST_LEVELS = Object.freeze([
  { min: 90, level: 'ROJO' },
  { min: 70, level: 'NARANJA' },
  { min: 50, level: 'AMARILLO' },
]);

export const SERVICE_KINDS = Object.freeze({
  CARABINEROS: 'Carabineros',
  BOMBEROS: 'Bomberos',
  SALUD: 'Salud (urgencia)',
  MUNICIPALIDAD: 'Municipalidad',
  PUNTO_ENCUENTRO: 'Punto de encuentro',
  ALBERGUE: 'Albergue',
});

export const HAIL_SIZES = Object.freeze({
  PEQUENO: 'pequeño (menos de 1 cm)',
  MEDIANO: 'mediano (1 a 2 cm)',
  GRANDE: 'grande (más de 2 cm): proteja autos y techos',
});

export const CHILE_CENTER = [-35.45, -71.65];
export const WALK_KMH = 4.5;
export const REFRESH_INTERVAL_MS = 3 * 60 * 1000; // alertas SENAPRED casi en tiempo real
export const REPORT_QUEUE_MAX_AGE_MS = 30 * 60 * 1000;

// Opciones del formulario de reporte → nivel que entiende la API y descripción por defecto.
export const REPORT_OPTIONS = Object.freeze({
  barro: { observed: 'DESBORDE', text: 'Baja barro o piedras por la quebrada (posible aluvión)' },
  sube: { observed: 'ALTO', text: 'El agua está subiendo, más alta de lo normal' },
  borde: { observed: 'ALTO', text: 'El agua llegó al borde del cauce' },
  inunda: { observed: 'DESBORDE', text: 'Hay calles o casas con agua' },
});

// Vibración en primer plano (en segundo plano la aplica el Service Worker).
export const VIBRATION = Object.freeze({
  ROJO: [500, 200, 500, 200, 500, 600, 1000, 200, 1000],
  NARANJA: [400, 200, 400, 200, 400],
  AMARILLO: [200, 100, 200],
});
