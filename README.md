"""
# PUTs Launcher

Launcher do SMP com visual maracujá — Forge **1.18.2** + mods da pasta `mods`.

## Fluxo

1. **BAIXAR** — aparece a barra de % / status / tempo restante  
   Salva tudo em `MinecraftPUTS/` (Java, Minecraft, Forge, mods)
2. Quando já está baixado, o botão vira **JOGAR**
3. Conta **Offline** (nick) ou **Microsoft** (login no navegador + skin 3D)

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
