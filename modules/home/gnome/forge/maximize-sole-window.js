    // nix-config: a window Forge sizes to a monitor's whole work area (alone
    // there, gaps hidden) is maximized instead, so GTK squares its corners
    // and drops its shadow. Leaving that size takes the usual path below,
    // which unmaximizes it again.
    const workspace = metaWindow.get_workspace();
    for (let i = 0; workspace && i < global.display.get_n_monitors(); i++) {
      const area = workspace.get_work_area_for_monitor(i);
      if (area.x !== rect.x || area.y !== rect.y || area.width !== rect.width || area.height !== rect.height)
        continue;
      if (metaWindow.is_fullscreen()) break;
      let maximized;
      try {
        maximized = metaWindow.is_maximized(); // GNOME 49+
      } catch (e) {
        maximized = metaWindow.get_maximized() === Meta.MaximizeFlags.BOTH;
      }
      if (maximized && metaWindow.get_monitor() === i) return;
      if (metaWindow.get_monitor() !== i) metaWindow.move_to_monitor(i);
      try {
        metaWindow.set_maximize_flags(Meta.MaximizeFlags.BOTH); // GNOME 49+
        metaWindow.maximize();
      } catch (e) {
        metaWindow.maximize(Meta.MaximizeFlags.BOTH);
      }
      return;
    }
