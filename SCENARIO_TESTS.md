# Scenario Tests (PST) — Notion Connector

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`.

---

## Прогон 2026-08-19

**Существующее покрытие до PST:** 119 тестов в 8 файлах — глубокое
покрытие search, чтения страниц, создания баз данных, комментариев,
строк, перемещений и корзины, включая явные регрессионные тесты на
типизацию `parent` (page vs database asymmetry). Аудит по точному имени
`@chat.function` нашёл **4 функции, никогда не тестировавшиеся**:

`list_comments`, `list_databases`, `list_users`, `update_page_content`.

**Новый файл:** `tests/test_pst_scenarios.py` — 10 сценариев: happy path
для каждой из 4 функций плюс blocked (нет токена) для `list_databases` и
`list_users`, error (страница не найдена) для `list_comments`, error
(пустой текст отклоняется с `NOTION_VALIDATION_FAILED`) и два happy пути
(`position=end` по умолчанию не шлёт override, `position=start` шлёт
`{"type": "start"}`) для `update_page_content`. Каждый проверяет не
только успех вызова, но и форму реального HTTP-запроса (правильный
фильтр объекта, правильный `block_id`, правильный `position`).

### Результат

129/129 тестов зелёные (119 существующих + 10 новых). **Реальных багов в
приложении не найдено.**

---
