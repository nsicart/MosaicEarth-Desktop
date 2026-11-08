(function () {
    'use strict';
    const panel = () => document.getElementById('notificationsDiv');
    const trigger = () => document.querySelector('[aria-controls="notificationsDiv"]');
    async function call(method, data = {}) {
        const response = await fetch('/Funciones.aspx/' + method, {
            method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
        });
        if (!response.ok) throw new Error('Notifications could not be updated. Please try again.');
        let value = (await response.json()).d;
        if (typeof value === 'string' && /^[\[{]/.test(value)) value = JSON.parse(value);
        return value;
    }
    function message(text, error) {
        const host = document.getElementById('notificacionesLoad');
        host.replaceChildren();host.style.display = '';
        const node = document.createElement('p');node.className = 'notifications-empty';
        node.textContent = text;if (error) node.setAttribute('role', 'alert');host.appendChild(node);
    }
    function badge(unread) {
        const dot = document.getElementById('greenDot');if (dot) dot.style.display = unread ? 'block' : 'none';
    }
    function target(row) {
        const url = row.SEO_URL_FRIENDLY || ({2:'/Client/RequestDetail.aspx?id='+Number(row.LINK_ID),3:'/en/Profile',4:'/Admin/Publications.aspx'})[row.TYPE_ID] || '/en/Projects';
        return url.startsWith('/') && !url.startsWith('//') && !url.includes('\\') ? url : '/en/Projects';
    }
    async function load() {
        const rows = await call('loadNotifications');const host = document.getElementById('notificacionesLoad');
        host.replaceChildren();host.style.display = '';badge(rows.length > 0);
        if (!rows.length) return message('You are up to date. Processing, publication, storage and credit updates will appear here.');
        for (const row of rows) {
            const card = document.createElement('article');card.className = 'notisdes';card.style.cssText = 'padding:14px 24px;display:flex;gap:12px;align-items:start';
            const link = document.createElement('a');link.href = target(row);link.style.cssText = 'color:#eef3ef;flex:1;min-width:0;overflow-wrap:anywhere';
            const title = document.createElement('strong');title.textContent = row.TITLE;
            const detail = document.createElement('p');detail.textContent = row.DESCRIPTION;detail.style.cssText = 'color:#c2ccc6;font-size:13px;margin:6px 0';
            link.append(title,detail);
            link.addEventListener('click', async event => {event.preventDefault();try {await call('setLeido',{id:row.ID});location.assign(link.href);} catch(e) {message(e.message,true);}});
            const dismiss = document.createElement('button');dismiss.type = 'button';dismiss.className = 'notification-toggle';dismiss.textContent = '✓';dismiss.setAttribute('aria-label','Mark as read: '+row.TITLE);
            dismiss.addEventListener('click',async()=>{try {await call('setLeido',{id:row.ID});await load();} catch(e) {message(e.message,true);}});
            card.append(link,dismiss);host.appendChild(card);
        }
    }
    window.mostrarNotificaciones = async function () {
        const box = panel();if (!box) return;if (!box.inert) return window.closeNotifications();
        box.inert = false;box.style.left = '0px';trigger()?.setAttribute('aria-expanded','true');box.querySelector('button')?.focus();
        message('Loading notifications…');try {await load();} catch(e) {message(e.message,true);}
    };
    window.closeNotifications = function () {
        const box = panel();if (!box) return;box.inert = true;box.style.left = '-400px';trigger()?.setAttribute('aria-expanded','false');trigger()?.focus();
    };
    window.setLeidoall = async function () {
        try {await call('setLeidoAll');await load();} catch(e) {message(e.message,true);}
    };
})();
