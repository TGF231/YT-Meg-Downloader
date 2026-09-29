# Changelog

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
versionamento segue [SemVer](https://semver.org/lang/pt-BR/).

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

[1.0.0]: https://github.com/TGF231/Pen-Drive-Maker/releases/tag/v1.0.0
