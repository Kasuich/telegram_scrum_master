from telegram_gateway.formatting import markdown_tables_to_lists, render_telegram_html


def test_render_telegram_html_formats_headings_and_emphasis() -> None:
    rendered = render_telegram_html("## Итог\n**Готово** и *важно*. Код: `storyPoints`.")

    assert rendered == ("<b>Итог</b>\n<b>Готово</b> и <i>важно</i>. Код: <code>storyPoints</code>.")


def test_render_telegram_html_escapes_user_content() -> None:
    rendered = render_telegram_html("Значение <script> & данные")

    assert rendered == "Значение &lt;script&gt; &amp; данные"


def test_render_telegram_html_preserves_links_and_code_blocks() -> None:
    rendered = render_telegram_html(
        '[Задача](https://tracker.yandex.ru/DARKHORSE-1)\n```json\n{"storyPoints": 5}\n```'
    )

    assert '<a href="https://tracker.yandex.ru/DARKHORSE-1">Задача</a>' in rendered
    assert "<pre>{&quot;storyPoints&quot;: 5}</pre>" in rendered


def test_markdown_table_becomes_nested_list() -> None:
    source = (
        "| # | Задача | Владелец | Дедлайн | Приоритет |\n"
        "|---|---|---|---|---|\n"
        "| 1 | Настроить бота | Роман | 12 июня | высокий |\n"
        "| 2 | Проверить API | Николай | — | средний |"
    )

    rendered = markdown_tables_to_lists(source)

    assert "|---|" not in rendered
    assert "1. **Настроить бота**" in rendered
    assert "• **Владелец:** Роман" in rendered
    assert "• **Дедлайн:** 12 июня" in rendered
    assert "2. **Проверить API**" in rendered
    assert "Дедлайн:** —" not in rendered


def test_rendered_table_uses_telegram_html() -> None:
    rendered = render_telegram_html(
        "| Пользователь | Открыто | Закрыто |\n|---|---|---|\n| Роман | 4 | 2 |"
    )

    assert "<b>Роман</b>" in rendered
    assert "<b>Закрыто:</b> 2" in rendered
    assert "|" not in rendered
