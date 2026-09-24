from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import format_date

# Tipo del aviso de bus que escucha la lista en tiempo real (static/src/views/...).
NEW_RECORDS_NOTIFICATION = "sales_import_analytics/new_records"
NOTIFIED_GROUP = "sales_import_analytics.group_user"

SALE_STATES = [
    ("draft", "Borrador"),
    ("confirmed", "Confirmada"),
    ("cancelled", "Cancelada"),
]


class SalesImportRecord(models.Model):
    """Venta importada. Sus invariantes viven aquí, no en el asistente, para que
    se cumplan por cualquier vía de creación (UI, importación, RPC)."""

    _name = "sales.import.record"
    _description = "Venta importada"
    _order = "date desc, id desc"

    date = fields.Date(string="Fecha", required=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Cliente", required=True, index=True, ondelete="restrict"
    )
    salesperson_id = fields.Many2one(
        "sales.import.salesperson",
        string="Vendedor",
        required=True,
        index=True,
        ondelete="restrict",
    )
    product_id = fields.Many2one(
        "product.product", string="Producto", required=True, index=True, ondelete="restrict"
    )
    quantity = fields.Float(string="Cantidad", required=True, digits="Product Unit")
    price_unit = fields.Monetary(string="Valor unitario", required=True, aggregator="avg")
    amount_total = fields.Monetary(string="Valor total", required=True, aggregator="sum")
    state = fields.Selection(
        SALE_STATES, string="Estado", required=True, index=True, default="draft"
    )
    batch_id = fields.Many2one(
        "sales.import.batch",
        string="Lote de importación",
        index=True,
        ondelete="cascade",
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Moneda", store=True
    )

    _quantity_positive = models.Constraint(
        "CHECK(quantity > 0)", "La cantidad debe ser mayor que cero."
    )
    _amount_total_positive = models.Constraint(
        "CHECK(amount_total > 0)", "El valor total debe ser mayor que cero."
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if records:
            records._notify_new_records()
        return records

    def _notify_new_records(self):
        """Avisa a las listas abiertas de que hay ventas nuevas: un aviso por lote.

        Se envía al grupo del módulo: ``ir.websocket`` suscribe cada sesión a los
        canales de todos los grupos de su usuario, así que solo lo reciben quienes
        tienen acceso. El payload va vacío a propósito: cada cliente recarga con
        sus propios permisos. El bus lo encola en ``precommit``, por lo que solo
        se envía si la transacción hace commit.
        """
        self.env.ref(NOTIFIED_GROUP)._bus_send(NEW_RECORDS_NOTIFICATION, {})

    @api.depends("partner_id", "product_id", "date")
    def _compute_display_name(self):
        for record in self:
            parts = [
                record.partner_id.name,
                record.product_id.display_name,
                record.date and format_date(self.env, record.date),
            ]
            record.display_name = " · ".join(part for part in parts if part)

    @api.constrains("quantity", "price_unit", "amount_total", "currency_id")
    def _check_amount_total_matches_lines(self):
        for record in self:
            expected = record.quantity * record.price_unit
            if record.currency_id.compare_amounts(record.amount_total, expected):
                raise ValidationError(self.env._(
                    "El valor total (%(total)s) no coincide con cantidad × valor unitario (%(expected)s).",
                    total=record.amount_total,
                    expected=record.currency_id.round(expected),
                ))
