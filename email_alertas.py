"""
Envio de emails de alerta via SMTP.

Nao usa nenhum servico pago obrigatoriamente — funciona com uma conta
Gmail normal (usando uma "palavra-passe de aplicacao", nao a password
da conta) ou com servicos gratuitos como o Brevo (antigo Sendinblue).

Configuracao (variaveis de ambiente, definidas no painel do Render,
sem editar codigo):
  SMTP_HOST   ex: smtp.gmail.com
  SMTP_PORT   ex: 587
  SMTP_USER   o teu email ou utilizador SMTP
  SMTP_PASS   palavra-passe de aplicacao (NUNCA a password normal)
  SMTP_FROM   o remetente que aparece no email (pode ser igual a SMTP_USER)

Se estas variaveis nao estiverem definidas, o envio e simplesmente
ignorado (com um aviso no log) — o resto da aplicacao continua a
funcionar normalmente.
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("achado.email")


def configurado() -> bool:
    return all(os.environ.get(v) for v in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"])


def enviar_email(destinatario: str, assunto: str, corpo_html: str):
    if not configurado():
        logger.warning("Envio de email ignorado — SMTP não configurado (ver variáveis de ambiente).")
        return False

    remetente = os.environ.get("SMTP_FROM", os.environ["SMTP_USER"])

    mensagem = MIMEText(corpo_html, "html", "utf-8")
    mensagem["Subject"] = assunto
    mensagem["From"] = remetente
    mensagem["To"] = destinatario

    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as servidor:
            servidor.starttls()
            servidor.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            servidor.sendmail(remetente, [destinatario], mensagem.as_string())
        return True
    except Exception as erro:
        logger.warning(f"Falha ao enviar email para {destinatario}: {erro}")
        return False


def montar_email_novos_imoveis(imoveis: list[dict], cidade: str) -> str:
    linhas = ""
    for imovel in imoveis:
        linhas += f"""
        <tr>
          <td style="padding: 12px; border-bottom: 1px solid #333;">
            <strong>{imovel.get('titulo', 'Sem título')}</strong><br>
            {imovel.get('local', '')}<br>
            {imovel.get('preco') and f"{imovel['preco']:,.0f} €".replace(',', '.') or '—'}<br>
            <a href="{imovel.get('url_original', '#')}">Ver anúncio original</a>
          </td>
        </tr>
        """
    return f"""
    <html><body style="font-family: sans-serif;">
      <h2>Novos imóveis em {cidade}</h2>
      <table style="width: 100%; border-collapse: collapse;">{linhas}</table>
      <p style="color: #888; font-size: 12px; margin-top: 20px;">
        Recebeste este email porque criaste um alerta na app Achado.
      </p>
    </body></html>
    """
