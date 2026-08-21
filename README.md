# Servidor local — pesquisa em tempo real, com histórico e alertas

Este servidor usa **SQLite** (um único ficheiro `achado.db`, criado
automaticamente) para guardar duas coisas: histórico de preços e
alertas por email. Não precisa de nenhum servidor de base de dados
à parte — continua a correr no Render sem complicação extra.

## Como correr

```bash
cd servidor-local
pip install -r requirements.txt
uvicorn main:app --reload
```

O servidor fica disponivel em `http://127.0.0.1:8000`.
Para testar os endpoints no browser: `http://127.0.0.1:8000/docs`

## Novidades: histórico de preços e alertas por email

- **Histórico**: cada vez que um imóvel aparece numa pesquisa, o preço
  fica registado (só grava uma linha nova quando o preço muda). Consulta
  em `GET /historico/{hash}`.
- **Alertas**: cria um em `POST /alertas` com email + filtros. A cada
  30 minutos, o servidor repete essa pesquisa sozinho e envia um email
  só com os imóveis que ainda não tinham aparecido antes nesse alerta.

### Configurar o envio de emails (necessário para os alertas funcionarem)

Sem isto, os alertas continuam a ser guardados e verificados, mas o
email simplesmente não é enviado (fica só um aviso no log).

No painel do Render (ou num ficheiro `.env` se testares localmente),
define estas variáveis de ambiente:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=o-teu-email@gmail.com
SMTP_PASS=xxxx xxxx xxxx xxxx      <- palavra-passe de APLICAÇÃO, não a normal
SMTP_FROM=o-teu-email@gmail.com
```

Para gerar uma palavra-passe de aplicação no Gmail: Conta Google →
Segurança → Verificação em dois passos (tem de estar ativada) →
Palavras-passe de aplicações. Alternativa sem Gmail: criar conta
gratuita em brevo.com (antigo Sendinblue), que dá credenciais SMTP
próprias sem precisares de ativar nada na tua conta de email pessoal.

## Antes de usar contra sites reais

O `main.py` tem três adaptadores de EXEMPLO:
- `exemplo_portal_a` / `exemplo_portal_b` — seletores CSS ilustrativos
- `portal_real_exemplo` — usa o método JSON-LD (ver `adaptador_json_ld.py`),
  mais robusto a mudanças de design, mas ainda com URL ilustrativo

Para cada site real que quiseres incluir:

1. Confirma o `robots.txt` do site (ex: `https://www.idealista.pt/robots.txt`)
2. Escolhe a abordagem:
   - **JSON-LD** (recomendado se o site o tiver na página de listagem):
     abre o código-fonte (view-source, não o inspecionar elemento) de
     uma página de resultados e procura por `application/ld+json`
   - **CSS tradicional**: inspeciona o HTML real e ajusta os seletores
3. Duplica o adaptador correspondente com o nome do site novo
4. Regista a nova fonte no dicionário `FONTES_DISPONIVEIS`

## Limitações desta abordagem

- **Velocidade**: cada pesquisa faz pedidos HTTP ao vivo; demora mais
  que uma pesquisa numa base de dados própria (segundos, não milissegundos).
- **Fragilidade**: se um site mudar o HTML (ou deixar de ter JSON-LD),
  o adaptador correspondente para de funcionar até seres atualizado.
- **Disco no Render (plano gratuito)**: não é garantidamente persistente
  entre reinícios do serviço — o histórico pode ocasionalmente perder-se.
  Aceitável para uso pessoal leve, não para guardar dados para sempre.
- **Deduplicação sem coordenadas**: usa localização textual + área +
  preço aproximado. Funciona bem na maioria dos casos, mas é menos
  preciso que comparar coordenadas GPS.
