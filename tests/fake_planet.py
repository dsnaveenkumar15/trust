from core.worker import JobType

JOB_TYPES = {"fake.echo": JobType(planet="fake", stages=[("echo", lambda job, out: job.payload)])}
from core.periodic import schedule


def schedule_periodic(conn, now):
    schedule(conn, "fake", "fake.echo", 60, now=now)
