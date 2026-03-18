# PowerWizard Web Dashboard

Локальный веб-мониторинг ДГУ через контроллеры PowerWizard 2.0 (Modbus RTU over raw TCP / MOXA).

## Требования

- Python 3.7+
- Только стандартная библиотека (без внешних зависимостей)

## Запуск

### Demo-режим (без оборудования)

```bash
bash install_and_run_demo.sh
```

или напрямую:

```bash
python3 run_demo.py
```

### Real-режим (с реальным оборудованием)

Перед запуском укажите адрес MOXA в `pw_backend.py`:

```python
HOST = "10.238.0.35"
PORT = 4001
```

Затем:

```bash
bash install_and_run_real.sh
```

или:

```bash
python3 run_real.py
```

### После запуска

Открыть браузер: [http://localhost:8000](http://localhost:8000)

## Порт

По умолчанию — 8000. Можно переопределить через переменную окружения:

```bash
PW_PORT=8080 python3 run_demo.py
```

## Структура

```
pw_backend.py          — опрос Modbus RTU (один сокет на регистр)
main.py                — HTTP-сервер на стандартной библиотеке
run_demo.py            — запуск в demo-режиме
run_real.py            — запуск в real-режиме
install_and_run_demo.sh
install_and_run_real.sh
static/
  index.html
  styles.css
  app.js
```

## Особенности backend

- Каждый регистр читается через отдельное TCP-соединение — исключает рассинхронизацию Modbus-потока
- Глобальный lock предотвращает пересекающиеся опросы
- Timeout: 3.0 сек, задержка между запросами: 0.15 сек

## Диагностика

Запустить poller напрямую для проверки связи:

```bash
python3 pw_backend.py
```
