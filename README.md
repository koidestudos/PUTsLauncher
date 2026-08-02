"""
# PUTs Launcher

Launcher simples do SMP (Forge **1.18.2** + mods da pasta `mods`).

## O que faz

Ao clicar em **JOGAR**, o launcher:

1. Cria a pasta **`MinecraftPUTS/`** no seu usuário  
   - Windows: `C:\\Users\\SEU_NOME\\MinecraftPUTS`  
   - Linux: `~/MinecraftPUTS`
2. Baixa **Java 17** (Mojang) se precisar
3. Baixa **Minecraft + Forge 40.3.11**
4. Copia os **mods** do pack
5. Abre o jogo

Enquanto baixa, mostra **%**, **o que está carregando** e **tempo restante**.

## Windows (.exe)

```bat
build_exe.bat
```

Ou baixe o artefato do GitHub Actions (**Build Windows EXE**).

Distribua assim:

```text
PUTsLauncher.exe
mods\*.jar
```

## Dev

```bash
pip install -r requirements.txt
python main.py
```

## Conta

- **Offline** — digite o nickname (ideal para SMP com `online-mode=false`)
- **Microsoft** — login no navegador (precisa Azure Client ID no `launcher_config.json`) ou importar do Minecraft Launcher oficial
