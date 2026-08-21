"""
Adaptador para o Imovirtual (imovirtual.com).

Baseado em inspeção real da página de resultados feita em 21/08/2026.
Padrão de URL confirmado:

  https://www.imovirtual.com/pt/resultados/{operacao}/{tipo}/{distrito}/{concelho}

  operacao: comprar | arrendar
  tipo: apartamento | t0 | moradia | terreno | imoveis-comerciais |
        imoveis-comerciais,escritorio | armazens | garagem | quarto
  distrito/concelho: minúsculas, sem acentos (ex: lisboa/lisboa, porto/porto)

AVISO: NÃO consegui verificar o robots.txt real deste site através das
minhas ferramentas — confirma tu mesmo em imovirtual.com/robots.txt
antes de usar isto com regularidade.

Abordagem em duas fases:
  1. A página de resultados não parece ter JSON-LD por anúncio (ou pelo
     menos não foi possível confirmar via extração em markdown), mas
     tem um padrão de URL muito estável para cada anúncio:
     /pt/anuncio/{slug}-ID{codigo}
  2. Visita-se cada anúncio individual (só os primeiros N, para não
     sobrecarregar o site) e lê-se o JSON-LD dessa página, que sites
     deste tipo costumam ter para SEO.

Isto faz MAIS pedidos por pesquisa (1 + N em vez de 1), por isso o
rate limiting (SEMAFORO) é ainda mais importante aqui.
"""

import re
import logging

import httpx

from adaptador_json_ld import extrair_blocos_json_ld, normalizar_bloco_imovel

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


def construir_url_pesquisa(cidade: str, tipo: str, operacao: str) -> str | None:
    operacao_slug = MAPA_OPERACAO.get(operacao)
    tipo_slug = MAPA_TIPO.get(tipo)
    if not operacao_slug or not tipo_slug:
        return None

    cidade_slug = cidade.strip().lower().replace(" ", "-")
    return f"https://www.imovirtual.com/pt/resultados/{operacao_slug}/{tipo_slug}/{cidade_slug}/{cidade_slug}"


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
    # Remove duplicados mantendo a ordem, e limita quantos vamos visitar
    links_unicos = list(dict.fromkeys(links_encontrados))[:MAX_ANUNCIOS_POR_PESQUISA]

    if not links_unicos:
        logger.warning(f"[imovirtual] nenhum link de anúncio encontrado em {url_pesquisa} — o padrão de URL pode ter mudado.")
        return []

    # --- Fase 2: visitar cada anúncio individual e extrair o JSON-LD ---
    resultados = []
    for link in links_unicos:
        url_anuncio = f"https://www.imovirtual.com{link}"
        try:
            async with semaforo:
                resposta_anuncio = await cliente.get(url_anuncio, headers=cabecalhos, timeout=15)
            if resposta_anuncio.status_code != 200:
                continue

            blocos = extrair_blocos_json_ld(resposta_anuncio.text)
            for bloco in blocos:
                item = normalizar_bloco_imovel(bloco, "imovirtual", url_anuncio)
                if item and item.get("titulo"):
                    item["tipo"] = tipo
                    item["operacao"] = operacao
                    resultados.append(item)
                    break  # só o primeiro bloco relevante por anúncio

        except (httpx.RequestError, httpx.TimeoutException) as erro:
            logger.warning(f"[imovirtual] falhou anúncio {url_anuncio}: {erro}")
            continue

    if not resultados:
        logger.warning(
            "[imovirtual] encontrei links de anúncios mas nenhum tinha JSON-LD "
            "reconhecível — pode ser preciso ajustar normalizar_bloco_imovel "
            "para os campos específicos que o Imovirtual usa."
        )

    return resultados
