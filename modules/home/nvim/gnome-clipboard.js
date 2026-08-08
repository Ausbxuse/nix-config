#!/usr/bin/env gjs

imports.gi.versions.Gdk = '3.0'
imports.gi.versions.Gtk = '3.0'

const ByteArray = imports.byteArray
const {Gdk, Gio, GLib, Gtk} = imports.gi
const System = imports.system

function fail(message) {
  printerr(message)
  return 1
}

function getClipboard(usePrimary) {
  const display = Gdk.Display.get_default()
  if (!display) {
    throw new Error('No GDK display available')
  }

  const selection = usePrimary ? 'PRIMARY' : 'CLIPBOARD'
  return Gtk.Clipboard.get_for_display(display, selection)
}

function readStdin() {
  const [, data] = GLib.file_get_contents('/dev/stdin')
  return ByteArray.toString(data)
}

function writeStdout(text) {
  // g_file_set_contents() writes a sibling temp file and renames it, which
  // cannot work when stdout is a pipe (Neovim reads this over a pipe) - it
  // fails with "Failed to create file /dev/stdout.XXXXXX: Permission denied".
  // Write straight to fd 1 instead.
  const stream = Gio.UnixOutputStream.new(1, false)
  stream.write_all(new TextEncoder().encode(text), null)
  stream.flush(null)
}

function keepClipboardOwnerAlive() {
  const loop = new GLib.MainLoop(null, false)
  for (const signal of [1, 2, 15]) {
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal, () => {
      loop.quit()
      return GLib.SOURCE_REMOVE
    })
  }
  loop.run()
}

function main(argv) {
  let usePrimary = false
  const args = []

  for (const arg of argv) {
    if (arg === '--primary') {
      usePrimary = true
    } else {
      args.push(arg)
    }
  }

  if (args.length !== 1 || (args[0] !== 'copy' && args[0] !== 'paste')) {
    return fail('Usage: nvim-gnome-clipboard [--primary] <copy|paste>')
  }

  Gtk.init(null)

  const clipboard = getClipboard(usePrimary)
  if (args[0] === 'copy') {
    clipboard.set_text(readStdin(), -1)
    keepClipboardOwnerAlive()
    return 0
  }

  writeStdout(clipboard.wait_for_text() || '')
  return 0
}

System.exit(main(ARGV))
