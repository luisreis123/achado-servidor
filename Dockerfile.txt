# Imagem oficial do Playwright — já vem com o Chromium e todas as
# bibliotecas de sistema necessárias para o correr, o que evita ter de
# as instalar manualmente (e evita erros difíceis de depurar).
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# O Chromium já vem instalado na imagem base, mas esta linha garante
# que fica atualizado e consistente com a versão do playwright pedida
# no requirements.txt
RUN playwright install chromium

COPY . .

# O Render define a variável de ambiente PORT automaticamente
CMD uvicorn main:app --host 0.0.0.0 --port $PORT
