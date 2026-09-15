local M = {}
local nix_packaged = vim.env.NVIM_NIX_PACKAGED == '1'

if nix_packaged then
  -- Home Manager's pack remains discoverable through 'packpath'. Exclude the
  -- mutable data-site root from 'runtimepath' so parsers, queries, or plugin
  -- files left by the former first-run installer cannot shadow Nix inputs.
  local data_site = vim.fs.joinpath(vim.fn.stdpath 'data', 'site')
  vim.opt.runtimepath:remove(data_site)
  vim.opt.runtimepath:remove(vim.fs.joinpath(data_site, 'after'))
  vim.g.nix_config_plugins_declarative = true
end

local function is_list(value)
  return type(value) == 'table' and vim.islist(value)
end

local function to_list(value)
  if value == nil then
    return {}
  end
  return is_list(value) and value or { value }
end

local function repo_to_url(repo)
  if repo:match '^[%w_.-]+/[%w_.-]+$' then
    return 'https://github.com/' .. repo
  end
  return repo
end

local function basename(path)
  return vim.fs.basename(path):gsub('%.git$', '')
end

local function plugin_name(spec)
  if spec.name then
    return spec.name
  end

  if spec.dir then
    return basename(spec.dir)
  end

  local src = spec.src or spec[1]
  return basename(src)
end

local function to_pack_spec(spec)
  local src = spec.src or spec[1]
  local pack_spec = {
    src = repo_to_url(src),
    name = plugin_name(spec),
  }

  if spec.branch then
    pack_spec.version = spec.branch
  elseif spec.tag then
    pack_spec.version = spec.tag
  elseif type(spec.version) == 'string' then
    if spec.version == '*' then
      pack_spec.version = nil
    elseif spec.version:find('%*', 1, true) then
      pack_spec.version = vim.version.range(spec.version)
    else
      pack_spec.version = spec.version
    end
  end

  return pack_spec
end

local function is_plugin_spec(spec)
  return type(spec) == 'table' and (type(spec[1]) == 'string' or spec.src ~= nil or spec.dir ~= nil)
end

local function flatten(specs, acc)
  for _, spec in ipairs(specs) do
    if type(spec) == 'string' then
      local normalized = { spec }
      table.insert(acc, { spec = normalized, source = normalized })
    elseif spec.enabled == false then
      goto continue
    elseif is_plugin_spec(spec) then
      table.insert(acc, { spec = spec, source = spec })
      if spec.dependencies then
        flatten(spec.dependencies, acc)
      end
    elseif is_list(spec) then
      flatten(spec, acc)
    end
    ::continue::
  end
end

local function run_build(build, name)
  if type(build) == 'string' then
    local command = build:gsub('^:', '')
    if vim.fn.exists(':' .. command:match '^%S+') == 2 then
      vim.cmd(command)
    end
    return
  end

  if type(build) == 'function' then
    build()
    return
  end

  vim.notify('Unsupported build hook for ' .. name, vim.log.levels.WARN)
end

local function is_lazy_spec(spec)
  if spec.lazy == false then
    return false
  end

  return spec.event ~= nil or spec.ft ~= nil or spec.cmd ~= nil or spec.keys ~= nil
end

local function nix_plugin_location(name)
  for _, root in ipairs(vim.opt.packpath:get()) do
    local hm_dir = vim.fs.joinpath(root, 'pack', 'hm')
    local real_hm_dir = vim.uv.fs_realpath(hm_dir)
    if real_hm_dir and vim.startswith(real_hm_dir, '/nix/store/') then
      for _, kind in ipairs { 'opt', 'start' } do
        if vim.uv.fs_stat(vim.fs.joinpath(hm_dir, kind, name)) then
          return {
            kind = kind,
            -- Use the store-backed pack root while loading so an old mutable
            -- pack with the same plugin name cannot also be sourced.
            root = vim.fs.dirname(vim.fs.dirname(real_hm_dir)),
          }
        end
      end
    end
  end
end

local function is_packaged(name)
  if nix_packaged then
    return nix_plugin_location(name) ~= nil
  end

  for _, kind in ipairs { 'start', 'opt' } do
    local pattern = 'pack/*/' .. kind .. '/' .. name
    if not vim.tbl_isempty(vim.fn.globpath(vim.o.packpath, pattern, false, true)) then
      return true
    end
  end
  return false
end

local function normalize_event(event)
  if event == 'VeryLazy' then
    return 'User'
  end
  return event
end

local function event_pattern(event)
  if event == 'VeryLazy' then
    return 'VeryLazy'
  end
  return nil
end

local function make_rhs_runner(rhs)
  if type(rhs) == 'function' then
    return rhs
  end

  local keys = vim.keycode(rhs)
  return function()
    vim.api.nvim_feedkeys(keys, 'm', false)
  end
end

function M.setup(spec_modules)
  if not vim.pack or not vim.pack.add then
    error 'This Neovim config now requires Neovim 0.12+ with vim.pack.'
  end

  local flattened = {}
  flatten(spec_modules, flattened)

  local builds = {}
  local seen_specs = {}
  local spec_by_name = {}
  local loaded = {}
  local configured = {}
  local registered = {}
  local lazy_names = {}

  local function packadd(name)
    if not nix_packaged then
      vim.cmd.packadd(name)
      return
    end

    local location = nix_plugin_location(name)
    if not location then
      error('Declarative Neovim plugin is missing from the Nix pack: ' .. name)
    end
    if location.kind == 'start' then
      return
    end

    local previous_packpath = vim.o.packpath
    vim.o.packpath = location.root
    local ok, err = pcall(vim.cmd.packadd, name)
    vim.o.packpath = previous_packpath
    if not ok then
      error(err)
    end

    -- `packadd` appends optional packages after Neovim's built-in runtime.
    -- Put the plugin itself first so its ftplugins run before the built-ins
    -- claim the buffer with `b:did_ftplugin`; retain its `after/` directory at
    -- the end of runtimepath. VimTeX otherwise never initializes for TeX files.
    local plugin_dir = vim.fs.joinpath(location.root, 'pack', 'hm', 'opt', name)
    vim.opt.runtimepath:remove(plugin_dir)
    vim.opt.runtimepath:prepend(plugin_dir)
  end

  local function ensure_registered(name)
    if registered[name] then
      return
    end

    local spec = spec_by_name[name]
    if not spec or spec.dir then
      return
    end

    if is_packaged(name) then
      registered[name] = true
      return
    end

    if nix_packaged then
      error('Declarative Neovim plugin is missing from the Nix pack: ' .. name)
    end

    vim.pack.add({ to_pack_spec(spec) }, {
      confirm = false,
      load = false,
    })
    registered[name] = true
  end

  local function load_plugin(name)
    local spec = spec_by_name[name]
    if not spec or loaded[name] then
      return
    end

    for _, dep in ipairs(to_list(spec.dependencies)) do
      if is_plugin_spec(dep) then
        load_plugin(plugin_name(dep))
      elseif type(dep) == 'string' then
        load_plugin(basename(dep))
      end
    end

    if spec.dir then
      if not vim.uv.fs_stat(spec.dir) then
        return
      end
      vim.opt.rtp:prepend(spec.dir)
    else
      ensure_registered(name)
      packadd(name)
    end

    loaded[name] = true

    if not configured[name] and spec.config then
      configured[name] = true
      spec.config(spec, spec.opts)
    else
      configured[name] = true
    end
  end

  local function register_key_loader(spec)
    for _, key in ipairs(to_list(spec.keys)) do
      if type(key) == 'table' and key[1] and key[2] then
        local opts = {}
        for opt_name, opt_value in pairs(key) do
          if type(opt_name) ~= 'number' and opt_name ~= 'ft' then
            opts[opt_name] = opt_value
          end
        end

        local mode = opts.mode or 'n'
        opts.mode = nil
        local lhs = key[1]
        local run_rhs = make_rhs_runner(key[2])

        vim.keymap.set(mode, lhs, function()
          load_plugin(plugin_name(spec))
          return run_rhs()
        end, opts)
      end
    end
  end

  local function register_cmd_loader(spec)
    for _, cmd in ipairs(to_list(spec.cmd)) do
      vim.api.nvim_create_user_command(cmd, function(ctx)
        pcall(vim.api.nvim_del_user_command, cmd)
        load_plugin(plugin_name(spec))
        local command = cmd
        if ctx.bang then
          command = command .. '!'
        end
        if ctx.args ~= '' then
          command = command .. ' ' .. ctx.args
        end
        vim.cmd(command)
      end, {
        nargs = '*',
        bang = true,
      })
    end
  end

  local function register_event_loader(spec)
    for _, event in ipairs(to_list(spec.event)) do
      local opts = {
        once = true,
        callback = function()
          load_plugin(plugin_name(spec))
        end,
      }
      local pattern = event_pattern(event)
      if pattern then
        opts.pattern = pattern
      end

      vim.api.nvim_create_autocmd(normalize_event(event), opts)
    end
  end

  local function register_ft_loader(spec)
    vim.api.nvim_create_autocmd('FileType', {
      pattern = to_list(spec.ft),
      once = false,
      callback = function()
        load_plugin(plugin_name(spec))
      end,
    })
  end

  for _, item in ipairs(flattened) do
    local spec = item.spec
    local name = plugin_name(spec)

    if spec.init then
      spec.init()
    end

    if item.source and not spec_by_name[name] then
      spec_by_name[name] = spec
    end

    if spec.build and not builds[name] then
      builds[name] = spec.build
    end

    if spec.dir then
      if not vim.uv.fs_stat(spec.dir) then
        vim.notify('Skipping missing local plugin: ' .. spec.dir, vim.log.levels.WARN)
      end
    elseif not seen_specs[name] then
      seen_specs[name] = true
    end

    lazy_names[name] = is_lazy_spec(spec)
  end

  vim.api.nvim_create_autocmd('PackChanged', {
    callback = function(ev)
      if ev.data.kind ~= 'install' and ev.data.kind ~= 'update' then
        return
      end

      local name = ev.data.spec.name
      local build = builds[name]
      if not build then
        return
      end

      if not ev.data.active then
        vim.cmd.packadd(name)
      end

      run_build(build, name)
    end,
  })

  vim.api.nvim_create_autocmd('VimEnter', {
    once = true,
    callback = function()
      vim.schedule(function()
        vim.api.nvim_exec_autocmds('User', { pattern = 'VeryLazy' })
      end)
    end,
  })

  for name, spec in pairs(spec_by_name) do
    -- Validate every enabled spec up front in declarative mode. A missing lazy
    -- plugin should fail during startup, not much later when its key or event
    -- first fires.
    if (nix_packaged or not lazy_names[name]) and not spec.dir then
      ensure_registered(name)
    end
  end

  if nix_packaged then
    vim.g.nix_config_plugins_validated = true
  end

  for name, spec in pairs(spec_by_name) do
    if lazy_names[name] then
      if spec.event then
        register_event_loader(spec)
      end
      if spec.ft then
        register_ft_loader(spec)
      end
      if spec.cmd then
        register_cmd_loader(spec)
      end
      if spec.keys then
        register_key_loader(spec)
      end
    else
      load_plugin(name)
    end
  end
end

return M
