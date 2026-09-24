import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { advanceTime, describe, destroy, expect, test } from "@odoo/hoot";
import { animationFrame, click, press } from "@odoo/hoot-dom";
import {
    defineModels,
    fields,
    models,
    mountView,
    MockServer,
    onRpc,
    serverState,
} from "@web/../tests/web_test_helpers";

import {
    NEW_RECORDS_NOTIFICATION,
    RELOAD_INTERVAL_MS,
} from "@sales_import_analytics/views/sales_realtime_list/sales_realtime_list_view";

class SalesImportRecord extends models.Model {
    _name = "sales.import.record";

    name = fields.Char();

    _records = [
        { id: 1, name: "Venta 1" },
        { id: 2, name: "Venta 2" },
    ];
}

// `product` depende de `mail`, así que el bundle de tests carga sus servicios y
// necesita sus modelos (incluye los del bus).
defineMailModels();
defineModels([SalesImportRecord]);
describe.current.tags("desktop");

const LIST_ARCH = `<list js_class="sales_import_realtime_list"><field name="name"/></list>`;

function notifyNewRecords() {
    MockServer.env["bus.bus"]._sendone(serverState.partnerId, NEW_RECORDS_NOTIFICATION, {});
}

async function mountRealtimeList(arch = LIST_ARCH) {
    onRpc("web_search_read", () => {
        expect.step("reload");
    });
    const view = await mountView({ type: "list", resModel: "sales.import.record", arch });
    await expect.waitForSteps(["reload"]); // carga inicial
    return view;
}

test("a notification reloads the native list", async () => {
    await mountRealtimeList();
    MockServer.env["sales.import.record"].create({ name: "Venta nueva" });
    notifyNewRecords();
    await expect.waitForSteps(["reload"]);
    await animationFrame(); // la recarga ya ocurrió; esperar a que se pinte
    expect(".o_data_row").toHaveCount(3);
});

test("a burst of notifications reloads at most twice", async () => {
    await mountRealtimeList();
    // Ráfaga: el servidor entrega los avisos acumulados en un mismo mensaje.
    const burst = Array.from({ length: 50 }, () => [
        serverState.partnerId,
        NEW_RECORDS_NOTIFICATION,
        {},
    ]);
    MockServer.env["bus.bus"]._sendmany(burst);
    await expect.waitForSteps(["reload"]); // leading
    await advanceTime(RELOAD_INTERVAL_MS);
    await expect.waitForSteps(["reload"]); // trailing
    await advanceTime(RELOAD_INTERVAL_MS * 3);
    await animationFrame();
    expect.verifySteps([]); // 50 avisos -> 2 recargas
});

test("after destroying the view nothing listens nor reloads", async () => {
    const view = await mountRealtimeList();
    notifyNewRecords();
    notifyNewRecords(); // deja una recarga pendiente en el throttle
    await expect.waitForSteps(["reload"]);

    destroy(view); // el usuario sale de la pantalla con un aviso pendiente
    await advanceTime(RELOAD_INTERVAL_MS * 3);
    notifyNewRecords();
    await advanceTime(RELOAD_INTERVAL_MS * 3);
    expect.verifySteps([]);
});

test("a row selection is not lost by a notification", async () => {
    await mountRealtimeList();
    await click(".o_data_row:first-child .o_list_record_selector input");
    await animationFrame();
    expect(".o_data_row .o_list_record_selector input:checked").toHaveCount(1);

    notifyNewRecords();
    await advanceTime(RELOAD_INTERVAL_MS * 2);
    expect.verifySteps([]); // aplazada mientras hay filas seleccionadas
    expect(".o_data_row .o_list_record_selector input:checked").toHaveCount(1);

    await click(".o_data_row:first-child .o_list_record_selector input"); // deselecciona
    await advanceTime(RELOAD_INTERVAL_MS);
    await expect.waitForSteps(["reload"]);
});

test("an inline edition is not discarded by a notification", async () => {
    await mountRealtimeList(
        `<list js_class="sales_import_realtime_list" editable="bottom"><field name="name"/></list>`
    );
    await click(".o_data_row:first-child .o_data_cell");
    await animationFrame();
    expect(".o_selected_row").toHaveCount(1);

    notifyNewRecords();
    await advanceTime(RELOAD_INTERVAL_MS * 2);
    expect.verifySteps([]); // aplazada mientras se edita
    expect(".o_selected_row").toHaveCount(1);

    await press("Escape"); // descarta la edición
    await advanceTime(RELOAD_INTERVAL_MS);
    await expect.waitForSteps(["reload"]);
});
