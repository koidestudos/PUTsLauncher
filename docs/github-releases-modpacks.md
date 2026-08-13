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

1. **Opções** → `dono/repo`
2. **+ Modpack** → instalar
3. Trocar instância no menu **Instância**

Java fica em `~/MinecraftPUTS/shared/`. Cada pack em `~/MinecraftPUTS/instances/<id>/minecraft/`.
