# Modpacks no Cloudflare R2

O launcher lista modpacks a partir de um **índice JSON público** no R2 (ou qualquer HTTPS).
Cada entrada vira uma **instância** local em `~/MinecraftPUTS/instances/<id>/`, no estilo CurseForge.

## Layout no bucket

```text
modpacks/
  index.json
  puts-smp-1.0.0.zip
  puts-lite-0.2.0.zip
```

Torne o bucket (ou o prefixo) público via R2.dev / custom domain.

## Índice

Veja `r2-modpack-index.example.json`. Campos importantes:

| Campo | Obrigatório | Notas |
|-------|-------------|--------|
| `id` | sim | slug da instância |
| `name` | sim | nome no menu |
| `download_url` | sim | URL HTTPS do zip |
| `version` | recomendado | exibido na UI |
| `mc_version` | recomendado | default `1.18.2` |
| `forge_version` / `loader_version` | recomendado | default do launcher |
| `sha256` | opcional | se preenchido, valida o zip |
| `server_ip` / `server_port` | opcional | auto-join ao jogar |

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

1. **Opções** → cole a URL do `index.json` (ex.: `https://pub-XXXX.r2.dev/modpacks/index.json`)
2. **+ Modpack** → escolha o pack → **Instalar instância**
3. Troque de instância no menu **Instância**
4. **✕** remove packs instalados (a instância padrão `puts-smp` fica)

Java fica compartilhado em `~/MinecraftPUTS/shared/`. Cada instância tem seu próprio `minecraft/` (Forge + mods).
