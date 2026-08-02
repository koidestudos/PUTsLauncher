"""
# PUTs Launcher

Launcher do SMP com o pack Forge **1.18.2** e os mods da pasta `mods` (estilo QSMP).

## O que faz

- Login **Offline** (nickname) ou **Microsoft**
- Instala automaticamente **Forge 1.18.2-40.3.11**
- Sincroniza os jars de `mods/` para a instância do jogo
- RAM configurável + IP do servidor opcional (entra direto)

## Como usar (Windows)

1. Baixe o artefato `PUTsLauncher-Windows` do GitHub Actions **ou** rode `build_exe.bat`
2. Deixe assim:

```text
PUTsLauncher.exe
mods\
  *.jar
```

3. Abra o `.exe`, escolha Offline ou Microsoft, clique **JOGAR**

No primeiro play o launcher baixa Minecraft + Forge (precisa de internet).

## Rodar em desenvolvimento

```bash
pip install -r requirements.txt
python main.py
```

## Conta Microsoft

Três caminhos:

1. **Importar oficial** — se você já está logado no Minecraft Launcher da Mojang/Microsoft, o PUTs tenta importar a sessão
2. **Login Microsoft** — exige um Azure Application (client) ID em `%APPDATA%/PUTsLauncher/launcher_config.json` (`azure_client_id`). Apps novos podem ser bloqueados pela Microsoft (Xbox Live) até aprovação
3. **Offline** — ideal para SMP com `online-mode=false`

## Onde ficam os arquivos do jogo

```text
%APPDATA%\PUTsLauncher\minecraft\   (Windows)
~/.local/share/PUTsLauncher/minecraft/  (Linux)
```

A pasta `mods` **ao lado do exe** (ou na raiz do repo) é a fonte do pack e é espelhada a cada play.
