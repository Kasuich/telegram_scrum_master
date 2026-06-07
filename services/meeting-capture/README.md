# Meeting Capture

Отдельный тяжелый сервис для записи встреч Telemost ботом-участником.

Сервис не использует официальный Telemost bot API: бот заходит по ссылке как обычный гость через Chromium/Playwright, пишет экран и звук через FFmpeg, сохраняет артефакты и при наличии SpeechKit/S3 строит транскрипт со спикерами и таймкодами.

## Что умеет

- Подключаться к Telemost по ссылке вида `https://telemost.yandex.ru/j/...`.
- Заходить как гость с именем `PM Assistant (recording)`.
- Ждать допуска из комнаты ожидания.
- Записывать встречу в `recording.webm` и отдельное аудио `audio.ogg`.
- Сохранять артефакты в локальное object storage или S3-compatible storage.
- Отправлять аудио в SpeechKit и сохранять transcript segments:
  `{start_ms, end_ms, speaker_label, text}`.
- Хранить состояние в Postgres: `meetings`, `meeting_artifacts`, `transcripts`.
- Давать HTTP API для постановки бота, остановки, статуса и транскрипта.

## Быстрый старт

Заполнить корневой `.env`, потому что `docker compose` читает именно его:

```env
DB_USER=pm_agent_test
DB_PASSWORD=changeme
DB_NAME=pm_agent_test
DEFAULT_TEAM_ID=00000000-0000-0000-0000-000000000001

YC_API_KEY=...
YC_FOLDER_ID=...

CAPTURE_BOT_DISPLAY_NAME=PM Assistant (recording)
CAPTURE_JOIN_TIMEOUT_SEC=900
CAPTURE_MAX_DURATION_SEC=14400
CAPTURE_AUDIO_TTL_DAYS=7
```

Запустить:

```bash
docker compose up --build meeting-capture
```

Проверить health:

```bash
curl http://localhost:8003/health
```

## Записать встречу

Создать capture job:

```bash
curl -X POST http://localhost:8003/meetings \
  -H "Content-Type: application/json" \
  -d '{
    "telemost_url": "https://telemost.yandex.ru/j/50061364845323",
    "consent_ack": true,
    "language": "ru-RU"
  }'
```

Ответ:

```json
{
  "meeting_id": "7a6b1b1f-1111-4444-9999-8e3f11111111",
  "status": "joining"
}
```

После этого организатор должен впустить бота из комнаты ожидания Telemost.

## API

### `POST /meetings`

Ставит бота на встречу.

Request:

```json
{
  "telemost_url": "https://telemost.yandex.ru/j/...",
  "starts_at": null,
  "title": null,
  "consent_ack": true,
  "language": "ru-RU"
}
```

Если `starts_at` пустой или в прошлом, бот заходит сразу. Если `starts_at` в будущем, сервис ставит внутреннюю отложенную задачу.

### `GET /meetings/{meeting_id}`

Возвращает статус, ошибки, timestamps, metadata и список артефактов.

```bash
curl http://localhost:8003/meetings/<meeting_id>
```

Статусы:

- `scheduled` — встреча запланирована на будущее.
- `joining` — бот открывает ссылку и пытается войти.
- `waiting_room` — бот дошел до комнаты ожидания.
- `recording` — бот допущен, запись идет.
- `transcribing` — запись завершена, идет STT.
- `ready` — транскрипт сохранен.
- `skipped` — бот не был допущен или встреча не состоялась.
- `failed` — ошибка браузера, recorder, storage или STT.

### `POST /meetings/{meeting_id}/stop`

Останавливает активную запись.

```bash
curl -X POST http://localhost:8003/meetings/<meeting_id>/stop
```

### `GET /meetings/{meeting_id}/transcript`

Возвращает готовый транскрипт.

```bash
curl http://localhost:8003/meetings/<meeting_id>/transcript
```

Response:

```json
{
  "meeting_id": "...",
  "source": "speechkit",
  "segments": [
    {
      "start_ms": 0,
      "end_ms": 2500,
      "speaker_label": "SPEAKER_00",
      "text": "Обсудили план релиза."
    }
  ],
  "participants_observed": []
}
```

## Конфигурация

### Capture

| Env | Default | Описание |
| --- | --- | --- |
| `CAPTURE_BOT_DISPLAY_NAME` | `PM Assistant (recording)` | Имя гостя в Telemost. |
| `CAPTURE_JOIN_TIMEOUT_SEC` | `900` | Сколько ждать допуска в комнату. |
| `CAPTURE_MAX_DURATION_SEC` | `14400` | Максимальная длительность записи. |
| `CAPTURE_AUDIO_TTL_DAYS` | `7` | TTL для артефактов в БД. `0` значит без TTL. |
| `CAPTURE_WORK_DIR` | `/tmp/meeting-capture` | Рабочая папка записи. |
| `CAPTURE_OBJECT_STORAGE_DIR` | `/tmp/meeting-capture-objects` | Локальное хранилище, если S3 не настроен. |

### Recorder

| Env | Default | Описание |
| --- | --- | --- |
| `CAPTURE_FFMPEG_BIN` | `ffmpeg` | Путь к FFmpeg. |
| `CAPTURE_DISPLAY` | `:99.0` | X11 display для `x11grab`. |
| `CAPTURE_VIDEO_SIZE` | `1280x720` | Размер записи. |
| `CAPTURE_FRAMERATE` | `15` | FPS записи. |
| `CAPTURE_PULSE_SOURCE` | `default` | PulseAudio source для захвата звука. |

### S3 / Object Storage

Если `S3_BUCKET`, `S3_ACCESS_KEY` и `S3_SECRET_KEY` не заданы, сервис пишет файлы в локальный Docker volume `meeting_capture_objects`.

```env
S3_ENDPOINT=
S3_BUCKET=
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_REGION=ru-central1
```

Для SpeechKit нужен объект с аудио, доступный SpeechKit. С локальным storage транскрипция не сможет отправить аудио наружу и вернет источник `speechkit_missing_audio_uri`.

### SpeechKit

```env
SPEECHKIT_API_KEY=...
SPEECHKIT_BASE_URL=https://stt.api.cloud.yandex.net
SPEECHKIT_POLL_INTERVAL_SEC=5
SPEECHKIT_TIMEOUT_SEC=3600
```

Если `SPEECHKIT_API_KEY` пустой, запись все равно сохранится, но transcript будет пустым с source `speechkit_unconfigured`.

## Интеграция с orchestrator

В `pm-orchestrator` зарегистрированы tools:

- `schedule_meeting_bot(url, starts_at="", title="", consent_ack=true, language="ru-RU")`
- `get_meeting_transcript(meeting_id)`

Они ходят в сервис по `MEETING_CAPTURE_URL`, default:

```env
MEETING_CAPTURE_URL=http://meeting-capture:8003
```

Для первого ручного smoke-теста лучше дергать `meeting-capture` напрямую через `localhost:8003`.

## Troubleshooting

### `Page.goto: net::ERR_CONNECTION_TIMED_OUT`

Это означает, что Chromium внутри контейнера не смог открыть `https://telemost.yandex.ru/...`.
Это не ошибка selector'ов Telemost.

Проверить сеть из контейнера:

```bash
docker compose exec meeting-capture python - <<'PY'
import urllib.request
print(urllib.request.urlopen("https://telemost.yandex.ru", timeout=20).status)
PY
```

Что проверить:

- У контейнера есть исходящий интернет.
- DNS внутри Docker резолвит `telemost.yandex.ru`.
- Корпоративная сеть/VPN/firewall не блокирует Yandex Telemost.
- На хосте ссылка открывается именно в браузере, без обязательного SSO/капчи.
- При медленной сети можно увеличить `CAPTURE_JOIN_TIMEOUT_SEC`.

### Бот завис в `waiting_room`

Организатор не впустил гостя. В Telemost нужно явно допустить участника `PM Assistant (recording)`.

### Транскрипт пустой

Проверь:

- `SPEECHKIT_API_KEY` задан.
- Используется S3/object storage, доступный SpeechKit.
- `audio.ogg` появился в артефактах встречи.

### Нет звука на записи

Проверь `CAPTURE_PULSE_SOURCE` и PulseAudio внутри контейнера. Для продового smoke лучше сначала записать короткую встречу и скачать `audio.ogg` из object storage.

## Тесты

```bash
uv run --group dev pytest services/meeting-capture/tests -q
```

Покрыто:

- валидация Telemost URL;
- state machine dispatcher;
- mocked bot/recorder/transcriber lifecycle;
- parser SpeechKit segments;
- generation object storage keys.
