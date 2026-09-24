from odoo import fields, models


class SalesImportSalesperson(models.Model):
    """Vendedor tal como aparece en los archivos de ventas.

    Es un catálogo propio y no ``res.users``: el vendedor de un archivo externo no
    tiene por qué ser usuario de Odoo, y crear usuarios (acceso, login, permisos)
    desde un Excel sería un riesgo de seguridad.
    """

    _name = "sales.import.salesperson"
    _description = "Vendedor importado"
    _order = "name"

    name = fields.Char(string="Nombre", required=True)
    user_id = fields.Many2one(
        "res.users",
        string="Usuario",
        help="Usuario de Odoo con el mismo nombre, si existe.",
    )
    active = fields.Boolean(string="Activo", default=True)

    _name_lower_unique = models.UniqueIndex(
        "(lower(name))",
        "Ya existe un vendedor con este nombre.",
    )
