# Работа с проектом

Перед правками прочитайте `docs/README.md` и проверьте `.gitnexus`.
Сохраняйте существующие владельцы UI из `dashboard/DESIGN.md` и
`dashboard/UX-CONTRACT.md`. Не запускайте аппаратные операции в unit-тестах.

Ключи и runtime-данные находятся вне Git checkout. Не копируйте `/etc`,
`/var/lib`, NetworkManager-профиль или резервные копии целиком в репозиторий.
Перед коммитом выполните `python3 tools/check_publication.py --staged`;
перед публикацией также выполните проверку `--history`.
