{
    "name": "Sales Import Analytics",
    "summary": "Importa ventas desde Excel, las analiza en un tablero y actualiza la lista en tiempo real",
    "description": """
Importación de ventas mensuales desde Excel mediante un asistente con validación
completa (todo o nada), tablero de análisis comercial (gráfico y tabla dinámica)
y actualización en tiempo real de la lista de ventas mediante el bus de Odoo.
    """,
    "version": "19.0.1.0.7",
    "category": "Sales",
    "author": "Francisco Javier Rendon Arroyave",
    "license": "LGPL-3",
    "depends": ["base", "web", "bus", "product"],
    "data": [
        "security/sales_import_security.xml",
        "security/ir.model.access.csv",
        "wizard/sales_import_wizard_views.xml",
        "views/sales_import_record_views.xml",
        "views/sales_import_batch_views.xml",
        "views/sales_import_salesperson_views.xml",
        "views/sales_import_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "sales_import_analytics/static/src/**/*",
        ],
        "web.assets_unit_tests": [
            "sales_import_analytics/static/tests/**/*",
        ],
    },
    "application": True,
    "installable": True,
}
