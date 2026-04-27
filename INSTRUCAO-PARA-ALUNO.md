# Como instalar seu agente Claude + Telegram em 5 minutos (sem mexer em terminal)

> Caminho recomendado pra alunos que NAO querem ficar copiando comandos manualmente na VPS.
> Voce vai abrir o Claude Code no seu computador, colar UM prompt, responder algumas perguntas, e o Claude faz o resto pra voce (SSH na VPS, instalacao, configuracao, deploy).
>
> Se voce e desenvolvedor e prefere fazer manualmente, veja o `README.md` (caminho avancado).

---

## Passo 1 - Compre uma VPS Ubuntu 22.04 (ou superior)

Recomendacoes:

| Provedor | Plano sugerido | Custo aprox |
|---|---|---|
| **Hostinger** | KVM 4 (4GB RAM, 2 vCPU) | R$30-60/mes |
| **Hetzner** | CX22 (4GB RAM, 2 vCPU) | EUR 4-6/mes |
| **DigitalOcean** | Basic 4GB | US$24/mes |
| **Vultr** | Cloud Compute 4GB | US$24/mes |

Importante na hora de comprar:
- Sistema operacional: **Ubuntu 22.04** (ou 24.04)
- Mínimo: **4 GB RAM** (8 GB recomendado se for usar prospect Insta + Maps)
- Disco: **mínimo 50 GB**
- Localizacao: qualquer uma. Brasil (Sao Paulo) reduz latencia, mas EUA/Europa funciona igual.

---

## Passo 2 - Anote os dados de acesso da VPS

Quando a VPS estiver pronta, o provedor te manda:

- **IP publico** (ex: `123.45.67.89`)
- **Usuario** (geralmente `root`)
- **Senha** (string aleatoria)

Anote num lugar seguro. O Claude vai te perguntar isso depois.

---

## Passo 3 - Abra o Claude Code no seu computador

Voce pode usar:

- **Site oficial**: [claude.com/code](https://claude.com/code)
- **Extensao VS Code**: instale "Claude Code" no marketplace do VS Code
- **Cursor IDE**: ja vem com integracao Claude

Faz login com sua conta Claude Pro ou Max.

---

## Passo 4 - Cole o prompt magico dentro do Claude

Copie o prompt abaixo e cole na conversa do Claude:

```
Quero instalar meu agente autonomo Claude + Telegram numa VPS Ubuntu.

Acessa o repositorio publico https://github.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson 
e segue o passo a passo de SETUP-AGENTE.md.

Vou te dar as informacoes conforme voce pedir:
- IP, usuario e senha da VPS
- Nome do meu agente (ex: Bia, Paula, Lucas)
- Meu nome
- Token do bot Telegram (eu crio no @BotFather quando voce pedir)
- Meu user_id Telegram (eu pego no @userinfobot quando voce pedir)
- (Opcional) Chave OpenAI pra transcrever audios
- (Opcional) Chave ElevenLabs pra voz feminina

Faz SSH na VPS por mim, instala o ambiente todo (bootstrap.sh), configura o agente, sobe systemd, e me confirma quando estiver no ar conversando comigo no Telegram.
```

> Tambem disponivel em [`prompt-instalador.txt`](./prompt-instalador.txt) pra copiar facil.

---

## Passo 5 - Responda as perguntas do Claude (calmamente, uma por vez)

O Claude vai te perguntar:

1. **IP da VPS** -> cole o IP que o provedor te deu
2. **Usuario** -> geralmente `root`
3. **Senha** -> a senha que o provedor te enviou
4. **Nome do agente** -> escolha um nome (ex: `Bia`, `Paula`, `Lucas`, `Marcus`)
5. **Seu nome** -> seu nome real (vai aparecer nos logs)
6. **Token do bot Telegram** -> nesse momento o Claude pode te guiar:
   - Abra o Telegram
   - Procure `@BotFather`
   - Mande `/newbot`
   - Escolha um nome (ex: `Bia AI`) e username (ex: `bia_ai_bot`)
   - Copie o token que aparece (ex: `1234567890:AAH...`)
   - Cole no Claude
7. **Seu user_id no Telegram** -> o Claude pode te guiar:
   - No Telegram, procure `@userinfobot`
   - Mande `/start`
   - Copie o numero (ex: `123456789`)
   - Cole no Claude
8. **(Opcional) Chave OpenAI** -> [platform.openai.com/api-keys](https://platform.openai.com/api-keys). Permite que o agente entenda audios que voce mandar.
9. **(Opcional) Chave ElevenLabs** -> [elevenlabs.io/profile](https://elevenlabs.io/profile). Permite voz feminina natural nas respostas em audio.

Calma. Uma resposta por vez. O Claude espera voce.

---

## Passo 6 - Aguarde o Claude instalar tudo

Enquanto o Claude trabalha, ele vai:

1. Fazer SSH na sua VPS automaticamente
2. Rodar o `bootstrap.sh` (instala Node, Python, Postgres, Caddy, etc) — leva ~5-10 min
3. Pedir pra voce abrir um link no navegador pra logar na sua conta Claude (so essa parte voce faz)
4. Rodar a configuracao completa do agente
5. Subir o systemd (servico que roda 24/7)
6. Testar a conexao com o Telegram

Quando ele falar **"agente no ar"**, abra o Telegram e mande um "oi" pro seu agente. Ele responde.

---

## Pronto. Como usar agora

- **Conversar**: abra o chat do bot que voce criou (`@bia_ai_bot` por exemplo) e converse normalmente
- **Mandar audio**: o bot transcreve via Whisper (se voce configurou OpenAI) e responde
- **Receber audio**: peca pro agente "responde em audio" se voce configurou ElevenLabs
- **Tarefas longas**: peca coisas tipo "pesquisa X na internet", "cria um codigo Y", "agenda Z" — o agente delega pros subagentes especializados (Paulo, Juliana, Jonathan, Rafael, Davi)

---

## Por que assim (entenda o que rola por baixo)

O **Claude Code** que voce abriu no seu PC e mais que um chat. Ele e um agente que sabe:
- Acessar VPS via SSH (igual um dev faria)
- Rodar comandos shell
- Editar arquivos remotos
- Criar e configurar servicos systemd
- Fazer push pra GitHub
- Configurar DNS no Cloudflare

Quando voce cola o prompt magico, ele vira o **instalador**. Voce so precisa fornecer os dados que so voce sabe (IP, senha, tokens). Ele faz o trabalho braco.

Depois que tudo subir, o **agente na VPS** assume. O Claude Code do seu PC pode ate fechar. O agente vive sozinho na VPS, conversando com voce 24/7 via Telegram.

---

## Problemas comuns

**Claude diz que nao consegue fazer SSH:**
Confirme: IP correto, usuario `root`, senha correta. Se a VPS for nova, espere 2-3 min pra ela bootar antes de tentar.

**O bootstrap demora muito:**
Normal. Instalar Node, Postgres, Caddy etc pode levar ate 10 min em VPS lenta. Deixe rodar.

**Claude nao consegue logar na conta Claude na VPS:**
A autenticacao precisa de browser. O Claude vai te pedir pra **copiar uma URL** e abrir no navegador do **seu PC**. Voce loga, copia o codigo de volta, cola no Claude. Ele continua.

**Telegram nao responde depois de tudo pronto:**
Cheque: o token do `@BotFather` esta certo? O `user_id` do `@userinfobot` esta certo? Voce ja mandou `/start` pro bot na primeira vez?

**Quero parar/reiniciar o agente:**
Peca pro Claude do seu PC: "faz SSH na minha VPS e reinicia o servico do agente Bia". Ele faz.

---

## Suporte

Issues: https://github.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/issues

Bom uso. Bem-vindo ao mundo dos agentes autonomos.
