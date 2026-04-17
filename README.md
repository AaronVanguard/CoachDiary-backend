# 📘 Дневник тренера — серверная часть

Привет! 👋
Меня зовут Матвей, и это мой учебный проект - серверная часть веб-сервиса **«Дневник тренера»** для учёта спортивной подготовки. Написан на Python с использованием **Django REST Framework**.

![433079695-dbcb3301-9881-4ced-b3f3-3ef03c6fbb42-round-corners](https://github.com/user-attachments/assets/bf4a6265-dc1b-43cd-b7c5-2a95cc311e03)

Legacy-версия проекта доступна [здесь](https://github.com/screenviolence/coachdiary).

## 🛠️ Стек технологий

- Python 3.12, Poetry
- Django 5 / Django REST Framework
- PostgreSQL 15
- Redis 7 + Celery
- Gunicorn (prod) / Django dev-server (dev)
- Caddy (reverse-proxy и автоматический HTTPS в prod)
- Docker / Docker Compose

## 📦 Структура сервисов

| Сервис          | Назначение                                                    |
|-----------------|---------------------------------------------------------------|
| `web`           | Django-приложение (API, админка)                              |
| `db`            | PostgreSQL                                                    |
| `redis`         | Брокер сообщений и результатов Celery                         |
| `celery_worker` | Фоновая обработка задач (рассылки, экспорт и пр.)             |
| `caddy`         | Reverse-proxy + статика + SPA (только профиль `prod`)         |

## 🚀 Запуск локально (dev)

### 1. Клонируйте репозиторий

```bash
git clone https://github.com/screenviolence/CoachDiary-backend.git
cd CoachDiary-backend
```

### 2. Создайте `.env` в корне проекта

```dotenv
# Django
DJANGO_SECRET_KEY=ваш_секретный_ключ
DJANGO_DEBUG=True

# База данных
DB_NAME=coachdiary
DB_USER=coach
DB_PASSWORD=ваш_пароль
DB_HOST=db
DB_PORT=5432

# Электронная почта
# При DJANGO_DEBUG=True письма выводятся в консоль — секцию ниже можно не заполнять.
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=465
EMAIL_USE_SSL=True
EMAIL_HOST_USER=do-not-reply@example.com
EMAIL_HOST_PASSWORD=пароль
DEFAULT_FROM_EMAIL=do-not-reply@example.com

# Redis / Celery
REDIS_HOST=redis
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# Домен (используется Caddy в prod; для dev можно оставить как есть)
DOMAIN=:80
SITE_URL=http://localhost:8000
```

### 3. Поднимите контейнеры

```bash
docker compose up -d --build
```

Применяется `docker-compose.override.yml`: Django запускается через `runserver` с hot-reload и пробросом портов PostgreSQL (`5432`) и Redis (`6379`).

Приложение будет доступно на http://127.0.0.1:8000.

### 4. (Опционально) Наполните базу тестовыми данными

```bash
docker compose exec web python manage.py create_test_data
```

Команда может занять некоторое время - она создаёт учителей, учеников, классы и результаты нормативов.

По умолчанию, доступны станут следующие пользователи:
email: user0@example.com и user1@example.com
password: password

### 5. Полезные команды

```bash
# Войти в контейнер приложения
docker compose exec web /bin/bash

# Логи в реальном времени
docker compose logs -f web

# Создать суперпользователя
docker compose exec web python manage.py createsuperuser

# Сгенерировать новую миграцию
docker compose exec web python manage.py makemigrations
```

## 🌐 Запуск в продакшене

В прод-профиле подключается `caddy`, который терминирует TLS, раздаёт собранную статику Django и фронтенд-SPA из `./frontend-dist`.

```bash
# DOMAIN должен указывать на реальный домен, направленный на сервер
docker compose --profile prod up -d --build
```

Caddy автоматически получит HTTPS-сертификат через Let's Encrypt для домена из переменной `DOMAIN`.

## 📖 Документация API

Схема генерируется `drf-spectacular`. В debug-режиме интерактивная документация доступна по адресу:

http://127.0.0.1:8000/api/docs/
