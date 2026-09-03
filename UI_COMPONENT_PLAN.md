# Notion Connector — UI component plan

Источники: `Docs/session-notes/UI_COMPONENT_VOCABULARY.md`, `UI_INTERFACE_STANDARD.md`,
`concepts/panels.md`. Основано на функционале `notion-connector`.

## 1. Компоненты

| Экран | Примитивы | Почему именно эти |
|---|---|---|
| Sidebar (left) | `ui.Column`(align="start") + `ui.Text`(workspace) + `ui.Divider` + `ui.Tree`(pages/databases hierarchy — реальная иерархия Notion) + `ui.Button`("App settings") | `Tree` — единственный примитив, точно отражающий вложенную структуру страниц Notion (страница → подстраницы → базы данных). |
| Database View (center, `center_overlay=True`) | `ui.Stats`(rows count) + `ui.DataTable`(колонки = свойства базы данных Notion, динамически; sortable) | База данных Notion — это буквально таблица со своей схемой свойств, прямое попадание в DataTable. |
| Page Viewer | Back-button + `ui.Header`(page title) + `ui.Markdown`(page content, read-only рендер блоков) | `Markdown` — примитив для рендера форматированного текстового контента страницы. |
| Page Row Detail (запись базы данных) | Back-button + `ui.KeyValue`(properties страницы-записи) + `ui.Markdown`(содержимое страницы) | Запись в базе данных Notion — тоже страница со своими property + телом. |
| Comments Panel | `ui.Timeline`(comments on page, автор+время) + `ui.TextArea`(param_name="comment", placeholder="Добавить комментарий...") | `Timeline` для треда комментариев страницы. |
| Search | `ui.Input`(param_name="query", placeholder="Найти страницу или базу данных...", on_submit=Call) + `ui.DataTable`(результаты: title, type, last edited) | Простой Input с submit + табличные результаты. |
| App Settings | `ui.Accordion`([Connections+Disconnect, Default Workspace Select]) | Централизованные настройки по стандарту. |

## 2. User flow (валидно по panel lifecycle)

1. **SESSION INIT** → `__panel__notion_sidebar` рендерит workspace + `Tree` иерархии
   страниц/баз данных (`list_databases` + `browse`); `auto_action` открывает последнюю
   активную базу данных, если `not active_view`.
2. Sidebar Tree: клик на узел базы данных → `ui.Call("__panel__notion_center",
   database_id=...)` → Database View (DataTable со свойствами как колонками).
3. Database View: клик на строку → `ui.Call(page_id=...)` → Page Row Detail
   (KeyValue свойств + Markdown содержимого).
4. Sidebar Tree: клик на узел обычной страницы → `ui.Call(page_id=...)` → Page Viewer
   (Header + Markdown).
5. Comments Panel открывается как часть Page Viewer/Page Row Detail — Timeline
   существующих комментариев + TextArea для нового → `add_comment` →
   `refresh_panels=["notion_center"]`.
6. Search: Input в сайдбаре сверху (`on_submit=ui.Call("search_notion")`) →
   DataTable результатов в отдельном center overlay.
7. "App settings" → отдельный center overlay с Accordion-секциями.

## 3. Конкретные экраны (screens)

### Screen: Database View (`notion_center` + `database_id`)
- Stats: количество строк.
- DataTable: колонки = свойства базы (динамическая схема) — row-click → Page Row Detail.

### Screen: Page Row Detail (`notion_center` + `page_id`, родитель = database)
- Back-button "← К базе данных".
- KeyValue: свойства записи.
- Markdown: тело страницы.
- Timeline + TextArea: комментарии.

### Screen: Page Viewer (`notion_center` + `page_id`, обычная страница)
- Back-button "← Назад".
- Header: заголовок страницы.
- Markdown: содержимое.
- Timeline + TextArea: комментарии.

### Screen: Search Results (`notion_search` + `query`)
- Input (поиск) сверху.
- DataTable: title, type (page/database), last edited — row-click → соответствующий
  Page Viewer или Database View.

### Screen: App settings (`notion_settings`)
- Accordion "Подключение": workspace, Disconnect (Dialog-подтверждение).
- Accordion "Рабочее пространство по умолчанию": Select.
