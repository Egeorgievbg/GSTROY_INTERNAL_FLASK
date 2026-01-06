# Printer Module / Service

Printer module (`printer_service`) симулира интерфейс към външен label server. Идеята е да се демонстрира как се извиква REST API, как се форматират payload-и и как се обработват грешки без реален хардуер.

## Структура

- `printer_service/__init__.py`: Blueprint `printer_bp` с префикс `/printer-hub`.
- Зависимости: `models.Printer`, `models.User`, Flask `Blueprint`, `login_required`, `urllib.request`.
- Конфигурация:
  - `ERP_LABEL_SERVER_TIMEOUT` и `ERP_LABEL_STATUS_TIMEOUT` за http таймаути.
  - `PRINTER_SERVER_URL` може да се зададе per warehouse или per printer.

## Основни функции

### `_printer_server(printer)`
Отговаря за трансформирането на адреса на принтера в пълен HTTP URL (добавя `http://`, премахва `/`, пада към warehouse нивото ако няма директен `server_url`).

### `_send_label_request(printer, endpoint, payload)`
1. Строи URL към външния сървър (`base/endpoint`).
2. JSON сериализира payload и го изпраща (timeout).
3. Обработва `HTTPError` и `URLError`, връща валидация към caller.

### `_sanitize_text` и `_clamp_copies`
Помощни функции:
- `_sanitize_text`: премахва опасни символи (`^`, `~`, newline) и ограничава дължината, за да не се счупи принтерът.
- `_clamp_copies`: гарантира, че бройката е между 1 и max (по подразбиране 50).

## Роутове

| Роут | Метод | Функция |
|------|-------|--------|
| `/printer-hub/print-product` | POST | Приема `name`, `barcode`, `cop ies`, `quantity`. Проверява достъп (потребителят е login и принтерът принадлежит на същия warehouse). Изпраща `_send_label_request` към принтер. |
| `/printer-hub/print-list` | POST | Приема `name`, `qr_data`, `copies`. Също се валидира, извиква `_send_label_request`. |
| `/printer-hub/status` (helper) | GET (вътрешно)| Може да се добави за проверка на състояние на принтера (използва `urllib.request`). |

## Защо е важен модулът?

1. Показа пример как да се интегрира с REST API при принтер, без да е необходим реален хардуер.
2. Демонстрира добри практики – таймаути, означения, обработка на грешки и формиране на payload.
3. Помага на фронт енда да изпраща заявки и да показва статус (flash съобщения).

Когато портваш към Django:
 - Превърни blueprint-а в Django `app`, със `urls.py` и класови гледки.
 - Модулът е подходящ за отделно Django management console за принтери.
 - Същите помощни функции `_sanitize_text` и `_clamp_copies` могат да живеят в `services/printer.py`.
