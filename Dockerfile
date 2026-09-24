# Imagen de Odoo 19 con las dependencias Python extra que necesiten los módulos
# del repo (ver requirements.txt).
#
# La imagen oficial `odoo:19.0` aporta el runtime (Python, wkhtmltopdf, libs del
# sistema y paquetes python3-* de Odoo 19). El código fuente de Odoo NO se toma
# de la imagen: docker-compose monta el clon local de Odoo 19 Community
# (ODOO_SRC en .env) y arranca su `odoo-bin`.
FROM odoo:19.0

USER root
# Se elimina el Odoo empaquetado en la imagen: su carpeta de addons entra en el
# namespace `odoo.addons` por delante de /opt/odoo/addons y haría que módulos
# como `sale` o `crm` se cargaran de la imagen en lugar del clon local.
RUN rm -rf /usr/lib/python3/dist-packages/odoo
COPY requirements.txt /tmp/requirements.txt
# --break-system-packages: la imagen de Odoo usa el Python del sistema (PEP 668).
RUN pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
USER odoo
