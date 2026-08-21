"""
Adaptador genérico usando dados estruturados JSON-LD (schema.org).

Muitos sites de imóveis incluem no HTML um bloco assim, pensado para o
Google entender o anúncio:

    <script type="application/ld+json">
      { "@type": "Product", "name": "...", "offers": {"price": 250000} }
    </script>

Ler este bloco é MAIS ESTÁVEL do que adivinhar classes CSS (".preco",
".titulo"...), porque tende a mudar com muito menos frequência do que
o design visual da página — sites alteram o CSS constantemente, mas
raramente tocam nos dados estruturados, porque isso afetaria o SEO.

LIMITAÇÃO IMPORTANTE: nem todos os sites colocam JSON-LD na página de
LISTAGEM (resultados de pesquisa) — muitos só o têm na página de cada
anúncio individual. Nesse caso, este adaptador teria de visitar cada
anúncio um a um, o que é mais lento e faz mais pedidos ao site (por
isso ainda mais importante respeitar o rate limiting).

ANTES DE USAR CONTRA UM SITE REAL:
  1. Confirma o robots.txt do site
  2. Abre uma página de resultados desse site e o código fonte
     (não o "inspecionar elemento" — o código fonte / view-source),
     e procura por 'application/ld+json' com Ctrl+F
  3. Se existir na página de listagem, ótimo — usa tal como está.
  4. Se só existir na página de cada anúncio, ajusta a função para
     visitar cada link individualmente (fica mais lento).
"""

import json
import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("achado.jsonld")


def extrair_blocos_json_ld(html: str) -> list[dict]:
    """Devolve todos os blocos JSON-LD encontrados numa página, já convertidos em dict."""
    soup = BeautifulSoup(html, "html.parser")
    blocos = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            conteudo = json.loads(script.string or "{}")
            # Alguns sites colocam uma lista de objetos em vez de um único objeto
            if isinstance(conteudo, list):
                blocos.extend(conteudo)
            else:
                blocos.append(conteudo)
        except (json.JSONDecodeError, TypeError):
            continue

    return blocos


def normalizar_bloco_imovel(bloco: dict, fonte: str, url_pagina: str) -> Optional[dict]:
    """
    Converte um bloco JSON-LD (tipicamente @type: Product, Offer, ou
    RealEstateListing/House/Apartment) para o schema comum da app.
    Os nomes de campo variam de site para site — esta função tenta os
    mais comuns, mas pode precisar de ajustes por fonte.
    """
    tipo_schema = bloco.get("@type", "")
    if tipo_schema not in ("Product", "Offer", "RealEstateListing", "House", "Apartment", "SingleFamilyResidence"):
        return None

    oferta = bloco.get("offers", {})
    if isinstance(oferta, list):
        oferta = oferta[0] if oferta else {}

    preco = oferta.get("price") or bloco.get("price")
    try:
        preco = float(preco) if preco is not None else None
    except (ValueError, TypeError):
        preco = None

    imagem = bloco.get("image")
    if isinstance(imagem, list):
        imagem = imagem[0] if imagem else None
    if isinstance(imagem, dict):
        imagem = imagem.get("url")

    endereco = bloco.get("address", {})
    local = endereco.get("addressLocality") if isinstance(endereco, dict) else None

    url_item = bloco.get("url") or url_pagina

    return {
        "fonte": fonte,
        "titulo": bloco.get("name"),
        "url_original": urljoin(url_pagina, url_item) if url_item else url_pagina,
        "preco": preco,
        "area_m2": None,   # raramente vem no JSON-LD padrao; ficaria None ate ajuste especifico do site
        "quartos": None,
        "local": local,
        "thumbnail_url": imagem,
    }


async def buscar_via_json_ld(
    cliente: httpx.AsyncClient,
    semaforo,
    cabecalhos: dict,
    fonte: str,
    url_pesquisa: str,
    tipo: str,
    operacao: str,
) -> list[dict]:
    """
    Função genérica: busca uma página de resultados, extrai todos os
    blocos JSON-LD relevantes, e normaliza-os. Usar como base para um
    adaptador por site, passando o URL de pesquisa já construído.
    """
    resultados = []
    try:
        async with semaforo:
            resposta = await cliente.get(url_pesquisa, headers=cabecalhos, timeout=15)
        if resposta.status_code != 200:
            logger.warning(f"[{fonte}] resposta {resposta.status_code} em {url_pesquisa}")
            return []

        blocos = extrair_blocos_json_ld(resposta.text)
        if not blocos:
            logger.warning(
                f"[{fonte}] nenhum JSON-LD encontrado em {url_pesquisa} — "
                f"este site provavelmente só tem dados estruturados na página de cada anúncio."
            )
            return []

        for bloco in blocos:
            item = normalizar_bloco_imovel(bloco, fonte, url_pesquisa)
            if item:
                item["tipo"] = tipo
                item["operacao"] = operacao
                resultados.append(item)

    except (httpx.RequestError, httpx.TimeoutException) as erro:
        logger.warning(f"[{fonte}] falhou: {erro}")

    return resultados[:20]
