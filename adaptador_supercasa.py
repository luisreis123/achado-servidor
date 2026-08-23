"""
Adaptador para o SUPERCASA (supercasa.pt).

Baseado em inspeção real da página de RESULTADOS, feita em 23/08/2026.

AVISO: NÃO consegui verificar o robots.txt real deste site através das
minhas ferramentas — confirma tu mesmo em supercasa.pt/robots.txt antes
de usar isto com regularidade.

PADRÃO DE URL — IMPORTANTE: varia consoante o tipo de imóvel, não é
uniforme como no Imovirtual:

  Apartamentos/Moradias: https://supercasa.pt/{comprar|arrendar}-casas/{cidade}/com-{apartamentos|moradias}
  Terrenos:               https://supercasa.pt/{comprar|arrendar}-terrenos/{cidade}
  Escritórios:            https://supercasa.pt/{comprar|arrendar}-escritorios/{cidade}
  Lojas/Armazéns:         https://supercasa.pt/{comprar|arrendar}-espacos_comerciais_ou_armazens/{cidade}

ESTRUTURA DO CARTÃO (ordem observada, igual em todos os anúncios):
  [preço] -> [título+morada (link, aponta para /algo/i<numero>)] ->
  "X quartos" -> "Área bruta Y m²" -> descrição longa -> características

MÉTODO DE EXTRAÇÃO: igual ao usado no Imovirtual — navegação da árvore
HTML a partir de cada link (find_all_previous/find_all_next), filtrando
texto de <script>/<style> e normalizando espaços sem quebra (\xa0).
"""

import re
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("achado.supercasa")

# tipo -> (segmento de categoria no URL, subpasta opcional)
MAPA_TIPO = {
    "apartamento": ("casas", "com-apartamentos"),
    "moradia": ("casas", "com-moradias"),
    "terreno": ("terrenos", None),
    "escritorio": ("escritorios", None),
    "loja": ("espacos_comerciais_ou_armazens", None),
    "armazem": ("espacos_comerciais_ou_armazens", None),
}

# Links de anúncio têm sempre o formato /algo-descritivo/i<numero>
PADRAO_LINK_ANUNCIO = re.compile(r'/[a-z0-9\-]+/i\d+')
PADRAO_PRECO = re.compile(r'(\d[\d \.]{2,}\d)[ ]?€(?!\s*/\s*m)')
PADRAO_QUARTOS = re.compile(r'(\d+)\s*quartos?')
PADRAO_AREA = re.compile(r'Área\s+(?:bruta|útil)\s+([\d,.]+)\s*m²')


def construir_url_pesquisa(cidade: str, tipo: str, operacao: str) -> str | None:
    categoria, subpasta = MAPA_TIPO.get(tipo, (None, None))
    if categoria is None:
        return None

    base = "comprar" if operacao == "venda" else "arrendar"
    cidade_slug = cidade.strip().lower().replace(" ", "-")

    url = f"https://supercasa.pt/{base}-{categoria}/{cidade_slug}"
    if subpasta:
        url += f"/{subpasta}"
    return url


def extrair_imoveis_da_pagina_de_resultados(html: str, tipo: str, operacao: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=PADRAO_LINK_ANUNCIO)
    resultados = []
    hrefs_vistos = set()

    for a in anchors:
        titulo = a.get_text(separator=" ", strip=True)
        titulo = re.sub(r"\s+", " ", titulo).strip()
        href = a.get("href", "")

        if not titulo or href in hrefs_vistos:
            continue
        hrefs_vistos.add(href)

        strings_antes = a.find_all_previous(string=True, limit=60)
        texto_antes = "\n".join(reversed([
            s.strip() for s in strings_antes
            if s and s.strip() and s.parent.name not in ("script", "style")
        ])).replace("\xa0", " ")

        strings_depois = a.find_all_next(string=True, limit=60)
        texto_depois = "\n".join([
            s.strip() for s in strings_depois
            if s and s.strip() and s.parent.name not in ("script", "style")
        ]).replace("\xa0", " ")

        precos_antes = PADRAO_PRECO.findall(texto_antes[-250:])
        preco = None
        if precos_antes:
            try:
                preco = float(precos_antes[-1].replace(" ", "").replace(".", ""))
            except ValueError:
                preco = None

        m_quartos = PADRAO_QUARTOS.search(texto_depois[:200])
        quartos = int(m_quartos.group(1)) if m_quartos else None

        m_area = PADRAO_AREA.search(texto_depois[:300])
        area_m2 = None
        if m_area:
            try:
                area_m2 = float(m_area.group(1).replace(",", "."))
            except ValueError:
                area_m2 = None

        # Foto: imagem mais próxima antes do link (best-effort — o site usa
        # carregamento lento, por isso pode não haver URL real disponível)
        thumbnail_url = None
        for img in a.find_all_previous("img", limit=6):
            candidato = img.get("data-src") or img.get("src")
            if not candidato and img.get("srcset"):
                candidato = img.get("srcset").split(",")[0].strip().split(" ")[0]
            if candidato and candidato.startswith("http"):
                thumbnail_url = candidato
                break

        # O título do Supercasa já inclui a morada (ex: "Apartamento T2 em
        # Penha de França, Lisboa") — não há um campo de morada separado
        # como no Imovirtual. Usamos o próprio título como "local" também,
        # para os pinos do mapa/geocoding terem algo a processar.
        local = titulo

        if preco is None:
            continue

        url_original = f"https://supercasa.pt{href}"

        resultados.append({
            "fonte": "supercasa",
            "titulo": titulo,
            "url_original": url_original,
            "preco": preco,
            "area_m2": area_m2,
            "quartos": quartos,
            "local": local,
            "thumbnail_url": thumbnail_url,
            "tipo": tipo,
            "operacao": operacao,
        })

    if not resultados:
        logger.warning(
            "[supercasa] nenhum imóvel extraído da página de listagem — "
            "a estrutura pode ter mudado ou os padrões precisam de ajuste."
        )

    return resultados


async def buscar_supercasa(cliente: httpx.AsyncClient, semaforo, cabecalhos: dict, cidade: str, tipo: str, operacao: str) -> list[dict]:
    url_pesquisa = construir_url_pesquisa(cidade, tipo, operacao)
    if not url_pesquisa:
        return []

    try:
        async with semaforo:
            resposta = await cliente.get(url_pesquisa, headers=cabecalhos, timeout=15)
        if resposta.status_code != 200:
            logger.warning(f"[supercasa] resposta {resposta.status_code} em {url_pesquisa}")
            return []
    except (httpx.RequestError, httpx.TimeoutException) as erro:
        logger.warning(f"[supercasa] falhou a obter listagem: {erro}")
        return []

    return extrair_imoveis_da_pagina_de_resultados(resposta.text, tipo, operacao)
