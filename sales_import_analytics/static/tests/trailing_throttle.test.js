import { advanceTime, describe, expect, test } from "@odoo/hoot";
import { debounce } from "@web/core/utils/timing";

import { createTrailingThrottle } from "@sales_import_analytics/utils/trailing_throttle";

describe.current.tags("desktop");

const INTERVAL = 2000;

function countingThrottle() {
    const counter = { runs: 0 };
    counter.throttle = createTrailingThrottle(() => counter.runs++, INTERVAL);
    return counter;
}

/** Llama a `fn` cada `every` ms durante `duration` ms (tiempo simulado). */
async function callRepeatedly(fn, every, duration) {
    for (let elapsed = 0; elapsed < duration; elapsed += every) {
        fn();
        await advanceTime(every);
    }
}

test("the first call runs immediately", async () => {
    const counter = countingThrottle();
    counter.throttle.call();
    expect(counter.runs).toBe(1);
});

test("a burst runs at most twice: leading and trailing", async () => {
    const counter = countingThrottle();
    for (let index = 0; index < 200; index++) {
        counter.throttle.call(); // 200 avisos en el mismo tick
    }
    expect(counter.runs).toBe(1); // leading
    await advanceTime(INTERVAL);
    expect(counter.runs).toBe(2); // trailing
    await advanceTime(INTERVAL * 3);
    expect(counter.runs).toBe(2); // nada más tras la ráfaga
});

test("a sustained stream keeps running once per window", async () => {
    const counter = countingThrottle();
    await callRepeatedly(() => counter.throttle.call(), 300, 10_000);
    expect(counter.runs).toBeGreaterThan(4); // 10 s / 2 s
});

test("the last call of a sequence always ends in a run", async () => {
    const counter = countingThrottle();
    counter.throttle.call();
    await advanceTime(500);
    counter.throttle.call();
    expect(counter.runs).toBe(1);
    await advanceTime(INTERVAL);
    expect(counter.runs).toBe(2);
});

test("cancel drops the pending run", async () => {
    const counter = countingThrottle();
    counter.throttle.call();
    counter.throttle.call();
    counter.throttle.cancel();
    await advanceTime(INTERVAL * 3);
    expect(counter.runs).toBe(1);
});

test("a call made from inside the callback is deferred, not re-entered", async () => {
    let runs = 0;
    const throttle = createTrailingThrottle(() => {
        runs++;
        if (runs < 3) {
            throttle.call(); // p. ej. reintentar porque hay una edición en curso
        }
    }, INTERVAL);
    throttle.call();
    expect(runs).toBe(1);
    await advanceTime(INTERVAL);
    expect(runs).toBe(2);
    await advanceTime(INTERVAL);
    expect(runs).toBe(3);
});

test("contrast: a plain debounce never runs during a sustained stream", async () => {
    let runs = 0;
    const debounced = debounce(() => runs++, 1000);
    await callRepeatedly(debounced, 300, 10_000);
    expect(runs).toBe(0); // inanición: el temporizador se reinicia en cada llamada
    await advanceTime(1000);
    expect(runs).toBe(1); // solo cuando el flujo se detiene
});
