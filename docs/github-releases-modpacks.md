# Modpacks via GitHub Releases

O launcher lista e baixa modpacks a partir de **GitHub Releases** (sem Cloudflare R2).

## Configuração no launcher

Em **Opções → Catálogo de modpacks**, use um destes formatos:

| Valor | Comportamento |
|-------|----------------|
| `dono/repo` | Consulta a API de Releases do repositório |
| `https://github.com/dono/repo` | Idem |
| URL HTTPS de um `index.json` | Baixa só esse índice (ex.: asset de um Release) |

Exemplo: `koidestudos/PUTsModpacks`

## Como publicar packs

### Opção A — só zips nos Releases (mais simples)

Crie um Release e anexe um ou mais `.zip`. Cada zip vira um modpack no catálogo.

```text
Release v1.0.0 — "PUTs SMP"
  └─ puts-smp.zip
```

### Opção B — `index.json` no Release (recomendado se tiver vários packs)

No Release, anexe:

```text
index.json
puts-smp-1.0.0.zip
puts-lite-0.2.0.zip
```

Exemplo de `index.json`:

```json
{
  "modpacks": [
    {
      "id": "puts-smp",
      "name": "PUTs SMP",
      "version": "1.0.0",
      "mc_version": "1.18.2",
      "loader": "forge",
      "forge_version": "1.18.2-40.3.11",
      "description": "Pack oficial do servidor PUTs.",
      "download_url": "puts-smp-1.0.0.zip",
      "server_ip": "play.exemplo.com",
      "server_port": 25565
    }
  ]
}
```

`download_url` pode ser:

- o **nome do asset** no mesmo Release (`puts-smp-1.0.0.zip`), ou
- uma URL HTTPS completa (`https://github.com/.../releases/download/.../file.zip`)

### Forge / Minecraft no `index.json`

O launcher instala o Forge **da instância**, não só 1.18.2.

No pack use:

```json
"mc_version": "1.20.1",
"forge_version": "47.4.10"
```

ou

```json
"mc_version": "1.20.1",
"forge_version": "1.20.1-47.4.10"
```

Nomes aceitos para o índice: `index.json`, `modpacks.json`, `catalog.json`.

## Conteúdo do zip

Na raiz (ou numa pasta única `NomeDoPack/`):

```text
mods/*.jar
config/          (opcional)
defaultconfigs/  (opcional)
resourcepacks/   (opcional)
shaderpacks/     (opcional)
options.txt      (opcional)
```

## No launcher

1. **+ Modpack** → cole um link CurseForge/Modrinth **ou** escolha um pack do catálogo GitHub
2. (Opcional) **Opções** → `dono/repo` para listar Releases
3. Trocar instância no menu **Instância**
4. **BAIXAR** / **JOGAR**

Java fica em `~/MinecraftPUTS/shared/`. Cada pack em `~/MinecraftPUTS/instances/<id>/minecraft/`.

O download do launcher **não** inclui pasta `mods`. Packs sobem pelo Release ou pelo link importado.

## Importar CurseForge / Modrinth

No diálogo **+ Modpack**, cole a URL da página do modpack:

- `https://modrinth.com/modpack/<slug>`
- `https://modrinth.com/modpack/<slug>/version/<versão>`
- `https://www.curseforge.com/minecraft/modpacks/<slug>`
- `https://www.curseforge.com/minecraft/modpacks/<slug>/files/<fileId>`

Loaders: **Forge**, **Fabric**, **NeoForge**, **Quilt**. O launcher baixa o `.mrpack` / zip, cria a instância e instala Java + o loader certo.

## Criar seu modpack

Em **+ Modpack → Criar meu modpack**:

1. Nome, versão do Minecraft e loader
2. Sem busca: lista os **mais baixados**; use os botões **Modrinth / CurseForge** pra filtrar
3. **+ Meu .jar** pra incluir um jar local
4. (Opcional) **Conectar GitHub** com um Personal Access Token (`repo`), escolha o repositório e marque publicar Release
5. Cria a instância — se publicou, o catálogo vira `dono/repo` e outros podem usar o mesmo link em Opções

## Cache

`~/MinecraftPUTS/cache` é limitado (~350 MiB). Packs importados são apagados depois da instalação; o launcher limpa arquivos velhos na abertura e após installs.

## Skins no modo offline (Ely.by)

Em **todo** modpack o launcher instala automaticamente o **CustomSkinLoader** com **Ely.by** na frente da lista.

1. Crie conta grátis em https://ely.by e envie a skin
2. No launcher (Offline), use o **mesmo nick** da conta Ely.by
3. Abra o jogo — a skin carrega sem Microsoft

