"""
Geocoding: converte texto de morada ("Rua X, Lisboa") em coordenadas
(latitude, longitude), usando o Nominatim — o serviço gratuito do
OpenStreetMap (o mesmo projeto que fornece os mapas usados na app).

REGRAS DE USO DO NOMINATIM (obrigatório respeitar):
  - Máximo 1 pedido por segundo
  - É obrigatório identificar a aplicação num User-Agent próprio
  - Resultados devem ser usados, não redistribuídos em massa

Para não voltar a pedir a mesma morada duas vezes (o que seria lento E
desnecessário), os resultados ficam guardados em cache (ver db.py,
tabela geocoding_cache) — a segunda vez que uma morada aparece, a
resposta vem da cache, instantaneamente.
"""

import asyncio
import logging

import httpx

import db

logger = logging.getLogger("achado.geocoding")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CABECALHOS_NOMINATIM = {
    "User-Agent": "AchadoAgregadorApp/0.1 (uso pessoal; +http://localhost)"
}

# Limite de segurança: no máximo, geocodificar este número de moradas
# NOVAS por pesquisa (as que já estão em cache não contam para este
# limite). Evita que uma pesquisa com muitos resultados novos demore
# minutos só a converter moradas em coordenadas.
MAX_GEOCODING_NOVOS_POR_PESQUISA = 15

_semaforo_nominatim = asyncio.Semaphore(1)  # nunca mais do que 1 pedido em simultâneo


async def geocodificar(cliente: httpx.AsyncClient, endereco: str) -> tuple[float, float] | None:
    """Devolve (latitude, longitude) para uma morada, ou None se não encontrar."""
    if not endereco or not endereco.strip():
        return None

    cache = db.obter_coordenadas_cache(endereco)
    if cache is not None:
        return cache if cache[0] is not None else None

    try:
        async with _semaforo_nominatim:
            resposta = await cliente.get(
                NOMINATIM_URL,
                params={"q": f"{endereco}, Portugal", "format": "json", "limit": 1},
                headers=CABECALHOS_NOMINATIM,
                timeout=10,
            )
            # Respeita o limite de 1 pedido/segundo do Nominatim
            await asyncio.sleep(1)

        dados = resposta.json()
        if not dados:
            db.guardar_coordenadas_cache(endereco, None, None)
            return None

        lat = float(dados[0]["lat"])
        lng = float(dados[0]["lon"])
        db.guardar_coordenadas_cache(endereco, lat, lng)
        return (lat, lng)

    except (httpx.RequestError, httpx.TimeoutException, KeyError, ValueError, IndexError) as erro:
        logger.warning(f"Geocoding falhou para '{endereco}': {erro}")
        return None


async def geocodificar_resultados(cliente: httpx.AsyncClient, resultados: list[dict]):
    """
    Preenche latitude/longitude em cada resultado que tenha morada
    ('local'), diretamente na lista passada (modifica in-place).
    Só faz pedidos novos ao Nominatim para moradas que ainda não estão
    em cache, e limita quantas moradas novas processa por pesquisa.
    """
    novos_geocodificados = 0

    for item in resultados:
        endereco = item.get("local")
        if not endereco:
            continue

        se_ja_em_cache = db.obter_coordenadas_cache(endereco) is not None

        if not se_ja_em_cache and novos_geocodificados >= MAX_GEOCODING_NOVOS_POR_PESQUISA:
            # Limite atingido — este imóvel fica sem pino no mapa desta vez,
            # mas continua visível na lista.
            continue

        coordenadas = await geocodificar(cliente, endereco)
        if not se_ja_em_cache:
            novos_geocodificados += 1

        if coordenadas:
            item["latitude"], item["longitude"] = coordenadas
