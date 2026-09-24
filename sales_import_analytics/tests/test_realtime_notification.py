import base64
from unittest.mock import patch

from odoo.tests import tagged

from ..models.sales_import_record import NEW_RECORDS_NOTIFICATION
from .common import VALID_ROW, SalesImportCommon, build_xlsx


@tagged("post_install", "-at_install")
class TestRealtimeNotification(SalesImportCommon):

    def _capture_bus(self):
        return patch.object(type(self.env["bus.bus"]), "_sendone", autospec=True)

    def test_one_notification_per_batch(self):
        with self._capture_bus() as sendone:
            self.env["sales.import.record"].create([self._sale_values()] * 50)
        sendone.assert_called_once()
        _bus, target, notification_type, payload = sendone.call_args.args
        self.assertEqual(target, self.env.ref("sales_import_analytics.group_user"))
        self.assertEqual(notification_type, NEW_RECORDS_NOTIFICATION)
        self.assertEqual(payload, {})

    def test_no_notification_without_records(self):
        with self._capture_bus() as sendone:
            self.env["sales.import.record"].create([])
        sendone.assert_not_called()

    def test_wizard_import_sends_a_single_notification(self):
        wizard = self.env["sales.import.wizard"].with_user(self.manager_importer).create({
            "file": base64.b64encode(build_xlsx([VALID_ROW] * 30)),
            "file_name": "realtime.xlsx",
        })
        with self._capture_bus() as sendone:
            wizard.action_import()
        self.assertEqual(sendone.call_count, 1)

    def test_users_outside_the_module_are_not_in_the_target_group(self):
        outsider = self.env["res.users"].create({"name": "Sin acceso", "login": "sia_outsider"})
        group = self.env.ref("sales_import_analytics.group_user")
        self.assertNotIn(group, outsider.all_group_ids)
        self.assertIn(group, self.manager_importer.all_group_ids)  # Responsable implica Usuario
