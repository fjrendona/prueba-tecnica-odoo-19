from odoo import fields, models


class SalesImportBatch(models.Model):
    """Una importación de un archivo Excel.

    Da trazabilidad (de qué archivo salió cada venta), permite deshacer una
    importación completa borrando el lote y detecta reimportaciones por la
    huella de su contenido. El usuario y la fecha son ``create_uid`` y ``create_date``.
    """

    _name = "sales.import.batch"
    _description = "Lote de importación de ventas"
    _order = "create_date desc, id desc"

    name = fields.Char(string="Archivo", required=True)
    file_hash = fields.Char(
        string="Huella del contenido",
        required=True,
        index=True,
        readonly=True,
        help="SHA-256 de las ventas del archivo (no de sus bytes), usada para detectar "
        "reimportaciones aunque el archivo se haya vuelto a guardar.",
    )
    record_ids = fields.One2many(
        "sales.import.record", "batch_id", string="Ventas", readonly=True
    )
    record_count = fields.Integer(string="Ventas importadas", readonly=True)
    new_partner_count = fields.Integer(string="Clientes nuevos", readonly=True)
    new_product_count = fields.Integer(string="Productos nuevos", readonly=True)
    new_salesperson_count = fields.Integer(string="Vendedores nuevos", readonly=True)
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )

    def action_open_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Ventas de %(batch)s", batch=self.name),
            "res_model": "sales.import.record",
            "view_mode": "list,form",
            "domain": [("batch_id", "=", self.id)],
        }
