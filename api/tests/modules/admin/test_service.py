from modules.admin.service import AdminService


def test_list_all_jobs_ordered_by_id(db):
    db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme'), (2, 'Globex')")
    db.execute("INSERT INTO jobs (id, company_id, kind, status) VALUES (2, 2, 'report', 'queued'), (1, 1, 'report', 'done')")
    db.commit()
    jobs = AdminService(db).list_all_jobs()
    assert [j["id"] for j in jobs] == [1, 2]
