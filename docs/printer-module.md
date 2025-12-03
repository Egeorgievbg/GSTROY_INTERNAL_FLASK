# Принтер модул (printer_service)

`printer_service` е независим blueprint под `/printer-hub`. Работи като proxy между фронт-енд заявките (`/product_detail`, `/pallet_detail`) и вътрешния label server.

## Основни функции

1. **Маршрути**  
   - `POST /printer-hub/print-product`: приема `printer_id`, продуктово име/баркод, количество и копия, и изпраща заявка към label server (`printers/{ip}/print-product-label`).  
   - `POST /printer-hub/print-list`: същото, но за списъци/QR кодове (`print-list-label`).

2. **Сигурност и scope**  
   - `@login_required` гарантира, че само оторизирани потребители могат да ползват принтерите.
   - `_user_warehouse_id()` проверява дали потребителят е присвоен към склад и позволява само неговите принтери.
   - `_sanitize_text` почиства имени, QR данни и баркодове, `_clamp_copies` ограничава до 50 копия.

3. **Доставяне към label server**  
   - `_send_label_request` строи URL от `printer.server_url` или принтерския склад, добавя JSON payload и timeout (env `ERP_LABEL_SERVER_TIMEOUT`).
   - Връща JSON `{"ok": true, "message": ...}` или error код 502 при проблем.

4. **Статус**  
   - `/printers/{ip}/status` се ползва от `admin_printers` за проверка дали принтерите са онлайн.

## Деплой и конфигурация

- Настройте `Printer.server_url` или `Warehouse.printer_server_url`, за да сочи към label server (`http://labels.gstroy.local` например).
- Уверете се, че label server поддържа endpoint `printers/<ip>/print-product-label` и връща JSON `{ "message": "Queued" }`.
- `printer_service` няма зависимости извън `Flask`/`requests` (използваме стандартен `urllib.request`).
