# Установка и сопровождение

Если не хотите выполнять команды самостоятельно, передайте агенту [SKILL.md](../SKILL.md) и этот репозиторий. Он должен проверить вашу машину, сделать доступную настройку и вести вас по шагам, требующим личного подтверждения.

## 1. Где выполнять команды

Ниже — **Linux-терминал**, открытый по SSH на VPS либо внутри WSL2. Это не команды PowerShell. Требуется systemd. На WSL сначала проверьте его поддержку; после изменения настроек перезапускайте среду только когда в ней нет важных задач.

Подготовьте Git, `uv`, Node.js/npm, `ffmpeg`, LibreOffice и Poppler. Установщик должен использовать инструкции этих проектов под вашу ОС. Для Ubuntu/Debian системные пакеты: `git ffmpeg libreoffice poppler-utils`. `uv` установит Python 3.14, нужный этому снимку Takopi. Для Codex установите официальный `@openai/codex`; исходный запуск проверялся с версией `0.157.1`. При выборе другой версии проверьте совместимость app-server и permissions.

Проверки перед продолжением:

```bash
git --version
uv --version
node --version
codex --version
ffmpeg -version
systemctl --version
```

Codex должен быть доступен сервису в `/usr/local/bin` или `/usr/bin`, а не только через nvm текущего пользователя. Не копируйте чужой файл авторизации.

## 2. Отдельная установка

Для новой установки используйте отдельного пользователя `jarvis` и путь `/opt/jarvis`. Не выполняйте команды поверх существующей установки без проверки и резервной копии. При изменении префикса нужно согласованно изменить пути в коде, инструкциях и сервисах — произвольный путь пока не поддерживается одной переменной.

```bash
sudo useradd --create-home --shell /bin/bash jarvis
sudo install -d -o jarvis -g jarvis /opt/jarvis
sudo -u jarvis git clone https://github.com/timoncool/jarvis-codex-bot.git /opt/jarvis
```

Создайте окружение от имени `jarvis`. Если `uv` установлен только у администратора, установщик сначала обеспечивает доступ к нему этому пользователю. Сам бинарник uv можно вызывать по его известному абсолютному пути.

```bash
sudo -u jarvis -H uv venv --python 3.14 /opt/jarvis/.venv
sudo -u jarvis -H uv pip install --python /opt/jarvis/.venv/bin/python -e /opt/jarvis -r /opt/jarvis/deploy/requirements-jarvis.txt
sudo -u jarvis mkdir -p /opt/jarvis/data/topics /home/jarvis/.codex /home/jarvis/.takopi
sudo -u jarvis cp /opt/jarvis/deploy/takopi.toml /home/jarvis/.takopi/takopi.toml
sudo -u jarvis cp /opt/jarvis/deploy/codex.config.example.toml /home/jarvis/.codex/config.toml
sudo -u jarvis cp /opt/jarvis/.env.example /opt/jarvis/.env
sudo chmod 600 /opt/jarvis/.env
```

Не повторяйте `cp` поверх заполненной конфигурации при обновлении.

## 3. Подключение аккаунтов

**Telegram:** откройте [BotFather](https://t.me/BotFather), отправьте `/newbot`, выберите имя и свободный username. На сервере откройте `sudo -u jarvis nano /opt/jarvis/.env` и заполните `TELEGRAM_BOT_TOKEN`. В nano: Ctrl+O → Enter → Ctrl+X. Не вставляйте токен в issue, README или скриншот.

**OpenRouter:** создайте ключ на [странице ключей](https://openrouter.ai/keys), проверьте баланс и заполните `OPENROUTER_API_KEY` в том же файле. Формат — `KEY=value`, без кавычек и пробелов вокруг `=`. Это отдельные расходы, подписка ChatGPT их не покрывает. `OPENROUTER_CALL_LIMIT` — общий потолок платных попыток, по умолчанию 20. Согласуйте рабочий предел после тестирования.

**Codex:** выполните вход на сервере от имени сервисного пользователя:

```bash
sudo -u jarvis -H codex login --device-auth
sudo -u jarvis -H codex login status
```

Откройте фактически выданную ссылку в браузере и подтвердите вход своим аккаунтом. Если у установленной версии иной процесс, агент должен проверить её `codex login --help`. Не передавайте агенту одноразовые коды, пароли и содержимое `auth.json`. Проверьте доступ к модели из `config.toml`; замену согласуйте, если модель недоступна.

## 4. Распознавание речи

По умолчанию используется CPU, `small`, `int8` — подходит и для VPS без NVIDIA, но скорость и потребление RAM нужно измерить. Первая загрузка скачает модель и может занять время.

```bash
sudo -u jarvis cp /opt/jarvis/deploy/asr.env.example /opt/jarvis/asr.env
```

Для совместимой NVIDIA GPU установите CUDA-зависимости:

```bash
sudo -u jarvis -H uv pip install --python /opt/jarvis/.venv/bin/python -r /opt/jarvis/deploy/requirements-cuda.txt
```

В `/opt/jarvis/asr.env` задайте `ASR_DEVICE=cuda`, `ASR_MODEL=large-v3`, `ASR_COMPUTE_TYPE=float16`. Требуются подходящий драйвер и достаточная видеопамять. Не выбирайте GPU-профиль на обычном VPS без NVIDIA.

Конфигурация распознавания моста находится в `/home/jarvis/.takopi/takopi.toml`. Для первого текстового теста без ASR временно выставьте `voice_transcription=false`; затем верните после проверки сервиса. Облачный ASR через OpenRouter доступен как инструмент, но автоматическое переключение входящих голосовых на него этим пакетом не настроено: это отдельная адаптация агента с согласованием расходов.

## 5. Запуск

```bash
sudo install -m 644 /opt/jarvis/deploy/jarvis-bot.service /etc/systemd/system/
sudo install -m 644 /opt/jarvis/deploy/jarvis-openrouter.service /etc/systemd/system/
sudo install -m 644 /opt/jarvis/deploy/jarvis-asr.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jarvis-openrouter jarvis-asr jarvis-bot
systemctl status jarvis-bot jarvis-openrouter jarvis-asr --no-pager
```

Откройте своего бота → `/start` → «Привет» → «Нарисуй котика» → «Расскажи анекдот голосом». Проверяйте фактическую доставку. Если что-то не сработало, агент изучает соответствующий сервис и исправляет причину, не создавая второй экземпляр бота.

На WSL дополнительно настройте запуск выбранного дистрибутива при входе в Windows и удержание его в работе. Имя дистрибутива и способ запуска зависят от вашей установки: агент должен определить их, создать задачу планировщика и проверить закрытие терминала. При сне/выключении Windows бот недоступен. На VPS достаточно проверить systemd-автозапуск. Не перезагружайте машину с другими задачами без согласования.

## 6. Проверки и обслуживание

```bash
sudo journalctl -u jarvis-bot -n 80 --no-pager
sudo journalctl -u jarvis-openrouter -n 80 --no-pager
sudo journalctl -u jarvis-asr -n 80 --no-pager
```

Не публикуйте журналы целиком: сообщения и URL могут содержать личные данные. Удалите секреты до передачи кому-либо.

Тесты без отправки реальных сообщений и платных запросов:

```bash
cd /opt/jarvis
sudo -u jarvis -H uv pip install --python .venv/bin/python pytest pytest-anyio pytest-cov
sudo -u jarvis .venv/bin/python -m pytest tests/test_jarvis_reliability.py tests/test_telegram_bridge.py -q -o addopts=
```

Обновляйте после резервной копии и проверки изменений. Сначала `systemctl stop jarvis-bot`: сервис ждёт активные задачи до 9 минут в пределах 10-минутного stop timeout. Не применяйте `kill -9`. Перед обновлением OpenRouter-воркера отдельно дождитесь завершения его генераций: принудительное прерывание неизвестного платного вызова не приведёт к автоматическому повтору.

Сохраните `/opt/jarvis/data`, `.env`, `asr.env`, `/home/jarvis/.codex` и `/home/jarvis/.takopi` в защищённую резервную копию. Копируйте SQLite через backup API либо после остановки всех писателей. Секреты не должны попадать в Git. При восстановлении сохраните владельца файлов и права; не затирайте журнал задач новой пустой базой.

Полный список приёмки и помощь при типичных ошибках находятся в [SKILL.md](../SKILL.md). Скилл описывает целевое поведение; перечень проверок именно этого снимка — в [STATUS.md](STATUS.md).
