"""Guarda o contrato de erros: todo erro de domínio precisa de tradução HTTP.

Um subclasse nova de JobsDomainError sem `except` correspondente em routes.py
viraria 500 silencioso — este teste falha antes disso chegar em produção.
"""

import inspect

from modules.jobs import routes
from modules.jobs.errors import JobsDomainError


def test_every_domain_error_has_a_route_translation():
    source = inspect.getsource(routes)
    untranslated = [error.__name__ for error in JobsDomainError.__subclasses__() if f"except {error.__name__}" not in source]
    assert untranslated == [], f"erros de domínio sem tradução HTTP em routes.py: {untranslated}"


def test_domain_error_hierarchy_is_flat():
    # A varredura acima usa __subclasses__() direto de JobsDomainError; se a
    # hierarquia ganhar níveis, o teste anterior ficaria cego para os netos.
    for error in JobsDomainError.__subclasses__():
        assert error.__subclasses__() == [], f"{error.__name__} tem subclasses — atualize a varredura do contrato"
