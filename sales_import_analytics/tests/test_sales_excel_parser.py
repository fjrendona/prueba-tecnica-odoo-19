import datetime
import io
import zipfile
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests import TransactionCase

from ..tools import sales_excel_parser
from ..tools.sales_excel_parser import AmountRules, content_fingerprint, parse_sales_workbook
from .common import SALES_HEADERS, VALID_ROW, build_xlsx


def exact_amounts(expected, received):
    return abs(expected - received) < 0.005


# Mismas reglas que aplica el asistente con una moneda de 2 decimales.
RULES = AmountRules(round_quantity=lambda quantity: round(quantity, 2), amounts_match=exact_amounts)


def parse(rows, headers=SALES_HEADERS):
    return parse_sales_workbook(build_xlsx(rows, headers), RULES)




class ParserCase(TransactionCase):
    """El parser es puro (no usa el ORM); el env solo se usa para traducir los
    mensajes, igual que hace el asistente."""

    def messages(self, result):
        return [error.render(self.env._) for error in result.errors]


@tagged("post_install", "-at_install")
class TestSalesExcelParserValid(ParserCase):

    def test_valid_row_is_typed(self):
        result = parse([VALID_ROW])
        self.assertFalse(result.errors)
        (sale,) = result.rows
        self.assertEqual(sale.row_number, 2)
        self.assertEqual(sale.date, datetime.date(2026, 6, 1))
        self.assertEqual(
            (sale.partner, sale.salesperson, sale.product),
            ("SIA Cliente A", "SIA Juan Pérez", "SIA Producto 1"),
        )
        self.assertEqual((sale.quantity, sale.price_unit, sale.amount_total), (2, 50000, 100000))
        self.assertEqual(sale.state, "confirmed")

    def test_headers_in_any_order_with_accents_case_and_extra_columns(self):
        headers = ["  ESTADO ", "valor  total", "Valor unitario", "cantidad", "Notas",
                   "producto", "VENDEDOR", "cliente", "fecha"]
        row = ["confirmada", 100000, 50000, 2, "ignorar", "SIA Producto 1", "SIA Juan Pérez", "SIA Cliente A",
               "2026-06-01"]
        result = parse([row], headers)
        self.assertFalse(result.errors)
        self.assertEqual(result.rows[0].amount_total, 100000)

    def test_date_formats(self):
        for value in (datetime.datetime(2026, 6, 1), "2026-06-01", "01/06/2026"):
            with self.subTest(value=value):
                row = [value, *VALID_ROW[1:]]
                self.assertEqual(parse([row]).rows[0].date, datetime.date(2026, 6, 1))

    def test_colombian_text_numbers(self):
        row = ["2026-06-01", "SIA Cliente A", "SIA Juan Pérez", "SIA Producto 1", "2,5", "1.234,50", "3.086,25",
               "Confirmada"]
        sale = parse([row]).rows[0]
        self.assertEqual((sale.quantity, sale.price_unit, sale.amount_total), (2.5, 1234.5, 3086.25))

    def test_state_variants(self):
        variants = {
            "Confirmada": "confirmed", "CONFIRMADO": "confirmed", "confirmed": "confirmed",
            "borrador": "draft", "Draft": "draft",
            "Cancelada": "cancelled", "anulada": "cancelled", "Canceled": "cancelled",
        }
        for text, state in variants.items():
            with self.subTest(text=text):
                self.assertEqual(parse([[*VALID_ROW[:7], text]]).rows[0].state, state)

    def test_empty_rows_are_ignored(self):
        result = parse([VALID_ROW, [None] * 8, ["  "] * 8, VALID_ROW])
        self.assertFalse(result.errors)
        self.assertEqual([sale.row_number for sale in result.rows], [2, 5])

    def test_text_is_trimmed_and_spaces_collapsed(self):
        row = ["2026-06-01", "  SIA  Cliente   A ", "SIA Juan  Pérez", "SIA Producto 1", 2, 50000, 100000,
               "Confirmada"]
        sale = parse([row]).rows[0]
        self.assertEqual((sale.partner, sale.salesperson), ("SIA Cliente A", "SIA Juan Pérez"))

    def test_content_fingerprint_ignores_order_and_name_case(self):
        other = [*VALID_ROW[:6], VALID_ROW[6], "Borrador"]
        first = parse([VALID_ROW, other]).rows
        second = parse([other, [VALID_ROW[0], VALID_ROW[1].upper(), *VALID_ROW[2:]]]).rows
        changed = parse([VALID_ROW, [*other[:7], "Cancelada"]]).rows
        self.assertEqual(content_fingerprint(first), content_fingerprint(second))
        self.assertNotEqual(content_fingerprint(first), content_fingerprint(changed))

    def test_header_row_can_start_below_blank_rows(self):
        workbook_rows = [[None] * 8, SALES_HEADERS, VALID_ROW]
        result = parse_sales_workbook(build_xlsx(workbook_rows, headers=None), RULES)
        self.assertFalse(result.errors)
        self.assertEqual(result.rows[0].row_number, 3)


@tagged("post_install", "-at_install")
class TestSalesExcelParserErrors(ParserCase):

    def assertSingleError(self, result, column, fragment):
        self.assertFalse(result.rows)
        self.assertEqual(len(result.errors), 1, self.messages(result))
        error = result.errors[0]
        self.assertEqual(error.column, column)
        self.assertIn(fragment, self.messages(result)[0])

    def test_required_fields(self):
        for index, column in enumerate(SALES_HEADERS):
            if column in ("Cantidad", "Valor Unitario", "Valor Total"):
                continue
            with self.subTest(column=column):
                row = list(VALID_ROW)
                row[index] = None
                self.assertSingleError(parse([row]), column, "obligatorio")

    def test_quantity_and_total_must_be_positive(self):
        cases = {
            "Cantidad": [*VALID_ROW[:4], 0, 50000, 100000, "Confirmada"],
            "Valor Total": [*VALID_ROW[:4], 2, 0, 0, "Confirmada"],
        }
        for column, row in cases.items():
            with self.subTest(column=column):
                result = parse([row])
                self.assertIn(column, [error.column for error in result.errors])
                self.assertTrue(any("mayor que cero" in text for text in self.messages(result)))

    def test_negative_quantity(self):
        result = parse([[*VALID_ROW[:4], -2, 50000, -100000, "Confirmada"]])
        self.assertEqual({error.column for error in result.errors}, {"Cantidad", "Valor Total"})

    def test_total_must_match_quantity_by_price(self):
        result = parse([[*VALID_ROW[:6], 90000, "Confirmada"]])
        self.assertSingleError(result, "Valor Total", "100,000.00")
        self.assertIn("90,000.00", self.messages(result)[0])

    def test_unknown_state(self):
        self.assertSingleError(parse([[*VALID_ROW[:7], "Pendiente"]]), "Estado", "Pendiente")

    def test_invalid_dates(self):
        for value in ("31/02/2026", "junio", "2026/06/01"):
            with self.subTest(value=value):
                self.assertSingleError(parse([[value, *VALID_ROW[1:]]]), "Fecha", value)

    def test_invalid_numbers(self):
        for value in ("dos", "1,2,3", "1.5", "50 000"):
            with self.subTest(value=value):
                row = [*VALID_ROW[:4], value, 50000, 100000, "Confirmada"]
                self.assertIn("Cantidad", [error.column for error in parse([row]).errors])

    def test_all_errors_are_reported_with_row_numbers(self):
        rows = [VALID_ROW, [*VALID_ROW[:7], "X"], VALID_ROW, [None, *VALID_ROW[1:4], 0, 1, 0, "Y"]]
        result = parse(rows)
        self.assertEqual({error.row_number for error in result.errors}, {3, 5})
        self.assertEqual(len(result.errors), 5)  # estado fila 3; fecha, cantidad, total y estado fila 5
        self.assertEqual(len(result.rows), 2)
        self.assertTrue(self.messages(result)[0].startswith("Fila 3 · Estado"))

    def test_not_an_excel_file(self):
        for content in (b"", b"not an excel", b"PK\x03\x04garbage"):
            with self.subTest(content=content):
                result = parse_sales_workbook(content, RULES)
                self.assertSingleError(result, None, "no es un Excel")

    def test_empty_workbook(self):
        result = parse_sales_workbook(build_xlsx([], headers=None), RULES)
        self.assertSingleError(result, None, "vacío")

    def test_missing_headers_are_all_reported(self):
        headers = ["Fecha", "Cliente", "Producto", "Cantidad", "Valor Total"]
        result = parse([["2026-06-01", "A", "P", 1, 1]], headers)
        self.assertSingleError(result, None, "Vendedor, Valor Unitario, Estado")

    def test_headers_without_rows(self):
        self.assertSingleError(parse([]), None, "no contiene ventas")

    def test_quantity_is_validated_as_it_will_be_stored(self):
        # La cantidad se guarda con 2 decimales: 0,004 se convierte en 0 y 0,333 en 0,33.
        result = parse([[*VALID_ROW[:4], 0.004, 3, 0.01, "Confirmada"]])
        self.assertIn("mayor que cero", self.messages(result)[0])
        result = parse([[*VALID_ROW[:4], 0.333, 3, 1.0, "Confirmada"]])
        self.assertSingleError(result, "Valor Total", "0.99")

    def test_corrupted_sheet_is_reported_not_raised(self):
        # Zip y manifiesto válidos, pero el XML de la hoja está truncado: openpyxl solo
        # lo descubre al recorrer las filas (lectura perezosa).
        source = zipfile.ZipFile(io.BytesIO(build_xlsx([VALID_ROW])))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    data = data[: len(data) // 2]
                target.writestr(item, data)
        result = parse_sales_workbook(buffer.getvalue(), RULES)
        self.assertSingleError(result, None, "no es un Excel")

    def test_row_limit(self):
        with patch.object(sales_excel_parser, "MAX_DATA_ROWS", 3):
            result = parse([VALID_ROW] * 4)
        self.assertSingleError(result, None, "máximo de 3 filas")
