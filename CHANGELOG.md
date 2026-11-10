# Changelog

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
versionamento segue [SemVer](https://semver.org/lang/pt-BR/).

## [2.4.0] - 2026-11-10

### Adicionado

- **Vários downloads e várias conversões ao mesmo tempo.** *Opções avançadas →
  Simultaneidade*: downloads simultâneos (padrão 2) e conversões simultâneas
  (padrão 2). Cada vaga de download respeita o seu intervalo; os inícios são
  escalonados. A conversão de uma faixa nunca segura o download da próxima.
- Um bloqueio detectado por uma vaga pausa todas; um erro fatal encerra todas.

### Alterado

- **Barras de progresso.** *Agora* mostra a média dos downloads em curso e
  quantas faixas estão convertendo; *Playlist* mostra `feitas/total`. Antes a
  barra só acompanhava um download, que dura poucos segundos, e quase não se
  movia.
- O gênero "um artista, um gênero" ignora o gênero de reserva (nome da
  playlist) na votação e é unificado ao fim da execução, para a ordem em que as
  conversões terminam não dividir um artista em duas pastas.

## [2.3.0] - 2026-11-10

### Corrigido

- **Opções avançadas não abria.** O QSettings do Windows devolve as opções
  ligadas/desligadas como texto (`"false"`), e a tela quebrava sem mostrar
  nada. Os valores agora são convertidos ao ler, e uma falha ao abrir a tela
  aparece numa mensagem em vez de sumir. Antes, `"false"` valia como ligado:
  uma opção desligada podia continuar ativa.
- **Agrupamento de gêneros errado.** O casamento por pedaço de texto confundia
  *Dancehall* com `dance`, *Reggaeton* com `reggae` e *Música Popular
  Brasileira* com `pop`. Agora só palavras inteiras contam, e em *Dance Pop* ou
  *Folk Rock* vale a última palavra (o gênero de base).

### Adicionado

- **Editar o agrupamento de subgêneros** (*Opções avançadas → Editar
  agrupamento…*), com campo de teste ao vivo e botão de restaurar o padrão.
- **Rótulos que não são gênero** (*Bollywood*, *Soundtrack*, *Anime*…) não viram
  mais pasta: a faixa usa o gênero de reserva. O grupo `(ignorar)` é editável.
- **Gêneros fora dos grupos** caem na reserva em vez de criar uma pasta para cada
  rótulo da internet (opção de manter o nome devolvido).
- **Um artista, um gênero**: as faixas de um artista seguem o gênero que já
  predomina na biblioteca.
- *Aplicar estrutura atual aos arquivos* agora aplica as regras de gênero atuais
  ao que já foi baixado e regrava a tag de gênero dos arquivos movidos.

### Alterado

- *Soundtrack* e *Score* saíram do grupo `Classica`: viram rótulos ignorados.

## [2.2.0] - 2026-11-03

### Alterado

- **Download e conversão em filas separadas.** A conversão (extrair o áudio,
  gravar tags, embutir capa, renomear e mover) roda numa thread própria: a
  próxima faixa começa a baixar sem esperar a anterior terminar de converter.
  Os downloads seguem em série e com intervalo, então a proteção contra
  bloqueio não muda.
- O áudio cru espera em `.pen-drive-maker/staging/` e só entra na biblioteca
  pronto. Parar ou ser bloqueado termina de converter o que já foi baixado.
- Falha de conversão conta uma tentativa só daquela faixa e não é tratada como
  bloqueio. Um arquivo alheio no destino nunca é sobrescrito.

## [2.1.0] - 2026-10-23

### Adicionado

- **Identificar e organizar arquivos soltos** (*Biblioteca*): músicas sem origem
  conhecida são buscadas no YouTube pelas tags ou pelo nome `Artista - Título`.
  Quando título, artista e duração batem, o arquivo é vinculado à URL, recebe
  tags corretas (com a URL de origem), é renomeado e movido para a pasta da
  estrutura escolhida, sem baixar de novo. Correspondências incertas só são
  aplicadas se você confirmar.
- **Cancelar tarefa**: interrompe verificação, cópia, reorganização ou
  identificação em andamento.

### Alterado

- Arquivos vinculados à mão não aparecem mais como "sem origem conhecida" na
  verificação da biblioteca.

## [2.0.0] - 2026-10-22

A pasta de saída deixa de ser uma fila de uma playlist e passa a ser uma
**biblioteca**: um catálogo de músicas, cada uma ligada à URL de onde veio e ao
lugar onde foi salva.

### Migração

- O banco de uma versão 1.x é convertido sozinho na primeira abertura, sem
  perder nada: a playlist vira a primeira da biblioteca, status, tentativas e
  erros são mantidos, e os caminhos ganham a forma relativa. Abrir de novo não
  duplica nada.

### Alterado

- **Uma URL nova não apaga mais o histórico.** Antes, trocar de playlist
  esvaziava a fila da anterior. Agora a playlist se soma à biblioteca, e
  músicas que outra já trouxe não são baixadas de novo.
- A árvore de pastas vem dos caminhos reais dos arquivos, não das tags: com
  estrutura manual, só o caminho diz como a biblioteca está organizada.
- O arquivo passa a levar nas tags o mesmo gênero da pasta. Antes o yt-dlp
  gravava a categoria do YouTube ("Music"), e o rádio mostrava outra coisa.
- Com a URL vazia, **Iniciar** completa a biblioteca em vez de recusar.

### Adicionado

- **Reconhecimento pela tag.** A URL de origem vai gravada em cada arquivo, e a
  biblioteca a lê de volta: um arquivo movido no Explorer é achado no novo
  lugar em vez de baixado de novo.
- **Faixas fixadas.** Local escolhido por você: fica onde está mesmo que a
  estrutura mude, e se for apagada volta para o mesmo lugar.
- **Página Biblioteca**, com busca, filtro por status e ações sobre a seleção:
  mover para pasta, voltar à estrutura automática, baixar de novo e remover.
- **Verificar biblioteca**: acha faixas movidas, devolve à fila as apagadas,
  adota arquivos postos à mão que trazem a URL de origem e lista áudio sem
  origem e duplicatas.
- **Aplicar estrutura atual aos arquivos**: muda os arquivos de pasta conforme
  a estrutura escolhida, sem baixar nada.
- **Copiar para outro pen drive**: reproduz a biblioteca com a mesma estrutura,
  copiando o que existe e deixando na fila o que faltar.
- **Exportar e importar** a biblioteca num arquivo `.json`, para levá-la a
  outro computador. Os caminhos usam `/` e valem entre sistemas.
- Removidas ficam marcadas no catálogo: atualizar a playlist não as traz de
  volta.
- Nova dependência: `mutagen`, para ler as tags. Importada no topo do módulo,
  de propósito: se faltar num executável, o programa falha ao abrir em vez de
  tratar todo arquivo como desconhecido e rebaixar tudo.
- Tarefas longas de biblioteca rodam numa thread, com progresso, sem congelar a
  janela.

### Corrigido

- A seção de botões do README ainda usava os nomes antigos de antes da 1.6.0.

## [1.6.0] - 2026-10-22

### Corrigido

- **A fila nunca conferia o disco.** O banco guardava "baixada" para cada faixa
  e confiava nisso para sempre: quem apagava ou movia as pastas continuava com
  a fila marcada como pronta, a árvore da interface (lida do banco) seguia
  mostrando pastas que já não existiam, e "reenfileirar" não ajudava porque só
  olhava faixas que *falharam*.
  - Ao abrir o programa e a cada **Iniciar**, as faixas marcadas como baixadas
    cujo arquivo sumiu voltam para a fila, e a árvore é refeita.
  - Novo botão **Verificar arquivos** faz a mesma conferência quando você quiser.
  - Uma extensão diferente com o mesmo nome conta como presente, e sobras como
    `.part` ou capas `.jpg` não.
- **Pen drive desconectado não zera a fila.** Com a unidade ausente todo arquivo
  pareceria sumido; a conferência só roda se a pasta de saída existia antes de
  o programa tentar criá-la.
- O exemplo "Destino" não acompanhava o campo Diretório de saída quando a pasta
  era digitada ou colada.

### Adicionado

- **Limpar fila e histórico…** (menu Fila): esquece a playlist, a fila e o
  histórico para recomeçar do zero, pedindo confirmação. Não apaga nenhum
  arquivo do disco. O cache de gêneros é mantido.

### Alterado

- Ações da fila com nomes que dizem o que fazem, e uma explicação ao passar o
  mouse: *Reenfileirar falhas* virou **Tentar de novo as que falharam**, e
  *Reextrair playlist* virou **Atualizar lista da playlist** (busca a playlist
  de novo e acrescenta as músicas novas, sem apagar nem rebaixar nada).

## [1.5.0] - 2026-10-22

### Adicionado

- Duas estruturas de pasta a mais: **Somente artista** e **Somente álbum**.
  As cinco opções saem agora de uma tabela única de níveis, em vez de casos
  escritos um a um.

  | Opção | Resultado |
  |---|---|
  | Gênero / Artista / Álbum (padrão) | `EDM/Avicii/True/02 - Wake Me Up.mp3` |
  | Gênero / Artista | `EDM/Avicii/Wake Me Up.mp3` |
  | Somente gênero | `EDM/Avicii - Wake Me Up.mp3` |
  | Somente artista | `Avicii/Wake Me Up.mp3` |
  | Somente álbum | `True/02 - Avicii - Wake Me Up.mp3` |

- Cada nível passa a ser gravado na sua própria coluna do banco, em vez de por
  posição: sem isso a árvore da interface mostraria um artista na coluna de
  gênero quando a estrutura fosse "somente artista".

### Corrigido

- As notas da 1.4.0 saíram com a seção duplicada, por uma entrada repetida
  neste arquivo.

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
  | Somente artista | `Avicii/Wake Me Up.mp3` |
  | Somente álbum | `True/02 - Avicii - Wake Me Up.mp3` |

  Gênero, artista e álbum continuam gravados na tag do arquivo em qualquer
  opção. Duas regras governam o nome: o número da faixa só aparece quando há
  pasta de álbum, porque fora dela ele não ordena nada; e o artista entra no
  nome quando não houver pasta dele. O gênero nunca entra — ele classifica,
  não identifica.
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

[2.4.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v2.4.0
[2.3.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v2.3.0
[2.2.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v2.2.0
[2.1.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v2.1.0
[2.0.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v2.0.0
[1.6.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.6.0
[1.5.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.5.0
[1.4.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.4.0
[2.0.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v2.0.0
[1.6.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.6.0
[1.5.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.5.0
[1.4.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.4.0
[1.3.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.3.0
[1.2.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.2.0
[1.1.1]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.1.1
[1.1.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.1.0
[1.0.3]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.3
[1.0.2]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.2
[1.0.1]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.1
[1.0.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.0
