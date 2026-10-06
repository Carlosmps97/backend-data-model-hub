"""Doc 110 — conexiones PROBADAS para los scripts.

Cada conexión nueva sube 256 KB (`SELECT length($1)`): en una ruta con pérdida
(50-70 KB/s) tarda varios segundos y se descarta; en una buena (≥1 MB/s) o
dentro de Databricks, milisegundos. Sólo el puente de scripts lo pide
(`create_pool(probe=True)`); la app abre sus conexiones como siempre."""
from __future__ import annotations

import asyncio

from app.core.db.lakebase import pool as pool_mod


class ProbeConn:
    def __init__(self, *, slow=False, fail=None) -> None:
        self.slow, self.fail = slow, fail
        self.closed = False
        self.sent: list[int] = []

    async def fetchval(self, sql, *args):
        self.sent.append(sum(len(a) for a in args if isinstance(a, str)))
        if self.fail is not None:
            raise self.fail
        if self.slow:
            await asyncio.sleep(3600)
        return self.sent[-1]

    def terminate(self) -> None:
        self.closed = True


def _script(monkeypatch, conns: list[ProbeConn]) -> list[ProbeConn]:
    queue, handed = list(conns), []

    async def fake_connect(*args, **kwargs):
        conn = queue.pop(0)
        handed.append(conn)
        return conn

    monkeypatch.setattr(pool_mod.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(pool_mod, "_PROBE_TIMEOUT_S", 0.05)
    return handed


def test_descarta_las_conexiones_lentas_y_entrega_la_primera_buena(monkeypatch):
    handed = _script(monkeypatch, [ProbeConn(slow=True), ProbeConn(slow=True), ProbeConn()])
    before = pool_mod.PROBE_STATS["descartadas"]
    conn = asyncio.run(pool_mod.probing_connect(host="h"))
    assert conn is handed[2]
    assert [c.closed for c in handed] == [True, True, False]
    assert pool_mod.PROBE_STATS["descartadas"] - before == 2
    assert handed[2].sent[0] >= 256 * 1024          # mide ancho de banda, no sólo latencia


def test_una_conexion_que_se_cae_en_la_prueba_tambien_se_descarta(monkeypatch):
    handed = _script(monkeypatch, [ProbeConn(fail=ConnectionResetError(54, "reset")), ProbeConn()])
    conn = asyncio.run(pool_mod.probing_connect(host="h"))
    assert conn is handed[1] and handed[0].closed


def test_si_todas_salen_lentas_sigue_con_la_ultima_sin_frenar_la_carga(monkeypatch):
    monkeypatch.setattr(pool_mod, "_PROBE_TRIES", 3)
    handed = _script(monkeypatch, [ProbeConn(slow=True) for _ in range(3)])
    conn = asyncio.run(pool_mod.probing_connect(host="h"))
    assert conn is handed[2] and not conn.closed
    assert [c.closed for c in handed[:2]] == [True, True]


class _FakePool:
    def acquire(self):
        class Conn:
            async def execute(self, sql):
                return "SELECT 1"

        class Acq:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *a):
                return False
        return Acq()


def test_solo_los_scripts_piden_conexiones_probadas(monkeypatch):
    seen: list[dict] = []

    async def fake_create_pool(**kwargs):
        seen.append(kwargs)
        return _FakePool()

    monkeypatch.setattr(pool_mod.asyncpg, "create_pool", fake_create_pool)
    asyncio.run(pool_mod._open_pool("h", False))
    asyncio.run(pool_mod._open_pool("h", False, probe=True))
    assert seen[0].get("connect") is None
    assert seen[1]["connect"] is pool_mod.probing_connect
