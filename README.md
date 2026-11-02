# House Design Process

Полная связка проектирования Enshrouded: два навыка, инструменты строителя, native-карточка чата, браузерный WebUI и постоянное хранилище в одном checkout.

| Компонент | Путь | Назначение |
| --- | --- | --- |
| Навык строительства | skills/enshrouded-building/ | Полная копия навыка и всех его правил |
| Навык отображения | skills/house-design/ | Показ реальных промежуточных решений в чате |
| Строитель | scripts/enshrouded_*.py | Компиляция, checker, рендер, импорт и экспорт blueprint |
| Viewer и данные | docs/enshrouded-houses/ | Исходный viewer, каталоги, реальные модели и текстуры |
| MCP App и WebUI | tools/house-design-app/ | Общая интерактивная сцена: вращение, масштаб, сдвиг и скрытие земли |
| Хранение | tools/house-design-app/design_session.py | SQLite: сессии, версии, события, контроль ревизий и dedup |

```text
enshrouded-building + house-design
 -> реальные промежуточные spec / фазы
 -> MCP update или scripts/house_process.py
 -> SQLite: сессии / версии / события
 -> карточка чата и WebUI той же сессии
 -> исходный Enshrouded viewer и игровые материалы
```

## Запуск

Python 3.12. Отдельные checkout Custodian_GM и castle-viewer не нужны: исходники, каталоги, template и визуальные ресурсы включены. Python-зависимости фиксированы в requirements.txt.

```powershell
python -m pip install -r requirements.txt
```

Для чата установите обе папки из skills/ в каталог навыков клиента. MCP stdio: команда — установленный Python, аргумент — абсолютный путь к tools/house-design-app/server.py этого checkout. В клиенте нужна поддержка MCP Apps. Конфигурация клиента и исходные чужие навыки автоматически не меняются.

```powershell
python tools/house-design-app/server.py --serve
```

WebUI: http://127.0.0.1:8765/ — явно выберите сессию. Он читает то же хранилище, что MCP App. Обновления появляются автоматически без сброса камеры. Сетка применяется только к форме без текстур; игровые материалы показываются без дополнительной сетки. Исходный viewer использует Three.js 0.147.0 из CDN и Google Fonts: браузеру нужен доступ к ним; игровые ресурсы загружаются из checkout.

## Работа строителя

Применяйте оба навыка. Публикуйте настоящие изменения по мере создания: эскиз объёмов, уточнение формы и проёмов, выбранные материалы. Не готовьте финальную модель для последующего проигрывания стадий. Контракт описан в docs/integration.md.

Файловый исполнитель может публиковать текущий spec в общее хранилище:

```powershell
python scripts/house_process.py begin "Мой дом"
```

```powershell
python scripts/house_process.py publish SESSION_ID work/current.json --phase "Эскиз"
```

Для следующей публикации передайте --revision с последней возвращённой ревизией. SQLite создаётся в work/house-app-integration/design-sessions/sessions.sqlite3. Сессии, снимки и события сохраняются между перезапусками. Runtime исключён из Git; резервная копия SQLite должна учитывать WAL.

## Проверки и экспорт

```powershell
python scripts/enshrouded_house.py check docs/enshrouded-houses/castle.json
```

```powershell
python scripts/enshrouded_bundle.py export docs/enshrouded-houses/castle.json work/castle-export --label "Замок"
```

```powershell
python scripts/enshrouded_bundle.py import work/castle-export -o work/castle.scene.json
```

```powershell
python -m unittest discover -s scripts -p "test_enshrouded_house.py" -v
```

```powershell
python -m unittest discover -s tools/house-design-app -p "test_*.py" -v
```

castle.json проходит checker без ошибок; предупреждения остаются видимыми. estate.json — исторический пример: тест намеренно обнаруживает его нарушения. Черновой показ не означает приёмки готового дома; перед экспортом действуют правила строителя. Игра, сервер и сейвы этим проектом не изменяются.

## Состав и происхождение

provenance/dependencies.json фиксирует исходники и хеши; docs/enshrouded-houses/visual/README.json — происхождение визуального каталога и сборку игры. Исходный enshrouded-building не изменён. Его устаревшие ссылки на пути ПК не являются runtime-зависимостями комплекта: рабочие инструменты лежат в scripts/ этого checkout.

Локальный код записи в игру, серверы, сейвы, ключи, runtime-сессии и запрещённые к публикации чужие чертежи не входят в комплект. Снимки миров — необязательный пользовательский вход для достройки существующей базы; для нового проекта они не нужны.

## Подтверждённые проверки комплекта

Проверено из отдельного чистого clone: все 463 хеша manifest совпали; runtime использует только scripts/ и docs/ этого clone. Прошли 45 тестов строителя и 24 теста app/WebUI, включая MCP stdio, HTTP-ограничения и исполнение browser bridge. Реальные новые эскиз, уточнение формы и выбор материалов поступили из файлового исполнителя через общую SQLite в WebUI; сессия восстановилась после перезапуска сервера. Последующее уточнение сохранило точные координаты камеры и тот же документ/canvas. Это проверка инструмента и хранилища, не игровая приёмка черновика.

При резервном копировании сохраняйте всю папку work/house-app-integration/design-sessions/, а не только SQLite: fragment-операции используют также frozen-файлы. В текущей версии сессии с fragment хранят абсолютные пути к этим файлам; перенос существующей истории в другой путь не поддержан автоматически. Это не требуется для запуска нового проекта из чистого checkout; для переноса геометрии используйте экспорт/импорт и новую сессию. Проверка последней правки сетки именно в ранее открытом desktop-чате остаётся отдельной незавершённой приёмкой.
