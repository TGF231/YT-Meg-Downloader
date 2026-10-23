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

## Biblioteca

A pasta de saída é uma **biblioteca**: um catálogo de músicas, cada uma ligada à
URL de onde veio e ao lugar onde foi salva. O catálogo fica dentro da própria
pasta, em `.pen-drive-maker/queue.db`, e vai junto com ela.

O que liga um arquivo à biblioteca é a **URL de origem gravada dentro dele**, na
tag de comentário. Por isso o caminho não é a única pista: você pode reorganizar
os arquivos no Explorer, e o programa os reconhece depois.

> Para isso, **Gravar metadados na tag** (Opções avançadas) precisa estar
> ligado, como vem por padrão. Sem tags, um arquivo movido parece sumido e é
> baixado de novo.

### Várias playlists, sem repetir

Cole a URL de outra playlist e clique em **Iniciar**: ela se soma à biblioteca.
Músicas que outra playlist já trouxe **não são baixadas de novo** — só ganham o
vínculo com a nova. O log diz quantas eram repetidas.

Com o campo de URL vazio, **Iniciar** completa a biblioteca: baixa o que estiver
na fila, vindo de qualquer playlist, de uma importação ou de faixas apagadas.

### A página Biblioteca

O botão **Biblioteca**, na barra, mostra todas as faixas com status, artista,
álbum, gênero, playlists e local. Dá para buscar e filtrar por status. Com faixas
selecionadas:

| Ação | O que faz |
|---|---|
| **Mover para pasta…** | Move os arquivos para uma pasta escolhida e os **fixa** lá |
| **Voltar à estrutura automática** | Tira a fixação; a próxima reorganização devolve as faixas às pastas automáticas |
| **Baixar de novo** | Põe de volta na fila (uma faixa fixada volta para o mesmo lugar) |
| **Remover da biblioteca…** | Tira da biblioteca, apagando ou não o arquivo; atualizar a playlist não a traz de volta |

### Estrutura manual

Faixa **fixada** é uma faixa cujo local foi escolhido por você. Ela fica onde
está mesmo que a estrutura de pastas mude, e se for apagada é baixada de novo
**no mesmo lugar**. Há três jeitos de fixar:

- **Mover pelo programa** — *Mover para pasta…* na página Biblioteca.
- **Mover no Explorer** — arraste os arquivos para onde quiser. No próximo
  **Iniciar** ou **Verificar biblioteca**, o programa acha cada um pela tag e
  passa a apontar para o novo lugar.
- **Montar do zero** — ponha arquivos baixados por este programa em pastas suas e
  clique em **Verificar biblioteca**: os que trouxerem a URL de origem entram no
  catálogo, fixados onde estão.

### Verificar biblioteca

Compara o catálogo com o disco, nesta ordem:

1. faixa que **mudou de pasta** → o catálogo passa a apontar para lá (e a fixa);
2. faixa que **sumiu** → volta para a fila;
3. arquivo que **o catálogo não conhecia** mas traz a URL de origem → entra na
   biblioteca;
4. áudio **sem origem** (baixado por outro programa) e músicas **duplicadas** →
   listados no log, sem mexer em nada.

Ao abrir o programa e a cada **Iniciar**, roda uma versão rápida: só confere se
os arquivos existem, e lê as tags apenas se algo sumiu. Com a pasta num pen
drive desconectado, nada é alterado — o programa avisa em vez de achar que você
apagou tudo.

### Identificar arquivos soltos

Músicas postas na pasta sem nenhuma origem registrada (outro programa, sem
metadados, nomes sem padrão) não fazem parte do catálogo. Use *Menu →
Biblioteca →* **Identificar arquivos soltos…**: para cada uma, o programa monta
uma pista a partir das tags (artista e título) ou do nome (`Artista - Título`,
ignorando número de faixa e `(Official Video)`), busca no YouTube pela API do
yt-dlp e compara título, artista e duração.

- correspondência **clara** → o arquivo é vinculado à URL, fica onde está e passa
  a valer para reorganizar, copiar e exportar;
- correspondência **incerta** → uma janela mostra arquivo × resultado e só
  vincula o que você marcar;
- **sem resultado** ou música que a biblioteca já tem em outro arquivo → listado
  no log, nada é alterado.

Os arquivos nunca são modificados, movidos ou apagados. A busca tem intervalo
entre as consultas, pode ser cancelada e para sozinha se várias falharem
seguidas (rede ou bloqueio). Sem nome nem tags aproveitáveis, o programa não
consegue adivinhar: esses ficam sem vínculo.

### Mudar a estrutura sem baixar de novo

Escolha a nova **Estrutura de pastas** e use *Menu → Biblioteca →* **Aplicar
estrutura atual aos arquivos…**. Os arquivos mudam de pasta, nada é baixado, as
faixas fixadas ficam onde estão e as pastas que ficarem vazias são removidas.
Um arquivo nunca é sobrescrito: se o nome estiver ocupado, ganha o id do vídeo.

### Reproduzir em outro pen drive

*Menu → Biblioteca →* **Copiar para outro pen drive…** reproduz a biblioteca em
outra pasta, com a mesma estrutura — inclusive a manual:

- **Copiar arquivos**: copia o que existe aqui e deixa na fila o que faltar.
- **Só a estrutura**: tudo será baixado no destino, nos mesmos locais. Útil
  quando estes arquivos não estão à mão.

No fim, o programa oferece trocar para o destino; aí **Iniciar** baixa lá o que
faltar.

Para levar a biblioteca a **outro computador**, use **Exportar biblioteca…**: um
arquivo `.json` com cada música, a URL e o local. Do outro lado, escolha a pasta
de destino, **Importar biblioteca…** e **Iniciar**. Os caminhos são gravados
com `/`, então o arquivo vale entre Windows e Linux.

### Os outros botões

| Botão | O que faz | Apaga arquivos? |
|---|---|---|
| **Iniciar / Retomar** | Continua a fila. Antes, confere se os arquivos baixados ainda existem | Não |
| **Verificar biblioteca** | A conferência completa descrita acima | Não |
| *Menu → Fila →* **Tentar de novo as que falharam** | Devolve à fila as faixas que esgotaram as tentativas | Não |
| *Menu → Fila →* **Atualizar lista da playlist** | Busca a playlist da URL de novo e acrescenta músicas novas | Não |
| *Menu → Biblioteca →* **Esquecer a biblioteca…** | Esquece playlists, fila e histórico desta pasta, para recomeçar do zero | Não |

Fechar o programa, parar no meio ou ser bloqueado não perde nada: o catálogo é
gravado a cada faixa.

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

O seletor **Estrutura de pastas** controla a profundidade:

| Opção | Resultado |
|---|---|
| Gênero / Artista / Álbum (padrão) | `EDM/Avicii/True/02 - Wake Me Up.mp3` |
| Gênero / Artista | `EDM/Avicii/Wake Me Up.mp3` |
| Somente gênero | `EDM/Avicii - Wake Me Up.mp3` |
| Somente artista | `Avicii/Wake Me Up.mp3` |
| Somente álbum | `True/02 - Avicii - Wake Me Up.mp3` |

O que sai da pasta entra no nome do arquivo, por duas regras:

- **o número da faixa só aparece quando há pasta de álbum** — fora dela ele não
  ordena nada, só intercala faixas de discos diferentes;
- **o artista entra no nome quando não houver pasta dele** — senão a faixa fica
  órfã no meio de centenas de outras.

O gênero nunca entra no nome: ele classifica, não identifica. Gênero, artista e
álbum seguem gravados na tag em qualquer opção, então o rádio continua
mostrando-os.

Com menos pastas cresce a chance de dois títulos iguais do mesmo artista
gerarem o mesmo nome; nesse caso a segunda faixa recebe o id do vídeo no nome,
e o log avisa.

O seletor **Estrutura de pastas** permite tirar o nível de álbum:

| Opção | Resultado |
|---|---|
| Gênero / Artista / Álbum (padrão) | `EDM/Avicii/True/02 - Wake Me Up.mp3` |
| Gênero / Artista | `EDM/Avicii/02 - Wake Me Up.mp3` |

O álbum fica gravado na tag do arquivo nos dois casos, então o rádio continua
mostrando-o — a pasta é só uma forma de separar. Com um nível a menos, duas
faixas do mesmo artista com o mesmo título poderiam gerar o mesmo nome; nesse
caso a segunda recebe o id do vídeo no nome, e o log avisa.

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
| `pen_drive_maker/store.py` | Catálogo da biblioteca em SQLite |
| `pen_drive_maker/organizer.py` | Metadados → pastas, saneamento de nomes |
| `pen_drive_maker/genre.py` | Consulta de gênero e cache |
| `pen_drive_maker/library.py` | Biblioteca: reconhecer, conferir, mover, exportar, copiar |
| `pen_drive_maker/identify.py` | Reconhecer arquivos soltos por nome/tags e vincular a uma URL |
| `pen_drive_maker/biblioteca_ui.py` | Página da biblioteca e tarefas em segundo plano |
| `pen_drive_maker/assinatura/` | Assinatura visual (tema, janela, ações) |
| `pen_drive_maker/tools.py` | Localização do ffmpeg e do runtime JavaScript |
| `pen_drive_maker/downloader.py` | Motor: extração, ritmo, bloqueios, download |
| `pen_drive_maker/gui.py` | Interface PySide6 |
| `tests/test_engine.py` | Testes offline do motor (`yt-dlp` substituído) |
| `tests/test_tools.py` | Testes da detecção de ferramentas externas |
| `tests/test_genre.py` | Testes da consulta de gênero (sem rede) |
| `tests/test_library.py` | Cenários de biblioteca, incluindo tags reais com ffmpeg |
| `tests/test_identify.py` | Reconhecimento de arquivos soltos (busca simulada) |
| `tests/test_sync.py` | Conferência entre o catálogo e o disco |
| `tests/test_gui.py` | Testes da interface (Qt offscreen) |
| `pyproject.toml` | Metadados do pacote e entry point `pen-drive-maker` |
| `packaging.spec` | Receita do PyInstaller para o executável standalone |
| `.github/workflows/release.yml` | Testes, build e publicação da release na tag |

Dependências Python: `PySide6`, `yt-dlp`, `yt-dlp-ejs` (os scripts que
resolvem os desafios JavaScript do YouTube) e `mutagen` (leitura das tags,
que é como a biblioteca reconhece arquivos movidos).

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
