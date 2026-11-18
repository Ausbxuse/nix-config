// Which side of your line of sight (the primary monitor's centre) each Ghostty
// window is on, for tmux's reading-width.sh: it keeps the agent column toward
// that line instead of centring it on a monitor off to the side.
//
// Writes "<pid> <left|centre|right>" lines, most recently used window first,
// to $XDG_RUNTIME_DIR/terminal-side, and re-runs the layout when they change.
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const FILE = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'terminal-side']);
const RELAYOUT = GLib.build_filenamev([GLib.get_home_dir(), '.local/bin/tmux/reading-width.sh']);

export default class TerminalSide extends Extension {
    enable() {
        this._windows = new Map();
        this._last = null;
        this._created = global.display.connect('window-created', (_display, window) => this._track(window));
        this._monitors = global.backend.get_monitor_manager().connect('monitors-changed', () => this._queue());
        for (const actor of global.get_window_actors())
            this._track(actor.meta_window);
        this._queue();
    }

    disable() {
        global.display.disconnect(this._created);
        global.backend.get_monitor_manager().disconnect(this._monitors);
        for (const [window, ids] of this._windows)
            ids.forEach(id => window.disconnect(id));
        if (this._idle)
            GLib.source_remove(this._idle);
        this._windows = this._idle = this._last = null;
    }

    _track(window) {
        if (this._windows.has(window))
            return;
        this._windows.set(window, [
            window.connect('position-changed', () => this._queue()),
            window.connect('size-changed', () => this._queue()),
            window.connect('unmanaged', () => {
                this._windows.get(window)?.forEach(id => window.disconnect(id));
                this._windows.delete(window);
                this._queue();
            }),
        ]);
        this._queue();
    }

    // Moves and resizes come in bursts; settle first.
    _queue() {
        if (this._idle)
            GLib.source_remove(this._idle);
        this._idle = GLib.timeout_add(GLib.PRIORITY_LOW, 250, () => {
            this._idle = 0;
            this._write();
            return GLib.SOURCE_REMOVE;
        });
    }

    _write() {
        const primary = global.display.get_monitor_geometry(global.display.get_primary_monitor());
        const sight = primary.x + primary.width / 2;
        const lines = [...this._windows.keys()]
            .filter(window => /ghostty/i.test(window.get_wm_class() ?? ''))
            .sort((a, b) => b.get_user_time() - a.get_user_time())
            .map(window => {
                const rect = window.get_frame_rect();
                const side = rect.x >= sight ? 'right' : rect.x + rect.width <= sight ? 'left' : 'centre';
                return `${window.get_pid()} ${side}\n`;
            });
        const text = lines.join('');
        if (text === this._last)
            return;
        this._last = text;
        try {
            GLib.file_set_contents(FILE, text);
            GLib.spawn_async(null, [RELAYOUT], null, GLib.SpawnFlags.DEFAULT, null);
        } catch (_error) {}
    }
}
