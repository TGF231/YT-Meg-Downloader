# Pen Drive Maker

Ferramenta gráfica (PySide6) para baixar playlists grandes do YouTube Music
usando a **API Python do `yt-dlp`**, sem disparar o bloqueio do servidor que
costuma travar outras ferramentas por volta de 500 músicas — e já organizando
os arquivos na estrutura de pastas de um pen drive automotivo.

```
<saída>/
├── MPB/
│   └── Djavan/
│       └── Luz/
│           ├── 01 - Flor de Lis.mp3
│           └── 02 - Sina.mp3
└── Rock/
    └── Legiao Urbana/
        └── Dois/
            └── 03 - Eduardo e Monica.mp3
```

## Por que não trava em playlists grandes

O gargalo das ferramentas comuns é pedir os metadados de todas as faixas de uma
vez: centenas de requisições em rajada, e o YouTube responde com `429`,
`403` ou a verificação de robô. Aqui o fluxo é separado em duas fases:

1. **Extração** — uma única requisição em modo *flat* (`extract_flat`) traz
   apenas id, título e URL de cada faixa. Uma playlist de 5.000 músicas custa
   uma requisição.
2. **Download** — a faixa só tem os metadados resolvidos e é baixada quando
   chega a sua vez na fila **e** depois do intervalo mínimo configurado, com
   jitter aleatório somado para não formar um padrão mecânico.

Cada faixa gasta **uma** extração de rede: o dicionário de informações obtido é
reaproveitado no download (mesmo caminho do `--load-info-json` do `yt-dlp`), em
vez de extrair a página duas vezes.

## Instalação

Três caminhos, do mais simples ao mais flexível.

**1. Executável standalone** — baixe o arquivo da sua plataforma na
[página de releases](https://github.com/TGF231/Pen-Drive-Maker/releases) e
execute. **Não precisa de nada instalado**: o `ffmpeg` e o `deno` vêm dentro
dele. Por isso o arquivo tem ~180 MB, e a primeira abertura leva alguns
segundos enquanto ele se descompacta.

```bash
# Linux
chmod +x PenDriveMaker-linux-x86_64
./PenDriveMaker-linux-x86_64
```

**2. Pacote Python** — instala o comando `pen-drive-maker`:

```bash
pip install pen_drive_maker-1.0.0-py3-none-any.whl
pen-drive-maker
```

**3. A partir do código**:

```bash
pip install -r requirements.txt
python main.py
```

### Dois pré-requisitos — já embutidos no executável

Quem usa o **executável standalone** não precisa instalar nada: as duas
ferramentas vêm dentro dele e têm prioridade sobre as do sistema. As
instruções abaixo valem para quem roda pelo pacote Python ou pelo código.
A interface verifica as duas antes de começar.

**1. Runtime JavaScript — obrigatório.** O YouTube exige a execução de um
script para liberar os links de áudio. Sem ele todo download falha com
`Signature solving failed` e `The page needs to be reloaded`, por mais que os
cookies estejam corretos. O Deno é o recomendado pelo `yt-dlp`:

```bash
winget install DenoLand.Deno            # Windows
curl -fsSL https://deno.land/install.sh | sh   # Linux
brew install deno                       # macOS
```

O `node`, o `bun` e o `qjs` também servem. No Windows há uma alternativa sem
instalar nada: baixe o `deno.exe` e coloque **na mesma pasta do
`PenDriveMaker.exe`** — o `yt-dlp` procura ali quando o programa está
empacotado.

**2. ffmpeg — necessário para converter.** Sem ele o áudio é salvo no formato
original, sem conversão para MP3, sem metadados e sem capa. Se a detecção
automática não achar, aponte o caminho no campo **ffmpeg** da interface e use
**Testar** para conferir.

```bash
winget install Gyan.FFmpeg       # Windows
sudo apt install ffmpeg          # Debian/Ubuntu
brew install ffmpeg              # macOS
```

## Uso

1. Cole a URL da playlist (`https://music.youtube.com/playlist?list=...`).
2. Escolha o diretório de saída — pode ser a raiz do pen drive.
3. Ajuste o intervalo entre downloads e, se quiser, informe cookies.
4. **Iniciar / Retomar**.

O que raramente muda — retentativas, backoff, metadados, caminho do ffmpeg —
fica em *Ferramentas → Opções avançadas*. O botão *Tema escuro* alterna a
aparência e a escolha fica salva.

As configurações ficam salvas entre execuções (`QSettings`).

### Retomada

A fila fica em `<saída>/.pen-drive-maker/queue.db` (SQLite). Fechar o programa,
parar no meio ou ser bloqueado não perde nada: ao clicar em **Iniciar /
Retomar** com o mesmo diretório de saída, a playlist **não** é reextraída e as
faixas já baixadas **não** são repetidas.

| Botão | O que faz |
|---|---|
| Iniciar / Retomar | Começa ou continua a fila |
| Pausar / Retomar | Pausa entre faixas, sem perder o estado |
| Parar | Encerra de forma limpa após a faixa atual |
| Reenfileirar falhas | Devolve as faixas marcadas como falha para a fila |
| Reextrair playlist | Rebusca a lista (para playlists que ganharam músicas novas) |

### Cookies

Cookies de uma conta logada reduzem bastante a chance de bloqueio e são
necessários para playlists privadas. Duas opções:

- **Arquivo `cookies.txt`** no formato Netscape (extensões de navegador geram).
- **Direto do navegador** — o `yt-dlp` lê o perfil instalado.

Os cookies são testados assim que você clica em **Iniciar**, antes de qualquer
requisição: se falharem, a ferramenta explica o motivo e oferece seguir sem
eles naquela execução.

#### "ffmpeg não encontrado" mesmo estando no PATH

O `PATH` do Windows aceita formatos que as bibliotecas do Python não resolvem.
A partir da 1.0.4 a busca normaliza entradas entre aspas, com variáveis não
expandidas e com espaços sobrando, e ainda procura nos locais de instalação do
winget, chocolatey e scoop. Se mesmo assim não achar:

1. Clique em **Testar**, ao lado do campo **ffmpeg**, para ver o que a busca
   encontra.
2. Use **Procurar...** e aponte para o `ffmpeg.exe`. O caminho fica salvo.
3. Ou coloque o `ffmpeg.exe` na mesma pasta do `PenDriveMaker.exe`.

Vale lembrar que uma alteração no `PATH` só vale para programas abertos depois
dela — se você acabou de instalar, feche e abra o programa.

#### `The page needs to be reloaded` / `Signature solving failed`

O YouTube exige resolver um desafio JavaScript, e isso precisa de **duas**
coisas: um runtime e os scripts do solucionador.

1. **Runtime** — Deno, Node, Bun ou QuickJS. Veja os pré-requisitos acima.
2. **Scripts** — o pacote `yt-dlp-ejs`. O `yt-dlp` embute apenas um stub que
   busca a biblioteca no npm durante o download, e esse download vem desligado
   por padrão. Se a mensagem citar *Remote components ... were skipped*, é isso
   que falta: `pip install yt-dlp-ejs`.

O executável standalone já traz os dois. O log de abertura mostra qual runtime
e qual versão do solucionador estão em uso.

Se o pacote local ficar defasado em relação ao `yt-dlp`, marque **Permitir
baixar componentes do yt-dlp quando faltarem** na interface: ele busca os
scripts sozinho, do GitHub e do npm.

#### `Could not copy Chrome cookie database`

**Essa mensagem cita o Chrome mesmo quando o navegador escolhido é outro.** O
`yt-dlp` usa o mesmo texto para todos os navegadores baseados em Chromium —
Chrome, Edge, Brave, Opera e Vivaldi. Se você selecionou o Edge, o Edge foi
consultado; só a mensagem é que fala em Chrome.

A causa é um `PermissionError` no Windows: o banco de cookies fica travado
enquanto o navegador está rodando.

1. Feche o navegador por completo, inclusive os processos em segundo plano:

   | Navegador | Onde desligar | Processo a encerrar |
   |---|---|---|
   | Edge | `edge://settings/system` — *Aumento de inicialização* e *Continuar executando extensões e aplicativos em segundo plano* | `msedge.exe` |
   | Chrome | `chrome://settings/system` — *Continuar executando apps em segundo plano* | `chrome.exe` |
   | Brave | `brave://settings/system` | `brave.exe` |

2. Se ainda falhar no Windows: os navegadores Chromium passaram a guardar os
   cookies com uma criptografia ligada ao aplicativo que o `yt-dlp` não
   consegue abrir. Use o **Firefox** ou um arquivo `cookies.txt`.
3. Para exportar o `cookies.txt`, use uma extensão que gere o formato Netscape,
   salve o arquivo e escolha **Arquivo cookies.txt** na interface.

### Playlists, mixes e rádios

URLs de mix ou rádio (o `list=` começa com `RD`) são geradas dinamicamente pelo
YouTube e podem devolver menos faixas do que o aplicativo mostra. Se a contagem
extraída vier bem abaixo do esperado, salve a playlist na sua biblioteca e use
a URL da playlist salva.

## Detecção de bloqueio

O motor classifica cada erro em três categorias:

| Categoria | Exemplos | Ação |
|---|---|---|
| **Bloqueio** | `HTTP Error 429`, `403`, `Sign in to confirm you're not a bot` | Pausa com backoff exponencial (padrão: 120 s, dobrando, teto de 1 h) e retoma. Não gasta tentativa da faixa. |
| **Permanente** | vídeo privado, removido, só para membros | Marca a faixa como falha e segue. Não tenta de novo. |
| **Transitório** | timeout, rede | Tenta de novo até o limite configurado. |

Falhas consecutivas (padrão: 5) também contam como bloqueio, porque é assim que
o bloqueio costuma aparecer na prática. Um download bem-sucedido zera o
contador de bloqueios. Se os bloqueios persistirem (padrão: 5 eventos), a
ferramenta **encerra de forma limpa** e explica no log como retomar.

### Se for bloqueado

1. Espere algumas horas.
2. Aumente o intervalo mínimo (60 s ou mais para playlists muito grandes).
3. Informe cookies de uma conta logada.
4. Clique em **Iniciar / Retomar** — nada já baixado é repetido.

## Organização das pastas

`<saída>/<Gênero>/<Artista>/<Álbum>/<NN - Título>.<ext>`

- **Gênero** é consultado na internet por padrão. O YouTube Music não informa
  gênero nenhum, então a ferramenta pergunta à busca pública do iTunes, que
  responde sem exigir chave. O seletor **Pasta de gênero** oferece:

  | Opção | O que vira a pasta |
  |---|---|
  | Consultar na internet (padrão) | O gênero da base externa; metadados e nome da playlist como reserva |
  | Nome da playlist | O nome da playlist — sua própria classificação, sem rede |
  | Metadados da faixa | O gênero declarado na faixa, se houver |
  | Sempre o gênero de reserva | Um texto fixo, igual para tudo |

  As respostas ficam em cache no banco da fila, então uma execução retomada
  não reconsulta nada. *Ferramentas → Limpar cache de gêneros* esquece tudo.

  **Subgêneros são agrupados** por padrão: numa playlist de EDM, *Dance*,
  *House* e *Electronic* viram `Eletronica`, em vez de três pastas para o que
  o ouvinte trata como uma. Desligue em *Opções avançadas* para usar a
  resposta crua.

  Se a consulta falhar — sem internet, serviço fora —, a faixa cai na reserva
  e o download segue. Depois de algumas falhas seguidas a consulta se desliga
  sozinha pelo resto da execução.
- **Artista** usa `album_artist` → `artists` → `artist` → canal, nessa ordem.
  O sufixo `- Topic` é removido e participações (`feat.`, `ft.`, `with`) vão
  para a pasta do artista principal. Nomes com `&` e vírgula são preservados
  inteiros (`Earth, Wind & Fire` não vira `Earth`).
- **Álbum** ausente vira `Singles`.
- Nomes são saneados para FAT32/exFAT: sem `<>:"/\|?*`, sem caracteres de
  controle, sem ponto ou espaço no fim, sem nomes reservados (`CON`, `LPT1`…),
  limitados em tamanho. A opção **Somente ASCII** (ligada por padrão) remove
  acentos, porque muitos aparelhos automotivos não leem UTF-8.

## Formatos de áudio

| Opção | Quando usar |
|---|---|
| **MP3** (padrão) | Máxima compatibilidade automotiva. Reconvertido em VBR de melhor qualidade. |
| **M4A / AAC** | Melhor qualidade: o YouTube entrega AAC, então é remux sem reconversão. |
| **Opus** | Menor tamanho; pouco suportado em carros. |
| **Melhor original** | Sem conversão nenhuma. |

Metadados e capa são embutidos quando o formato de destino suporta.

## Estrutura do código

| Arquivo | Conteúdo |
|---|---|
| `main.py` | Ponto de entrada |
| `pen_drive_maker/config.py` | `AppConfig` e validação |
| `pen_drive_maker/store.py` | Fila persistente em SQLite |
| `pen_drive_maker/organizer.py` | Metadados → pastas, saneamento de nomes |
| `pen_drive_maker/genre.py` | Consulta de gênero e cache |
| `pen_drive_maker/assinatura/` | Assinatura visual (tema, janela, ações) |
| `pen_drive_maker/tools.py` | Localização do ffmpeg e do runtime JavaScript |
| `pen_drive_maker/downloader.py` | Motor: extração, ritmo, bloqueios, download |
| `pen_drive_maker/gui.py` | Interface PySide6 |
| `tests/test_engine.py` | Testes offline do motor (`yt-dlp` substituído) |
| `tests/test_tools.py` | Testes da detecção de ferramentas externas |
| `tests/test_genre.py` | Testes da consulta de gênero (sem rede) |
| `tests/test_gui.py` | Testes da interface (Qt offscreen) |
| `pyproject.toml` | Metadados do pacote e entry point `pen-drive-maker` |
| `packaging.spec` | Receita do PyInstaller para o executável standalone |
| `.github/workflows/release.yml` | Testes, build e publicação da release na tag |

Dependências Python: `PySide6`, `yt-dlp` e `yt-dlp-ejs` (os scripts que
resolvem os desafios JavaScript do YouTube).

### Ferramentas de terceiros embutidas

O executável standalone embute dois programas, executados como processos
separados pelo `yt-dlp`:

| Programa | Origem | Licença |
|---|---|---|
| ffmpeg | [builds estáticos do BtbN](https://github.com/BtbN/FFmpeg-Builds) | GPL |
| deno | [denoland/deno](https://github.com/denoland/deno) | MIT |

O texto completo das licenças vai no arquivo `THIRD-PARTY-LICENSES.txt`,
publicado em cada release e embutido no executável. O código-fonte do ffmpeg
está em <https://ffmpeg.org/download.html> e os scripts de build no
repositório do BtbN. O Pen Drive Maker em si é MIT.

```bash
python -m unittest discover -s tests -v
```

Os testes não importam PySide6, então rodam sem Qt instalado.

## Publicar uma nova versão

1. Atualize `__version__` em `pen_drive_maker/__init__.py` e adicione a seção
   correspondente em `CHANGELOG.md`.
2. Crie e envie a tag:

   ```bash
   git tag -a v1.1.0 -m "v1.1.0"
   git push origin v1.1.0
   ```

O workflow `.github/workflows/release.yml` roda os testes, gera wheel e sdist,
compila os executáveis para Linux e Windows com o PyInstaller
(`packaging.spec`) e publica a release com todos os artefatos, usando a seção
do `CHANGELOG.md` como notas.

Também dá para publicar sem enviar a tag: em **Actions → Release → Run
workflow**, informe a versão (ex.: `v1.0.0`) e o workflow cria a tag no commit
escolhido junto com a release.

Para gerar os artefatos localmente:

```bash
python -m build                          # dist/*.whl e dist/*.tar.gz
pyinstaller --noconfirm packaging.spec   # dist/PenDriveMaker
```
