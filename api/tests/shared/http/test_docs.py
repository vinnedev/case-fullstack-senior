import pytest
from fastapi.testclient import TestClient

import main
from shared.http.graceful_shutdown import GracefulShutdown


@pytest.fixture(autouse=True)
def fresh_shutdown(monkeypatch):
    monkeypatch.setattr(main, "shutdown", GracefulShutdown(main.SERVICE))


def test_docs_serves_branded_swagger():
    response = TestClient(main.app).get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "galaxies-logo" in body
    assert "SwaggerUIBundle" in body
    assert main.app.openapi_url is not None
    assert main.app.openapi_url in body
    assert "docExpansion" in body


def test_default_docs_disabled():
    assert main.app.docs_url is None


def test_docs_serves_the_brand_wordmark_as_png():
    response = TestClient(main.app).get("/galaxies-logo.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0


def test_docs_page_references_the_brand_wordmark():
    html = TestClient(main.app).get("/docs").text
    assert '<img class="galaxies-logo" src="/galaxies-logo.png" alt="Galaxies">' in html
