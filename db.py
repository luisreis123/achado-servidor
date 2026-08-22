"""
Persistencia em SQLite.

SQLite nao e um servidor separado — e um ficheiro (achado.db) que vive
ao lado deste ficheiro .py. Nao precisa de instalar nada extra (vem
incluido no Python), nem de conta em lado nenhum.

AVISO sobre o Render (plano gratuito): o disco nao e garantidamente
persistente entre reinicios do servico. Isto e aceitavel para uso
pessoal leve — o historico pode ocasionalmente "reiniciar" — mas nao
o uses para dados que precises de guardar para sempre.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

CAMINHO_BD = Path(__file__).parent / "achado.db"


@contextmanager
def ligar():
    conn = sqlite3.connect(CAMINHO_BD)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def inicializar():
    with ligar() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alertas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                cidade TEXT NOT NULL,
                tipos TEXT NOT NULL,
                operacao TEXT NOT NULL,
                fontes TEXT NOT NULL,
                criado_em INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS imoveis_vistos (
                alerta_id INTEGER NOT NULL,
                hash_imovel TEXT NOT NULL,
                visto_em INTEGER NOT NULL,
                PRIMARY KEY (alerta_id, hash_imovel)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS historico_precos (
                hash_imovel TEXT NOT NULL,
                titulo TEXT,
                local TEXT,
                preco REAL,
                registado_em INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_historico_hash ON historico_precos(hash_imovel)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS geocoding_cache (
                endereco TEXT PRIMARY KEY,
                latitude REAL,
                longitude REAL,
                obtido_em INTEGER NOT NULL
            )
        """)


# --- Alertas (pesquisas guardadas) ---

def criar_alerta(email: str, cidade: str, tipos: str, operacao: str, fontes: str) -> int:
    with ligar() as conn:
        cursor = conn.execute(
            "INSERT INTO alertas (email, cidade, tipos, operacao, fontes, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (email, cidade, tipos, operacao, fontes, int(time.time())),
        )
        return cursor.lastrowid


def listar_alertas(email: str | None = None) -> list[dict]:
    with ligar() as conn:
        if email:
            linhas = conn.execute("SELECT * FROM alertas WHERE email = ?", (email,)).fetchall()
        else:
            linhas = conn.execute("SELECT * FROM alertas").fetchall()
        return [dict(l) for l in linhas]


def apagar_alerta(alerta_id: int):
    with ligar() as conn:
        conn.execute("DELETE FROM alertas WHERE id = ?", (alerta_id,))
        conn.execute("DELETE FROM imoveis_vistos WHERE alerta_id = ?", (alerta_id,))


# --- Deduplicação entre execuções do alerta (para saber o que é "novo") ---

def ja_visto(alerta_id: int, hash_imovel: str) -> bool:
    with ligar() as conn:
        linha = conn.execute(
            "SELECT 1 FROM imoveis_vistos WHERE alerta_id = ? AND hash_imovel = ?",
            (alerta_id, hash_imovel),
        ).fetchone()
        return linha is not None


def marcar_visto(alerta_id: int, hash_imovel: str):
    with ligar() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO imoveis_vistos (alerta_id, hash_imovel, visto_em) VALUES (?, ?, ?)",
            (alerta_id, hash_imovel, int(time.time())),
        )


# --- Histórico de preços ---

def registar_preco(hash_imovel: str, titulo: str, local: str, preco: float | None):
    if preco is None:
        return
    with ligar() as conn:
        # Só regista se o preço mudou desde a última observação (evita entradas repetidas)
        ultimo = conn.execute(
            "SELECT preco FROM historico_precos WHERE hash_imovel = ? ORDER BY registado_em DESC LIMIT 1",
            (hash_imovel,),
        ).fetchone()
        if ultimo and ultimo["preco"] == preco:
            return
        conn.execute(
            "INSERT INTO historico_precos (hash_imovel, titulo, local, preco, registado_em) VALUES (?, ?, ?, ?, ?)",
            (hash_imovel, titulo, local, preco, int(time.time())),
        )


def obter_historico(hash_imovel: str) -> list[dict]:
    with ligar() as conn:
        linhas = conn.execute(
            "SELECT preco, registado_em FROM historico_precos WHERE hash_imovel = ? ORDER BY registado_em ASC",
            (hash_imovel,),
        ).fetchall()
        return [dict(l) for l in linhas]


# --- Cache de geocoding (morada -> coordenadas) ---

def obter_coordenadas_cache(endereco: str) -> tuple[float, float] | None:
    with ligar() as conn:
        linha = conn.execute(
            "SELECT latitude, longitude FROM geocoding_cache WHERE endereco = ?",
            (endereco,),
        ).fetchone()
        return (linha["latitude"], linha["longitude"]) if linha else None


def guardar_coordenadas_cache(endereco: str, latitude: float | None, longitude: float | None):
    with ligar() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO geocoding_cache (endereco, latitude, longitude, obtido_em) VALUES (?, ?, ?, ?)",
            (endereco, latitude, longitude, int(time.time())),
        )
