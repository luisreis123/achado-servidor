"""
Busca páginas usando um browser real (Playwright + Chromium), em vez de
um simples pedido HTTP. Necessário para sites que carregam mais
resultados via JavaScript à medida que se faz scroll (scroll infinito),
como o Imovirtual — que não tem um URL de "página 2" tradicional.

O browser é iniciado UMA VEZ quando o servidor arranca (ver lifespan em
main.py) e reutilizado por todas as pesquisas — abrir um browser novo a
cada pedido seria demasiado lento e pesado em memória.
"""

import asyncio
import logging

from playwright.async_api import Browser, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("achado.playwright")

# Textos comuns em botões de consentimento de cookies (para tentar fechar
# o banner, que de outra forma pode bloquear cliques/scroll na página)
TEXTOS_ACEITAR_COOKIES = ["Aceitar", "Aceitar todos", "Aceito", "Concordo", "Accept all", "Accept"]

# O plano gratuito do Render tem pouca CPU/RAM — correr duas páginas
# Playwright em simultâneo pode fazer com que ambas fiquem lentas de mais
# e atinjam o tempo limite. Por isso, no máximo 1 de cada vez.
_semaforo_playwright = asyncio.Semaphore(1)


async def obter_html_com_scroll(
    browser: Browser,
    url: str,
    seletor_contagem: str,
    max_scrolls: int = 12,
    espera_entre_scrolls_ms: int = 1400,
) -> str | None:
    """
    Abre a URL num separador novo, tenta fechar o banner de cookies, e
    faz scroll repetidamente até o número de elementos que correspondem
    a `seletor_contagem` (ex: 'a[href*="/pt/anuncio/"]') deixar de
    aumentar — sinal de que já não há mais resultados a carregar.

    Devolve o HTML final (já com todo o conteúdo carregado), ou None se
    algo correu mal.
    """
    async with _semaforo_playwright:
        pagina = await browser.new_page(
            user_agent="Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
            viewport={"width": 412, "height": 915},
        )

        # Bloquear imagens/fontes/media acelera muito o carregamento e
        # poupa memória — não precisamos delas, só do texto/estrutura
        async def _bloquear_recursos_pesados(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()

        await pagina.route("**/*", _bloquear_recursos_pesados)

        try:
            await pagina.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Tentar fechar o banner de cookies, se aparecer (não é grave se falhar)
            for texto in TEXTOS_ACEITAR_COOKIES:
                try:
                    botao = pagina.get_by_role("button", name=texto, exact=False)
                    await botao.click(timeout=2000)
                    logger.info(f"Banner de cookies fechado (botão '{texto}')")
                    break
                except PlaywrightTimeoutError:
                    continue

            contagem_anterior = -1
            sem_crescimento_seguido = 0

            for i in range(max_scrolls):
                contagem_atual = await pagina.evaluate(
                    f"document.querySelectorAll('{seletor_contagem}').length"
                )

                if contagem_atual == contagem_anterior:
                    sem_crescimento_seguido += 1
                    if sem_crescimento_seguido >= 2:
                        # Duas tentativas seguidas sem crescer = já não há mais a carregar
                        logger.info(f"Scroll parado ao fim de {i} tentativas — {contagem_atual} itens encontrados")
                        break
                else:
                    sem_crescimento_seguido = 0

                contagem_anterior = contagem_atual

                await pagina.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(espera_entre_scrolls_ms / 1000)

            html = await pagina.content()
            return html

        except PlaywrightTimeoutError as erro:
            logger.warning(f"Timeout ao carregar {url}: {erro}")
            return None
        except Exception as erro:
            logger.warning(f"Erro inesperado ao processar {url}: {erro}")
            return None
        finally:
            await pagina.close()
