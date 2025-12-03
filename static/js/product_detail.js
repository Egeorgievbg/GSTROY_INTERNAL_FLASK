(function () {
    const config = window.productDetailConfig || {};
    const messages = Object.assign({
        printerMissing: "Please select a printer.",
        printing: "Sending job to the printer hub...",
        printSuccess: "Print job queued successfully.",
        printError: "Unable to reach the printer hub."
    }, config.messages || {});

    function renderBarcode() {
        const value = config.barcodeValue;
        const barcodeEl = document.getElementById("real-barcode");
        if (!value || !barcodeEl || typeof JsBarcode !== "function") {
            return;
        }
        try {
            JsBarcode(barcodeEl, value, {
                format: "CODE128",
                lineColor: "#334155",
                width: 2,
                height: 50,
                displayValue: true,
                fontOptions: "bold",
                font: "Monospace",
                background: "transparent",
                margin: 0
            });
        } catch (error) {
            console.error(error);
        }
    }

    function setupPrinterControls() {
        const printBtn = document.getElementById("product-print-btn");
        if (!printBtn) {
            return;
        }

        const printerSelect = document.getElementById("product-printer-select");
        const copiesInput = document.getElementById("product-print-copies");
        const quantityInput = document.getElementById("product-print-quantity");
        const statusEl = document.getElementById("product-print-status");
        const unitInfo = config.unitInfo || "";
        const productName = config.productName || "";
        const productBarcode = config.productBarcode || "";
        const defaultPrinterId = config.defaultPrinterId;

        function setStatus(message, isSuccess) {
            if (!statusEl) {
                return;
            }
            statusEl.textContent = message;
            statusEl.classList.toggle("text-success", Boolean(isSuccess));
            statusEl.classList.toggle("text-danger", Boolean(isSuccess) === false);
        }

        printBtn.addEventListener("click", function () {
            const selectedValue = printerSelect?.value;
            const printerId = selectedValue ? parseInt(selectedValue, 10) : defaultPrinterId;
            if (!printerId) {
                setStatus(messages.printerMissing, false);
                return;
            }

            let copies = parseInt(copiesInput?.value, 10);
            if (isNaN(copies) || copies < 1) {
                copies = 1;
            }

            let quantity = parseFloat(quantityInput?.value);
            if (isNaN(quantity) || quantity <= 0) {
                quantity = 1;
            }

            printBtn.disabled = true;
            setStatus(messages.printing, true);

            fetch("/printer-hub/print-product", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    printer_id: printerId,
                    name: productName,
                    barcode: productBarcode,
                    copies: copies,
                    quantity: quantity,
                    unit_info: unitInfo
                })
            })
                .then(async (response) => {
                    const contentType = response.headers.get("content-type") || "";
                    let data;
                    if (contentType.includes("application/json")) {
                        data = await response.json();
                    } else {
                        data = { message: await response.text() };
                    }
                    return { ok: response.ok, data };
                })
                .then(({ ok, data }) => {
                    if (!ok) {
                        throw new Error(data.message || messages.printError);
                    }
                    setStatus(data.message || messages.printSuccess, true);
                })
                .catch((err) => {
                    console.error(err);
                    setStatus(err.message || messages.printError, false);
                })
                .finally(() => {
                    printBtn.disabled = false;
                });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        renderBarcode();
        setupPrinterControls();
    });

    window.copyPageLink = function () {
        navigator.clipboard.writeText(window.location.href);
    };

    window.toggleCompetitorIcon = function (element) {
        const arrow = element.querySelector("#competitorArrow");
        if (arrow) {
            arrow.classList.toggle("rotate-180");
        }
    };
})();


// Запазваме съществуващата конфигурация
window.productDetailConfig = {{ {
    "barcodeValue": product.ean or product.item_number,
    "defaultPrinterId": product_default_printer_id if product_default_printer_id else None,
    "productId": product.id,
    "unitInfo": product_detail_unit_info,
    "productName": product.name,
    "productBarcode": product.barcode or product.item_number
}|tojson }};

document.addEventListener('DOMContentLoaded', function() {
    // 1. Инициализация на основния баркод на страницата
    JsBarcode("#real-barcode", window.productDetailConfig.barcodeValue, {
        format: "CODE128",
        lineColor: "#333",
        width: 2,
        height: 50,
        displayValue: true
    });

    // 2. Логика при отваряне на модала
    const printModal = document.getElementById('printLabelModal');
    if (printModal) {
        printModal.addEventListener('shown.bs.modal', function () {
            // Генерираме баркода вътре в модала (по-малък вариант)
            JsBarcode("#modal-barcode", window.productDetailConfig.barcodeValue, {
                format: "CODE128",
                lineColor: "#000",
                width: 1.5,
                height: 30,
                displayValue: false, // Без числа под чертите за по-чист вид
                margin: 0
            });
        });
    }
});

// Helper функция за +/- бутоните
function adjustValue(elementId, delta) {
    const input = document.getElementById(elementId);
    let val = parseInt(input.value) || 0;
    val += delta;
    if (val < 1) val = 1;
    input.value = val;
}
