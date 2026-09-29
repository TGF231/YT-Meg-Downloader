# Changelog

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
versionamento segue [SemVer](https://semver.org/lang/pt-BR/).

## [1.0.3] - 2026-09-29

### Corrigido

- **O executável standalone não continha os scripts do solucionador de desafios
  do YouTube.** O yt-dlp guarda esses `.js` dentro do próprio pacote, e o hook
  oficial dele para o PyInstaller só coleta os do pacote opcional
  `yt_dlp_ejs`. Sem eles todo download falhava com "The page needs to be
  reloaded", mesmo com um runtime JavaScript instalado. O `packaging.spec`
  passa a coletá-los.
- Falhas de desafio JavaScript não consomem mais as retentativas da faixa: sem
  um runtime instalado, nenhuma repetição resolveria.

### Adicionado

- Verificação de runtime JavaScript (deno, bun, node ou qjs) antes de começar,
  com instruções de instalação por sistema. A busca cobre o PATH e a pasta do
  próprio executável, que é onde o yt-dlp procura quando o programa está
  empacotado — basta colocar o `deno.exe` ao lado do `PenDriveMaker.exe`.
- Aviso sobre o ffmpeg ausente quando ele é necessário para o formato escolhido
  (conversão, metadados ou capa), com a opção de baixar sem converter.
- Ambas as verificações podem ser ignoradas pelo usuário, caso a detecção
  falhe numa instalação fora do comum.

## [1.0.2] - 2026-09-29

### Corrigido

- A orientação sobre cookies repetia a palavra "Chrome" da mensagem do yt-dlp
  mesmo quando o navegador escolhido era outro. O yt-dlp usa esse texto para
  qualquer navegador baseado em Chromium; agora a mensagem nomeia o navegador
  realmente consultado e explica a diferença.
- A instrução para fechar o navegador passou a ser específica: o Edge aponta
  para `edge://settings/system`, o "Aumento de inicialização" e o processo
  `msedge.exe`; o Chrome e o Brave têm as suas.
- Navegadores não-Chromium (Firefox, Safari) deixaram de receber a observação
  sobre a criptografia do Windows, que não se aplica a eles.

## [1.0.1] - 2026-09-29

### Corrigido

- Falha ao carregar cookies (por exemplo, `Could not copy Chrome cookie
  database`, quando o navegador está aberto) encerrava a execução com
  "Erro inesperado: failed to load cookies", sem dizer o que fazer. Agora os
  cookies são testados antes de extrair a playlist e a mensagem explica o
  passo a passo: fechar o navegador, usar o Firefox ou exportar um
  `cookies.txt`.
- Uma falha de cookie no meio da fila não é mais contada como falha da faixa.

### Adicionado

- Diálogo ao iniciar quando os cookies não carregam, com a opção de seguir sem
  cookies só naquela execução, sem alterar a configuração salva.

## [1.0.0] - 2026-09-29

Primeira versão.

### Adicionado

- Extração e download separados: a playlist é lida uma única vez em modo flat
  (`extract_flat`) e cada faixa só tem os metadados resolvidos quando chega a
  sua vez na fila, com uma única extração de rede por faixa (o dicionário de
  informações é reaproveitado no download, como o `--load-info-json` faz).
- Fila persistente em SQLite (`<saída>/.pen-drive-maker/queue.db`) com status e
  número de tentativas por item; execução interrompida retoma sem reextrair a
  playlist nem repetir faixas já baixadas.
- Intervalo mínimo configurável entre downloads, com jitter aleatório.
- Detecção de bloqueio (HTTP 429, 403, verificação de robô, falhas
  consecutivas) com backoff exponencial, retomada da fila e encerramento limpo
  após bloqueios persistentes.
- Classificação de erros em bloqueio, permanente e transitório: erro permanente
  não consome retentativas, bloqueio não penaliza a faixa.
- Organização automática em `Gênero/Artista/Álbum/NN - Título`, com nomes
  saneados para FAT32/exFAT e opção de somente ASCII.
- Saída em MP3, M4A, Opus ou formato original, com metadados e capa embutidos.
- Cookies opcionais por arquivo Netscape ou direto do navegador.
- Interface PySide6 com contadores, barras de progresso, log colorido, árvore
  da estrutura de pastas criada e controles de pausar, retomar e parar.

[1.0.3]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.3
[1.0.2]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.2
[1.0.1]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.1
[1.0.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.0
