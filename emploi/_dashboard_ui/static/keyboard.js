// Keyboard shortcuts for Emploi Dashboard
(function() {
    let selectedIndex = -1;
    const cards = () => document.querySelectorAll('.offer-card');

    function updateSelection() {
        cards().forEach((c, i) => c.style.outline = i === selectedIndex ? '2px solid #3b82f6' : '');
    }

    document.addEventListener('keydown', function(e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

        const items = cards();
        switch(e.key) {
            case 'j':
                selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
                updateSelection();
                if (items[selectedIndex]) items[selectedIndex].scrollIntoView({block: 'nearest'});
                break;
            case 'k':
                selectedIndex = Math.max(selectedIndex - 1, 0);
                updateSelection();
                if (items[selectedIndex]) items[selectedIndex].scrollIntoView({block: 'nearest'});
                break;
            case 'Enter':
                if (selectedIndex >= 0 && items[selectedIndex]) {
                    const link = items[selectedIndex].querySelector('a');
                    if (link) window.open(link.href, '_blank');
                }
                break;
            case '/':
                e.preventDefault();
                document.querySelector('input[name="q"]')?.focus();
                break;
            case 'Escape':
                document.querySelectorAll('.modal').forEach(m => m.style.display = 'none');
                break;
            case '?':
                document.getElementById('shortcuts-modal')?.classList.toggle('hidden');
                break;
        }
    });
})();
