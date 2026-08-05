"""
Two answer saves arriving at genuinely the same moment, against real PostgreSQL.

This is the one property in the autosave path that the rest of the suite is
structurally incapable of testing. Everything else runs on SQLite, which
serialises write transactions -- so the read-check-write in
`attempt_repository._write_answer` can never interleave there, and the version
check appears correct no matter how it is implemented. On PostgreSQL, with two
connections, it interleaves happily:

    T1: SELECT ... -> answer_version 4
    T2: SELECT ... -> answer_version 4     (T1 has not committed)
    T1: version 5 > 4, UPDATE, COMMIT
    T2: version 5 > 4, UPDATE, COMMIT      <- decided against a stale read

Both writes pass a check that was correct in isolation, and the winner is
whichever committed last -- the exact last-write-wins behaviour `answer_version`
exists to prevent, reintroduced by the gap between reading and writing.

SELECT ... FOR UPDATE is what closes it: T2 blocks at its SELECT until T1
commits, then re-reads and sees version 5. These tests drive the repository
directly on two separate connections, because two HTTP requests through
TestClient are serialised by the test client and would prove nothing.

Skips cleanly with no PostgreSQL available -- see test_migrations_postgres.py
for how to provide one.
"""
import threading

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

# Reuse the server fixture rather than booting a second PostgreSQL: pgserver
# start-up dominates the runtime of both files.
from tests.test_migrations_postgres import postgres_server  # noqa: F401,E402


@pytest.fixture
def pg_session_factory(postgres_server):  # noqa: F811
    """A schema built from the models, plus one attempt and one answer row.

    create_all rather than the Alembic chain on purpose: this is testing
    concurrency behaviour in the repository, and test_migrations_postgres.py
    already covers whether the migrations produce this schema.
    """
    from app.database.session import Base
    from app.models import attempt as attempt_models  # noqa: F401  (registers tables)

    url = postgres_server("autosave_concurrency")
    engine = sa.create_engine(url, pool_size=5, max_overflow=5)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    yield Session, engine
    engine.dispose()


def _seed_attempt_and_answer(Session):
    """One attempt row and one answer at version 4, bypassing the API.

    The FKs to students/exams/questions are satisfied by inserting the minimum
    parent rows; this test is about two writers on one answer row, not about the
    exam workflow that produced it.
    """
    from app.models.attempt import StudentAnswer, StudentExamAttempt

    session = Session()
    try:
        # FKs are not enforced against rows we never create if we insert with
        # them switched off for this session -- simpler and far less brittle
        # than building a whole exam to test a row lock.
        session.execute(sa.text("ALTER TABLE student_answers DISABLE TRIGGER ALL"))
        session.execute(sa.text("ALTER TABLE student_exam_attempts DISABLE TRIGGER ALL"))
        attempt = StudentExamAttempt(id=1, student_id=1, exam_id=1, question_order="1")
        session.add(attempt)
        session.flush()
        session.add(StudentAnswer(attempt_id=1, question_id=1, selected_option_id=10,
                                  answer_version=4))
        session.commit()
    finally:
        session.close()


def test_the_newest_version_wins_across_many_concurrent_writers(pg_session_factory):
    """The invariant, under enough pressure to defeat a lucky schedule.

    A single pair of racing writers can happen to commit in the right order even
    with no locking at all, so a two-thread test that passes proves little. Six
    writers with versions 5..10 released together, repeated over several rounds,
    does not: without the row lock at least one round ends with a lower version
    stored, because each writer decided against a read that was already stale by
    the time it wrote.
    """
    from app.repositories import attempt_repository

    Session, _engine = pg_session_factory
    _seed_attempt_and_answer(Session)

    base_version = 4
    for round_number in range(4):
        versions = list(range(base_version + 1, base_version + 7))  # six writers
        ready = threading.Barrier(len(versions), timeout=15)
        errors = []

        def writer(version):
            session = Session()
            try:
                ready.wait()
                attempt_repository.upsert_answer(
                    session, 1, 1, version * 10,
                    answer_version=version, idempotency_key=f"r{round_number}-v{version}",
                )
            except Exception as error:
                errors.append(error)
            finally:
                session.close()

        threads = [threading.Thread(target=writer, args=(v,)) for v in versions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors, f"round {round_number}: a writer raised: {errors}"

        session = Session()
        try:
            stored = session.execute(sa.text(
                "SELECT selected_option_id, answer_version FROM student_answers "
                "WHERE attempt_id = 1 AND question_id = 1")).one()
        finally:
            session.close()

        highest = max(versions)
        assert stored.answer_version == highest, (
            f"round {round_number}: stored version {stored.answer_version}, expected {highest} "
            "-- an older save overwrote a newer one"
        )
        assert stored.selected_option_id == highest * 10
        base_version = highest


def test_a_stale_writer_racing_a_newer_one_always_loses(pg_session_factory):
    """The failure that actually loses a candidate's work.

    A stalled version-5 retry arrives at the same instant as a genuinely newer
    version-6 change. Whatever the scheduling, the candidate must end up with
    what they last chose -- version 6 -- not with the stale retry that happened
    to commit last.
    """
    from app.repositories import attempt_repository

    Session, _engine = pg_session_factory
    _seed_attempt_and_answer(Session)

    ready = threading.Barrier(2, timeout=10)
    errors = []

    def writer(option_id, version, key):
        session = Session()
        try:
            ready.wait()
            attempt_repository.upsert_answer(session, 1, 1, option_id,
                                             answer_version=version, idempotency_key=key)
        except Exception as error:  # surfaced below rather than swallowed
            errors.append(error)
        finally:
            session.close()

    threads = [
        threading.Thread(target=writer, args=(50, 5, "stale-retry")),
        threading.Thread(target=writer, args=(60, 6, "newest")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, f"a writer raised: {errors}"

    session = Session()
    try:
        stored = session.execute(sa.text(
            "SELECT selected_option_id, answer_version FROM student_answers "
            "WHERE attempt_id = 1 AND question_id = 1")).one()
    finally:
        session.close()

    assert stored.answer_version == 6, "the stale retry overwrote a newer answer"
    assert stored.selected_option_id == 60


def test_two_first_time_writers_do_not_crash_on_the_unique_constraint(pg_session_factory):
    """FOR UPDATE locks rows that exist; it cannot lock one that does not.

    Two saves for a question answered for the very first time can both find
    nothing and both INSERT. uq_attempt_question rejects the loser, and
    _write_answer retries it once under the lock rather than surfacing an
    IntegrityError as a 503 to a candidate whose answer was fine.
    """
    from app.repositories import attempt_repository

    Session, _engine = pg_session_factory
    _seed_attempt_and_answer(Session)  # seeds question_id 1; we use 2 here

    ready = threading.Barrier(2, timeout=10)
    errors = []

    def writer(option_id, version, key):
        session = Session()
        try:
            ready.wait()
            attempt_repository.upsert_answer(session, 1, 2, option_id,
                                             answer_version=version, idempotency_key=key)
        except Exception as error:
            errors.append(error)
        finally:
            session.close()

    threads = [
        threading.Thread(target=writer, args=(70, 1, "first")),
        threading.Thread(target=writer, args=(80, 1, "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, f"the insert race was not handled: {errors}"

    session = Session()
    try:
        rows = session.execute(sa.text(
            "SELECT count(*) FROM student_answers WHERE attempt_id = 1 AND question_id = 2")).scalar()
    finally:
        session.close()
    assert rows == 1
