import base64
import time
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..wizard import sales_import_wizard
from .common import VALID_ROW, SalesImportCommon, build_xlsx


def sale_row(partner="SIA Cliente A", salesperson="SIA Juan Pérez", product="SIA Producto 1", state="Confirmada",
             quantity=2, price_unit=50000):
    return ["2026-06-01", partner, salesperson, product, quantity, price_unit, quantity * price_unit, state]


@tagged("post_install", "-at_install")
class TestSalesImportWizard(SalesImportCommon):

    def _import(self, rows, user=None, file_name="ventas.xlsx", **wizard_values):
        return self._import_content(build_xlsx(rows), user, file_name, **wizard_values)

    def _import_content(self, content, user=None, file_name="ventas.xlsx", **wizard_values):
        wizard = self.env["sales.import.wizard"].with_user(user or self.manager_importer).create({
            "file": base64.b64encode(content),
            "file_name": file_name,
            **wizard_values,
        })
        return wizard.action_import()

    def _batch(self, file_name="ventas.xlsx"):
        return self.env["sales.import.batch"].search([("name", "=", file_name)], order="id desc", limit=1)

    # Flujo feliz (US1)

    def test_import_creates_batch_and_sales(self):
        rows = [sale_row(partner=f"SIA Cliente {index}", product=f"SIA Producto {index % 3}") for index in range(20)]
        action = self._import(rows)

        batch = self._batch()
        self.assertEqual(batch.record_count, 20)
        self.assertEqual(len(batch.record_ids), 20)
        self.assertEqual(batch.new_partner_count, 20)
        self.assertEqual(batch.new_product_count, 2)  # "SIA Producto 1" ya existe
        self.assertEqual(batch.new_salesperson_count, 0)
        self.assertEqual(batch.create_uid, self.manager_importer)
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("20 ventas importadas", action["params"]["message"])
        self.assertEqual(action["params"]["next"]["domain"], [("batch_id", "=", batch.id)])

    def test_name_variants_resolve_to_a_single_partner(self):
        self._import([sale_row(partner=name) for name in ("SIA Cliente B", "sia cliente b ", "SIA CLIENTE  B")])
        partners = self._batch().record_ids.partner_id
        self.assertEqual(len(partners), 1)
        self.assertEqual(partners.name, "SIA Cliente B")

    def test_existing_masters_are_reused(self):
        self._import([sale_row(partner="sia cliente a", product="sia-p-001", salesperson="SIA JUAN PÉREZ")])
        sale = self._batch().record_ids
        self.assertEqual(sale.partner_id, self.partner)
        self.assertEqual(sale.product_id, self.product)  # por referencia interna
        self.assertEqual(sale.salesperson_id, self.salesperson)
        self.assertEqual(self._batch().new_partner_count + self._batch().new_product_count, 0)

    def test_product_found_by_name_when_code_does_not_match(self):
        self._import([sale_row(product="sia producto 1")])
        self.assertEqual(self._batch().record_ids.product_id, self.product)

    def test_new_salesperson_links_matching_user_and_never_creates_users(self):
        users_before = self.env["res.users"].search_count([])
        self._import([sale_row(salesperson=self.user_importer.name), sale_row(salesperson="SIA Ana Gómez")])
        salespersons = self._batch().record_ids.salesperson_id
        self.assertEqual(
            {person.name: person.user_id for person in salespersons},
            {self.user_importer.name: self.user_importer, "SIA Ana Gómez": self.env["res.users"]},
        )
        self.assertEqual(self.env["res.users"].search_count([]), users_before)

    def test_new_product_is_minimal_consumable(self):
        list_price = self.product.list_price
        self._import([sale_row(product="SIA Producto Nuevo")])
        product = self._batch().record_ids.product_id
        self.assertEqual((product.name, product.type), ("SIA Producto Nuevo", "consu"))
        self.assertEqual(self.product.list_price, list_price)

    def test_user_imports_when_all_masters_exist(self):
        self._import([sale_row(salesperson="SIA Vendedor Nuevo")], user=self.user_importer)
        batch = self._batch()
        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.new_salesperson_count, 1)

    # Errores (US2)

    def test_invalid_rows_create_nothing_and_report_everything(self):
        rows = [sale_row() for _index in range(20)]
        rows[3][7] = "Pendiente"
        rows[10][4] = 0
        rows[15][0] = None
        sales_before = self.env["sales.import.record"].search_count([])
        with self.assertRaises(UserError) as error:
            self._import(rows)
        text = str(error.exception)
        for fragment in ("Fila 5 · Estado", "Fila 12 · Cantidad", "Fila 17 · Fecha"):
            self.assertIn(fragment, text)
        self.assertEqual(self.env["sales.import.record"].search_count([]), sales_before)
        self.assertFalse(self._batch())

    def test_error_list_is_truncated(self):
        with patch.object(sales_import_wizard, "MAX_REPORTED_ERRORS", 2), self.assertRaises(UserError) as error:
            self._import([sale_row(state="X")] * 5)
        self.assertIn("… y 3 errores más.", str(error.exception))

    def test_file_size_limit(self):
        with patch.object(sales_import_wizard, "MAX_FILE_SIZE", 10), self.assertRaises(UserError) as error:
            self._import([VALID_ROW])
        self.assertIn("MB", str(error.exception))

    def test_file_extension_must_be_xlsx(self):
        with self.assertRaises(UserError) as error:
            self._import([VALID_ROW], file_name="ventas.xlsm")
        self.assertIn(".xlsx", str(error.exception))

    def test_archived_salesperson_is_reused_not_recreated(self):
        self.salesperson.active = False
        self._import([VALID_ROW])
        self.assertEqual(self._batch().record_ids.salesperson_id, self.salesperson)
        self.assertEqual(self._batch().new_salesperson_count, 0)

    def test_invalid_file_is_rejected(self):
        with self.assertRaises(UserError) as error:
            self._import_content(b"esto no es un excel")
        self.assertIn("no es un Excel", str(error.exception))

    def test_duplicate_file_is_blocked_unless_forced(self):
        content = build_xlsx([VALID_ROW])
        self._import_content(content)
        first_batch = self._batch()

        wizard = self.env["sales.import.wizard"].create({"file": base64.b64encode(content)})
        self.assertEqual(wizard.duplicate_batch_id, first_batch)
        with self.assertRaises(UserError) as error:
            self._import_content(content)
        self.assertIn("ya se importó", str(error.exception))

        self._import_content(content, force_reimport=True)
        self.assertEqual(self.env["sales.import.batch"].search_count([("file_hash", "=", first_batch.file_hash)]), 2)

    def test_resaved_file_with_same_sales_is_detected_as_duplicate(self):
        # Mismas ventas, otros bytes: columna extra, filas en otro orden y otras
        # mayúsculas, como al abrir y volver a guardar el archivo.
        rows = [sale_row(partner="SIA Cliente R"), sale_row(product="SIA Producto R", quantity=3)]
        self._import(rows)
        resaved = build_xlsx(
            [[*row, "nota"] for row in [rows[1], [*rows[0][:1], "sia cliente r", *rows[0][2:]]]],
            headers=["Fecha", "Cliente", "Vendedor", "Producto", "Cantidad", "Valor Unitario",
                     "Valor Total", "Estado", "Observaciones"],
        )
        self.assertNotEqual(resaved, build_xlsx(rows))
        with self.assertRaises(UserError) as error:
            self._import_content(resaved, file_name="ventas_reguardado.xlsx")
        self.assertIn("ya se importó", str(error.exception))

    def test_different_sales_are_not_duplicates(self):
        self._import([sale_row(quantity=1)])
        self._import([sale_row(quantity=2)], file_name="otro_mes.xlsx")
        self.assertEqual(self._batch("otro_mes.xlsx").record_count, 1)

    def test_user_cannot_create_partners_or_products(self):
        salespersons_before = self.env["sales.import.salesperson"].search_count([])
        with self.assertRaises(UserError) as error:
            self._import(
                [sale_row(partner="SIA Cliente Z", product="SIA Producto Z", salesperson="SIA Vendedor Z")],
                user=self.user_importer,
            )
        text = str(error.exception)
        self.assertIn("El cliente «SIA Cliente Z» no existe; solo un Responsable puede crearlo.", text)
        self.assertIn("El producto «SIA Producto Z» no existe; solo un Responsable puede crearlo.", text)
        self.assertFalse(self.env["res.partner"].search([("name", "=", "SIA Cliente Z")]))
        self.assertEqual(self.env["sales.import.salesperson"].search_count([]), salespersons_before)

    # Borrado de lotes (FR-015)

    def test_manager_deleting_batch_deletes_its_sales(self):
        self._import([VALID_ROW, VALID_ROW])
        batch = self._batch()
        sales = batch.record_ids
        batch.with_user(self.manager_importer).unlink()
        self.assertFalse(sales.exists())

    def test_user_cannot_delete_batches_or_sales(self):
        self._import([VALID_ROW])
        batch = self._batch()
        with self.assertRaises(AccessError):
            batch.with_user(self.user_importer).unlink()
        with self.assertRaises(AccessError):
            batch.record_ids.with_user(self.user_importer).unlink()

    # Rendimiento (SC-002)

    def test_ten_thousand_rows_import_in_under_thirty_seconds(self):
        content = build_xlsx([sale_row()] * 10_000)
        start = time.monotonic()
        self._import_content(content)
        elapsed = time.monotonic() - start
        self.assertEqual(self._batch().record_count, 10_000)
        self.assertLess(elapsed, 30, f"La importación de 10.000 filas tardó {elapsed:.1f} s")
