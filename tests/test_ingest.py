from datetime import date

from licita.ingest.backfill import plan
from licita.rulings.scrape import parse_page

# Fixture pages follow the invented test profile's layout (tests/fixtures/profiles/test.toml), not any real site.
PAGE_FULL = """<html><body><h1>Resolución 12/2024</h1>
<p>Dictada el: 9/4/2024</p><p>Entidad contratante: Hospital General de Villaejemplo</p>
<p>Recurso contra: Pliegos</p><p>Objeto del expediente: suministro de mobiliario de oficina</p>
<p>Clase de contrato: Suministro</p><p>Fallo: Estimación</p>
<a href="docs/res-12-texto.pdf">Imprimir esta página</a></body></html>"""

PAGE_MISSING_FIELD = """<div>Dictada el: 2/10/2024 Entidad contratante: Hospital Universitario Santa Ficticia
Recurso contra: Adjudicación Clase de contrato: Suministro Fallo: Estimación
Volver</div>"""


def test_parse_full_page():
    r = parse_page(PAGE_FULL, 2024, 12)
    assert r["organo"] == "Hospital General de Villaejemplo"
    assert r["acto"] == "Pliegos" and r["tipo_resolucion"] == "Estimación"
    assert r["objeto"].startswith("suministro de mobiliario")
    assert r["pdf_url"] == "https://tribunal.example/resoluciones/anio-2024/docs/res-12-texto.pdf"


def test_parse_is_order_independent_when_a_field_is_missing():
    r = parse_page(PAGE_MISSING_FIELD, 2024, 40)
    assert r["objeto"] is None
    assert r["acto"] == "Adjudicación"
    assert r["tipo_resolucion"] == "Estimación"
    assert r["pdf_url"] is None


def test_backfill_plan_yearly_then_monthly():
    names = [a.name for a in plan(date(2026, 10, 8))]
    assert "643_2017" in names and "643_2025" in names and "643_2026" not in names
    assert "643_202609" in names and "643_202610" not in names     # current month not archived yet
    assert "1044_2017" in names and "1044_202601" in names


def test_plan_current_month_is_opt_in():
    from datetime import date

    from licita.ingest.backfill import current_month, plan
    names = {a.name for a in plan(date(2026, 10, 9))}
    assert "643_202609" in names and "643_202610" not in names
    names = {a.name for a in plan(date(2026, 10, 9), include_current=True)}
    assert {"643_202610", "1044_202610"} <= names
    assert current_month(date(2026, 10, 9)) == {"643_202610", "1044_202610"}
