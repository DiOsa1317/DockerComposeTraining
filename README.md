# Отчёт: Flask + PostgreSQL + Redis за Nginx (hw2)

Стенд из четырёх сервисов, поднимается одной командой `docker compose up -d --build`. Снаружи опубликован только nginx, приложение ходит в Postgres и Redis по внутренней сети. Постоянное состояние — заметки в Postgres, счётчик `hits` в Redis временный.

## Стек и архитектура

| Сервис  | Образ                 | Роль                                       | Сети            |
|---------|-----------------------|--------------------------------------------|-----------------|
| `nginx` | `nginx:1.27-alpine`   | входная точка, публикует `${NGINX_PORT}`   | `front`         |
| `app`   | сборка `./app`        | Flask + gunicorn, эндпоинты `/` и `/health`| `front`, `back` |
| `db`    | `postgres:16-alpine`  | заметки                                    | `back`          |
| `redis` | `redis:7-alpine`      | счётчик `hits`                             | `back`          |

Приложение читает `DATABASE_URL` и `REDIS_URL` из окружения. Образ `app` собирается многостадийно: в отдельной стадии ставятся зависимости, и только потом копируются исходники.

## Запуск и готовность

`docker compose ps` — все сервисы healthy, внешний порт опубликован только у nginx:

```text
NAME          IMAGE                COMMAND                  SERVICE   CREATED              STATUS                        PORTS
hw2-app-1     hw2-app              "gunicorn -b 0.0.0.0…"   app       About a minute ago   Up About a minute (healthy)   
hw2-db-1      postgres:16-alpine   "docker-entrypoint.s…"   db        About a minute ago   Up About a minute (healthy)   5432/tcp
hw2-nginx-1   nginx:1.27-alpine    "/docker-entrypoint.…"   nginx     About a minute ago   Up About a minute             0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
hw2-redis-1   redis:7-alpine       "docker-entrypoint.s…"   redis     About a minute ago   Up About a minute (healthy)   6379/tcp
```

Запрос через входной сервис:

```text
$ curl http://localhost:8080/
{"last_note":"control-note-2026-10-09","message":"App is working!","redis_hits":"1.0000000000"}
```

## Healthcheck и порядок запуска

У `db` проверка `pg_isready`, у `redis` — `redis-cli ping`, у `app` — запрос к `/health` через python (в `python:3.12-slim` нет curl). В `depends_on` у `app` стоит `condition: service_healthy`, поэтому он поднимается только после того, как готовы `db` и `redis`: в выводе `up` видно `Waiting → Healthy → Starting`.

## Сети и публикация портов (п.4)

`front` объединяет nginx и app, `back` — app, db и redis. Наружу публикуется только nginx, и подключён он исключительно к `front`:

```text
$ docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' hw2-nginx-1
hw2_front
```

Проверяю, что из nginx до БД не дотянуться:

```text
$ docker compose exec nginx getent hosts db; echo "getent exit=$?"
getent exit=2
$ docker compose exec nginx nc -z -w 2 172.21.0.2 5432; echo "nc exit=$?"
nc exit=1
```

`getent exit=2` — DNS-имя `db` не резолвится; `nc exit=1` — прямое TCP-подключение к внутреннему IP БД отвергнуто. Из app доступ, наоборот, работает:

```text
$ docker compose exec app getent hosts db
172.21.0.2      db
$ docker compose exec app python -c "import socket; socket.create_connection(('db',5432),3); socket.create_connection(('redis',6379),3); print('ok')"
ok
```

app состоит в двух сетях, но маршрутизатором между ними не становится: у контейнера выключен `net.ipv4.ip_forward`, и Docker не добавляет правила forward/NAT между пользовательскими bridge-сетями. Обращения к БД — это исходящие соединения самого процесса Flask со своего интерфейса в `back`, чужие пакеты он не пересылает. А nginx даже не знает DNS-имени `db`, поэтому до БД не добирается.

## Что сохраняется

Постоянно — заметки в PostgreSQL; именованный том `pgdata` смонтирован в `/var/lib/postgresql/data` (путь для 16-й версии образа). Временно — счётчик `hits` в Redis: тома у него нет, потеря допускается.

## Переменные окружения (п.5)

Изменяемые значения вынесены в `.env`: `COMPOSE_PROJECT_NAME`, `NGINX_PORT`, `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, `REDIS_URL`. Рабочий `.env` в git не попадает (исключён через `.gitignore`), а в репозитории лежит `.env.example` с безопасными значениями.

## Заметки и холодный бэкап (п.3)

### Сохранение заметок

Сначала таблица не сохранялась: `psycopg2` работает не в auto-commit, а `CREATE TABLE` закрывался `conn.close()` без `commit()` — транзакция откатывалась. Добавила в `app/main.py` `conn.autocommit = True`, и после пересборки `app` таблица создаётся и остаётся в БД (пользователь `dilushanotes`, база `notesdb`):

```text
$ docker compose exec db psql -U dilushanotes -d notesdb -c "\dt"
           List of relations
 Schema | Name  | Type  |    Owner     
--------+-------+-------+--------------
 public | notes | table | dilushanotes
(1 row)
```

### Контрольная заметка

Кладу в базу запись `control-note-2026-10-09` и читаю её через приложение:

```text
$ docker compose exec db psql -U dilushanotes -d notesdb -c "INSERT INTO notes (content) VALUES ('control-note-2026-10-09');"
INSERT 0 1
$ curl -s http://localhost:8080/
{"last_note":"control-note-2026-10-09","message":"App is working!","redis_hits":"1.0000000000"}
$ docker compose exec db psql -U dilushanotes -d notesdb -c "SELECT id, content FROM notes;"
 id |         content
----+-------------------------
  1 | control-note-2026-10-09
(1 row)
```

### Холодный бэкап тома

Останавливаю стек **без** `-v`, архивирую том через временный контейнер в каталог `backups/` (он лежит вне тома), удаляю и заново создаю только свой том, распаковываю архив и поднимаю стенд заново:

```text
$ docker compose down
 ✔ Container hw2-nginx-1 Removed
 ✔ Container hw2-app-1   Removed
 ✔ Container hw2-redis-1 Removed
 ✔ Container hw2-db-1    Removed
 ✔ Network hw2_front     Removed
 ✔ Network hw2_back      Removed

$ docker volume ls | grep hw2_pgdata
local     hw2_pgdata

$ mkdir -p backups
$ docker run --rm -v hw2_pgdata:/volume:ro -v "$PWD/backups":/backup alpine tar -czf /backup/pg_data.tar.gz -C /volume .
$ ls -lh backups/
total 6,6M
-rw-r--r-- 1 root root 6,6M окт  9 15:14 pg_data.tar.gz

$ docker volume rm hw2_pgdata
hw2_pgdata
$ docker volume create hw2_pgdata
hw2_pgdata
$ docker run --rm -v hw2_pgdata:/volume -v "$PWD/backups":/backup alpine tar -xzf /backup/pg_data.tar.gz -C /volume

$ docker compose up -d
 ✔ Container hw2-redis-1 Healthy
 ✔ Container hw2-db-1    Healthy
 ✔ Container hw2-app-1   Healthy
 ✔ Container hw2-nginx-1 Started

$ curl -s http://localhost:8080/
{"last_note":"control-note-2026-10-09","message":"App is working!","redis_hits":"1.0000000000"}
$ docker compose exec db psql -U dilushanotes -d notesdb -c "SELECT id, content FROM notes;"
 id |         content
----+-------------------------
  1 | control-note-2026-10-09
(1 row)
```

Контрольная заметка осталась на месте после удаления и пересоздания тома, значит постоянное состояние действительно живёт в `hw2_pgdata`. Счётчик Redis сбросился в `1` — у `redis` нет тома, это временное состояние, потеря которого допустима. Каталог `backups/` добавила в `.gitignore`, чтобы дамп не попал в репозиторий.

## Оптимизация образа (dive до/после, п.6)

Метрики `dive` до и после оптимизации `app/Dockerfile`:

| Метрика | До | После |
|---|---|---|
| Размер (`docker images`) | 146 MB | 131 MB |
| Efficiency | 87.17 % | 97.01 % |
| Wasted (`userWastedPercent`) | 39.26 % | 10.83 % |

Что изменилось в `Dockerfile`:

- зависимости ставятся в отдельный venv в builder-стадии, а в финальный образ копируется только он (`COPY --from=builder /venv /venv`), а не весь системный Python;
- `pip install --no-cache-dir --no-compile` плюс `PYTHONDONTWRITEBYTECODE=1` — в образ не попадают `.pyc` и кеш pip;
- из venv удалены `pip`/`setuptools`, в рантайме они не нужны;
- убран неэффективный `RUN find … -name '*.pyc' -delete` — удаление в отдельном слое размер образа не уменьшает, слои аддитивны;
- добавлен `app/.dockerignore` (`__pycache__/`, `*.pyc`, `.env`, `*.md`).

```text
$ docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -e CI=true wagoodman/dive:latest hw2-app
  efficiency: 97.0073 %
  wastedBytes: 5650309 bytes (5.7 MB)
  userWastedPercent: 10.8293 %
Results:
  FAIL: highestUserWastedPercent: too many bytes wasted, relative to the user bytes added (%-user-wasted-bytes=0.10829320762348622 > threshold=0.1)
  SKIP: highestWastedBytes: rule disabled
  PASS: lowestEfficiency
```

После оптимизации правило `lowestEfficiency` проходит. Оставшийся FAIL по `userWastedPercent` (5.7 MB, 10.83 %) приходится на служебные файлы **базового** образа (`/var/cache/debconf/*`, `/var/lib/dpkg/*`, `/var/log/apt/*`), которые дублируются между слоями `python:3.12-slim`; собственные слои приложения чистые. Убрать их можно было бы сменой базового образа (distroless/alpine), но из-за `psycopg2-binary` это неудобно и не стоит того.

## Имя проекта и имена ресурсов

Имя берётся из `COMPOSE_PROJECT_NAME=hw2`. Приоритет: флаг `-p` → `COMPOSE_PROJECT_NAME` из `.env` → имя каталога. Отсюда:

- контейнеры — `hw2-app-1`, `hw2-db-1`, `hw2-nginx-1`, `hw2-redis-1`;
- сети — `hw2_front`, `hw2_back`;
- том — `hw2_pgdata`.

## Итоговый `docker compose config` (пароль замаскирован)

```yaml
name: hw2
services:
  app:
    build:
      context: /home/diliya/Documents/3course/devops/hw2/app
      dockerfile: Dockerfile
    depends_on:
      db:
        condition: service_healthy
        required: true
      redis:
        condition: service_healthy
        required: true
    environment:
      COMPOSE_PROJECT_NAME: hw2
      DATABASE_URL: postgresql://dilushanotes:***@db:5432/notesdb
      NGINX_PORT: "8080"
      POSTGRES_DB: notesdb
      POSTGRES_PASSWORD: ***
      POSTGRES_USER: dilushanotes
      REDIS_URL: redis://redis:6379/0
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/health').status==200 else 1)
      timeout: 3s
      interval: 5s
      retries: 5
      start_period: 5s
    networks:
      back: null
      front: null
  db:
    environment:
      POSTGRES_DB: notesdb
      POSTGRES_PASSWORD: ***
      POSTGRES_USER: dilushanotes
    healthcheck:
      test:
        - CMD-SHELL
        - pg_isready -U dilushanotes -d notesdb
      timeout: 3s
      interval: 5s
      retries: 5
    image: postgres:16-alpine
    networks:
      back: null
    volumes:
      - type: volume
        source: pgdata
        target: /var/lib/postgresql/data
        volume: {}
  nginx:
    depends_on:
      app:
        condition: service_healthy
        required: true
    image: nginx:1.27-alpine
    networks:
      front: null
    ports:
      - mode: ingress
        target: 80
        published: "8080"
        protocol: tcp
    volumes:
      - type: bind
        source: /home/diliya/Documents/3course/devops/hw2/nginx/default.conf
        target: /etc/nginx/conf.d/default.conf
        read_only: true
        bind: {}
  redis:
    healthcheck:
      test:
        - CMD
        - redis-cli
        - ping
      timeout: 3s
      interval: 5s
      retries: 5
    image: redis:7-alpine
    networks:
      back: null
networks:
  back:
    name: hw2_back
  front:
    name: hw2_front
volumes:
  pgdata:
    name: hw2_pgdata
```

## `docker compose down` и `down -v`

- `docker compose down` — останавливает и удаляет контейнеры и сети, но **сохраняет** том `hw2_pgdata` с заметками.
- `docker compose down -v` — дополнительно удаляет том и все данные.

Для холодного бэкапа использую `down` без `-v`.

## Разработка через `docker compose watch` (★)

Чтобы не пересобирать образ на каждое изменение, добавила `compose.override.yaml` с секцией `develop.watch`:

```yaml
services:
  app:
    develop:
      watch:
        - action: rebuild
          path: ./app/requirements.txt
        - action: sync+restart
          path: ./app
          target: /app
          ignore:
            - requirements.txt
            - __pycache__/
```

Правило `rebuild` стоит первым и срабатывает на `requirements.txt`, остальные файлы `./app` синхронизируются в контейнер с перезапуском процесса (`sync+restart` — gunicorn сам код не перечитывает). Запускаю `docker compose watch`: при правке `app/main.py` контейнер обновился и перезапустился без полной пересборки. Merge override проверяется в `docker compose config`.

## CI: `dive` в GitHub Actions (★)

Пороги лежат в `.dive-ci`:

```yaml
rules:
  lowestEfficiency: 0.95
  highestWastedBytes: 50MB
  highestUserWastedPercent: 0.10
```

Workflow `.github/workflows/dive.yaml` собирает образ приложения и запускает `dive` в CI-режиме; при выходе за пороги `dive` возвращает ненулевой код — job падает:

```yaml
name: dive-ci
on:
  push:
  pull_request:
jobs:
  dive:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build app image
        run: docker build -t hw2-app ./app
      - name: Run dive (CI, thresholds)
        run: |
          docker run --rm \
            -v /var/run/docker.sock:/var/run/docker.sock \
            -v "$PWD/.dive-ci:/.dive-ci:ro" \
            -e CI=true \
            wagoodman/dive:latest --ci-config /.dive-ci hw2-app
```

Тот же запуск локально даёт `exit=1`: образ не проходит порог `highestUserWastedPercent` (10.83 % > 10 %), то есть job действительно валится. При пороге `0.15` тот же образ проходит, и job зелёный.
