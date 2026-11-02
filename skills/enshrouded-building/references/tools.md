# Инструменты и где они лежат

## Скрипты (репозиторий Custodian_GM, папка `scripts/`)

| Скрипт | Для чего |
|---|---|
| `enshrouded_house.py` | `check` (все правила), `plan -z N` (срез этажа текстом), `export` (воксели и `placements`), `templates` |
| `enshrouded_bundle.py` | `import <папка-чертежа> -o x.scene.json`, `export <проект> <папка> --label "..."` |
| `enshrouded_view.py` | страница с 3D, планом по этажам, ведомостью и режимом «Пешком»; `--site` — галерея для веб-сервера |
| `enshrouded_assist.py` | помощник: читает построенное (снимок, захват, проект), выдаёт доработки по-русски |
| `enshrouded_house_render.py` | быстрый PNG-рендер проекта |
| `test_enshrouded_house.py` | тесты правил |

```text
python scripts/enshrouded_house.py check  docs/enshrouded-houses/castle.json
python scripts/enshrouded_house.py plan   docs/enshrouded-houses/castle.json -z 2
python scripts/enshrouded_view.py   docs/enshrouded-houses/castle.json -o castle.html
python scripts/enshrouded_bundle.py export docs/enshrouded-houses/castle.json docs/enshrouded-houses/blueprints/custodian-castle-v1 --label "Замок"
python scripts/enshrouded_bundle.py import <папка-чертежа> -o piece.scene.json
python scripts/enshrouded_assist.py <снимок> --md advice.md -o advice.json [--level N]
python -m unittest scripts/test_enshrouded_house.py
```

**Где актуальная версия.** Всё влито в `main` (инструмент и просмотрщик —
PR #2, #3; правила жизнеспособности — #4; помощник — #5). Если в рабочей
копии `D:\Projects\Custodian_GM\scripts` этих скриптов нет (копия не
обновлена с `main`), на ПК готовая копия лежит в
`D:\Projects\castle-viewer\src\scripts`; импортируй оттуда, как делает
`gen_mage_tower.py`:

```python
sys.path.insert(0, r"D:\Projects\castle-viewer\src\scripts")
import enshrouded_house as eh
```

Python на ПК: `C:\Users\Hatzy\AppData\Local\Programs\Python\Python312\python.exe`.

## Данные (`docs/enshrouded-houses/`)

`materials.json` (блоки), `prop-catalog.json` (все предметы, габариты,
UUID), `npc-stations.json` (мастера и станки), `lit-lamps.json`,
`enshrouded-geometry-templates.json` (шаблоны молота), `worlds/` (захваты
настоящих баз), `visual/` (модели и текстуры игры для просмотрщика),
`blueprints/` (готовые чертежи и `FORMAT.md`), `gen_*.py` (генераторы).

## ПК пользователя

| Путь | Что там |
|---|---|
| `D:\Projects\Custodian_GM` | основная рабочая копия |
| `D:\Projects\Custodian_GM\work\enshrouded\blueprints\` | чертежи и снимки построек из игры; эталон `mayor-manor-furnished-no-stations-v1` |
| `D:\Projects\Custodian_GM\deployment\enshrouded-house-builder\` | локальный строитель (Lua, `catalog-host/host_planner.py`) — **только локально, не коммитить** |
| `D:\Projects\Custodian_GM\deployment\enshrouded-house-builder\skill\` | навык постройки одобренного чертежа (`build_house.py`) — строит, а не проектирует |
| `D:\Projects\Custodian_GM\.agent\` | рабочие заметки и извлечённые данные игры — не коммитить |
| `D:\Projects\castle-viewer\` | веб-просмотрщик: `src\` (код), `rebuild.cmd` (пересобрать галерею из `work\enshrouded\blueprints`), `start.cmd`, `site\` |

## Просмотр

- Страница самодостаточна: модели и текстуры игры встроены, ≤ 16 МБ.
- Скриншоты локально: собери с `--three-dir ""`, положи рядом
  `three.min.js`, `OrbitControls.js`, `GLTFLoader.js`, запусти Playwright
  (`--use-gl=swiftshader`), двигай срез ползунком `#z`. Ниже нуля земля
  скрывается сама.
- Режим «Пешком»: WASD, мышь, Shift — бегом, пробел — прыжок, E — дверь,
  R — к алтарю, Q — выйти. Пройди сам от входа до каждой комнаты.
- Галерея на ПК: положи папку в `work\enshrouded\blueprints` и запусти
  `D:\Projects\castle-viewer\rebuild.cmd`.

## Постройка в игре

Строит Хранитель через каталог (`build_house.py` / чат UI) только
одобренный чертёж. Ты готовишь чертёж `prepared_offline`; регистрация в
CatalogApproval, постройка, рестарты и любые записи в мир — решение
пользователя. Неизвестный результат постройки — сверять, а не повторять.
