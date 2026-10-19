from datetime import date, datetime
import sys
import types
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool


isolated_database = types.ModuleType("database")
isolated_database.engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
isolated_database.Base = declarative_base()
isolated_database.SessionLocal = sessionmaker(bind=isolated_database.engine)
isolated_database.ensure_local_sqlite_schema = lambda _base: None
sys.modules["database"] = isolated_database

import main
from models import AttendanceSession, Employee, User


@main.app.get("/__pay_queries_test_login")
def pay_queries_test_login(request: main.Request, allowed: bool = True):
    request.session.clear()
    request.session.update(
        user_id=1, is_admin=False, time_attendance=allowed,
    )
    return {"ok": True}


test_login_route = main.app.router.routes.pop()
main.app.router.routes.insert(-1, test_login_route)


class PayQueriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.Base.metadata.create_all(
            bind=isolated_database.engine,
            tables=[User.__table__, Employee.__table__, AttendanceSession.__table__],
        )

    def setUp(self):
        with isolated_database.SessionLocal() as db:
            db.query(AttendanceSession).delete()
            db.query(Employee).delete()
            db.query(User).delete()
            db.add(User(
                id=1,
                email="pay-queries@example.invalid",
                password_hash="test",
                time_attendance=True,
            ))
            db.add_all([
                Employee(
                    PIN=101, Employee_id="0001", Name_="Ada", Surname_="Lovelace",
                    Company="PCM", Site="Durban", Division="Operations",
                ),
                Employee(
                    PIN=202, Employee_id="0002", Name_="Grace", Surname_="Hopper",
                    Company="PCM", Site="Durban", Division="Operations",
                ),
            ])
            db.add_all([
                AttendanceSession(
                    pin="101", check_in=datetime(2026, 8, 21, 9, 0),
                    check_out=datetime(2026, 8, 21, 17, 30), status="closed",
                ),
                AttendanceSession(
                    pin="101", check_in=datetime(2026, 8, 21, 23, 0),
                    check_out=datetime(2026, 8, 22, 1, 0), status="closed",
                ),
                AttendanceSession(
                    pin="101", check_in=datetime(2026, 9, 20, 23, 0),
                    check_out=datetime(2026, 9, 21, 1, 0), status="closed",
                ),
                AttendanceSession(
                    pin="101", check_in=datetime(2026, 8, 25, 10, 0),
                    check_out=None, status="open",
                ),
                AttendanceSession(
                    pin="999", check_in=datetime(2026, 8, 23, 10, 0),
                    check_out=datetime(2026, 8, 23, 11, 0), status="closed",
                ),
            ])
            db.commit()
        self.client = TestClient(main.app)
        self.client.get("/__pay_queries_test_login")

    def tearDown(self):
        self.client.close()

    def test_period_defaults_to_21st_through_20th_across_year_boundary(self):
        self.assertEqual(
            main.get_pay_query_period(date(2026, 10, 19)),
            (date(2026, 9, 21), date(2026, 10, 20)),
        )
        self.assertEqual(
            main.get_pay_query_period(date(2026, 10, 21)),
            (date(2026, 10, 21), date(2026, 11, 20)),
        )
        self.assertEqual(
            main.get_pay_query_period(date(2026, 1, 20)),
            (date(2025, 12, 21), date(2026, 1, 20)),
        )
        self.assertEqual(
            main.get_pay_query_period(date(2026, 12, 21)),
            (date(2026, 12, 21), date(2027, 1, 20)),
        )

    def test_report_splits_overnight_hours_and_includes_roster_and_unknown_pins(self):
        response = self.client.get(
            "/api/pay-queries",
            params={"start_date": "2026-08-21", "end_date": "2026-09-20"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(len(result["days"]), 31)
        self.assertEqual(result["days"][0], "2026-08-21")
        self.assertEqual(result["days"][-1], "2026-09-20")
        rows = {row["employee_number"]: row for row in result["rows"]}
        self.assertEqual(set(rows), {"0001", "0002", "999"})
        self.assertEqual(rows["0001"]["name"], "Ada")
        self.assertEqual(rows["0001"]["daily_seconds"]["2026-08-21"], 9 * 3600 + 30 * 60)
        self.assertEqual(rows["0001"]["daily_seconds"]["2026-08-22"], 3600)
        self.assertEqual(rows["0001"]["daily_seconds"]["2026-09-20"], 3600)
        self.assertEqual(rows["0001"]["total_seconds"], 11 * 3600 + 30 * 60)
        self.assertEqual(rows["0002"]["total_seconds"], 0)
        self.assertEqual(rows["999"]["total_seconds"], 3600)
        self.assertEqual(result["included_sessions"], "completed")
        self.assertNotIn("due_hours", rows["0001"])
        self.assertNotIn("balance", rows["0001"])

    def test_rejects_invalid_reversed_and_excessive_ranges(self):
        for params in (
            {"start_date": "2026-02-30", "end_date": "2026-03-01"},
            {"start_date": "2026-09-21", "end_date": "2026-09-20"},
            {"start_date": "2026-01-01", "end_date": "2026-03-05"},
        ):
            with self.subTest(params=params):
                self.assertEqual(
                    self.client.get("/api/pay-queries", params=params).status_code,
                    400,
                )

    def test_access_control_applies_to_page_and_api(self):
        self.assertEqual(self.client.get("/pay-queries").status_code, 200)
        self.client.get("/__pay_queries_test_login?allowed=false")
        self.assertEqual(
            self.client.get("/pay-queries", follow_redirects=False).status_code,
            302,
        )
        self.assertEqual(self.client.get("/api/pay-queries").status_code, 403)


if __name__ == "__main__":
    unittest.main()
