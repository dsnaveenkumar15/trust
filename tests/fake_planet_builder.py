from core.worker import JobType


def build_job_types(conn):
    def stage(job, out):
        conn.execute("INSERT INTO events(type, version, payload, created_at) VALUES ('built', 1, '{}', 0)")
        return 1

    return {"fake.built": JobType("fake", [("s", stage)])}
