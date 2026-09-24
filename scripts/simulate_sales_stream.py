"""Simula un flujo sostenido de ventas para ver la lista en tiempo real.

Crea una venta cada INTERVAL_SECONDS durante DURATION_SECONDS, con un commit
por venta, de modo que cada una genera su propio aviso de bus (como si llegaran
de otros usuarios o de una integración). Con la lista de ventas abierta en el
navegador, debe verse una recarga cada ~2 s mientras dure el flujo, no una por
venta y no ninguna hasta el final (que es lo que haría un debounce simple).

Uso (desde la raíz del repo):

    docker exec -i odoo_19 python3 /opt/odoo/odoo-bin shell -d odoo19_db \
        --db_host=db --db_user=odoo --db_password=odoo \
        --addons-path=/opt/odoo/addons,/mnt/extra-addons --http-port=8098 \
        < scripts/simulate_sales_stream.py
"""

import time

DURATION_SECONDS = 20
INTERVAL_SECONDS = 0.3

partner = env["res.partner"].search([("name", "=", "Cliente Streaming")], limit=1) or env[
    "res.partner"
].create({"name": "Cliente Streaming"})
product = env["product.product"].search([("name", "=", "Producto Streaming")], limit=1) or env[
    "product.product"
].create({"name": "Producto Streaming", "type": "consu"})
salesperson = env["sales.import.salesperson"].search(
    [("name", "=", "Vendedor Streaming")], limit=1
) or env["sales.import.salesperson"].create({"name": "Vendedor Streaming"})
env.cr.commit()

created = 0
deadline = time.monotonic() + DURATION_SECONDS
while time.monotonic() < deadline:
    env["sales.import.record"].create({
        "date": "2026-06-30",
        "partner_id": partner.id,
        "salesperson_id": salesperson.id,
        "product_id": product.id,
        "quantity": 1,
        "price_unit": 1000,
        "amount_total": 1000,
        "state": "confirmed",
    })
    env.cr.commit()  # el aviso de bus sale al hacer commit
    created += 1
    time.sleep(INTERVAL_SECONDS)

print(f"{created} ventas creadas en {DURATION_SECONDS} s")
