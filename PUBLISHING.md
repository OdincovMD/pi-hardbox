# Подготовка публикации

Удалённый репозиторий: https://github.com/OdincovMD/pi-hardbox.
Основная ветка — main. Проект содержит исходники, начальный коммит,
unit-файлы, примеры конфигурации, документацию и проверки публикации.

Перед отправкой:

```bash
git status --short
git diff --cached --check
python3 tools/check_publication.py --staged
python3 tools/check_publication.py --history
git remote -v
```

На работающей PI Hardbox дополнительно сравните Git с реальными установленными
значениями в памяти root (включая закрытые резервные копии):

```bash
sudo python3 tools/check_publication.py --staged --installed
sudo python3 tools/check_publication.py --history --installed
```

Эта проверка не выводит значения и не сохраняет их в файлы. Для Git используется
`-c safe.directory=точный-путь-checkout`; глобальные настройки Git не меняются.

Установите локальный pre-commit hook:

```bash
git config core.hooksPath .githooks
```

Проверка index читает именно подготовленные Git blobs. Изменение файла
после `git add` не скрывает секрет, который уже оказался в index.
Проверка history проходит все достижимые коммиты, включая другие ветки и теги.
Вывод содержит только пути и категории находок, без самих значений.

Для обновлений используйте origin и обычный push ветки main после проверок.
Fetch URL: https://github.com/OdincovMD/pi-hardbox.git. Push URL использует
существующую SSH-авторизацию GitHub, без токенов в URL или настройках проекта.

Исходники img2txt опубликованы отдельно; в этом проекте хранятся интеграция
с основным ключом и шаблоны запуска. Бинарник Xray не включён: URL версии
и SHA256 находятся в `gateway/release.json`. Правила доменов загружаются
из v2fly/domain-list-community; источник использует MIT. Лицензии сторонних компонентов применяются отдельно.
Проект пока не содержит выбранной владельцем лицензии на собственный код.
