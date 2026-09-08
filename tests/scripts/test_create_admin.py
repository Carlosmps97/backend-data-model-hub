"""Builders puros de create_admin (seed que corre el one-shot de Databricks):
cuenta local `admin` con su contraseña y whitelist SSO de Modeladores. Sin BD."""
from __future__ import annotations

from app.core.security import verify_password
from scripts.create_admin import (
    ADMIN_PASSWORD, ADMIN_USERNAME, MODELER_EMAILS,
    build_admin_doc, build_modeler_users,
)

# Padrón declarado de la imagen (14 correos con rol Modeler).
_EXPECTED = {
    "angellachavez@bcp.com.pe", "edsonmio@bcp.com.pe", "henryyancul@bcp.com.pe",
    "jaimesanchez@bcp.com.pe", "jessicavega@bcp.com.pe", "jesusmsanchez@bcp.com.pe",
    "jferro@bcp.com.pe", "jhonnyimillones@bcp.com.pe", "juanapoon@bcp.com.pe",
    "katterinemaguina@bcp.com.pe", "kevinavalos@bcp.com.pe", "paulisla@bcp.com.pe",
    "ricardohinostroza@bcp.com.pe", "roggerpuse@bcp.com.pe",
}


def test_modeler_emails_es_el_padron_declarado():
    assert set(MODELER_EMAILS) == _EXPECTED
    assert len(MODELER_EMAILS) == len(set(MODELER_EMAILS)) == 14  # sin duplicados
    assert all(e == e.lower() and e.endswith("@bcp.com.pe") for e in MODELER_EMAILS)


def test_build_modeler_users_son_entradas_de_whitelist_sso():
    users = build_modeler_users()
    assert {u["_id"] for u in users} == _EXPECTED
    for u in users:
        assert u["_id"] == u["email"]              # username == correo
        assert u["role"] == "modelador"            # rol Modeler
        assert u["status"] == "active"
        assert u["projectIds"] == []               # todos los proyectos hasta acotar
        assert u["flgactive"] is True
        assert "passwordHash" not in u             # SSO: nunca contraseña local


def test_build_admin_doc_lleva_la_contrasena_declarada():
    admin = build_admin_doc()
    assert admin["_id"] == ADMIN_USERNAME == "admin"
    assert admin["role"] == "administrador"
    assert ADMIN_PASSWORD == "BCP$4M4Y2026"
    assert verify_password(ADMIN_PASSWORD, admin["passwordHash"]) is True
    assert verify_password("admin", admin["passwordHash"]) is False  # ya no es la vieja


def test_admin_no_colisiona_con_la_whitelist():
    assert ADMIN_USERNAME not in {u["_id"] for u in build_modeler_users()}
