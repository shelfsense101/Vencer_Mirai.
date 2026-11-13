
document.addEventListener('DOMContentLoaded', () => {

    // PRODUCT SEARCH
    const search = document.getElementById('searchInput');

    if (search) {
        search.addEventListener('input', () => {
            const q = search.value.toLowerCase();

            document.querySelectorAll('#productRows tr[data-search]')
                .forEach(row => {
                    row.hidden = !row.dataset.search.includes(q);
                });
        });
    }

    // FLASH MESSAGES
    document.querySelectorAll('.flash').forEach(el => {
        setTimeout(() => {
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 400);
        }, 5000);
    });

    // EXISTING QUICK SCAN SUBMISSION
    const scanButton = document.getElementById('scanSubmit');

    if (scanButton) {
        scanButton.addEventListener('click', async () => {
            const input = document.getElementById('scanBarcode');
            const msg = document.getElementById('scanMessage');

            if (!input || !msg || !input.value.trim()) return;

            const token = document.querySelector(
                'meta[name="csrf-token"]'
            )?.content || '';

            try {
                const res = await fetch('/api/scan', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': token
                    },
                    body: JSON.stringify({
                        barcode: input.value.trim()
                    })
                });

                const data = await res.json();

                msg.textContent =
                    data.message || data.error || 'Request completed';

                msg.className = data.ok
                    ? 'success-text'
                    : 'danger-text';

                if (data.ok) input.value = '';

            } catch (error) {
                msg.textContent = 'Could not contact the server. Try again.';
            }
        });
    }

    // ADD PRODUCT CAMERA SCANNER
    const productStart = document.getElementById('productScanStart');
    const productStop = document.getElementById('productScanStop');
    const productBarcode = document.getElementById('productBarcode');
    const productMessage = document.getElementById('productScanMessage');

    let productScanner = null;

    if (productStart) {
        productStart.addEventListener('click', async () => {

            if (!window.Html5Qrcode) {
                productMessage.textContent =
                    'Scanner library unavailable. Please refresh the page.';
                return;
            }

            try {
                if (productScanner) return;

                productScanner = new Html5Qrcode('product-scanner-reader');

                await productScanner.start(
                    { facingMode: 'environment' },
                    {
                        fps: 10,
                        qrbox: { width: 250, height: 120 },
                        formatsToSupport: [
                            Html5QrcodeSupportedFormats.EAN_13,
                            Html5QrcodeSupportedFormats.EAN_8,
                            Html5QrcodeSupportedFormats.UPC_A,
                            Html5QrcodeSupportedFormats.UPC_E,
                            Html5QrcodeSupportedFormats.CODE_128,
                            Html5QrcodeSupportedFormats.CODE_39
                        ]
                    },
                    async (decodedText) => {

                        productBarcode.value = decodedText;

                        productMessage.textContent =
                            'Barcode scanned successfully: ' + decodedText;

                        productMessage.className = 'success-text';

                        await stopProductScanner();
                    },
                    () => {}
                );

                productMessage.textContent = 'Camera active. Point it at a barcode.';
                productStop.hidden = false;

            } catch (error) {
                productMessage.textContent =
                    'Could not open camera. Check browser permissions and HTTPS.';
                productScanner = null;
            }
        });
    }

    async function stopProductScanner() {
        if (productScanner) {
            try {
                await productScanner.stop();
                productScanner.clear();
            } catch (error) {
                console.error('Scanner stop error:', error);
            }

            productScanner = null;
        }

        if (productStop) productStop.hidden = true;
    }

    if (productStop) {
        productStop.addEventListener('click', async () => {
            await stopProductScanner();
            productMessage.textContent = 'Camera stopped.';
        });
    }

    // EXISTING QUICK SCAN CAMERA
    const start = document.getElementById('startScanner');
    const stop = document.getElementById('stopScanner');

    let scanner = null;

    if (start) {
        start.addEventListener('click', async () => {
            const msg = document.getElementById('scanMessage');

            if (!window.Html5Qrcode) {
                msg.textContent = 'Camera library unavailable.';
                return;
            }

            try {
                if (scanner) return;

                scanner = new Html5Qrcode('scanner-reader');

                await scanner.start(
                    { facingMode: 'environment' },
                    { fps: 10, qrbox: { width: 240, height: 140 } },
                    async text => {
                        const input = document.getElementById('scanBarcode');

                        if (input) {
                            input.value = text;
                            stop.click();
                            scanButton?.click();
                        }
                    },
                    () => {}
                );

                msg.textContent = 'Camera scanner active.';

            } catch (error) {
                msg.textContent = 'Camera could not start. Check browser permissions.';
                scanner = null;
            }
        });
    }

    if (stop) {
        stop.addEventListener('click', async () => {
            if (scanner) {
                try {
                    await scanner.stop();
                    scanner.clear();
                } catch (error) {}

                scanner = null;
            }

            const msg = document.getElementById('scanMessage');
            if (msg) msg.textContent = 'Camera scanner stopped.';
        });
    }

});
