// Potato Corner — Global JS

$(document).ready(function () {

    // ── Cart count refresh ───────────────────────────────
    function refreshCartCount() {
        $.get('/api/cart-count', function (data) {
            $('.cart-count').text(data.count || 0);
        }).fail(function () { /* silently fail if not logged in */ });
    }

    // ── CSRF helper (Flask doesn't require it for JSON by default) ──
    function postJSON(url, data, success, error) {
        $.ajax({
            url: url,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(data),
            success: success,
            error: error || function (xhr) {
                console.error('Request failed:', xhr.responseText);
            }
        });
    }

    // ── Toast helper ─────────────────────────────────────
    function showToast(msg, type) {
        type = type || 'success';
        const colors = { success: 'bg-success', danger: 'bg-danger', warning: 'bg-warning text-dark', info: 'bg-info' };
        const toastEl = document.getElementById('cartToast');
        if (toastEl) {
            toastEl.className = `toast align-items-center text-white ${colors[type]} border-0`;
            $('#toastMsg').text(msg);
            new bootstrap.Toast(toastEl, { delay: 2500 }).show();
        }
    }

    // ── Auto-dismiss alerts after 4s ─────────────────────
    setTimeout(function () {
        $('.alert.fade.show').each(function () {
            const alert = bootstrap.Alert.getOrCreateInstance(this);
            if (alert) alert.close();
        });
    }, 4000);

    // ── Active nav link highlight ─────────────────────────
    const path = window.location.pathname;
    $('.nav-link').each(function () {
        const href = $(this).attr('href');
        if (href && path.startsWith(href) && href !== '/') {
            $(this).addClass('active').css('background', 'rgba(255,255,255,0.18)');
        }
    });

    // ── Expose globally for inline scripts ───────────────
    window.pcToast = showToast;
    window.pcPost  = postJSON;
});
