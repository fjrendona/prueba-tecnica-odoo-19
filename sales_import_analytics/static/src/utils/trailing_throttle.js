/**
 * Throttle con disparo al inicio y al final (leading + trailing).
 *
 * - La primera llamada con el throttle en reposo ejecuta `callback` al instante
 *   y abre una ventana de `interval` ms.
 * - Las llamadas dentro de la ventana solo la marcan como pendiente.
 * - Al cerrarse la ventana, si hubo llamadas pendientes se ejecuta `callback`
 *   una vez y se abre otra ventana.
 *
 * Resultado: una ráfaga produce como máximo dos ejecuciones, un flujo sostenido
 * produce una por ventana mientras dure, y la última llamada siempre termina en
 * una ejecución. Un debounce simple no sirve aquí: con llamadas continuas
 * reinicia su temporizador indefinidamente y no ejecuta nunca.
 *
 * @param {() => void} callback
 * @param {number} interval milisegundos por ventana
 * @returns {{ call: () => void, cancel: () => void }}
 */
export function createTrailingThrottle(callback, interval) {
    let timeoutId = null;
    let pending = false;

    function openWindow() {
        timeoutId = setTimeout(closeWindow, interval);
    }

    function closeWindow() {
        timeoutId = null;
        if (pending) {
            pending = false;
            run();
        }
    }

    // La ventana se abre antes de ejecutar: si `callback` vuelve a llamar a
    // `call()` (p. ej. para reintentar), queda como pendiente y no se reentra.
    function run() {
        openWindow();
        callback();
    }

    return {
        call() {
            if (timeoutId !== null) {
                pending = true;
                return;
            }
            run();
        },
        cancel() {
            clearTimeout(timeoutId);
            timeoutId = null;
            pending = false;
        },
    };
}
