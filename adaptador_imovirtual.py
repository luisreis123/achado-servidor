"""
Adaptador para o Imovirtual (imovirtual.com).

Baseado em inspeção real da página de RESULTADOS (não de anúncios
individuais), feita em 22/08/2026. Padrão de URL confirmado:

  https://www.imovirtual.com/pt/resultados/{operacao}/{tipo}/{distrito}/{concelho}

  operacao: comprar | arrendar
  tipo: apartamento | t0 | moradia | terreno | imoveis-comerciais |
        imoveis-comerciais,escritorio | armazens | garagem | quarto
  distrito/concelho: minúsculas, sem acentos (ex: lisboa/lisboa, porto/porto)

AVISO: NÃO consegui verificar o robots.txt real deste site através das
minhas ferramentas — confirma tu mesmo em imovirtual.com/robots.txt
antes de usar isto com regularidade.

MÉTODO DE EXTRAÇÃO: a própria página de RESULTADOS já contém todos os
dados necessários (preço, tipologia, área, morada, link) para cada um
dos ~36 anúncios que mostra — não é preciso visitar cada anúncio
individual. Isto é mais rápido (1 pedido em vez de dezenas) e mais
leve para o site de origem. A ordem observada em cada cartão é:

  [preço + preço/m²] -> [título (link)] -> [morada] -> "Tipologia" ->
  valor -> "Preço por metro quadrado" (na verdade mostra a ÁREA, não
  um preço — confirmado por inspeção real) -> valor -> "Andar" -> valor

A extração usa essa ordem como guia, com janelas de texto antes/depois
do título de cada anúncio.

PAGINAÇÃO: não consegui confirmar o parâmetro correto para pedir a
página 2+ de resultados (tentei ?page=2 e ?search[page]=2, nenhum
funcionou como esperado). Por agora, cada pesquisa devolve só a
primeira página (tipicamente ~36 imóveis). Fica por explorar.
"""

import re
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("achado.imovirtual")

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
PADRAO_PRECO = re.compile(r'(\d[\d \.]{2,}\d)[ ]?€(?!\s*/\s*m)')
# NOTA: no site real, o campo "Preço por metro quadrado" nos cartões da
# LISTAGEM mostra na verdade a ÁREA (m²), não um preço — confirmado por
# inspeção real de várias páginas de resultados. Mantemos o nome do
# padrão fiel ao rótulo do site, para ser óbvio de onde vem.
PADRAO_AREA_LISTAGEM = re.compile(r'Preço por metro quadrado\s*\n*\s*([\d,.]+)\s*m²')
PADRAO_TIPOLOGIA = re.compile(r'Tipologia\s*\n*\s*(\S+)')


def construir_url_pesquisa(cidade: str, tipo: str, operacao: str) -> str | None:
    operacao_slug = MAPA_OPERACAO.get(operacao)
    tipo_slug = MAPA_TIPO.get(tipo)
    if not operacao_slug or not tipo_slug:
        return None

    cidade_slug = cidade.strip().lower().replace(" ", "-")
    return f"https://www.imovirtual.com/pt/resultados/{operacao_slug}/{tipo_slug}/{cidade_slug}/{cidade_slug}"


def _tipologia_para_quartos(tipologia: str | None) -> int | None:
    """Converte 'T2' -> 2, 'T0' -> 0. Devolve None se não reconhecer (ex: 'T9+', 'T3-T4')."""
    if not tipologia:
        return None
    m = re.match(r'[Tt](\d+)$', tipologia)
    if m:
        return int(m.group(1))
    return None


def extrair_imoveis_da_pagina_de_resultados(html: str, tipo: str, operacao: str) -> list[dict]:
    """
    Extrai TODOS os imóveis diretamente da página de listagem (não visita
    cada anúncio individual). Muito mais rápido e dá mais resultados por
    pesquisa (tipicamente 36, o total de uma página do site).

    Em vez de procurar o texto do título dentro do texto completo da
    página (abordagem anterior, que falhava sempre que o título estava
    dividido por elementos HTML aninhados, ex: <span> dentro do link),
    navega-se a árvore HTML diretamente a partir de cada link — usando
    find_all_previous/find_all_next para apanhar os nós de texto reais
    antes e depois do link, na ordem em que existem no documento.
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=PADRAO_LINK_ANUNCIO)
    resultados = []
    hrefs_vistos = set()

    for a in anchors:
        # separator=" " evita que texto de <span>s aninhados fique colado
        # sem espaço (ex: "Apartamento T3para venda")
        titulo = a.get_text(separator=" ", strip=True)
        titulo = re.sub(r"\s+", " ", titulo).strip()
        href = a.get("href", "")

        if not titulo or href in hrefs_vistos:
            continue
        hrefs_vistos.add(href)

        # Nós de texto reais antes/depois do link, na ordem do documento
        # (mais próximos do link primeiro, por isso invertemos "antes").
        # Ignora-se texto que pertença a tags <script>/<style> (ex: CSS
        # gerado por bibliotecas como Emotion, que por vezes aparece
        # embutido dentro do próprio link) — não é conteúdo visível.
        strings_antes = a.find_all_previous(string=True, limit=60)
        texto_antes = "\n".join(reversed([
            s.strip() for s in strings_antes
            if s and s.strip() and s.parent.name not in ("script", "style")
        ]))
        # \xa0 (espaço sem quebra) é usado por alguns sites entre dígitos
        # de preços, e parece visualmente igual a um espaço normal — mas
        # não é reconhecido como tal pelos padrões abaixo sem normalizar.
        texto_antes = texto_antes.replace("\xa0", " ")

        strings_depois = a.find_all_next(string=True, limit=60)
        texto_depois = "\n".join([
            s.strip() for s in strings_depois
            if s and s.strip() and s.parent.name not in ("script", "style")
        ])
        texto_depois = texto_depois.replace("\xa0", " ")

        # Preço: o último valor encontrado logo antes do link
        precos_antes = PADRAO_PRECO.findall(texto_antes[-250:])
        preco = None
        if precos_antes:
            try:
                preco = float(precos_antes[-1].replace(" ", "").replace(".", ""))
            except ValueError:
                preco = None

        m_tipologia = PADRAO_TIPOLOGIA.search(texto_depois)
        tipologia = m_tipologia.group(1) if m_tipologia else None
        quartos = _tipologia_para_quartos(tipologia)

        m_area = PADRAO_AREA_LISTAGEM.search(texto_depois)
        area_m2 = None
        if m_area:
            try:
                area_m2 = float(m_area.group(1).replace(",", "."))
            except ValueError:
                area_m2 = None

        # Morada: primeira linha a seguir ao link que não faça parte do
        # próprio título (o primeiro nó de texto "depois" pode ainda ser
        # um fragmento do título, se este estiver dividido em <span>s)
        linhas_depois = [l.strip() for l in texto_depois.split("\n") if l.strip()]
        titulo_compacto = titulo.replace(" ", "")
        i = 0
        while i < len(linhas_depois) and linhas_depois[i].replace(" ", "") in titulo_compacto:
            i += 1
        local = None
        if i < len(linhas_depois) and linhas_depois[i] != "Tipologia":
            local = linhas_depois[i]

        if preco is None:
            continue

        url_original = f"https://www.imovirtual.com{href}"

        resultados.append({
            "fonte": "imovirtual",
            "titulo": titulo,
            "url_original": url_original,
            "preco": preco,
            "area_m2": area_m2,
            "quartos": quartos,
            "local": local,
            "thumbnail_url": None,
            "tipo": tipo,
            "operacao": operacao,
        })

    if not resultados:
        logger.warning(
            "[imovirtual] nenhum imóvel extraído da página de listagem — "
            "a estrutura pode ter mudado ou os padrões precisam de ajuste."
        )

    return resultados


async def buscar_imovirtual(cliente: httpx.AsyncClient, semaforo, cabecalhos: dict, cidade: str, tipo: str, operacao: str) -> list[dict]:
    """
    Busca imóveis diretamente na página de listagem do Imovirtual — um
    único pedido HTTP por pesquisa (mais rápido e mais leve para o site
    de origem do que visitar cada anúncio individualmente).
    """
    url_pesquisa = construir_url_pesquisa(cidade, tipo, operacao)
    if not url_pesquisa:
        return []

    try:
        async with semaforo:
            resposta = await cliente.get(url_pesquisa, headers=cabecalhos, timeout=15)
        if resposta.status_code != 200:
            logger.warning(f"[imovirtual] resposta {resposta.status_code} em {url_pesquisa}")
            return []
    except (httpx.RequestError, httpx.TimeoutException) as erro:
        logger.warning(f"[imovirtual] falhou a obter listagem: {erro}")
        return []

    return extrair_imoveis_da_pagina_de_resultados(resposta.text, tipo, operacao)

