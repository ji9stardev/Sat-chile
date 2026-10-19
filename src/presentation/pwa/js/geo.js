// Geolocalización con mensajes claros para cada tipo de error.

const ERROR_MESSAGES = {
  1: 'Permiso de ubicación denegado. Actívelo en los ajustes del navegador para este sitio y vuelva a intentar.',
  2: 'No se pudo determinar su ubicación. Active el GPS o salga a un lugar más abierto.',
  3: 'El GPS tardó demasiado. Intente nuevamente.',
};

/**
 * @returns {Promise<{lat: number, lon: number, accuracy: number}>}
 */
export function getPosition({ highAccuracy = true, timeoutMs = 20000, maxAgeMs = 60000 } = {}) {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new Error('Este navegador no permite obtener la ubicación.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          accuracy: position.coords.accuracy,
        }),
      (error) => reject(new Error(ERROR_MESSAGES[error.code] ?? 'No se pudo obtener su ubicación.')),
      { enableHighAccuracy: highAccuracy, timeout: timeoutMs, maximumAge: maxAgeMs },
    );
  });
}
