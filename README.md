# PUTs Launcher

Launcher do SMP com visual maracujá — Forge **1.18.2** + mods da pasta `mods`.

## Fluxo

1. **BAIXAR** — aparece a barra de % / status / tempo restante  
   Salva em `MinecraftPUTS/` (Java compartilhado + instância ativa)
2. Quando já está baixado, o botão vira **JOGAR**
3. Conta **Offline** (nick) ou **Microsoft** (login no navegador + skin 3D)
4. **Instâncias** — troque de pack no menu; **+ Modpack** instala catálogo do Cloudflare R2

## Instâncias + R2

Pasta por pack:

```text
~/MinecraftPUTS/
  shared/          # Java 17
  instances/
    puts-smp/minecraft/
    meu-pack/minecraft/
  launcher_config.json
```

Configure a URL do índice em **Opções**. Documentação: [`docs/r2-modpacks.md`](docs/r2-modpacks.md).

## Windows

```bat
build_exe.bat
```

Distribua:

```text
PUTsLauncher.exe
mods\*.jar
```

## Dev

```bash
pip install -r requirements.txt
python main.py
```
