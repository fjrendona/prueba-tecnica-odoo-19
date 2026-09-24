from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from .common import SalesImportCommon


@tagged("post_install", "-at_install")
class TestSalesImportRecord(SalesImportCommon):

    def test_create_valid_sale(self):
        sale = self.env["sales.import.record"].create(self._sale_values())
        self.assertEqual(sale.amount_total, 100000)
        self.assertEqual(sale.currency_id, self.company.currency_id)
        self.assertIn("SIA Cliente A", sale.display_name)

    @mute_logger("odoo.sql_db")
    def test_quantity_must_be_positive(self):
        for quantity in (0, -1):
            with self.subTest(quantity=quantity), self.assertRaises(IntegrityError):
                self.env["sales.import.record"].create(
                    self._sale_values(quantity=quantity, amount_total=100000)
                )
                self.env.flush_all()

    @mute_logger("odoo.sql_db")
    def test_amount_total_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            self.env["sales.import.record"].create(
                self._sale_values(quantity=2, price_unit=0, amount_total=0)
            )
            self.env.flush_all()

    def test_amount_total_must_match_quantity_by_price(self):
        with self.assertRaises(ValidationError):
            self.env["sales.import.record"].create(self._sale_values(amount_total=90000))

    def test_amount_total_accepts_currency_rounding(self):
        # 3 × 0,333 = 0,999, que redondeado a la moneda (0,01) es 1,00.
        sale = self.env["sales.import.record"].create(
            self._sale_values(quantity=3, price_unit=0.333, amount_total=1.0)
        )
        self.assertEqual(sale.amount_total, 1.0)

    def test_constraints_apply_on_write(self):
        sale = self.env["sales.import.record"].create(self._sale_values())
        with self.assertRaises(ValidationError):
            sale.quantity = 3

    @mute_logger("odoo.sql_db")
    def test_salesperson_name_is_unique_case_insensitive(self):
        with self.assertRaises(IntegrityError):
            self.env["sales.import.salesperson"].create({"name": "sia juan pérez"})
            self.env.flush_all()

    def test_multi_company_isolation(self):
        other_company = self.env["res.company"].create({"name": "Otra compañía"})
        sale = self.env["sales.import.record"].create(self._sale_values())
        other_user = new_test_user(
            self.env,
            login="sales_import_other_company",
            groups="sales_import_analytics.group_user",
            company_id=other_company.id,
            company_ids=[other_company.id],
        )
        visible = self.env["sales.import.record"].with_user(other_user).search([])
        self.assertNotIn(sale, visible)
        self.assertIn(sale, self.env["sales.import.record"].with_user(self.user_importer).search([]))

    def test_dashboard_aggregates_confirmed_sales_by_month(self):
        Sale = self.env["sales.import.record"]
        Sale.create([
            self._sale_values(date="2026-06-01", state="confirmed"),
            self._sale_values(date="2026-06-15", state="confirmed", quantity=1, amount_total=50000),
            self._sale_values(date="2026-06-20", state="cancelled"),
            self._sale_values(date="2026-06-21", state="draft"),
            self._sale_values(date="2026-07-02", state="confirmed"),
        ])
        groups = Sale._read_group(
            # Acotado al cliente del test: la BD puede tener ventas reales de esas fechas.
            [("state", "=", "confirmed"), ("partner_id", "=", self.partner.id)],
            ["date:month"],
            ["amount_total:sum", "__count"],
        )
        totals = {month.month: (amount, count) for month, amount, count in groups}
        self.assertEqual(totals, {6: (150000, 2), 7: (100000, 1)})
