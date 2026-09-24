"""Lectura y validación del Excel de ventas.

Módulo puro: recibe los bytes del archivo y devuelve filas tipadas más la lista
completa de errores. No usa el ORM, así que se puede probar sin base de datos, y
el asistente decide qué hacer con el resultado (todo o nada).
"""

import datetime
import hashlib
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from odoo.tools import remove_accents
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

MAX_DATA_ROWS = 10_000

# Cabecera normalizada del Excel -> (clave del campo, etiqueta para los mensajes).
SALES_COLUMNS = {
    "fecha": ("date", "Fecha"),
    "cliente": ("partner", "Cliente"),
    "vendedor": ("salesperson", "Vendedor"),
    "producto": ("product", "Producto"),
    "cantidad": ("quantity", "Cantidad"),
    "valor unitario": ("price_unit", "Valor Unitario"),
    "valor total": ("amount_total", "Valor Total"),
    "estado": ("state", "Estado"),
}
COLUMN_LABELS = dict(SALES_COLUMNS.values())  # clave del campo -> etiqueta

# Variantes normalizadas del estado -> valor de la selección del modelo.
STATE_ALIASES = {
    "borrador": "draft",
    "draft": "draft",
    "confirmada": "confirmed",
    "confirmado": "confirmed",
    "confirmed": "confirmed",
    "cancelada": "cancelled",
    "cancelado": "cancelled",
    "anulada": "cancelled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

TEXT_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")

# Número escrito como texto en formato colombiano: punto de miles y coma decimal
# ("50.000", "1.234,50", "50000", "12,5").
COLOMBIAN_NUMBER = re.compile(r"^-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?$")


@dataclass(frozen=True)
class SaleRow:
    row_number: int
    date: datetime.date
    partner: str
    salesperson: str
    product: str
    quantity: float
    price_unit: float
    amount_total: float
    state: str


@dataclass(frozen=True, eq=False)  # LazyGettext prohíbe __eq__/__hash__
class ParseError:
    """Error de fila (con fila y columna) o de archivo (sin ellas).

    ``message`` es un texto traducible diferido (o un ``str`` ya traducido). Se
    traduce de forma explícita con :meth:`render` y el ``env._`` del llamador:
    una traducción implícita en ``__str__`` dependería de encontrar un ``env`` en
    la pila de llamadas.
    """

    row_number: int | None
    column: str | None
    message: object

    def render(self, translate) -> str:
        """:param translate: normalmente ``env._``; acepta ``str`` y ``LazyGettext``."""
        message = self.message if isinstance(self.message, str) else translate(self.message)
        if self.row_number is None:
            return message
        return translate(_lt(
            "Fila %(row)s · %(column)s: %(message)s",
            row=self.row_number,
            column=self.column,
            message=message,
        ))


@dataclass
class ParseResult:
    rows: list[SaleRow] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


class _CellError(ValueError):
    """Valor de celda inválido; el mensaje explica el motivo al usuario."""


def normalize_text(value) -> str:
    """Cabeceras y estados comparables: sin tildes, sin mayúsculas y con espacios colapsados."""
    return " ".join(remove_accents(str(value)).casefold().split())


def normalize_name(value) -> str:
    """Clave para comparar nombres de clientes, productos y vendedores.

    A diferencia de :func:`normalize_text` conserva las tildes: la búsqueda en la
    base de datos usa ``=ilike``, que (sin la opción ``unaccent`` de Odoo) distingue
    "Pérez" de "Perez", y ambas comparaciones deben coincidir para no crear
    duplicados.
    """
    return " ".join(str(value).casefold().split())


@dataclass(frozen=True)
class AmountRules:
    """Reglas numéricas que conoce el llamador (el ORM) y no este módulo.

    Se validan los valores tal como los guardará el modelo, para que una fila
    aceptada aquí no falle después en sus restricciones sin número de fila.
    """

    round_quantity: Callable[[float], float]  # precisión decimal de la cantidad
    amounts_match: Callable[[float, float], bool]  # (esperado, recibido) con la tolerancia de la moneda


# Errores posibles al abrir o recorrer un .xlsx dañado. En modo read_only openpyxl
# lee las hojas de forma perezosa, así que también pueden surgir al iterar filas.
# SyntaxError cubre los errores de XML (ElementTree y lxml).
_UNREADABLE_FILE_ERRORS = (
    InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError, SyntaxError,
)


def content_fingerprint(rows) -> str:
    """Huella SHA-256 del contenido de las ventas, no de los bytes del archivo.

    Excel (y openpyxl) guardan metadatos como la fecha de guardado, así que el
    mismo contenido guardado dos veces produce bytes distintos. La huella usa las
    filas ya leídas (nombres normalizados, sin número de fila y sin importar el
    orden), de modo que un archivo reguardado se reconoce como el mismo.
    """
    canonical = sorted(
        [
            row.date.isoformat(),
            normalize_name(row.partner),
            normalize_name(row.salesperson),
            normalize_name(row.product),
            row.quantity,
            row.price_unit,
            row.amount_total,
            row.state,
        ]
        for row in rows
    )
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


def parse_sales_workbook(content: bytes, rules: AmountRules) -> ParseResult:
    """Lee la primera hoja del Excel y valida todas sus filas."""
    try:
        workbook = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    except _UNREADABLE_FILE_ERRORS:
        return _file_error(_lt("El archivo no es un Excel (.xlsx) válido."))
    try:
        if not workbook.worksheets:
            return _file_error(_lt("El archivo no contiene hojas."))
        return _parse_sheet(workbook.worksheets[0].iter_rows(values_only=True), rules)
    except _UNREADABLE_FILE_ERRORS:
        return _file_error(_lt("El archivo no es un Excel (.xlsx) válido."))
    finally:
        workbook.close()


def _parse_sheet(sheet_rows, rules) -> ParseResult:
    header = _find_header(sheet_rows)
    if header is None:
        return _file_error(_lt("El archivo está vacío."))
    header_row_number, header_values = header

    column_index, missing = _map_columns(header_values)
    if missing:
        return _file_error(_lt(
            "Faltan columnas obligatorias: %(columns)s.", columns=", ".join(missing)
        ))

    result = ParseResult()
    data_rows = 0
    for row_number, values in enumerate(sheet_rows, start=header_row_number + 1):
        if _is_empty(values):
            continue
        data_rows += 1
        if data_rows > MAX_DATA_ROWS:
            return _file_error(_lt(
                "El archivo supera el máximo de %(max)s filas de ventas.", max=MAX_DATA_ROWS
            ))
        sale, errors = _parse_row(row_number, values, column_index, rules)
        result.errors.extend(errors)
        if sale:
            result.rows.append(sale)

    if not data_rows:
        return _file_error(_lt("El archivo no contiene ventas."))
    return result


def _find_header(sheet_rows):
    for row_number, values in enumerate(sheet_rows, start=1):
        if not _is_empty(values):
            return row_number, values
    return None


def _map_columns(header_values):
    """Devuelve ``{clave_campo: índice}`` y las etiquetas de las columnas que faltan."""
    column_index = {}
    for index, value in enumerate(header_values):
        column = SALES_COLUMNS.get(normalize_text(value)) if value is not None else None
        if column and column[0] not in column_index:
            column_index[column[0]] = index
    missing = [label for key, label in SALES_COLUMNS.values() if key not in column_index]
    return column_index, missing


def _is_empty(values) -> bool:
    return all(value is None or (isinstance(value, str) and not value.strip()) for value in values)


def _parse_row(row_number, values, column_index, rules):
    parsed, errors = {}, []
    for key, label in SALES_COLUMNS.values():
        index = column_index[key]
        raw = values[index] if index < len(values) else None
        try:
            parsed[key] = _convert(key, raw)
        except _CellError as error:
            errors.append(ParseError(row_number, label, error.args[0]))

    if "quantity" in parsed:
        parsed["quantity"] = rules.round_quantity(parsed["quantity"])
    errors.extend(_check_amounts(row_number, parsed, rules.amounts_match))
    if errors:
        return None, errors
    return SaleRow(row_number=row_number, **parsed), []


def _check_amounts(row_number, parsed, amounts_match):
    errors = []
    if parsed.get("quantity") is not None and parsed["quantity"] <= 0:
        errors.append(ParseError(row_number, COLUMN_LABELS["quantity"], _lt("Debe ser mayor que cero.")))
    if parsed.get("amount_total") is not None and parsed["amount_total"] <= 0:
        errors.append(ParseError(row_number, COLUMN_LABELS["amount_total"], _lt("Debe ser mayor que cero.")))
    if errors or not {"quantity", "price_unit", "amount_total"} <= parsed.keys():
        return errors

    expected = parsed["quantity"] * parsed["price_unit"]
    if not amounts_match(expected, parsed["amount_total"]):
        errors.append(ParseError(row_number, COLUMN_LABELS["amount_total"], _lt(
            "No coincide con Cantidad × Valor Unitario: se esperaba %(expected)s y se recibió %(received)s.",
            expected=f"{expected:,.2f}",
            received=f"{parsed['amount_total']:,.2f}",
        )))
    return errors


def _convert(key, raw):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise _CellError(_lt("Campo obligatorio."))
    return _CONVERTERS[key](raw)


def _to_text(raw) -> str:
    return " ".join(str(raw).split())


def _to_date(raw) -> datetime.date:
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    if isinstance(raw, str):
        for date_format in TEXT_DATE_FORMATS:
            try:
                return datetime.datetime.strptime(raw.strip(), date_format).date()
            except ValueError:
                continue
    raise _CellError(_lt(
        "Fecha no válida «%(value)s». Use una celda de fecha o el formato AAAA-MM-DD o DD/MM/AAAA.",
        value=raw,
    ))


def _to_number(raw) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip()
    if COLOMBIAN_NUMBER.match(text):
        return float(text.replace(".", "").replace(",", "."))
    raise _CellError(_lt(
        "Número no válido «%(value)s». Use una celda numérica o el formato 1.234,50.",
        value=raw,
    ))


def _to_state(raw) -> str:
    state = STATE_ALIASES.get(normalize_text(raw))
    if state:
        return state
    raise _CellError(_lt(
        "Estado no reconocido «%(value)s». Valores admitidos: Borrador, Confirmada, Cancelada.",
        value=raw,
    ))


def _file_error(message) -> ParseResult:
    return ParseResult(errors=[ParseError(None, None, message)])


_CONVERTERS = {
    "date": _to_date,
    "partner": _to_text,
    "salesperson": _to_text,
    "product": _to_text,
    "quantity": _to_number,
    "price_unit": _to_number,
    "amount_total": _to_number,
    "state": _to_state,
}
