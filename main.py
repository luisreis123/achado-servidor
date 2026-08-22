"""
Servidor de pesquisa em tempo real, com histórico de preços e alertas
por email — sem base de dados gerida externamente (usa SQLite, um
único ficheiro local) e sem servidor de scraping contínuo além deste.

IMPORTANTE:
Os adaptadores marcados como TEMPLATE têm seletores/URLs ilustrativos.
Antes de apontar a um site real:
  1. Confirma o robots.txt do site
  2. Inspeciona o HTML real (ou os blocos JSON-LD, ver adaptador_json_ld.py)
  3. Mantém o rate limiting (ver SEMAFORO)

Como correr:
  pip install -r requirements.txt
  uvicorn main:app --reload

Variáveis de ambiente opcionais (para os alertas por email funcionarem):
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
  (ver email_alertas.py para detalhes)
"""

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import email_alertas
from adaptador_json_ld import buscar_via_json_ld
from adaptador_imovirtual import buscar_imovirtual

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("achado")

agendador = AsyncIOScheduler()


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    db.inicializar()
    agendador.add_job(verificar_todos_os_alertas, "interval", minutes=30, id="verificar_alertas")
    agendador.start()
    logger.info("Servidor iniciado — base de dados pronta, agendador de alertas a correr a cada 30 min.")
    yield
    agendador.shutdown()


app = FastAPI(title="Achado — pesquisa em tempo real", lifespan=ciclo_de_vida)

# Permite que o ficheiro HTML (aberto localmente ou noutro domínio) chame este servidor
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Limita quantos pedidos HTTP simultaneos fazemos no total,
# para nao sobrecarregar os sites de origem
SEMAFORO = asyncio.Semaphore(4)

CABECALHOS = {
    "User-Agent": "AchadoAgregadorBot/0.1 (uso pessoal; +http://localhost)"
}


# ---------------------------------------------------------------------------
# Adaptadores por fonte — um por site. Cada um recebe os filtros e devolve
# uma lista de dicionarios ja normalizados para o schema comum.
# ---------------------------------------------------------------------------

async def buscar_exemplo_portal_a(cliente: httpx.AsyncClient, cidade: str, tipo: str, operacao: str) -> list[dict]:
    """
    TEMPLATE — substituir 'exemplo-portal-a.pt' e os seletores pelos
    reais depois de confirmar robots.txt e inspecionar o HTML do site.
    """
    fonte = "exemplo_portal_a"
    url = f"https://www.exemplo-portal-a.pt/pesquisa?local={cidade}&tipo={tipo}&op={operacao}"

    resultados = []
    try:
        async with SEMAFORO:
            resposta = await cliente.get(url, headers=CABECALHOS, timeout=10)
        if resposta.status_code != 200:
            logger.warning(f"[{fonte}] resposta {resposta.status_code}")
            return []

        soup = BeautifulSoup(resposta.text, "html.parser")
        anuncios = soup.select("article.listing-item")  # ajustar ao HTML real

        for anuncio in anuncios[:20]:  # limite de seguranca por pesquisa
            item = {
                "fonte": fonte,
                "titulo": _texto(anuncio.select_one(".titulo")),
                "url_original": _href_absoluto(anuncio.select_one("a"), url),
                "preco": _numero(anuncio.select_one(".preco")),
                "area_m2": _numero(anuncio.select_one(".area")),
                "quartos": _numero(anuncio.select_one(".quartos")),
                "local": _texto(anuncio.select_one(".localizacao")) or cidade,
                "tipo": tipo,
                "operacao": operacao,
                "thumbnail_url": _src_absoluto(anuncio.select_one("img"), url),
            }
            if item["titulo"] and item["url_original"]:
                resultados.append(item)

    except (httpx.RequestError, httpx.TimeoutException) as erro:
        logger.warning(f"[{fonte}] falhou: {erro}")
        return []

    return resultados


async def buscar_exemplo_portal_b(cliente: httpx.AsyncClient, cidade: str, tipo: str, operacao: str) -> list[dict]:
    """Segundo adaptador de exemplo — duplicar este padrão por cada site novo."""
    fonte = "exemplo_portal_b"
    # ... implementacao equivalente, com seletores proprios deste site
    return []


async def buscar_portal_real_json_ld(cliente: httpx.AsyncClient, cidade: str, tipo: str, operacao: str) -> list[dict]:
    """
    EXEMPLO de adaptador para um site real, usando o método JSON-LD
    (ver adaptador_json_ld.py) em vez de seletores CSS.

    ATENÇÃO: o URL abaixo ('exemplo-real.pt') e a estrutura da query
    são ilustrativos — substitui pelo padrão de URL real do site que
    escolheres, DEPOIS de confirmares o robots.txt. O padrão de URL de
    pesquisa varia muito de site para site (parâmetros diferentes,
    slugs diferentes para cidade/tipo/operação).
    """
    fonte = "portal_real_exemplo"
    operacao_slug = "arrendar" if operacao == "arrendamento" else "comprar"
    url = f"https://www.exemplo-real.pt/{operacao_slug}-{tipo}/{cidade.lower()}/"

    return await buscar_via_json_ld(cliente, SEMAFORO, CABECALHOS, fonte, url, tipo, operacao)


async def _buscar_imovirtual_wrapper(cliente: httpx.AsyncClient, cidade: str, tipo: str, operacao: str) -> list[dict]:
    """Adapta a assinatura de buscar_imovirtual ao formato comum dos outros adaptadores."""
    return await buscar_imovirtual(cliente, SEMAFORO, CABECALHOS, cidade, tipo, operacao)


# Registo de fontes disponiveis: id -> (nome legivel, funcao adaptadora)
FONTES_DISPONIVEIS = {
    "exemplo_portal_a": ("Portal A (exemplo)", buscar_exemplo_portal_a),
    "exemplo_portal_b": ("Portal B (exemplo)", buscar_exemplo_portal_b),
    "portal_real_exemplo": ("Portal real (por configurar)", buscar_portal_real_json_ld),
    "imovirtual": ("Imovirtual", _buscar_imovirtual_wrapper),
}


# ---------------------------------------------------------------------------
# Funcoes auxiliares de extracao/normalizacao
# ---------------------------------------------------------------------------

def _texto(el) -> Optional[str]:
    return el.get_text(strip=True) if el else None


def _numero(el) -> Optional[float]:
    if not el:
        return None
    bruto = el.get_text(strip=True)
    limpo = "".join(c for c in bruto if c.isdigit() or c in ",.")
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _href_absoluto(el, base_url: str) -> Optional[str]:
    if not el or not el.get("href"):
        return None
    href = el["href"]
    if href.startswith("http"):
        return href
    from urllib.parse import urljoin
    return urljoin(base_url, href)


def _src_absoluto(el, base_url: str) -> Optional[str]:
    if not el or not el.get("src"):
        return None
    src = el["src"]
    if src.startswith("http"):
        return src
    from urllib.parse import urljoin
    return urljoin(base_url, src)


def _hash_dedup(item: dict) -> str:
    """
    Sem geocoding em tempo real (teria de chamar um servico externo por
    resultado, o que tornaria a pesquisa lenta), a deduplicacao aqui usa
    localizacao textual + area + preco aproximado como impressao digital.
    Menos precisa que comparar coordenadas, mas nao exige passos extra.
    """
    local = (item.get("local") or "").strip().lower()
    area = round(item.get("area_m2") or 0, -1)  # arredonda a dezena
    preco = round((item.get("preco") or 0) / 5000) * 5000  # janelas de 5000 EUR
    chave = f"{local}|{item.get('tipo')}|{area}|{preco}"
    return hashlib.sha256(chave.encode()).hexdigest()


def _agrupar_duplicados(items: list[dict]) -> list[dict]:
    """Agrupa itens com o mesmo hash de deduplicacao, juntando as fontes."""
    grupos: dict[str, dict] = {}

    for item in items:
        h = _hash_dedup(item)
        if h not in grupos:
            item["_fontes"] = [item["fonte"]]
            item["_urls_por_fonte"] = {item["fonte"]: item["url_original"]}
            grupos[h] = item
        else:
            grupos[h]["_fontes"].append(item["fonte"])
            grupos[h]["_urls_por_fonte"][item["fonte"]] = item["url_original"]

    return list(grupos.values())


# ---------------------------------------------------------------------------
# Lógica de pesquisa reutilizável (usada pelo endpoint /pesquisar e pelos alertas)
# ---------------------------------------------------------------------------

async def executar_pesquisa(cidade: str, tipos_lista: list[str], operacao: str, fontes_pedidas: list[str]) -> list[dict]:
    tarefas = []
    async with httpx.AsyncClient() as cliente:
        for fonte_id in fontes_pedidas:
            if fonte_id not in FONTES_DISPONIVEIS:
                continue
            _, funcao = FONTES_DISPONIVEIS[fonte_id]
            for tipo in tipos_lista:
                tarefas.append(funcao(cliente, cidade, tipo, operacao))

        resultados_por_fonte = await asyncio.gather(*tarefas, return_exceptions=True)

    todos_items = []
    for resultado in resultados_por_fonte:
        if isinstance(resultado, Exception):
            logger.warning(f"Uma fonte falhou: {resultado}")
            continue
        todos_items.extend(resultado)

    agrupados = _agrupar_duplicados(todos_items)

    # Regista o preço de cada resultado no histórico (só grava se mudou desde a última vez)
    for item in agrupados:
        h = _hash_dedup(item)
        item["_hash"] = h
        db.registar_preco(h, item.get("titulo"), item.get("local"), item.get("preco"))

    return agrupados


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@app.get("/pesquisar")
async def pesquisar(
    cidade: str = Query(..., description="Cidade ou area, ex: Lisboa"),
    tipos: str = Query("", description="Tipos separados por virgula, ex: apartamento,terreno"),
    operacao: str = Query("venda", description="venda | arrendamento"),
    fontes: str = Query("", description="IDs de fontes separadas por virgula. Vazio = todas."),
):
    tipos_lista = [t for t in tipos.split(",") if t] or ["apartamento", "moradia", "terreno"]
    fontes_pedidas = [f for f in fontes.split(",") if f] or list(FONTES_DISPONIVEIS.keys())

    agrupados = await executar_pesquisa(cidade, tipos_lista, operacao, fontes_pedidas)

    return {
        "total": len(agrupados),
        "fontes_consultadas": fontes_pedidas,
        "resultados": agrupados,
    }


@app.get("/fontes")
async def listar_fontes():
    """Devolve a lista de fontes disponiveis, para preencher os checkboxes no frontend."""
    return [{"id": id_, "nome": nome} for id_, (nome, _) in FONTES_DISPONIVEIS.items()]


@app.get("/historico/{hash_imovel}")
async def historico_precos(hash_imovel: str):
    """Devolve as observações de preço registadas para um imóvel (pelo seu hash de deduplicação)."""
    pontos = db.obter_historico(hash_imovel)
    if not pontos:
        raise HTTPException(status_code=404, detail="Sem histórico registado para este imóvel ainda.")
    return {"hash": hash_imovel, "pontos": pontos}


# ---------------------------------------------------------------------------
# Alertas por email
# ---------------------------------------------------------------------------

class NovoAlerta(BaseModel):
    email: str
    cidade: str
    tipos: str = ""       # ex: "apartamento,moradia" — vazio = todos
    operacao: str = "venda"
    fontes: str = ""      # vazio = todas as fontes disponíveis


@app.post("/alertas")
async def criar_alerta(alerta: NovoAlerta):
    alerta_id = db.criar_alerta(alerta.email, alerta.cidade, alerta.tipos, alerta.operacao, alerta.fontes)

    # Corre a pesquisa uma vez já agora, só para marcar os resultados atuais
    # como "já vistos" — assim o primeiro email só chega quando houver algo
    # NOVO a partir de agora, e não repete tudo o que já existe hoje.
    tipos_lista = [t for t in alerta.tipos.split(",") if t] or ["apartamento", "moradia", "terreno"]
    fontes_lista = [f for f in alerta.fontes.split(",") if f] or list(FONTES_DISPONIVEIS.keys())
    resultados = await executar_pesquisa(alerta.cidade, tipos_lista, alerta.operacao, fontes_lista)
    for item in resultados:
        db.marcar_visto(alerta_id, item["_hash"])

    return {"id": alerta_id, "mensagem": "Alerta criado. Vais receber um email quando surgir algo novo."}


@app.get("/alertas")
async def listar_alertas_utilizador(email: str = Query(...)):
    return db.listar_alertas(email)


@app.delete("/alertas/{alerta_id}")
async def apagar_alerta(alerta_id: int):
    db.apagar_alerta(alerta_id)
    return {"mensagem": "Alerta apagado."}


async def verificar_todos_os_alertas():
    """
    Corrido pelo agendador a cada 30 minutos: para cada alerta guardado,
    repete a pesquisa e envia email só com os imóveis que ainda não
    tinham sido vistos por esse alerta.
    """
    logger.info("A verificar alertas...")
    for alerta in db.listar_alertas():
        tipos_lista = [t for t in alerta["tipos"].split(",") if t] or ["apartamento", "moradia", "terreno"]
        fontes_lista = [f for f in alerta["fontes"].split(",") if f] or list(FONTES_DISPONIVEIS.keys())

        try:
            resultados = await executar_pesquisa(alerta["cidade"], tipos_lista, alerta["operacao"], fontes_lista)
        except Exception as erro:
            logger.warning(f"Alerta {alerta['id']} falhou: {erro}")
            continue

        novos = [item for item in resultados if not db.ja_visto(alerta["id"], item["_hash"])]

        for item in resultados:
            db.marcar_visto(alerta["id"], item["_hash"])

        if novos:
            corpo = email_alertas.montar_email_novos_imoveis(novos, alerta["cidade"])
            email_alertas.enviar_email(
                alerta["email"],
                f"{len(novos)} imóve{'l' if len(novos) == 1 else 'is'} novo{'s' if len(novos) != 1 else ''} em {alerta['cidade']}",
                corpo,
            )
            logger.info(f"Alerta {alerta['id']}: {len(novos)} novos, email enviado.")


@app.get("/depurar_imovirtual")
async def depurar_imovirtual(cidade: str = Query("Lisboa"), tipo: str = Query("apartamento")):
    """
    ENDPOINT TEMPORÁRIO DE DIAGNÓSTICO — não faz parte da app final.
    Vai buscar a página real do Imovirtual e devolve informação sobre o
    que o servidor efetivamente recebeu, para perceber porque a extração
    normal está a falhar (sem isto, estávamos só a adivinhar).
    """
    from adaptador_imovirtual import construir_url_pesquisa, PADRAO_LINK_ANUNCIO
    from bs4 import BeautifulSoup

    url = construir_url_pesquisa(cidade, tipo, "venda")

    async with httpx.AsyncClient() as cliente:
        resposta = await cliente.get(url, headers=CABECALHOS, timeout=15, follow_redirects=True)

    html = resposta.text
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=PADRAO_LINK_ANUNCIO)

    palavras_suspeitas = ["captcha", "cloudflare", "checking your browser", "verifique que é humano", "acesso bloqueado", "robot", "bot detected"]
    texto_lower = html.lower()
    sinais_de_bloqueio = [p for p in palavras_suspeitas if p in texto_lower]

    return {
        "url_pedido": url,
        "status_code": resposta.status_code,
        "url_final_apos_redirects": str(resposta.url),
        "tamanho_html_bytes": len(html),
        "titulo_da_pagina": soup.find("title").get_text() if soup.find("title") else None,
        "num_links_de_anuncio_encontrados": len(anchors),
        "contem_texto_tipologia": "Tipologia" in html,
        "contem_texto_preco_por_m2": "Preço por metro quadrado" in html,
        "possiveis_sinais_de_bloqueio": sinais_de_bloqueio,
        "amostra_primeiros_1500_caracteres_do_texto": soup.get_text(separator=" ", strip=True)[:1500],
    }


@app.get("/")
async def raiz():
    return {"status": "ok", "mensagem": "Servidor Achado a correr. Ver /docs para testar os endpoints."}
