# PUTs Launcher

Launcher do SMP com visual maracujá. Mods vêm do **+ Modpack** (link CurseForge/Modrinth, criador próprio, ou catálogo GitHub), não junto do EXE.

## Fluxo

1. **+ Modpack** → cole um link CurseForge/Modrinth (Forge/Fabric/NeoForge), **crie seu pack** buscando mods, ou use o catálogo GitHub
2. **BAIXAR** → Java + loader daquela instância
3. **JOGAR** — Offline ou Microsoft

## Loaders

Suportados no index e no launcher: **Forge**, **Fabric**, **NeoForge**, **Quilt**.

No `index.json` use `"loader"` + `"loader_version"` (ou o alias `"forge_version"`).

## Distribuição

Só o EXE:

```text
PUTsLauncher.exe
```

Cache em `~/MinecraftPUTS/cache` é limpo automaticamente (packs importados e teto de espaço).
Docs: [`docs/github-releases-modpacks.md`](docs/github-releases-modpacks.md).

## Windows build

```bat
build_exe.bat
```

## Dev

O ambiente de desenvolvimento fica isolado em Docker (inclusive Python, Tkinter e dependências). Na VM `dreamer`:

```bash
cd /putslauncher
docker compose -f compose.dev.yml build
docker compose -f compose.dev.yml run --rm gui pytest
docker compose -f compose.dev.yml up gui
```

A GUI fica disponível por noVNC em `http://192.168.1.21:3002/vnc.html?autoconnect=1&resize=scale`. Para usar outra porta, defina `PUTS_PORT`; para encerrar, use `Ctrl+C`. A porta só fica aberta enquanto o contêiner estiver rodando.
