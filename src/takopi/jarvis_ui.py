from dataclasses import replace
from html import escape

COMMANDS = [('start', 'Главное меню'), ('clear', 'Новый разговор'), ('resume', 'Мои разговоры'), ('memory', 'Что я помню'), ('cancel', 'Остановить задачу'), ('help', 'Возможности')]


def button(text, action, style=None):
    value = {'text': text, 'callback_data': 'z:' + action}
    if style:
        value['style'] = style
    return value


HOME = '<b>Джарвис</b>\nМеньше рутины. Больше времени на своё.\n\n<b>Просто напиши, что хочешь.</b>\nКнопки ниже — только подсказки. Можно сразу прислать голосовое, фото или файл.\n\n<b>С чего начнём?</b>'
HOME_ROWS = [[button('Создать документ', 'docs'), button('Нарисовать', 'images')], [button('Разобрать запись', 'voice'), button('Написать или найти', 'search')], [button('Ещё возможности', 'more', 'primary')], [button('Мои разговоры', 'resume'), button('Настройки', 'settings')]]


async def card(cfg, msg, text, rows, *, edit=False):
    if edit:
        result = await cfg.bot.edit_message_text(chat_id=msg.chat_id, message_id=msg.message_id, text=text, parse_mode='HTML', reply_markup={'inline_keyboard': rows})
        if result is not None:
            return
    await cfg.bot.send_message(chat_id=msg.chat_id, message_thread_id=msg.thread_id, text=text, parse_mode='HTML', reply_markup={'inline_keyboard': rows})


async def welcome(cfg, msg):
    await card(cfg, msg, HOME, HOME_ROWS)


async def callback(cfg, msg, action, store):
    from takopi.jarvis import preprocess, preference, topic_dir, db
    back = [[button('‹ Главное меню', 'home')]]
    if action == 'home':
        await card(cfg, msg, HOME, HOME_ROWS, edit=True)
        return
    texts = {
        'docs': '<b>Документ — от идеи до файла</b>\n\nПисьмо в Word, бюджет в Excel, презентация или PDF. Опиши задачу и для кого результат.\n\n<blockquote>Сделай таблицу расходов на месяц с формулами и итогами.</blockquote>\nГотовый файл появится прямо в чате.',
        'images': '<b>Превратим идею в картинку</b>\n\nОпиши сюжет и стиль. Для правок пришли изображение и расскажи, что изменить.\n\n<blockquote>Нарисуй рыжего кота у окна в стиле книжной иллюстрации.</blockquote>',
        'voice': '<b>Расскажи голосом</b>\n\nПришли голосовое, кружочек, аудио или видео. Можно получить расшифровку, краткое содержание или список дел.\n\n<blockquote>Выдели главное и выпиши договорённости из этой записи.</blockquote>',
        'search': '<b>Нужные слова и ответы</b>\n\nНапишу письмо, сокращу текст, сравню варианты и найду информацию с источниками.\n\n<blockquote>Помоги вежливо перенести встречу на следующую неделю.</blockquote>',
        'more': '<b>Что ещё можно поручить</b>\n\n<b>Озвучка</b> — превратить текст в аудио.\n<b>Видео</b> — создать ролик по описанию.\n<b>Фото</b> — разобрать изображение или отредактировать.\n<b>Файлы</b> — прочитать, преобразовать, собрать данные.\n<b>Память</b> — сохранить важное для следующих разговоров.\n\nПросто напиши просьбу обычными словами.',
    }
    if action in texts:
        await card(cfg, msg, texts[action], back, edit=True)
        return
    if action.startswith('mode:'):
        preference(msg.chat_id, msg.thread_id, 'mode', action.split(':')[1])
        action = 'settings'
    if action == 'settings':
        mode = preference(msg.chat_id, msg.thread_id, 'mode') or 'act'
        model = preference(msg.chat_id, msg.thread_id, 'model') or 'gpt-6-luna'
        text = '<b>Настройки помощника</b>\n\n<b>Модель</b> · ' + escape(model) + '\n<b>Рассуждение</b> · среднее\n\nКак выполнять задачи?'
        rows = [[button(('✓ ' if mode == 'act' else '') + 'Сразу делать', 'mode:act', 'primary' if mode == 'act' else None)], [button(('✓ ' if mode == 'plan' else '') + 'Сначала согласовать план', 'mode:plan', 'primary' if mode == 'plan' else None)], [button('Память', 'memory'), button('Выбрать модель', 'model')], *back]
        await card(cfg, msg, text, rows, edit=True)
        return
    if action == 'memory':
        path = topic_dir(msg.chat_id, msg.thread_id)
        content = '\n'.join(p.read_text() for p in (path/'memory.md', path/'user-memory.md') if p.exists())
        notes = '\n'.join(line for line in content.splitlines() if line.strip() and not line.startswith('#'))
        text = '<b>Что я помню</b>\n\n' + (escape(notes[:2800]) if notes else 'Пока здесь пусто.') + '\n\nЧтобы добавить, напиши: «Запомни: …». Чтобы удалить — «Забудь …».'
        await card(cfg, msg, text, back, edit=True)
        return
    if action == 'resume':
        with db() as conn:
            rows = conn.execute('SELECT session,name FROM conversations WHERE chat=? AND topic=? ORDER BY updated DESC LIMIT 8', (msg.chat_id, msg.thread_id or 0)).fetchall()
        buttons = [[button(row['name'][:38], 'do:/resume ' + str(i))] for i, row in enumerate(rows, 1)]
        await card(cfg, msg, '<b>Мои разговоры</b>\n\n' + ('Выбери, какой продолжить.' if rows else 'Новый разговор начнётся с твоего сообщения.'), [[button('＋ Новый разговор', 'do:/clear', 'primary')], *buttons, *back], edit=True)
        return
    command = action[3:] if action.startswith('do:') else '/' + action
    await preprocess(cfg, replace(msg, text=command), store)
