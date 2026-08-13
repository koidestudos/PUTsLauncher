# PUTs Launcher

Launcher do SMP com visual maracujá. Mods vêm do **GitHub Releases** (`+ Modpack`), não junto do EXE.

## Fluxo

1. Em **Opções**, configure o catálogo: `dono/repo` (ex.: `koidestudos/PUTsModpacks`)
2. **+ Modpack** → instala o pack (ex.: PUTs SMP) do Release
3. **BAIXAR** → Java + Forge daquela instância
4. **JOGAR** — Offline ou Microsoft

## Distribuição

Só o EXE:

```text
PUTsLauncher.exe
```

Os zips de modpack ficam no **GitHub Releases** + `index.json`.  
Docs: [`docs/github-releases-modpacks.md`](docs/github-releases-modpacks.md).

## Windows build

```bat
build_exe.bat
```

## Dev

```bash
pip install -r requirements.txt
python main.py
```
