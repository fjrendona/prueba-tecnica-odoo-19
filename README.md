# Prueba Técnica - Odoo 19

Addons personalizados para Odoo 19 Community, ejecutado con Docker sobre el código fuente del clon local de Odoo 19.

## Requisitos

- Docker + Docker Compose v2
- Clon de Odoo 19 Community (`git clone -b 19.0 https://github.com/odoo/odoo.git`)

## Puesta en marcha

```bash
cp .env.example .env          # ajustar ODOO_SRC a la ruta del clon de Odoo 19
mkdir -p logs && chmod 777 logs
docker compose build odoo
docker compose up -d
```

En el primer arranque Odoo crea la base de datos `POSTGRES_DB` e instala `base` (sin datos demo, el comportamiento por defecto de Odoo 19). Seguir el progreso en `logs/odoo.log`.

| Servicio | Contenedor | Host |
|---|---|---|
| Odoo 19 | `odoo_19` | http://localhost:8070 (admin / admin) |
| PostgreSQL 16 | `odoo19_db` | `localhost:5433` |

Los puertos se cambian con `ODOO_PORT` y `DB_PORT` en `.env`.

## Cómo está montado

- `Dockerfile`: parte de `odoo:19.0` (runtime y dependencias del sistema), elimina el Odoo empaquetado en la imagen e instala `requirements.txt`.
- `docker-compose.yml`: monta el clon local (`ODOO_SRC`) en `/opt/odoo` (solo lectura) y este repo en `/mnt/extra-addons`; arranca `/opt/odoo/odoo-bin`.
- `docker-compose.override.yml` (se aplica automáticamente): modo desarrollo con `--dev=reload,xml`, nivel de log `ODOO_LOG_LEVEL` y log en `logs/odoo.log`. Para arrancar sin él: `docker compose -f docker-compose.yml up -d`.

## Operaciones habituales

Instalar / actualizar un módulo:

```bash
docker exec -it odoo_19 python3 /opt/odoo/odoo-bin -d odoo19_db \
  --db_host=db --db_user=odoo --db_password=odoo \
  --addons-path=/opt/odoo/addons,/mnt/extra-addons \
  -u <modulo> --stop-after-init --http-port=8099
docker restart odoo_19
```

(`-i <modulo>` para instalarlo; añadir `--test-enable --test-tags=/<modulo>` para correr sus tests.)

Recrear la base de datos desde cero:

```bash
docker compose down -v
docker compose up -d
```

> Mientras el repo no contenga ningún módulo, Odoo avisa `invalid addons directory '/mnt/extra-addons', skipped`. Tras crear el primer módulo hay que reiniciar el contenedor para que la ruta entre en el `addons_path`.
