import io

import openpyxl

from odoo.tests import TransactionCase, new_test_user

SALES_HEADERS = [
    "Fecha", "Cliente", "Vendedor", "Producto",
    "Cantidad", "Valor Unitario", "Valor Total", "Estado",
]
VALID_ROW = ["2026-06-01", "SIA Cliente A", "SIA Juan Pérez", "SIA Producto 1", 2, 50000, 100000, "Confirmada"]


def build_xlsx(rows, headers=SALES_HEADERS) -> bytes:
    """Construye un .xlsx en memoria: cabeceras y filas tal cual."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    if headers:
        sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class SalesImportCommon(TransactionCase):
    """Datos mínimos: las BD de Odoo 19 no traen demo, así que todo se crea aquí."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.user_importer = new_test_user(
            cls.env, login="sales_import_user", groups="sales_import_analytics.group_user"
        )
        cls.manager_importer = new_test_user(
            cls.env, login="sales_import_manager", groups="sales_import_analytics.group_manager"
        )
        cls.partner = cls.env["res.partner"].create({"name": "SIA Cliente A"})
        cls.product = cls.env["product.product"].create(
            {"name": "SIA Producto 1", "default_code": "SIA-P-001", "type": "consu"}
        )
        cls.salesperson = cls.env["sales.import.salesperson"].create({"name": "SIA Juan Pérez"})

    @classmethod
    def _sale_values(cls, **overrides):
        values = {
            "date": "2026-06-01",
            "partner_id": cls.partner.id,
            "salesperson_id": cls.salesperson.id,
            "product_id": cls.product.id,
            "quantity": 2,
            "price_unit": 50000,
            "amount_total": 100000,
            "state": "confirmed",
        }
        values.update(overrides)
        return values
