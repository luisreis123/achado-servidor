"""
Adaptador para o Imovirtual (imovirtual.com).

Baseado em inspeção real da página de resultados E de uma página de
anúncio individual, feita em 22/08/2026. Padrão de URL confirmado:

  https://www.imovirtual.com/pt/resultados/{operacao}/{tipo}/{distrito}/{concelho}

  operacao: comprar | arrendar
  tipo: apartamento | t0 | moradia | terreno | imoveis-comerciais |
        imoveis-comerciais,escritorio | armazens | garagem | quarto
  distrito/concelho: minúsculas, sem acentos (ex: lisboa/lisboa, porto/porto)

AVISO: NÃO consegui verificar o robots.txt real deste site através das
minhas ferramentas — confirma tu mesmo em imovirtual.com/robots.txt
antes de usar isto com regularidade.

MÉTODO DE EXTRAÇÃO: este site não usa dados estruturados JSON-LD nas
páginas de anúncio (confirmado por teste real — nenhum bloco reconhecido
foi encontrado). Em vez disso, as páginas têm uma estrutura de texto
muito regular, com rótulos fixos em português ("Tipologia:", "Área:",
etc.) seguidos do valor. Isto é o que exploramos aqui: extração por
texto simples (soup.get_text()) + expressões regulares sobre esses
rótulos, em vez de seletores CSS ou classes (que mudam com o design).

Abordagem em duas fases:
  1. Página de resultados → extrair os links dos anúncios (por padrão
     de URL, muito estável: /pt/anuncio/{slug}-ID{codigo})
  2. Visitar cada anúncio individual (só os primeiros N) e extrair os
     dados do texto da página.
"""

import re
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("achado.imovirtual")

# Quantos anúncios individuais visitar por pesquisa (limite de segurança)
MAX_ANUNCIOS_POR_PESQUISA = 8

MAPA_OPERACAO = {
    "venda": "comprar",
    "arrendamento": "arrendar",
}

MAPA_TIPO = {
    "apartamento": "apartamento",
    "moradia": "moradia",
    "terreno": "terreno",
    "loja": "imoveis-comerciais",
    "escritorio": "imoveis-comerciais,escritorio",
    "armazem": "armazens",
}

PADRAO_LINK_ANUNCIO = re.compile(r'/pt/anuncio/[a-z0-9\-]+-ID[A-Za-z0-9]+')

# Padrões de extração — mais tolerantes a espaços/quebras de linha entre elementos
PADRAO_PRECO = re.compile(r'(\d[\d\s\.]{2,}\d)\s*€(?!\s*/\s*m)')
PADRAO_AREA = re.compile(r'Área[^:]*:\s*\n*\s*([\d,.]+)\s*m²')
PADRAO_TIPOLOGIA = re.compile(r'Tipologia:\s*\n*\s*(\S+)')
PADRAO_WC = re.compile(r'casas de banho:\s*\n*\s*(\d+)')
PADRAO_LOCAL_META = re.compile(r' em (.+?), por ')


def construir_url_pesquisa(cidade: str, tipo: str, operacao: str) -> str | None:
    operacao_slug = MAPA_OPERACAO.get(operacao)
    tipo_slug = MAPA_TIPO.get(tipo)
    if not operacao_slug or not tipo_slug:
        return None

    cidade_slug = cidade.strip().lower().replace(" ", "-")
    return f"https://www.imovirtual.com/pt/resultados/{operacao_slug}/{tipo_slug}/{cidade_slug}/{cidade_slug}"


def _tipologia_para_quartos(tipologia: str | None) -> int | None:
    """Converte 'T2' -> 2, 'T0' -> 0, 'Estúdio' -> 0. Devolve None se não reconhecer."""
    if not tipologia:
        return None
    m = re.match(r'[Tt](\d+)', tipologia)
    if m:
        return int(m.group(1))
    if "estúdio" in tipologia.lower() or "estudio" in tipologia.lower():
        return 0
    return None


def extrair_dados_anuncio(html: str, url_anuncio: str, fonte: str, tipo: str, operacao: str) -> dict | None:
    """Extrai os dados de uma página de anúncio individual, por texto/rótulos."""
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    titulo = h1.get_text(strip=True) if h1 else None

    meta_titulo_tag = soup.find("meta", attrs={"property": "og:title"})
    if not titulo and meta_titulo_tag:
        # Reserva: og:title costuma ter o formato "Título - endereço - ID • site"
        bruto = meta_titulo_tag.get("content", "")
        titulo = bruto.split(" • ")[0].strip() or None

    meta_desc_tag = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag.get("content", "") if meta_desc_tag else ""

    meta_img_tag = soup.find("meta", attrs={"property": "og:image"})
    thumbnail_url = meta_img_tag.get("content") if meta_img_tag else None

    texto = soup.get_text(separator="\n")

    precos = PADRAO_PRECO.findall(texto)
    preco = None
    if precos:
        try:
            preco = float(precos[0].replace(" ", "").replace(".", ""))
        except ValueError:
            preco = None

    # Reserva: se não encontrou o preço no corpo da página, tenta na meta description
    if preco is None:
        precos_meta = PADRAO_PRECO.findall(meta_desc)
        if precos_meta:
            try:
                preco = float(precos_meta[0].replace(" ", "").replace(".", ""))
            except ValueError:
                preco = None

    m_area = PADRAO_AREA.search(texto)
    area_m2 = None
    if m_area:
        try:
            area_m2 = float(m_area.group(1).replace(",", "."))
        except ValueError:
            area_m2 = None

    m_tipologia = PADRAO_TIPOLOGIA.search(texto)
    quartos = _tipologia_para_quartos(m_tipologia.group(1) if m_tipologia else None)

    m_local = PADRAO_LOCAL_META.search(meta_desc)
    local = m_local.group(1) if m_local else None

    if not titulo or preco is None:
        # DIAGNÓSTICO: mostra uma amostra do texto real da página, para
        # ajustar os padrões com precisão da próxima vez, em vez de adivinhar.
        amostra = texto.strip()[:600].replace("\n", " | ")
        logger.warning(
            f"[imovirtual] falha a extrair {url_anuncio} "
            f"(titulo={'ok' if titulo else 'FALTA'}, preco={'ok' if preco is not None else 'FALTA'}). "
            f"Amostra do texto: {amostra}"
        )
        return None

    return {
        "fonte": fonte,
        "titulo": titulo,
        "url_original": url_anuncio,
        "preco": preco,
        "area_m2": area_m2,
        "quartos": quartos,
        "local": local,
        "thumbnail_url": thumbnail_url,
        "tipo": tipo,
        "operacao": operacao,
    }


async def buscar_imovirtual(cliente: httpx.AsyncClient, semaforo, cabecalhos: dict, cidade: str, tipo: str, operacao: str) -> list[dict]:
    url_pesquisa = construir_url_pesquisa(cidade, tipo, operacao)
    if not url_pesquisa:
        return []

    # --- Fase 1: obter os links dos anúncios na página de resultados ---
    try:
        async with semaforo:
            resposta = await cliente.get(url_pesquisa, headers=cabecalhos, timeout=15)
        if resposta.status_code != 200:
            logger.warning(f"[imovirtual] resposta {resposta.status_code} em {url_pesquisa}")
            return []
    except (httpx.RequestError, httpx.TimeoutException) as erro:
        logger.warning(f"[imovirtual] falhou a obter listagem: {erro}")
        return []

    links_encontrados = PADRAO_LINK_ANUNCIO.findall(resposta.text)
    links_unicos = list(dict.fromkeys(links_encontrados))[:MAX_ANUNCIOS_POR_PESQUISA]

    if not links_unicos:
        logger.warning(f"[imovirtual] nenhum link de anúncio encontrado em {url_pesquisa} — o padrão de URL pode ter mudado.")
        return []

    # --- Fase 2: visitar cada anúncio individual e extrair os dados por texto ---
    resultados = []
    for link in links_unicos:
        url_anuncio = f"https://www.imovirtual.com{link}"
        try:
            async with semaforo:
                resposta_anuncio = await cliente.get(url_anuncio, headers=cabecalhos, timeout=15)
            if resposta_anuncio.status_code != 200:
                continue

            item = extrair_dados_anuncio(resposta_anuncio.text, url_anuncio, "imovirtual", tipo, operacao)
            if item:
                resultados.append(item)

        except (httpx.RequestError, httpx.TimeoutException) as erro:
            logger.warning(f"[imovirtual] falhou anúncio {url_anuncio}: {erro}")
            continue

    if not resultados:
        logger.warning(
            "[imovirtual] encontrei links de anúncios mas não consegui extrair dados "
            "de nenhum — os padrões de texto podem precisar de ajuste."
        )

    return resultados

