import base64
import binascii

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import escape_psql, float_round

from ..tools.sales_excel_parser import (
    COLUMN_LABELS,
    MAX_DATA_ROWS,
    AmountRules,
    ParseError,
    content_fingerprint,
    normalize_name,
    parse_sales_workbook,
)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXTENSION = ".xlsx"
MAX_REPORTED_ERRORS = 50
MANAGER_GROUP = "sales_import_analytics.group_manager"


class SalesImportWizard(models.TransientModel):
    """Orquesta la importación: lee, valida todo y solo entonces crea (todo o nada).

    La lectura y validación del archivo la hace el parser puro; aquí se resuelven
    las referencias contra la base de datos y se crean el lote y las ventas.
    """

    _name = "sales.import.wizard"
    _description = "Asistente de importación de ventas"

    file = fields.Binary(string="Archivo Excel", required=True, attachment=False)
    file_name = fields.Char(string="Nombre del archivo")
    force_reimport = fields.Boolean(
        string="Importar de todas formas",
        help="Importa el archivo aunque ya se haya importado antes con el mismo contenido.",
    )
    duplicate_batch_id = fields.Many2one(
        "sales.import.batch",
        string="Importado antes en",
        compute="_compute_duplicate_batch_id",
    )
    limits_note = fields.Char(compute="_compute_limits_note")

    def _compute_limits_note(self):
        self.limits_note = self.env._(
            "Máximo %(size)s MB y %(rows)s filas.",
            size=MAX_FILE_SIZE // 1024 // 1024,
            rows=f"{MAX_DATA_ROWS:,}".replace(",", "."),
        )

    @api.depends("file")
    def _compute_duplicate_batch_id(self):
        """Aviso previo en el formulario; ``action_import`` lo vuelve a comprobar."""
        for wizard in self:
            batch = self.env["sales.import.batch"]
            if wizard.file:
                try:
                    parsed = parse_sales_workbook(base64.b64decode(wizard.file), wizard._amount_rules())
                except (binascii.Error, ValueError):
                    parsed = None  # el error de lectura se reporta al importar
                if parsed and parsed.rows and not parsed.errors:
                    batch = wizard._find_batch_by_hash(content_fingerprint(parsed.rows))
            wizard.duplicate_batch_id = batch

    def action_import(self):
        self.ensure_one()
        content = self._read_file_content()
        parsed = parse_sales_workbook(content, self._amount_rules())
        references = _SaleReferences(self.env, parsed.rows)
        errors = parsed.errors + references.missing_master_errors()
        if errors:
            raise UserError(self._format_errors(errors))

        file_hash = content_fingerprint(parsed.rows)
        self._check_not_imported(file_hash)

        references.create_missing()
        batch = self._create_batch(file_hash, len(parsed.rows), references)
        self.env["sales.import.record"].create(
            [references.sale_values(row, batch) for row in parsed.rows]
        )
        return self._success_action(batch)

    # Lectura y comprobaciones previas

    def _decode_file(self) -> bytes:
        try:
            return base64.b64decode(self.file)
        except (binascii.Error, ValueError):
            raise UserError(self.env._("No se pudo leer el archivo subido."))

    def _read_file_content(self) -> bytes:
        if self.file_name and not self.file_name.lower().endswith(ALLOWED_EXTENSION):
            raise UserError(self.env._(
                "El archivo debe ser un Excel %(extension)s.", extension=ALLOWED_EXTENSION
            ))
        content = self._decode_file()
        if len(content) > MAX_FILE_SIZE:
            raise UserError(self.env._(
                "El archivo pesa %(size).1f MB y el máximo es %(max)s MB.",
                size=len(content) / 1024 / 1024,
                max=MAX_FILE_SIZE // 1024 // 1024,
            ))
        return content

    def _find_batch_by_hash(self, file_hash):
        return self.env["sales.import.batch"].search([("file_hash", "=", file_hash)], limit=1)

    def _check_not_imported(self, file_hash):
        if self.force_reimport:
            return
        previous = self._find_batch_by_hash(file_hash)
        if previous:
            raise UserError(self.env._(
                "Este archivo ya se importó el %(date)s en el lote «%(batch)s». "
                "Marque «Importar de todas formas» si quiere importarlo otra vez.",
                date=fields.Datetime.to_string(previous.create_date),
                batch=previous.name,
            ))

    def _amount_rules(self) -> AmountRules:
        """Las mismas reglas que aplicará el modelo al guardar: la cantidad se
        redondea a la precisión del campo y los importes se comparan con la
        tolerancia de la moneda."""
        currency = self.env.company.currency_id
        _precision, quantity_digits = self.env["sales.import.record"]._fields["quantity"].get_digits(self.env)
        return AmountRules(
            round_quantity=lambda quantity: float_round(quantity, precision_digits=quantity_digits),
            amounts_match=lambda expected, received: not currency.compare_amounts(expected, received),
        )

    def _format_errors(self, errors) -> str:
        lines = [error.render(self.env._) for error in errors[:MAX_REPORTED_ERRORS]]
        if len(errors) > MAX_REPORTED_ERRORS:
            lines.append(self.env._(
                "… y %(count)s errores más.", count=len(errors) - MAX_REPORTED_ERRORS
            ))
        return self.env._(
            "No se importó ninguna venta. Corrija el archivo y vuelva a intentarlo:\n\n%(errors)s",
            errors="\n".join(lines),
        )

    # Creación

    def _create_batch(self, file_hash, record_count, references):
        return self.env["sales.import.batch"].create({
            "name": self.file_name or self.env._("Importación sin nombre"),
            "file_hash": file_hash,
            "record_count": record_count,
            "new_partner_count": len(references.new_partners),
            "new_product_count": len(references.new_products),
            "new_salesperson_count": len(references.new_salespersons),
        })

    def _success_action(self, batch):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": self.env._("Importación completada"),
                "message": self.env._(
                    "%(sales)s ventas importadas (%(partners)s clientes, %(products)s productos "
                    "y %(salespersons)s vendedores nuevos).",
                    sales=batch.record_count,
                    partners=batch.new_partner_count,
                    products=batch.new_product_count,
                    salespersons=batch.new_salesperson_count,
                ),
                "next": batch.action_open_records(),
            },
        }


class _SaleReferences:
    """Resuelve clientes, productos y vendedores del archivo contra la base de datos.

    Hace una consulta por modelo (no una por fila) y guarda el resultado en
    diccionarios indexados por el nombre normalizado.
    """

    def __init__(self, env, rows):
        self.env = env
        self.rows = rows
        self.partners = self._search_by_names("res.partner", "name", {row.partner for row in rows})
        self.products = self._search_products({row.product for row in rows})
        # Incluye archivados: el nombre es único también entre ellos, y crearlo de
        # nuevo violaría el índice.
        self.salespersons = self._search_by_names(
            "sales.import.salesperson", "name", {row.salesperson for row in rows}, active_test=False
        )
        self.new_partners = self._missing_names(self.partners, (row.partner for row in rows))
        self.new_products = self._missing_names(self.products, (row.product for row in rows))
        self.new_salespersons = self._missing_names(
            self.salespersons, (row.salesperson for row in rows)
        )

    def missing_master_errors(self) -> list[ParseError]:
        """Solo un Responsable puede crear clientes y productos (constitución V)."""
        if self.env.user.has_group(MANAGER_GROUP):
            return []
        errors = []
        for row in self.rows:
            if normalize_name(row.partner) in self.new_partners:
                errors.append(ParseError(row.row_number, COLUMN_LABELS["partner"], self.env._(
                    "El cliente «%(name)s» no existe; solo un Responsable puede crearlo.",
                    name=row.partner,
                )))
            if normalize_name(row.product) in self.new_products:
                errors.append(ParseError(row.row_number, COLUMN_LABELS["product"], self.env._(
                    "El producto «%(name)s» no existe; solo un Responsable puede crearlo.",
                    name=row.product,
                )))
        return errors

    def create_missing(self):
        self.partners.update(self._create_missing_partners())
        self.products.update(self._create_missing_products())
        self.salespersons.update(self._create_missing_salespersons())

    def sale_values(self, row, batch) -> dict:
        return {
            "date": row.date,
            "partner_id": self.partners[normalize_name(row.partner)].id,
            "salesperson_id": self.salespersons[normalize_name(row.salesperson)].id,
            "product_id": self.products[normalize_name(row.product)].id,
            "quantity": row.quantity,
            "price_unit": row.price_unit,
            "amount_total": row.amount_total,
            "state": row.state,
            "batch_id": batch.id,
            "company_id": batch.company_id.id,
        }

    # Búsqueda

    def _search_by_names(self, model, field_name, names, active_test=True) -> dict:
        if not names:
            return {}
        domain = Domain.OR([(field_name, "=ilike", escape_psql(name))] for name in names)
        records = self.env[model].with_context(active_test=active_test).search_fetch(
            domain, [field_name], order="id"
        )
        found = {}
        for record in records:
            found.setdefault(normalize_name(record[field_name]), record)  # el más antiguo gana
        return found

    def _search_products(self, names) -> dict:
        by_code = self._search_by_names("product.product", "default_code", names)
        remaining = {name for name in names if normalize_name(name) not in by_code}
        return self._search_by_names("product.product", "name", remaining) | by_code

    @staticmethod
    def _missing_names(found, names) -> dict:
        """``{clave normalizada: nombre tal como aparece por primera vez}``."""
        missing = {}
        for name in names:
            key = normalize_name(name)
            if key not in found:
                missing.setdefault(key, name)
        return missing

    # Creación de maestros faltantes

    def _check_can_create_masters(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise UserError(self.env._("Solo un Responsable puede crear clientes y productos."))

    def _create_missing_partners(self) -> dict:
        """EXCEPCIÓN DOCUMENTADA a la regla de sudo() (constitución V, v1.1.0).

        Un usuario interno no puede crear contactos. Condiciones: solo ``create``,
        lista blanca de campos {name}, usuario Responsable verificado y creación
        contada en el lote (``new_partner_count``).
        """
        if not self.new_partners:
            return {}
        self._check_can_create_masters()
        partners = self.env["res.partner"].sudo().create(
            [{"name": name} for name in self.new_partners.values()]
        ).sudo(False)
        return dict(zip(self.new_partners, partners))

    def _create_missing_products(self) -> dict:
        """EXCEPCIÓN DOCUMENTADA a la regla de sudo() (constitución V, v1.1.0).

        Un usuario interno no puede crear productos. Condiciones: solo ``create``,
        lista blanca de campos {name, type} (bien consumible, sin precio), usuario
        Responsable verificado y creación contada en el lote (``new_product_count``).
        """
        if not self.new_products:
            return {}
        self._check_can_create_masters()
        products = self.env["product.product"].sudo().create(
            [{"name": name, "type": "consu"} for name in self.new_products.values()]
        ).sudo(False)
        return dict(zip(self.new_products, products))

    def _create_missing_salespersons(self) -> dict:
        if not self.new_salespersons:
            return {}
        users = self._search_by_names("res.users", "name", set(self.new_salespersons.values()))
        salespersons = self.env["sales.import.salesperson"].create([
            {"name": name, "user_id": users.get(key, self.env["res.users"]).id}
            for key, name in self.new_salespersons.items()
        ])
        return dict(zip(self.new_salespersons, salespersons))

