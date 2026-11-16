"""Allowlisted read-only ERP chat requests and bounded plain-text rendering."""

ERP = 'http://frontend:8080/api/method/warehouse_core.'
ERP_ROUTE = ERP + 'ai_chat.route'
ERP_SEND = ERP + 'ai_chat.send'
ERP_OPTIONS = ERP + 'availability.options'
ERP_COMBINED = ERP + 'ai_chat.combined'
ERP_USAGE = ERP + 'ai_chat.question_usage'
HELP = ('Векторія ✦ ШІ-помічник\n\nЗнаходжу товари, залишки й резерви та пояснюю роботу у Вектор ERP за доступними інструкціями.\n\n'
        'Запит про кількість перевіряю в ERP, про характеристики — у статтях. Для самої назви запропоную вибір.\n'
        '«Покажи залишки» або артикул на зразок SERVO-1 — прямий пошук в ERP.\n'
        '«Чи є сервопривід і яка гарантія?» — пошук товарів і відповіді в базі знань.\n\n'
        'Не змінюю документи й не маю доступу до оплат або замовлень. Кожен запит незалежний.')


def units(text):
    return len(text.encode('utf-16-le')) // 2


def conversational_stock(stock, limit=3300):
    """Compose prose from authorized ERP values without a second model request."""
    rows, filters = stock['rows'], stock.get('filters') or {}
    term = filters.get('query')
    labels = [('query', 'пошук'), ('warehouse', 'склад'), ('company', 'компанія'), ('item_group', 'група')]
    scope = [f'{label}: {clipped(str(filters[key]), 100) if limit < 3300 else filters[key]}'
             for key, label in labels if filters.get(key)]
    state = {'in_stock': 'є фізичний залишок', 'free': 'є вільний залишок',
             'all': 'включно з нульовими залишками', 'production': 'є виробнича потреба'}.get(filters.get('stock_state'))
    if state:
        scope.append(state)
    footer = ('\n\nУточнення пошуку: ' + '; '.join(scope) + '.') if scope else ''
    footer += f"\n\nПеревірено у Вектор ERP: {stock['checked_at']} ({stock['timezone']})."
    budget = limit - units(footer) - 220
    if not rows:
        text = (f'Не знайшла залишків за запитом «{term}»' if term else 'Не знайшла залишків')
        text += ' серед перевірених доступних записів.'
        text += '\n\nНапишіть іншу частину назви або артикул; для огляду — «Покажи залишки».'
        text += '\nПро залишки на недоступних складах зробити висновок не можу.'
    else:
        # Do not total different products, units, companies or a partial result page.
        positive = any(row['actual_qty'] > 0 for row in rows)
        text = ('Так, знайшла товар у наявності.' if len(rows) == 1 else 'Ось що знайшла у доступних залишках.') if positive else (
            'Знайшла записи в ERP, але додатного фізичного залишку в них зараз немає.')
        shown = 0
        for row in rows:
            unit = {'Nos': 'шт.', 'Unit': 'шт.'}.get(row['stock_uom'], row['stock_uom'])
            block = (f"\n\n«{row['item_name']}» ({row['item_code']}) на складі «{row['warehouse']}» "
                     f"у компанії {row['company']}: {row['actual_qty']:g} {unit}").rstrip('.') + '.'
            block += f" Вільний залишок — {row['free_qty']:g} {unit}".rstrip('.') + '.'
            if row['reserved_stock']:
                block += f" Фізично зарезервовано {row['reserved_stock']:g} {unit}".rstrip('.') + '.'
            if row['actual_qty'] > 0 and row['free_qty'] <= 0:
                block += ' Увесь фізичний залишок зарезервований.'
            demands = [f'{label} — {row[key]:g} {unit}' for key, label in (
                ('production_demand', 'виробництво'), ('planned_demand', 'виробничі плани'),
                ('sales_demand', 'продажі'), ('subcontract_demand', 'субпідряд')) if row[key]]
            if demands:
                block += '\nОкремо в ERP є потреби: ' + '; '.join(demands).rstrip('.') + '.'
                block += ' Вони можуть перетинатися з фізичним резервом.'
            if row['ordered_qty']:
                block += f"\nОчікується надходження {row['ordered_qty']:g} {unit}".rstrip('.') + '.'
                block += ' Цієї кількості ще немає на складі.'
            if units(text + block) > budget:
                break
            text += block
            shown += 1
        if shown < len(rows) or stock.get('has_more') or stock.get('refine_search'):
            text += '\n\nЦе частина результатів. Уточніть назву, артикул або склад, щоб звузити пошук.'
    if not rows and stock.get('refine_search'):
        text += '\nПеревірено лише частину записів; уточніть склад або назву.'
    return text + footer


def clipped(text, limit):
    if units(text) <= limit:
        return text
    return text.encode('utf-16-le')[:max(0, limit - 1) * 2].decode('utf-16-le', errors='ignore') + '…'


def knowledge_text(result, limit):
    sources = result.get('sources') or [] if result.get('ok') else []
    citations = ''.join(f"\n[{i}] {clipped(str(s['title']), 140)} "
                        f"({clipped(str(s['name']), 140)}, редакція {s['revision']})"
                        for i, s in enumerate(sources, 1))
    answer = (result.get('text') if result.get('ok') else result.get('message')) or 'Не знайдено доступних статей.'
    return clipped(answer, limit - units(citations)) + citations


def render_auto(products, knowledge):
    """Reserve room for both sources, stock freshness and article citations."""
    stock = products.get('availability') if products.get('ok') else None
    if stock:
        product_text = conversational_stock(stock, limit=1550)
    else:
        product_text = products.get('message') or 'Не вдалося перевірити залишки в ERP.'
    product_text = clipped(product_text, 1550)
    return '📦 Товари та залишки\n' + product_text + '\n\n📚 База знань\n' + knowledge_text(knowledge, 1700)


def render(result, conversational=False):
    if not isinstance(result, dict):
        raise ValueError('Invalid ERP response')
    if not result.get('ok'):
        return result.get('message') or 'ERP не зміг виконати пошук.'
    stock = result.get('availability')
    if not stock:
        text = result.get('text') or 'Не знайдено доступних даних.'
        for index, source in enumerate(result.get('sources') or [], 1):
            text += f"\n[{index}] {source['title']} ({source['name']}, редакція {source['revision']})"
        return text
    if conversational:
        return conversational_stock(stock)
    rows = stock['rows']
    heading = f"Вектор ERP · {stock['checked_at']} ({stock['timezone']})"
    filters = stock.get('filters') or {}
    for key, label in (('query', 'Пошук'), ('warehouse', 'Склад'), ('company', 'Компанія'), ('item_group', 'Група товарів')):
        if filters.get(key):
            heading += f'\n{label}: {filters[key]}'
    state = {'in_stock': 'є фізичний залишок', 'free': 'є вільний залишок',
             'all': 'усі залишки, включно з нульовими', 'production': 'є виробнича потреба'}.get(filters.get('stock_state'))
    if state:
        heading += f'\nУмова: {state}.'
    text, shown = heading, 0
    for row in rows:
        block = (f"\n\n{row['item_name']} [{row['item_code']}]\n{row['warehouse']} · {row['company']}\n"
                 f"Фізично: {row['actual_qty']:g} {row['stock_uom']}; резерв: {row['reserved_stock']:g}; вільно: {row['free_qty']:g}\n"
                 f"Потреби: виробництво {row['production_demand']:g}, плани {row['planned_demand']:g}, "
                 f"продажі {row['sales_demand']:g}, субпідряд {row['subcontract_demand']:g}. Очікується: {row['ordered_qty']:g}.")
        if units(text + block) > 2900:
            break
        text += block
        shown += 1
    if not rows:
        text += ('\n\nЗа цим пошуком у перевірених доступних залишках записів не знайдено.'
                 '\nЦе не підтверджує відсутність товару на недоступних складах.'
                 '\n\nНапишіть частину назви чи артикул '
                 'або «Покажи залишки» для огляду доступних позицій.')
    else:
        text += f'\n\nПоказано позицій: {shown}.'
        text += '\nВільно — фізичний залишок мінус фізичний резерв. Планові потреби показані окремо; це не підтвердження комплектності.'
    if shown < len(rows) or stock.get('has_more') or stock.get('refine_search'):
        text += '\nЄ ще результати. Уточніть назву або артикул у наступному повідомленні.'
    return text
