import ast
from datetime import date, datetime
import logging
from pathlib import Path
import re
import secrets
import types
import unittest
from typing import Any, Optional
from unittest.mock import patch

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware


ROOT = Path(__file__).resolve().parents[1]


def load_isolated_models():
    # Avoid importing database.py or main.py: both initialize the configured database.
    tree = ast.parse((ROOT / "models.py").read_text(encoding="utf-8-sig"))
    tree.body = [
        node for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and node.module == "database")
    ]
    module = types.ModuleType("isolated_personnel_models")
    module.Base = declarative_base()
    exec(compile(tree, str(ROOT / "models.py"), "exec"), module.__dict__)
    return module


MODELS = load_isolated_models()


class PersonnelEnrolmentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        MODELS.Base.metadata.create_all(self.engine, tables=[
            MODELS.User.__table__, MODELS.Employee.__table__,
            MODELS.PersonnelEmployee.__table__,
        ])
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([
                MODELS.User(id=1, email="one@example.invalid", password_hash="test",
                            time_attendance=True),
                MODELS.User(id=2, email="two@example.invalid", password_hash="test",
                            time_attendance=True),
            ])
            db.commit()

        names = {
            "get_db", "_sync_session_permissions", "_get_or_refresh_permission",
            "_ensure_page_access", "_ensure_api_access",
            "_personnel_enrolment_response", "personnel_hub", "personnel_enrolment",
            "save_personnel_enrolment",
        }
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
        selected = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                node.decorator_list = []
                selected.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "PERSONNEL_FORM_SECTIONS"
                for target in node.targets
            ):
                selected.append(node)
        namespace = {
            "Session": Session, "SessionLocal": self.sessions,
            "User": MODELS.User, "PersonnelEmployee": MODELS.PersonnelEmployee,
            "Request": Request, "Depends": Depends, "HTTPException": HTTPException,
            "RedirectResponse": RedirectResponse,
            "templates": Jinja2Templates(directory=str(ROOT / "templates")),
            "Optional": Optional, "Any": Any, "datetime": datetime, "date": date,
            "logging": logging, "secrets": secrets, "re": re, "text": text,
            "IntegrityError": IntegrityError,
            "__name__": "personnel_enrolment_test",
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]),
                     str(ROOT / "main.py"), "exec"), namespace)
        self.namespace = namespace
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test-only")
        self.app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
        self.app.add_api_route(
            "/personnel-hub", namespace["personnel_hub"], methods=["GET"])
        self.app.add_api_route(
            "/personnel-hub/enrolment", namespace["personnel_enrolment"], methods=["GET"])
        self.app.add_api_route(
            "/personnel-hub/enrolment/save", namespace["save_personnel_enrolment"],
            methods=["POST"])

        @self.app.get("/test-login")
        def login(request: Request, user_id: int = 1, allowed: bool = True):
            request.session.clear()
            request.session.update(
                user_id=user_id, is_admin=False, time_attendance=allowed)
            return {"ok": True}

        self.client = TestClient(self.app)
        self.client.get("/test-login")

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def payload(self, **changes):
        response = self.client.get("/personnel-hub/enrolment")
        self.assertEqual(response.status_code, 200)
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        values = {
            "csrf_token": token, "employee_number": "0001",
            "first_name": "Test", "surname": "Person", "employment_status": "active",
        }
        values.update(changes)
        return values

    def save(self, **changes):
        return self.client.post(
            "/personnel-hub/enrolment/save", data=self.payload(**changes),
            follow_redirects=False,
        )

    def test_create_refresh_and_edit_preserve_audit_and_attendance(self):
        response = self.save(
            identity_type="passport", identity_number="P001", email="test@example.invalid",
            phone="0123456789", address_line_1="10 Example Road", address_line_2="Unit 2",
            city="Example Town", province="Example Region", postal_code="0002",
            next_of_kin_name="Kin", next_of_kin_relationship="Sibling",
            next_of_kin_phone="0987654321", company="PCM", site="Site A",
            division="Ops", job_title="Operator", start_date="2026-01-01",
            end_date="2026-12-31",
            created_by_user_id="999", updated_by_user_id="999",
        )
        self.assertEqual(response.status_code, 303)
        location = response.headers["location"]
        page = self.client.get(location)
        self.assertIn("Personnel profile saved.", page.text)
        self.assertIn('value="0001"', page.text)
        self.assertIn('value="2026-01-01"', page.text)
        self.assertIn('value="10 Example Road"', page.text)
        self.assertNotIn("Personnel profile saved.", self.client.get(location).text)
        with self.sessions() as db:
            person = db.query(MODELS.PersonnelEmployee).one()
            person_id = person.id
            self.assertEqual(person.employee_number, "0001")
            self.assertEqual(person.created_by_user_id, 1)
            self.assertEqual(person.updated_by_user_id, 1)
            self.assertIsNotNone(person.created_at)
            self.assertIsNotNone(person.updated_at)
            self.assertEqual(person.next_of_kin_phone, "0987654321")
            self.assertEqual(person.end_date, date(2026, 12, 31))
            self.assertEqual(db.query(MODELS.Employee).count(), 0)

        self.client.get("/test-login?user_id=2")
        response = self.save(profile_id=str(person_id), job_title="Supervisor",
                             employment_status="inactive")
        self.assertEqual(response.status_code, 303)
        self.assertIn("Personnel profile updated.", self.client.get(location).text)
        with self.sessions() as db:
            person = db.get(MODELS.PersonnelEmployee, person_id)
            self.assertEqual(person.job_title, "Supervisor")
            self.assertEqual(person.created_by_user_id, 1)
            self.assertEqual(person.updated_by_user_id, 2)
            self.assertEqual(person.employment_status, "inactive")
            self.assertIsNone(person.email)
            self.assertEqual(db.query(MODELS.PersonnelEmployee).count(), 1)
            self.assertEqual(db.query(MODELS.Employee).count(), 0)

    def test_duplicate_create_and_edit(self):
        self.assertEqual(self.save().status_code, 303)
        response = self.save(first_name="Kept")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already enrolled", response.text)
        self.assertIn('value="Kept"', response.text)
        second = self.save(employee_number="0002")
        self.assertEqual(second.status_code, 303)
        second_id = second.headers["location"].split("=")[1]
        self.assertEqual(self.save(profile_id=second_id).status_code, 400)
        with self.sessions() as db:
            self.assertEqual(db.query(MODELS.PersonnelEmployee).count(), 2)

    def test_invalid_fields_do_not_save(self):
        cases = [
            ({"first_name": "   "}, "Name is required"),
            ({"employee_number": "x" * 51}, "50 characters"),
            ({"surname": "x" * 101}, "100 characters"),
            ({"email": "bad-email"}, "valid email"),
            ({"identity_type": "other"}, "Choose ID or passport"),
            ({"identity_number": "ID001"}, "both the identity type and number"),
            ({"employment_status": "on_leave"}, "active or inactive"),
            ({"start_date": "2026-02-30"}, "valid date"),
            ({"start_date": "0999-01-01"}, "valid date"),
            ({"start_date": "2026-10-01", "end_date": "2026-09-01"}, "before start"),
            ({"manager_personnel_id": "999"}, "no longer exists"),
            ({"manager_personnel_id": "abc"}, "valid manager"),
            ({"manager_personnel_id": "9" * 5000}, "valid manager"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes):
                response = self.save(**changes)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.text)
        with self.sessions() as db:
            self.assertEqual(db.query(MODELS.PersonnelEmployee).count(), 0)

    def test_manager_selection_and_self_manager_rejection(self):
        first = self.save()
        manager_id = first.headers["location"].split("=")[1]
        second = self.save(employee_number="0002", manager_personnel_id=manager_id)
        self.assertEqual(second.status_code, 303)
        with self.sessions() as db:
            person = db.query(MODELS.PersonnelEmployee).filter_by(employee_number="0002").one()
            self.assertEqual(person.manager_personnel_id, int(manager_id))
        response = self.save(profile_id=manager_id, manager_personnel_id=manager_id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("own manager", response.text)
        edit = self.client.get(first.headers["location"])
        self.assertNotIn(f'<option value="{manager_id}"', edit.text)

    def test_access_and_csrf(self):
        data = self.payload()
        response = self.client.post("/personnel-hub/enrolment/save",
                                    data={**data, "csrf_token": "wrong"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/personnel-hub/enrolment/save",
                                    data={**data, "csrf_token": "\u00e9"})
        self.assertEqual(response.status_code, 403)
        self.client.get("/test-login?allowed=false")
        response = self.client.get("/personnel-hub/enrolment", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.post("/personnel-hub/enrolment/save", data=data).status_code, 403)
        self.client.cookies.clear()
        response = self.client.get("/personnel-hub/enrolment", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(self.client.post("/personnel-hub/enrolment/save", data=data).status_code, 401)
        self.client.get("/test-login?user_id=999")
        self.assertEqual(self.client.get("/personnel-hub/enrolment").status_code, 401)

    def test_missing_and_invalid_edit_ids(self):
        self.assertEqual(self.client.get("/personnel-hub/enrolment?edit=999").status_code, 404)
        self.assertEqual(self.client.get("/personnel-hub/enrolment?edit=0").status_code, 400)
        self.assertEqual(self.save(profile_id="999").status_code, 404)
        self.assertEqual(self.save(profile_id="invalid").status_code, 400)
        self.assertEqual(self.save(profile_id="9" * 5000).status_code, 400)

    def test_integrity_failure_rolls_back_and_reports_error(self):
        data = self.payload()
        with patch.object(Session, "commit", side_effect=IntegrityError("test", {}, Exception("test"))):
            with self.assertLogs("personnel_enrolment_test", level="ERROR"):
                response = self.client.post("/personnel-hub/enrolment/save", data=data)
        self.assertEqual(response.status_code, 409)
        self.assertIn("could not be saved", response.text)
        with self.sessions() as db:
            self.assertEqual(db.query(MODELS.PersonnelEmployee).count(), 0)

    def test_output_is_escaped(self):
        self.assertEqual(self.save(first_name='<script>alert("x")</script>').status_code, 303)
        page = self.client.get("/personnel-hub/enrolment")
        self.assertNotIn('<script>alert("x")</script>', page.text)
        self.assertIn("&lt;script&gt;", page.text)

    def test_hub_exposes_enrolment_as_available(self):
        response = self.client.get("/personnel-hub")
        self.assertEqual(response.status_code, 200)
        self.assertIn("4 available", response.text)
        self.assertIn("3 planned", response.text)
        self.assertIn("Create and edit personnel profiles", response.text)


if __name__ == "__main__":
    unittest.main()
