# Cambios

Registro de versiones de SAT-Chile. La versión es la del Service Worker (`VERSION` en
`src/presentation/pwa/sw.js`): al subirla, los celulares descargan la interfaz nueva.
Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/), versionado semántico.

## [2.11.0] · 2026-10-20

### Cambiado
- Inicio rediseñado, ordenado de lo más urgente a lo de consulta:
  1. La tarjeta de nivel muestra el rótulo de la amenaza y una píldora con el nivel.
  2. La nueva tarjeta «Qué hacer ahora» muestra 3 pasos numerados. Sin alerta, muestra «Para estar preparado».
  3. Acceso a la ruta segura, más dos accesos rápidos: lluvia por hora y reportar crecida.
  4. Alertas oficiales agrupadas en «En su comuna», «En toda la región» y, plegadas,
     «Otras amenazas en la región».
  5. Tarjeta «Números de emergencia» con los 6 números y el teléfono municipal.
- Una alerta de SENAPRED para toda la región, cuando su punto está en verde, se muestra como aviso aparte
  y no cambia el color de la tarjeta de nivel.
- La lista «Emergencias cerca de usted» se movió a Punto seguro.

### Quitado
- Los dos botones de llamada sueltos (133, 132) de Inicio: ahora están en la tarjeta de números.

## [2.10.0] · 2026-10-20

### Cambiado
- Solo fija el nivel la alerta amarilla o roja de SENAPRED que nombra la comuna y es de lluvia, crecida,
  aluvión o tormenta. Las regionales se informan y las de otras amenazas no cambian el nivel.
- El filtro hidrológico baja un nivel por cada condición atenuante (suelo seco, isoterma baja) en vez de anular
  la alerta.
- En Monitoreo, el medidor de lluvia usa los mismos umbrales que la fila de desborde.
- Menos recordatorios push (AMARILLO 12 h, NARANJA 3 h, ROJO 1 h). El aviso de aluvión solo llega a quien vive
  cerca de una quebrada.

### Corregido
- El refresco automático ya no recentra el mapa ni borra los refugios buscados.
- Si no hay refugios a menos de 10 km, se muestran los más cercanos (hasta 100 km).
- Texto duplicado «este minuto · este minuto» en las alertas oficiales.
