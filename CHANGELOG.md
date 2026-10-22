# Changelog

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
versionamento segue [SemVer](https://semver.org/lang/pt-BR/).

## [1.4.0] - 2026-10-22

### Adicionado

- Seletor **Estrutura de pastas**: `Gênero / Artista / Álbum` (padrão) ou
  `Gênero / Artista`. O álbum continua gravado na tag do arquivo nos dois
  casos — a pasta é só uma forma de separar, e menos níveis significam menos
  navegação no rádio do carro.
- Desempate de nomes repetidos. Com um nível a menos, duas faixas do mesmo
  artista com o mesmo título cairiam no mesmo arquivo: a segunda seria pulada
  como "já baixada" e sumiria em silêncio. Agora ela recebe o id do vídeo no
  nome, com aviso no log.

### Corrigido

- A árvore da interface mostrava um nível de álbum que não existia no disco
  quando a pasta estava desligada.

## [1.4.0] - 2026-10-22

### Adicionado

- Seletor **Estrutura de pastas**, com três níveis de profundidade. O que sai
  da pasta entra no nome do arquivo, para a faixa continuar identificável e a
  ordenação continuar fazendo sentido:

  | Opção | Resultado |
  |---|---|
  | Gênero / Artista / Álbum (padrão) | `EDM/Avicii/True/02 - Wake Me Up.mp3` |
  | Gênero / Artista | `EDM/Avicii/Wake Me Up.mp3` |
  | Somente gênero | `EDM/Avicii - Wake Me Up.mp3` |

  Artista e álbum continuam gravados na tag do arquivo em qualquer opção. O
  número da faixa sai junto com a pasta de álbum, porque fora dela ele só
  embaralharia faixas de álbuns diferentes.
- Desempate de nomes repetidos. Com menos pastas, duas faixas do mesmo artista
  com o mesmo título cairiam no mesmo arquivo: a segunda seria pulada como "já
  baixada" e sumiria em silêncio. Agora recebe o id do vídeo no nome, com
  aviso no log.

### Corrigido

- A árvore da interface mostrava níveis que não existiam no disco quando as
  pastas correspondentes estavam desligadas.

## [1.3.0] - 2026-10-06

### Adicionado

- **Consulta de gênero numa base externa.** O YouTube Music não informa
  gênero; a busca pública do iTunes informa, sem exigir chave. Passa a ser a
  fonte padrão, com o nome da playlist e os metadados como reserva.
- As respostas ficam em cache no mesmo banco da fila, inclusive as ausências,
  para não reconsultar a cada retomada. Falha de rede **não** é gravada: um
  erro temporário não pode deixar a faixa sem gênero para sempre.
- Subgêneros são agrupados por padrão: numa playlist de EDM, *Dance*, *House*
  e *Electronic* viram uma pasta só, em vez de três. Desligável nas opções.
- Interface com a assinatura visual da família: acento #7D0E0E, temas claro e
  escuro alternáveis, barra de ações e densidade automática.

### Alterado

- **Interface simplificada.** A tela principal ficou com o que muda a cada
  uso: playlist, destino, cookies, gênero, formato e intervalo. Ritmo,
  retentativas, metadados, ffmpeg e afins foram para *Ferramentas → Opções
  avançadas*.
- As ações viraram barra de ferramentas, vindas de uma tabela única, com um
  teste que falha se alguma linha apontar para um método inexistente.

## [1.2.0] - 2026-10-06

### Alterado

- **A pasta de gênero passa a usar o nome da playlist.** O YouTube Music quase
  nunca preenche o campo de gênero, então tudo caía em `Sem Genero`. O nome da
  playlist já estava guardado no banco desde a extração, mas nunca era
  consultado: quem monta uma playlist chamada "EDM" já classificou as faixas, e
  é essa classificação que serve ao pen drive.

### Adicionado

- Seletor "Pasta de gênero" com três opções: o nome da playlist (padrão), os
  metadados da faixa com a playlist de reserva, ou um texto fixo. O campo
  antigo "Pasta para gênero ausente" virou "Gênero de reserva".
- O nome vem do banco, então vale igual numa execução retomada, sem reextrair.
- O exemplo de estrutura de destino mostra o nome que a pasta terá de verdade,
  conforme a fonte escolhida, em vez da palavra "Gênero" sempre.

## [1.1.1] - 2026-09-30

### Corrigido

- **Faltava o pacote `yt-dlp-ejs`, e todo download falhava.** O `yt-dlp` traz
  embutido apenas um stub de 245 bytes que importa a biblioteca do solucionador
  do npm em tempo de execução — download que vem desligado por padrão. O
  resultado era `The page needs to be reloaded` em toda faixa, mesmo com o
  Deno funcionando. O pacote virou dependência e é embutido no executável:
  traz a biblioteca de 148 KB localmente, na versão 0.8.0 que o yt-dlp espera.
- **O exemplo de estrutura de destino misturava separadores**, mostrando
  `C:\Users\User\PenDrive/Gênero/Artista/...`. Agora o caminho é normalizado
  para o separador do sistema, inclusive quando vem do seletor de pastas do Qt,
  que devolve barras normais no Windows.
- O mesmo exemplo mostrava acentos que a opção "Somente ASCII" remove, e uma
  extensão fixa. Agora passa pelo saneamento real e usa o formato escolhido.

### Adicionado

- Opção "Permitir baixar componentes do yt-dlp quando faltarem", desligada por
  padrão, para o caso de o pacote local ficar defasado em relação ao yt-dlp.
- O log de abertura informa a versão do solucionador em uso.

## [1.1.0] - 2026-09-29

A 1.0.4 não chegou a ser publicada: o job de testes falhou no CI porque a
suíte dependia das ferramentas instaladas na máquina. As correções dela estão
incluídas aqui.

### Adicionado

- **ffmpeg e deno embutidos no executável.** O programa deixa de exigir
  qualquer instalação: as ferramentas embutidas são procuradas antes das do
  sistema, e o executável passou de ~54 MB para ~180 MB. Um caminho indicado
  à mão continua tendo prioridade sobre o embutido.
- `scripts/fetch_tools.py`, usado pelo workflow para baixar as ferramentas e
  reunir as licenças delas em `THIRD-PARTY-LICENSES.txt`, publicado junto da
  release e embutido no executável.

### Corrigido

- A sondagem de um executável usava só `-version`, que o deno recusa e o node
  também. Agora tenta `--version` antes, e descarta saídas que reclamam do
  argumento mesmo terminando com código 0.
- A suíte de testes deixou de depender das ferramentas instaladas na máquina:
  passa igual com PATH vazio.

- **O ffmpeg não era detectado mesmo estando no PATH.** A busca usava só o
  `shutil.which`, que não trata três formatos de entrada de PATH que o shell do
  Windows aceita: entre aspas (`"C:\ffmpeg\bin"`), com variável não expandida
  (`%USERPROFILE%\bin`, quando a chave do registro é REG_SZ) e com espaços
  sobrando. Qualquer uma delas fazia o programa dizer que o ffmpeg não estava
  instalado.
- **O caminho do ffmpeg não era repassado ao yt-dlp.** Mesmo quando a interface
  o encontrava, o yt-dlp refazia a busca por conta própria e falhava de novo.
  Agora recebe `ffmpeg_location`.

### Adicionado

- Campo "ffmpeg" na interface, com botões Procurar e Testar, para apontar o
  executável ou a pasta quando a detecção automática não bastar.
- Busca nos locais usuais de instalação do Windows: atalhos e pacotes do
  winget, chocolatey, scoop, `C:\ffmpeg\bin` e `Program Files`.
- Cada candidato é confirmado executando `-version`, sem piscar console.
- Módulo `pen_drive_maker/tools.py` reunindo a localização de ferramentas
  externas.

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

[1.4.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.4.0
[1.4.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.4.0
[1.3.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.3.0
[1.2.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.2.0
[1.1.1]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.1.1
[1.1.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.1.0
[1.0.3]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.3
[1.0.2]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.2
[1.0.1]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.1
[1.0.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.0
