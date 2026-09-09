"""Builders puros de create_admin (seed que corre el one-shot de Databricks):
cuenta local `admin` con su contraseña y whitelist SSO (admins + modeladores,
doc 77 §6). Sin BD."""
from __future__ import annotations

from app.core.security import verify_password
from scripts.create_admin import (
    ADMIN_EMAILS, ADMIN_PASSWORD, ADMIN_USERNAME, MODELER_EMAILS,
    build_admin_doc, build_whitelist_users,
)

# Padrón declarado de la imagen (14 correos con rol Modeler).
_MODELERS = {
    "angellachavez@bcp.com.pe", "edsonmio@bcp.com.pe", "henryyancul@bcp.com.pe",
    "jaimesanchez@bcp.com.pe", "jessicavega@bcp.com.pe", "jesusmsanchez@bcp.com.pe",
    "jferro@bcp.com.pe", "jhonnyimillones@bcp.com.pe", "juanapoon@bcp.com.pe",
    "katterinemaguina@bcp.com.pe", "kevinavalos@bcp.com.pe", "paulisla@bcp.com.pe",
    "ricardohinostroza@bcp.com.pe", "roggerpuse@bcp.com.pe",
}


def test_padrones_declarados():
    assert ADMIN_EMAILS == ("carlosperez@bcp.com.pe",)
    assert set(MODELER_EMAILS) == _MODELERS
    todos = (*ADMIN_EMAILS, *MODELER_EMAILS)
    assert len(todos) == len(set(todos)) == 15          # sin duplicados entre listas
    assert all(e == e.lower() and e.endswith("@bcp.com.pe") for e in todos)


def test_whitelist_da_administrador_a_los_admins_y_modelador_al_resto():
    users = build_whitelist_users()
    roles = {u["_id"]: u["role"] for u in users}
    assert roles["carlosperez@bcp.com.pe"] == "administrador"
    assert {e: roles[e] for e in _MODELERS} == {e: "modelador" for e in _MODELERS}
    assert len(users) == 15


def test_whitelist_son_entradas_sso_sin_contrasena():
    for u in build_whitelist_users():
        assert u["_id"] == u["email"]              # username == correo
        assert u["status"] == "active"
        assert u["projectIds"] == []               # todos los proyectos hasta acotar
        assert u["flgactive"] is True
        assert "passwordHash" not in u             # SSO: nunca contraseña local


def test_whitelist_acepta_listas_vacias_sin_fallar():
    assert build_whitelist_users(admins=(), modelers=()) == []
    solo_admin = build_whitelist_users(admins=("jefe@bcp.com.pe",), modelers=())
    assert [u["role"] for u in solo_admin] == ["administrador"]


def test_whitelist_normaliza_y_descarta_vacios():
    users = build_whitelist_users(admins=("  JEFE@BCP.com.pe ", "", "   "),
                                  modelers=("Otro@BCP.com.pe",))
    assert [u["_id"] for u in users] == ["jefe@bcp.com.pe", "otro@bcp.com.pe"]


def test_correo_en_ambas_listas_queda_como_administrador():
    users = build_whitelist_users(admins=("dos@bcp.com.pe",),
                                  modelers=("dos@bcp.com.pe", "uno@bcp.com.pe"))
    assert [(u["_id"], u["role"]) for u in users] == [
        ("dos@bcp.com.pe", "administrador"), ("uno@bcp.com.pe", "modelador")]


def test_build_admin_doc_lleva_la_contrasena_declarada():
    admin = build_admin_doc()
    assert admin["_id"] == ADMIN_USERNAME == "admin"
    assert admin["role"] == "administrador"
    assert ADMIN_PASSWORD == "BCP$4M4Y2026"
    assert verify_password(ADMIN_PASSWORD, admin["passwordHash"]) is True
    assert verify_password("admin", admin["passwordHash"]) is False  # ya no es la vieja


def test_admin_no_colisiona_con_la_whitelist():
    assert ADMIN_USERNAME not in {u["_id"] for u in build_whitelist_users()}
