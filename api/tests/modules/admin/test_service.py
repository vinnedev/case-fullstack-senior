from modules.admin.service import AdminService


def seed(db):
    db.execute("INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES (1, 'Acme', 2, 20), (2, 'Globex', 3, 100)")
    db.execute(
        """
        INSERT INTO jobs (id, company_id, kind, status) VALUES
          (1, 1, 'report', 'done'),
          (2, 1, 'report', 'failed'),
          (3, 1, 'report', 'queued'),
          (4, 2, 'report', 'running')
        """
    )
    db.commit()


def test_list_all_jobs_ordered_by_id_with_original_columns(db):
    seed(db)
    jobs = AdminService(db).list_all_jobs()
    assert [j["id"] for j in jobs] == [1, 2, 3, 4]
    assert set(jobs[0]) == {"id", "company_id", "status"}


def test_list_all_jobs_filters_by_company_and_status(db):
    seed(db)
    service = AdminService(db)
    assert [j["id"] for j in service.list_all_jobs(company_id=1)] == [1, 2, 3]
    assert [j["id"] for j in service.list_all_jobs(company_id=1, status="failed")] == [2]
    assert service.count_all_jobs(company_id=1) == 3
    assert service.count_all_jobs(company_id=2, status="running") == 1


def test_list_companies_aggregates_by_status(db):
    seed(db)
    service = AdminService(db)
    companies = service.list_companies()
    assert service.count_companies() == 2
    acme, globex = companies
    assert acme["name"] == "Acme"
    assert (acme["total_jobs"], acme["done"], acme["failed"], acme["queued"], acme["running"], acme["cancelled"]) == (3, 1, 1, 1, 0, 0)
    assert (globex["total_jobs"], globex["running"]) == (1, 1)
    assert globex["max_concurrent_jobs"] == 3 and globex["job_quota"] == 100


def test_list_companies_paginates_before_aggregating(db):
    seed(db)
    page = AdminService(db).list_companies(limit=1, offset=1)
    assert [c["id"] for c in page] == [2]


def test_company_without_jobs_has_zeroed_counters(db):
    db.execute("INSERT INTO companies (id, name) VALUES (7, 'Vazia')")
    db.commit()
    company = AdminService(db).list_companies()[0]
    assert company["total_jobs"] == 0 and company["queued"] == 0 and company["done"] == 0
