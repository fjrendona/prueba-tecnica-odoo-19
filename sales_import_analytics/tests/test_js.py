from odoo.tests import HttpCase, tagged

from odoo.addons.web.tests.test_js import HOOTCommon, unit_test_error_checker

HOOT_SUITE = "@sales_import_analytics"


@tagged("post_install", "-at_install")
class TestSalesImportAnalyticsJs(HttpCase):
    """Ejecuta en Chrome headless los tests hoot de static/tests/ de este módulo."""

    _generate_hash = HOOTCommon._generate_hash

    def test_hoot_suite(self):
        self.browser_js(
            f"/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={self._generate_hash(HOOT_SUITE)}",
            "",
            "",
            login="admin",
            timeout=600,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=unit_test_error_checker,
        )
