// Agent Hub states as a row of dots in the top bar. Polls the hub's compact
// /api/status endpoint and only touches actors when a state changes.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Soup from 'gi://Soup';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const HUB = 'http://127.0.0.1:8765';
// Seconds between polls; slower while the hub is unreachable.
const POLL = 2;
const RETRY = 15;
const LABELS = {running: 'Working', waiting: 'Needs you', blocked: 'Blocked', done: 'Ready for review',
    idle: 'Idle', recovering: 'Recovering', exited: 'Exited', offline: 'Host offline', unknown: 'Checking'};
const tone = state => ({recovering: 'running', exited: 'offline'})[state] ?? (state in LABELS ? state : 'idle');

// Activities, these dots, the Qbing companion, then everything else. The
// window title's width changes with every window switch, and anything after it
// slides under a resting pointer and leaves a stale hover highlight. The
// companion extension enforces the same order, so either may run first.
function pinOrder() {
    const left = Main.panel._leftBox;
    ['activities', 'agent-dots@local', 'cstimer-companion@local']
        .map(name => Main.panel.statusArea[name]?.container)
        .filter(actor => actor?.get_parent() === left)
        .forEach((actor, i) => {
            if (left.get_child_at_index(i) !== actor) left.set_child_at_index(actor, i);
        });
}
export default class AgentDots extends Extension {
    enable() {
        this._agents = [];
        this._signature = null;
        this._session = new Soup.Session({timeout: 5});
        this._cancel = new Gio.Cancellable();
        this._button = new PanelMenu.Button(0.0, 'Agent dots', false);
        this._dots = new St.BoxLayout({style_class: 'agent-dots', y_align: Clutter.ActorAlign.CENTER});
        this._button.add_child(this._dots);
        this._button.visible = false;
        // The agent list is built only when the menu opens.
        this._list = new PopupMenu.PopupMenuSection();
        this._button.menu.addMenuItem(this._list);
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._button.menu.addAction('Open Agent Hub', () => Gio.AppInfo.launch_default_for_uri(`${HUB}/`, null));
        this._button.menu.connect('open-state-changed', (_menu, open) => { if (open) this._fillMenu(); });
        Main.panel.addToStatusArea(this.uuid, this._button, 0, 'left');
        pinOrder();
        // Unite adds its window title at index 1 whenever it enables, which
        // can be after us; restore the order once the addition settles.
        this._leftSignal = Main.panel._leftBox.connect('child-added', () => {
            this._pinIdle ||= GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                this._pinIdle = 0;
                pinOrder();
                return GLib.SOURCE_REMOVE;
            });
        });
        this._poll();
    }
    disable() {
        this._cancel.cancel();
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = 0;
        Main.panel._leftBox.disconnect(this._leftSignal);
        if (this._pinIdle) GLib.source_remove(this._pinIdle);
        this._leftSignal = this._pinIdle = 0;
        this._session.abort();
        this._button.destroy();
        this._button = this._dots = this._list = this._session = this._cancel = this._agents = null;
    }
    _schedule(seconds) {
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, seconds, () => {
            this._timer = 0;
            this._poll();
            return GLib.SOURCE_REMOVE;
        });
    }
    _poll() {
        const message = Soup.Message.new('GET', `${HUB}/api/status`);
        this._session.send_and_read_async(message, GLib.PRIORITY_LOW, this._cancel, (session, result) => {
            if (this._cancel.is_cancelled()) return;
            let agents = null;
            try {
                const bytes = session.send_and_read_finish(result);
                if (message.get_status() === Soup.Status.OK)
                    agents = JSON.parse(new TextDecoder().decode(bytes.get_data())).agents;
            } catch (_error) {}
            this._render(agents ?? []);
            this._schedule(agents ? POLL : RETRY);
        });
    }
    _render(agents) {
        this._agents = agents;
        const signature = agents.map(agent => tone(agent.state)).join(' ');
        if (signature === this._signature) return;
        this._signature = signature;
        const dots = this._dots.get_children();
        while (dots.length > agents.length) dots.pop().destroy();
        agents.forEach((agent, i) => {
            let dot = dots[i];
            if (!dot) {
                dot = new St.Widget({y_align: Clutter.ActorAlign.CENTER});
                this._dots.add_child(dot);
            }
            dot.style_class = `agent-dot ${tone(agent.state)}`;
        });
        this._button.visible = agents.length > 0;
    }
    _fillMenu() {
        const menu = this._list;
        menu.removeAll();
        const hosts = new Set(this._agents.map(agent => agent.host)).size > 1;
        for (const agent of this._agents) {
            const item = new PopupMenu.PopupBaseMenuItem({reactive: false, style_class: 'agent-dots-item'});
            item.add_child(new St.Widget({style_class: `agent-dot ${tone(agent.state)}`, y_align: Clutter.ActorAlign.CENTER}));
            item.add_child(new St.Label({text: agent.title, x_expand: true, y_align: Clutter.ActorAlign.CENTER}));
            const detail = `${LABELS[agent.state] ?? agent.state}${hosts ? ` · ${agent.host}` : ''}`;
            item.add_child(new St.Label({text: detail, style_class: 'agent-dots-host', y_align: Clutter.ActorAlign.CENTER}));
            menu.addMenuItem(item);
        }
    }
}
