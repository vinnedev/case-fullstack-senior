"""Bateria adversarial: cada teste tenta QUEBRAR uma regra de negócio.

Regras sob ataque (fontes: TASKS.md, README.md, DECISIONS.md):
  R1  X-Auth é obrigatório e validado em TODAS as rotas (401, nunca 500)
  R2  Idempotency-Key opcional mas validada: em branco/gigante → 422; replay
      com payload diferente → 409; ausente → o servidor gera a própria chave
  R3  Isolamento por tenant em toda leitura/mutação (404, sem vazar existência)
  R4  Role admin exigida nas rotas /admin (403) — e role NÃO dá poder cross-tenant
  R5  Transições de estado válidas apenas (cancel/retry → 409 fora delas)
  R6  Máximo de 3 tentativas (retry no limite → 409)
  R7  Limite de concorrência por empresa (429)
  R8  Validação de tipos/formatos/faixas em toda borda (422, nunca 500)
  R9  Nenhum input malicioso vira SQL/erro interno
"""

import pytest

USER = {"X-Auth": "1:user"}
ADMIN = {"X-Auth": "1:admin"}
OTHER = {"X-Auth": "2:user"}
KEY = {"Idempotency-Key": "brk"}

ALL_ROUTES = [
    ("get", "/jobs"),
    ("get", "/jobs/1"),
    ("get", "/jobs/1/result"),
    ("post", "/jobs"),
    ("post", "/jobs/1/cancel"),
    ("post", "/jobs/1/retry"),
    ("get", "/admin/jobs"),
    ("get", "/admin/dlq"),
    ("get", "/admin/companies"),
]


@pytest.fixture()
def seeded(db):
    db.execute("INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES (1, 'Acme', 2, 20), (2, 'Globex', 2, 100)")
    db.execute(
        """
        INSERT INTO jobs (id, company_id, kind, status, attempts) VALUES
          (1, 1, 'report', 'done', 1),
          (2, 1, 'import', 'queued', 0),
          (3, 2, 'report', 'done', 1),
          (4, 1, 'report', 'failed', 1),
          (5, 1, 'report', 'failed', 3),
          (6, 1, 'report', 'cancelled', 1),
          (7, 1, 'report', 'running', 1)
        """
    )
    db.execute("INSERT INTO job_results (job_id, payload) VALUES (1, 'resultado 1'), (3, 'resultado 3')")
    db.execute("SELECT setval('jobs_id_seq', 1000)")
    db.commit()


class TestR1AuthMandatory:
    @pytest.mark.parametrize(("method", "path"), ALL_ROUTES)
    def test_every_route_rejects_missing_auth(self, client, seeded, method, path):
        resp = getattr(client, method)(path, headers=KEY if method == "post" else None)
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            " ",
            ":",
            "::",
            "abc:user",
            "-1:user",
            "0:user",
            "1:",
            ":user",
            "1.5:user",
            "1.0:user",
            "1e2:user",
            " 1:user",
            "1 :user",
            "+1:user",
            "01:user",
        ],
    )
    @pytest.mark.parametrize(("method", "path"), ALL_ROUTES)
    def test_every_route_rejects_malformed_auth(self, client, seeded, method, path, bad):
        resp = getattr(client, method)(path, headers={"X-Auth": bad, **KEY})
        assert resp.status_code == 401, f"{method} {path} com X-Auth={bad!r} devolveu {resp.status_code}"

    def test_huge_company_id_never_500s(self, client, seeded):
        headers = {"X-Auth": f"{10**19}:user"}
        assert client.get("/jobs", headers=headers).status_code == 200
        assert client.get("/jobs", headers=headers).json() == []
        create = client.post("/jobs", json={"kind": "x"}, headers={**headers, **KEY})
        assert create.status_code == 401  # empresa desconhecida

    def test_unknown_company_reads_are_empty_not_errors(self, client, seeded):
        headers = {"X-Auth": "77:user"}
        assert client.get("/jobs", headers=headers).json() == []
        assert client.get("/jobs/1", headers=headers).status_code == 404


class TestR2IdempotencyKey:
    def test_missing_key_creates_job_with_server_generated_key(self, client, seeded, db):
        client.post("/jobs/2/cancel", headers=USER)  # libera vaga no limite de concorrência
        resp = client.post("/jobs", json={"kind": "x"}, headers=USER)
        assert resp.status_code == 201
        stored = db.execute("SELECT idempotency_key FROM jobs WHERE id = %s", (resp.json()["id"],)).fetchone()
        assert stored["idempotency_key"].startswith("srv-")

    @pytest.mark.parametrize("bad", ["", " ", "   "])
    def test_blank_key_is_rejected(self, client, seeded, bad):
        resp = client.post("/jobs", json={"kind": "x"}, headers={**USER, "Idempotency-Key": bad})
        assert resp.status_code == 422, f"chave em branco {bad!r} devolveu {resp.status_code}"

    def test_oversized_key_is_rejected(self, client, seeded):
        resp = client.post("/jobs", json={"kind": "x"}, headers={**USER, "Idempotency-Key": "k" * 201})
        assert resp.status_code == 422

    def test_replay_with_different_body_is_conflict(self, client, seeded, db):
        # semântica padrão de idempotência: mesma chave + payload diferente é
        # erro do cliente (409), nunca replay silencioso do job original
        client.post("/jobs/2/cancel", headers=USER)  # libera vaga no limite de concorrência
        client.post("/jobs/7/cancel", headers=USER)
        headers = {**USER, "Idempotency-Key": "replay-diff"}
        first = client.post("/jobs", json={"kind": "original"}, headers=headers)
        assert first.status_code == 201
        second = client.post("/jobs", json={"kind": "diferente"}, headers=headers)
        assert second.status_code == 409
        kinds = [r["kind"] for r in db.execute("SELECT kind FROM jobs WHERE idempotency_key = 'replay-diff'").fetchall()]
        assert kinds == ["original"]

    def test_replay_with_same_body_returns_original(self, client, seeded):
        client.post("/jobs/2/cancel", headers=USER)
        client.post("/jobs/7/cancel", headers=USER)
        headers = {**USER, "Idempotency-Key": "replay-same"}
        first = client.post("/jobs", json={"kind": "original"}, headers=headers).json()
        second = client.post("/jobs", json={"kind": "original"}, headers=headers)
        assert second.status_code == 201
        assert second.json()["id"] == first["id"]


class TestR3TenantIsolation:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("get", "/jobs/3"), ("get", "/jobs/3/result"), ("post", "/jobs/3/cancel"), ("post", "/jobs/3/retry")],
    )
    def test_cross_tenant_is_404_never_403(self, client, seeded, method, path):
        resp = getattr(client, method)(path, headers=USER)
        assert resp.status_code == 404  # 404, não 403: não confirmamos que o recurso existe

    def test_list_never_leaks_other_tenant(self, client, seeded):
        ids = {j["id"] for j in client.get("/jobs?limit=200", headers=USER).json()}
        assert 3 not in ids


class TestR4AdminRole:
    @pytest.mark.parametrize("path", ["/admin/jobs", "/admin/dlq"])
    def test_valid_non_admin_role_is_forbidden(self, client, seeded, path):
        assert client.get(path, headers={"X-Auth": "1:user"}).status_code == 403

    @pytest.mark.parametrize("path", ["/admin/jobs", "/admin/dlq"])
    @pytest.mark.parametrize("role", ["Admin", "ADMIN", "admin ", " admin", "administrator", "root", "admin;", "admin:x"])
    def test_unknown_roles_are_rejected_fail_closed(self, client, seeded, path, role):
        # whitelist de roles no AuthContext: qualquer variação desconhecida é
        # credencial inválida (401), nunca "autenticado sem permissão" (403)
        resp = client.get(path, headers={"X-Auth": f"1:{role}"})
        assert resp.status_code == 401, f"role {role!r} não foi rejeitada em {path}"

    @pytest.mark.parametrize("path", ["/admin/jobs", "/admin/dlq"])
    def test_admin_role_grants_access(self, client, seeded, path):
        assert client.get(path, headers=ADMIN).status_code == 200

    def test_admin_role_does_not_grant_cross_tenant_mutation(self, client, seeded):
        # admin enxerga tudo em /admin, mas NÃO cancela/retry job de outra empresa
        assert client.post("/jobs/3/cancel", headers=ADMIN).status_code == 404
        assert client.post("/jobs/3/retry", headers=ADMIN).status_code == 404

    def test_admin_role_does_not_grant_cross_tenant_reads(self, client, seeded):
        # /admin expõe só a visão administrativa (id/status/contagens); o job e
        # o RESULTADO SENSÍVEL de outra empresa continuam 404 mesmo para admin
        assert client.get("/jobs/3", headers=ADMIN).status_code == 404
        assert client.get("/jobs/3/result", headers=ADMIN).status_code == 404
        ids = {j["id"] for j in client.get("/jobs?limit=200", headers=ADMIN).json()}
        assert 3 not in ids

    def test_admin_overview_never_exposes_payloads(self, client, seeded):
        # nem /admin/jobs nem /admin/companies carregam payload/kind/erro de job
        for path in ("/admin/jobs", "/admin/companies"):
            for row in client.get(path, headers=ADMIN).json():
                assert "payload" not in row and "last_error" not in row and "kind" not in row


class TestR5StateTransitions:
    @pytest.mark.parametrize("job_id", [1, 5, 6])  # done, failed, cancelled
    def test_cancel_only_from_queued_or_running(self, client, seeded, job_id):
        assert client.post(f"/jobs/{job_id}/cancel", headers=USER).status_code == 409

    @pytest.mark.parametrize("job_id", [1, 2, 6, 7])  # done, queued, cancelled, running
    def test_retry_only_from_failed(self, client, seeded, job_id):
        assert client.post(f"/jobs/{job_id}/retry", headers=USER).status_code == 409

    def test_double_cancel_second_conflicts(self, client, seeded):
        assert client.post("/jobs/2/cancel", headers=USER).status_code == 200
        assert client.post("/jobs/2/cancel", headers=USER).status_code == 409

    def test_state_survives_rejected_transition(self, client, seeded):
        client.post("/jobs/1/cancel", headers=USER)
        assert client.get("/jobs/1", headers=USER).json()["status"] == "done"


class TestR6MaxAttempts:
    def test_retry_at_limit_conflicts_with_reason(self, client, seeded):
        resp = client.post("/jobs/5/retry", headers=USER)
        assert resp.status_code == 409
        assert "tentativas" in resp.json()["detail"]

    def test_exhausted_job_stays_failed(self, client, seeded):
        client.post("/jobs/5/retry", headers=USER)
        assert client.get("/jobs/5", headers=USER).json()["status"] == "failed"


class TestR7ConcurrencyLimit:
    def test_limit_blocks_and_recovers_after_cancel(self, client, seeded):
        # empresa 1 tem queued(2) + running(7) = 2 ativos, limite 2
        blocked = client.post("/jobs", json={"kind": "x"}, headers={**USER, "Idempotency-Key": "c1"})
        assert blocked.status_code == 429
        client.post("/jobs/2/cancel", headers=USER)
        allowed = client.post("/jobs", json={"kind": "x"}, headers={**USER, "Idempotency-Key": "c2"})
        assert allowed.status_code == 201


class TestR8InputValidation:
    @pytest.mark.parametrize(
        "kind",
        [123, None, ["x"], {"a": 1}, True, 1.5, "", "x" * 101, "   ", "\t\n", "a\u0000b", "a\u007fb", "a\u0085b"],
    )
    def test_invalid_kind_never_creates_job(self, client, seeded, db, kind):
        before = db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
        resp = client.post("/jobs", json={"kind": kind}, headers={**USER, **KEY})
        after = db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
        assert resp.status_code == 422, f"kind={kind!r} devolveu {resp.status_code}"
        assert after == before

    def test_missing_and_malformed_bodies(self, client, seeded):
        assert client.post("/jobs", headers={**USER, **KEY}).status_code == 422
        assert client.post("/jobs", content=b"nao e json", headers={**USER, **KEY, "Content-Type": "application/json"}).status_code == 422
        assert client.post("/jobs", content=b'{"kind": "x"', headers={**USER, **KEY, "Content-Type": "application/json"}).status_code == 422

    @pytest.mark.parametrize(
        "query",
        [
            "limit=0",
            "limit=201",
            "limit=-5",
            "limit=abc",
            "limit=1.5",
            "offset=-1",
            "offset=abc",
            "status=DONE",
            "status=invalido",
        ],
    )
    def test_invalid_query_params_are_422(self, client, seeded, query):
        assert client.get(f"/jobs?{query}", headers=USER).status_code == 422, query

    @pytest.mark.parametrize("job_id", ["0", "-1", "abc", "1.5", "1e3"])
    def test_invalid_job_ids_are_422(self, client, seeded, job_id):
        assert client.get(f"/jobs/{job_id}", headers=USER).status_code == 422

    def test_huge_job_id_is_404_not_500(self, client, seeded):
        assert client.get(f"/jobs/{10**18}", headers=USER).status_code == 404

    def test_offset_beyond_data_is_empty_with_correct_total(self, client, seeded):
        resp = client.get("/jobs?offset=100000&limit=10", headers=USER)
        assert resp.status_code == 200
        assert resp.json() == []
        assert int(resp.headers["X-Total-Count"]) == 6

    @pytest.mark.parametrize(("method", "path"), [("delete", "/jobs/1"), ("put", "/jobs/1"), ("patch", "/jobs/1"), ("delete", "/jobs")])
    def test_unsupported_methods_are_405(self, client, seeded, method, path):
        assert getattr(client, method)(path, headers=USER).status_code == 405


class TestR9NoInjection:
    @pytest.mark.parametrize(
        "payload",
        ["report'; DROP TABLE jobs; --", 'x" OR "1"="1', "a%' OR 1=1 --", "🔥'); DELETE FROM companies; --"],
    )
    def test_kind_injection_stored_literally(self, client, seeded, db, payload):
        headers = {**USER, "Idempotency-Key": f"inj-{hash(payload)}"}
        client.post("/jobs/2/cancel", headers=USER)  # libera vaga no limite
        resp = client.post("/jobs", json={"kind": payload}, headers=headers)
        if resp.status_code == 201:
            stored = db.execute("SELECT kind FROM jobs WHERE id = %s", (resp.json()["id"],)).fetchone()["kind"]
            assert stored == payload
        assert db.execute("SELECT count(*) AS n FROM companies").fetchone()["n"] == 2

    def test_auth_header_with_extra_separators_keeps_least_privilege(self, client, seeded):
        # "1:admin:extra" vira role "admin:extra" — fora da whitelist, logo 401
        assert client.get("/admin/jobs", headers={"X-Auth": "1:admin:extra"}).status_code == 401
        assert client.get("/jobs", headers={"X-Auth": "1:admin:extra"}).status_code == 401


class TestR10ProtocolAbuse:
    def test_duplicate_auth_headers_do_not_escalate(self, client, seeded):
        # httpx envia os dois valores; a API não pode escolher o mais permissivo
        resp = client.get("/admin/jobs", headers=[("X-Auth", "1:user"), ("X-Auth", "1:admin")])
        assert resp.status_code in (401, 403)

    def test_wrong_content_type_is_rejected(self, client, seeded):
        resp = client.post(
            "/jobs",
            content=b'{"kind": "x"}',
            headers={**USER, **KEY, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 422

    def test_oversized_body_never_500s(self, client, seeded):
        resp = client.post("/jobs", json={"kind": "x" * 100_000}, headers={**USER, **KEY})
        assert resp.status_code == 422

    def test_unknown_body_fields_are_rejected(self, client, seeded, db):
        before = db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
        resp = client.post("/jobs", json={"kind": "ok", "status": "done", "id": 999}, headers={**USER, **KEY})
        assert resp.status_code == 422
        assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == before

    def test_total_count_never_leaks_other_tenant_volume(self, client, seeded):
        mine = int(client.get("/jobs?limit=1", headers=USER).headers["X-Total-Count"])
        theirs = int(client.get("/jobs?limit=1", headers=OTHER).headers["X-Total-Count"])
        assert (mine, theirs) == (6, 1)

    def test_filtered_total_matches_filtered_rows(self, client, seeded):
        resp = client.get("/jobs?status=failed&limit=200", headers=USER)
        assert len(resp.json()) == int(resp.headers["X-Total-Count"]) == 2


class TestR11AdminScope:
    def test_admin_sees_all_companies_by_design(self, client, seeded):
        # decisão documentada: /admin é visão global (o case define assim)
        rows = client.get("/admin/jobs", headers=ADMIN).json()
        assert {r["company_id"] for r in rows} == {1, 2}

    def test_admin_of_other_company_sees_the_same_global_view(self, client, seeded):
        a = client.get("/admin/jobs", headers=ADMIN).json()
        b = client.get("/admin/jobs", headers={"X-Auth": "2:admin"}).json()
        assert [r["id"] for r in a] == [r["id"] for r in b]

    def test_dlq_is_empty_and_valid_without_failures(self, client, seeded):
        assert client.get("/admin/dlq", headers=ADMIN).json() == []

    @pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1"])
    def test_dlq_validates_pagination(self, client, seeded, query):
        assert client.get(f"/admin/dlq?{query}", headers=ADMIN).status_code == 422


class TestR12OpenApiContract:
    """A spec precisa refletir as regras reais — doc que mente é doc que engana."""

    def test_x_auth_is_declared_as_required_security_on_every_operation(self, client):
        spec = client.get("/openapi.json").json()
        assert "X-Auth" in spec["components"]["securitySchemes"]
        for path, methods in spec["paths"].items():
            for method, op in methods.items():
                assert op.get("security"), f"{method.upper()} {path} sem exigência de credencial"
                assert {"X-Auth": []} in op["security"]

    def test_every_operation_documents_shutdown_503(self, client):
        spec = client.get("/openapi.json").json()
        for path, methods in spec["paths"].items():
            for method, op in methods.items():
                assert "503" in op["responses"], f"{method.upper()} {path} sem 503 (graceful shutdown) documentado"

    def test_declared_error_responses_match_route_behavior(self, client):
        # espelho 1:1 do que cada rota pode devolver de fato; divergência aqui
        # significa Swagger mentindo (código possível não documentado, ou o inverso)
        expected = {
            ("get", "/jobs"): {"401", "422", "503"},
            ("get", "/jobs/{job_id}"): {"401", "404", "422", "503"},
            ("get", "/jobs/{job_id}/result"): {"401", "404", "422", "503"},
            ("post", "/jobs"): {"401", "409", "422", "429", "503"},
            ("post", "/jobs/{job_id}/cancel"): {"401", "404", "409", "422", "503"},
            ("post", "/jobs/{job_id}/retry"): {"401", "404", "409", "422", "503"},
            ("get", "/admin/jobs"): {"401", "403", "422", "503"},
            ("get", "/admin/dlq"): {"401", "403", "422", "503"},
            ("get", "/admin/companies"): {"401", "403", "422", "503"},
        }
        spec = client.get("/openapi.json").json()
        for (method, path), codes in expected.items():
            declared = {code for code in spec["paths"][path][method]["responses"] if not code.startswith("2")}
            assert declared == codes, f"{method.upper()} {path}: declarado {sorted(declared)}, esperado {sorted(codes)}"

    def test_idempotency_key_is_declared_only_on_create(self, client):
        spec = client.get("/openapi.json").json()
        create = {p["name"]: p for p in spec["paths"]["/jobs"]["post"]["parameters"]}
        assert create["Idempotency-Key"].get("required") is not True
        for path, methods in spec["paths"].items():
            for method, op in methods.items():
                if (path, method) == ("/jobs", "post"):
                    continue
                assert "Idempotency-Key" not in {p["name"] for p in op.get("parameters", [])}

    def test_status_fields_are_never_free_strings(self, client):
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        domain = {"queued", "running", "done", "failed", "cancelled"}
        # cada modelo declara o conjunto exato que aquela operação pode devolver:
        # leituras e o create (cujo replay de Idempotency-Key devolve o job
        # original em qualquer estado), o domínio inteiro; cancel/retry, o
        # único valor possível
        for name, expected in (
            ("JobSummary", domain),
            ("JobDetail", domain),
            ("AdminJob", domain),
            ("JobCreated", domain),
            ("JobCancelled", {"cancelled"}),
            ("JobRetried", {"queued"}),
        ):
            status = schemas[name]["properties"]["status"]
            values = set(status.get("enum", []))
            if "const" in status:
                values = {status["const"]}
            assert values == expected, f"{name}.status = {values or 'string livre'}, esperado {expected}"

    def test_nullable_fields_are_still_always_present(self, client):
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        # o campo existe sempre na resposta; o que varia é o valor ser null
        assert "last_error" in schemas["JobDetail"]["required"]
        for field in ("job_id", "last_error", "trace_id", "job_created_at"):
            assert field in schemas["DeadLetterJob"]["required"]

    def test_paginated_routes_document_total_count_header(self, client):
        spec = client.get("/openapi.json").json()
        for path in ("/jobs", "/admin/jobs", "/admin/dlq", "/admin/companies"):
            headers = spec["paths"][path]["get"]["responses"]["200"].get("headers", {})
            assert "X-Total-Count" in headers, f"{path} não documenta X-Total-Count"

    def test_admin_routes_return_total_count_at_runtime(self, client, seeded):
        for path, expected in (("/admin/jobs", 7), ("/admin/dlq", 0)):
            resp = client.get(f"{path}?limit=1", headers=ADMIN)
            assert int(resp.headers["X-Total-Count"]) == expected

    def test_every_schema_and_field_is_documented(self, client):
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        undocumented = []
        for name, sch in schemas.items():
            if name in ("HTTPValidationError", "ValidationError"):
                continue
            if not sch.get("description"):
                undocumented.append(name)
            for field, fs in sch.get("properties", {}).items():
                if not fs.get("description") and not fs.get("title"):
                    undocumented.append(f"{name}.{field}")
        assert not undocumented, f"sem descrição: {undocumented}"
