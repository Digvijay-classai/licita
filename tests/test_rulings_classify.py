import pytest

from licita.rulings.classify import challenges_pliegos, is_health, outcome


@pytest.mark.parametrize("text,label", [
    ("Desestimación", "dismissed"),
    ("desestimatoria", "dismissed"),          # contains "estima": must NOT be upheld
    ("Desestimación e inadmisión", "dismissed"),
    ("Estimación", "upheld"),
    ("estimatoria", "upheld"),
    ("Estimación y retroacción", "upheld"),
    ("Nulidad de la adjudicación y retroacción previa a la invitación", "upheld"),
    ("Estimación parcial", "partly_upheld"),
    ("estimatoria parcial", "partly_upheld"),
    ("Estimación parcial y retroacción", "partly_upheld"),
    ("Inadmisión por cuantía", "inadmissible"),
    ("archivo", "inadmissible"),
    ("Terminación por pérdida del objeto", "inadmissible"),
    (None, "unknown"),
    ("", "unknown"),
])
def test_outcome(text, label):
    assert outcome(text) == label


def test_health_and_pliegos():
    assert is_health("Hospital Universitario Santa Ficticia de Villaejemplo")
    assert is_health("Servicio de Salud de la Región de Ejemplo")
    assert is_health("SALUDEJ Compras Centralizadas")          # regional health-service acronym from the profile
    assert not is_health("Ayuntamiento de Villaejemplo")
    assert challenges_pliegos("Pliegos") and challenges_pliegos("Anuncio de licitación y los pliegos")
    assert not challenges_pliegos("Adjudicación")
