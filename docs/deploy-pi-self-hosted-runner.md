# Self-hosted GitHub Actions runner на Raspberry Pi (только деплой)

Workflow **CD Raspberry Pi** (`.github/workflows/cd-pi.yml`) гоняет **pytest на `ubuntu-latest`**, а шаг **docker compose** выполняется на **вашем Pi**, если на нём зарегистрирован runner с меткой **`vkuswill-pi`**.

Доступ из интернета к домашней сети **не нужен**: Pi сам подключается к GitHub.

## Синхронизация файлов workflow с машины разработчика

Если в `~/.ssh/config` задан хост **`vkbot`** (или другой — задайте `PI_SSH_HOST`), из корня репозитория на Mac/Linux:

```bash
make sync-cd-pi
# или
bash scripts/sync_cd_files_to_pi.sh
```

Копируются `cd-pi.yml`, `deploy-pi.sh`, `pi-install-github-runner.sh`, обновлённый `cd.yml`, README и связанные файлы. Каталог на Pi по умолчанию `~/vkuswill_bot` (`PI_REMOTE_DIR` при необходимости).

## Требования

- Raspberry Pi **64-bit** (ARM64), Debian/Ubuntu-подобная ОС  
- Docker и плагин Compose (как в `scripts/pi-bootstrap.sh`)  
- Пользователь, под которым крутится runner, в группе **`docker`**  
- **Постоянный клон** репозитория с рабочим **`.env`**, `ssl/`, при необходимости `deploy/amnezia-wg0.conf` и т.д.  
- Из этого клона должен работать **`git fetch`** (для private repo — SSH key или credential helper у того же пользователя)

## Метка runner

При настройке runner добавьте **собственную** метку (именно она указана в workflow):

```text
vkuswill-pi
```

Стандартные метки `self-hosted`, `Linux`, `ARM64` обычно добавляются автоматически для arm64.

## Установка runner

### Вариант A — скрипт в репозитории (на Pi)

Из **клона** репозитория (или скопируйте `scripts/pi-install-github-runner.sh` на Pi):

```bash
cd /path/to/vkuswill_bot
bash scripts/pi-install-github-runner.sh
```

Скрипт скачает последний **actions-runner-linux-arm64** с GitHub, распакует в `~/actions-runner-vkuswill` (или `ACTIONS_RUNNER_DIR`), запустит `./bin/installdependencies.sh`.

**Авторегистрация** (токен из **Settings → Actions → Runners → New self-hosted runner**, живёт ~1 час):

```bash
export RUNNER_REGISTRATION_TOKEN="xxxxxxxx"
export RUNNER_REPO_URL="https://github.com/OWNER/REPO"
export RUNNER_INSTALL_SERVICE=1   # опционально: сразу systemd
bash scripts/pi-install-github-runner.sh
```

Метка **`vkuswill-pi`** добавляется автоматически. Затем (если не задали `RUNNER_INSTALL_SERVICE=1`):

```bash
cd ~/actions-runner-vkuswill && ./svc.sh install && ./svc.sh start
```

Локально удобно: `make pi-install-github-runner` (то же самое).

### Вариант B — вручную по инструкции GitHub

**Settings → Actions → Runners → New self-hosted runner** → **Linux**, **ARM64** — скопировать команды загрузки и `./config.sh`, в метках указать **`vkuswill-pi`**, затем `./svc.sh install && ./svc.sh start`.

## Путь к постоянному клону

Workflow по умолчанию вызывает `deploy/deploy-pi.sh` с корнем:

- **`$HOME/vkuswill_bot`** у пользователя runner’а, если не задана variable **`PI_DEPLOY_PATH`**

Задайте в репозитории **Settings → Secrets and variables → Actions → Variables**:

| Variable            | Назначение                                      |
|---------------------|-------------------------------------------------|
| `PI_DEPLOY_PATH`    | Абсолютный путь к клону на Pi (где `.env`)      |
| `PI_COMPOSE_PROFILES` | Для деплоя по тегу: например `caddy` или `cf-tunnel` |

Для ручного **workflow_dispatch** профили можно переопределить полем формы **compose_profiles**.

## Проверка

1. **Actions → CD Raspberry Pi → Run workflow** (ветка `main`, при необходимости **skip tests**).  
2. Убедитесь, что job **Deploy (Pi self-hosted)** взял ваш runner и контейнеры перезапустились.

## Замечания

- Временный checkout в `_work/...` используется только для свежего `deploy/deploy-pi.sh`; **данные и секреты** должны жить в **`PI_DEPLOY_PATH`**.  
- Если runner не в сети, job **Deploy** будет ждать очереди — это нормально.  
- 32-bit Pi (armv7) не совпадёт с меткой **ARM64** в workflow — смените `runs-on` под свои метки или используйте 64-bit ОС.
