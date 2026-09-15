local opt = vim.opt
local osc52 = require 'vim.ui.clipboard.osc52'
local term_program = vim.env.TERM_PROGRAM or ''
local current_desktop = vim.env.XDG_CURRENT_DESKTOP or ''
local clipboard_cache = {}
local gnome_clipboard_jobs = {}

local M = {}

function M.encode_clipboard(lines, regtype)
  local text = table.concat(lines, '\n')
  if regtype == 'V' then
    text = text .. '\n'
  end
  return text
end

function M.decode_clipboard(text)
  local regtype = 'v'
  if text:sub(-1) == '\n' then
    text = text:sub(1, -2)
    regtype = 'V'
  end
  return { vim.split(text, '\n', { plain = true }), regtype }
end

local function remember_clipboard(register, lines, regtype)
  local cached = {
    lines = vim.deepcopy(lines),
    regtype = regtype or 'v',
  }
  cached.text = M.encode_clipboard(cached.lines, cached.regtype)
  clipboard_cache[register] = cached
  return cached.text
end

local function read_clipboard(register, text)
  local cached = clipboard_cache[register]
  if cached and cached.text == text then
    return { vim.deepcopy(cached.lines), cached.regtype }
  end

  clipboard_cache[register] = nil
  return M.decode_clipboard(text)
end

local function stop_gnome_clipboard_job(register)
  local job = gnome_clipboard_jobs[register]
  if not job or job:is_closing() then
    gnome_clipboard_jobs[register] = nil
    return
  end

  job:kill 'sigterm'
  gnome_clipboard_jobs[register] = nil
end

local function gnome_copy(primary)
  return function(lines, regtype)
    local register = primary and '*' or '+'
    local text = remember_clipboard(register, lines, regtype)
    local cmd = { 'nvim-gnome-clipboard', 'copy' }
    if primary then
      table.insert(cmd, 2, '--primary')
    end

    stop_gnome_clipboard_job(register)
    gnome_clipboard_jobs[register] = vim.system(cmd, { stdin = text }, function()
      gnome_clipboard_jobs[register] = nil
    end)
  end
end

local gnome_paste_warned = false

local function gnome_paste(primary)
  return function()
    local cmd = { 'nvim-gnome-clipboard', 'paste' }
    if primary then
      table.insert(cmd, 2, '--primary')
    end

    local result = vim.system(cmd, { text = true }):wait()
    if result.code ~= 0 or result.stdout == nil then
      -- No wl-paste fallback on purpose: wl-clipboard is what triggers the
      -- Mutter permission popups this helper exists to avoid. Surface the
      -- failure once instead, so a broken helper is never again a silent `p`
      -- that pastes nothing.
      if not gnome_paste_warned then
        gnome_paste_warned = true
        vim.notify('nvim-gnome-clipboard paste failed: ' .. vim.trim(result.stderr or '(no stderr)'), vim.log.levels.ERROR)
      end
      return {}
    end

    return read_clipboard(primary and '*' or '+', result.stdout)
  end
end

local function wl_copy(primary)
  return function(lines, regtype)
    local register = primary and '*' or '+'
    local text = remember_clipboard(register, lines, regtype)
    local cmd = { 'wl-copy', '--type', 'text/plain' }
    if primary then
      table.insert(cmd, 2, '--primary')
    end

    vim.system(cmd, { stdin = text, detach = true }, function() end)
  end
end

local function wl_paste(primary)
  return function()
    local cmd = { 'wl-paste', '--no-newline' }
    if primary then
      table.insert(cmd, 2, '--primary')
    end

    local result = vim.system(cmd, { text = true }):wait()
    if result.code ~= 0 or not result.stdout then
      return {}
    end

    return read_clipboard(primary and '*' or '+', result.stdout)
  end
end

local function osc52_copy_with_cache(register)
  local copy = osc52.copy(register)
  return function(lines, regtype)
    local text = remember_clipboard(register, lines, regtype)
    copy(vim.split(text, '\n', { plain = true }))
  end
end

local function osc52_paste_from_cache(register)
  return function()
    local cached = clipboard_cache[register]
    if not cached then
      return { {}, 'v' }
    end

    return { vim.deepcopy(cached.lines), cached.regtype }
  end
end

local function osc52_clipboard(name)
  return {
    name = name,
    copy = {
      ['+'] = osc52_copy_with_cache '+',
      ['*'] = osc52_copy_with_cache '*',
    },
    paste = {
      ['+'] = osc52_paste_from_cache '+',
      ['*'] = osc52_paste_from_cache '*',
    },
    cache_enabled = 0,
  }
end

local function tmux_clipboard()
  local paste_plus = osc52_paste_from_cache '+'
  local paste_star = osc52_paste_from_cache '*'

  -- OSC 52 is write-only from Neovim's perspective.  Use the local desktop
  -- clipboard for reads when Neovim is running inside tmux, while retaining
  -- OSC 52 for writes so tmux can forward yanks to the terminal client.
  if current_desktop:match 'GNOME' and vim.fn.executable 'nvim-gnome-clipboard' == 1 then
    paste_plus = gnome_paste(false)
    paste_star = gnome_paste(true)
  elseif vim.fn.executable 'wl-paste' == 1 then
    paste_plus = wl_paste(false)
    paste_star = wl_paste(true)
  end

  return {
    name = 'tmux-osc52-desktop-paste',
    copy = {
      ['+'] = osc52_copy_with_cache '+',
      ['*'] = osc52_copy_with_cache '*',
    },
    paste = {
      ['+'] = paste_plus,
      ['*'] = paste_star,
    },
    cache_enabled = 0,
  }
end

vim.api.nvim_create_autocmd('VimLeavePre', {
  callback = function()
    stop_gnome_clipboard_job '+'
    stop_gnome_clipboard_job '*'
  end,
})

vim.g.mapleader = ' '
vim.g.maplocalleader = ' '

if vim.env.TMUX and vim.env.TMUX ~= '' then
  -- Send OSC 52 from the pane so tmux forwards the yank to every client
  -- displaying it. Paste through the local desktop clipboard because OSC 52
  -- cannot read the system clipboard back into Neovim.
  vim.g.clipboard = tmux_clipboard()
elseif current_desktop:match 'GNOME' then
  vim.g.clipboard = {
    name = 'gnome-gtk',
    copy = {
      ['+'] = gnome_copy(false),
      ['*'] = gnome_copy(true),
    },
    paste = {
      ['+'] = gnome_paste(false),
      ['*'] = gnome_paste(true),
    },
    cache_enabled = 0,
  }
elseif term_program == 'ghostty' or term_program == 'WezTerm' then
  -- Avoid wl-clipboard popups on compositors like GNOME/Mutter by using terminal OSC 52.
  vim.g.clipboard = osc52_clipboard 'terminal-osc52-cache'
else
  vim.g.clipboard = {
    name = 'wayland-lua',
    copy = {
      ['+'] = wl_copy(false),
      ['*'] = wl_copy(true),
    },
    paste = {
      ['+'] = wl_paste(false),
      ['*'] = wl_paste(true),
    },
    cache_enabled = 0,
  }
end

local default_options = {
  clipboard = 'unnamedplus',
  statusline = ' %f %m %r %=%-13a %k %S %l:%L ',
  -- The configuration is immutable when deployed by Nix.  Learned spelling
  -- words are mutable state and therefore belong under XDG_STATE_HOME.
  spellfile = vim.fs.joinpath(vim.fn.stdpath 'state', 'spell', 'en.utf-8.add'),
  number = true,
  relativenumber = true,
  breakindent = true,
  undofile = true,
  ignorecase = true,
  smartcase = true,
  updatetime = 250,
  timeoutlen = 1000,
  splitright = true,
  splitbelow = true,
  list = true,
  listchars = { tab = '  ', trail = '·', nbsp = '␣' },
  cursorline = true,
  scrolloff = 10,
  fillchars = { eob = ' ', fold = ' ' },
  foldmethod = 'expr',
  foldexpr = 'v:lua.vim.treesitter.foldexpr()',
  foldtext = "v:lua.require'config.foldtext'.foldtext()",
  foldlevel = 999,
  hidden = true, -- required to keep multiple buffers and open multiple buffers
  pumheight = 10, -- pop up menu height
  showtabline = 0, -- always show tabs
  swapfile = false, -- creates a swapfile
  termguicolors = true, -- set term gui colors (most terminals support this)
  undodir = vim.fs.joinpath(vim.fn.stdpath 'cache', 'undo'), -- set an undo directory
  writebackup = false, -- if a file is being edited by another program (or was written to file while editing with another program), it is not allowed to be edited
  expandtab = true, -- convert tabs to spaces
  shiftwidth = 2, -- the number of spaces inserted for each indentation
  tabstop = 2, -- insert 2 spaces for a tab
  numberwidth = 2, -- set number column width to 2 {default 4}
  signcolumn = 'yes',
  -- statuscolumn = '%l%s',
  wrap = true, -- display long lines with wrap
  linebreak = true,
  spell = false,
  sidescrolloff = 8,
  pumblend = 10,
  winblend = 10, -- keep notify transparent
  colorcolumn = '', -- fixes indentline for now
  shada = "!,'10000,<50,s10,h,:10000",
  -- completeopt = { 'fuzzy', 'menu', 'menuone', 'noselect' },
  -- omnifunc = '',
  -- completefunc = '',
}

opt.shortmess:append 'c'
opt.iskeyword:append '-'

for k, v in pairs(default_options) do
  vim.opt[k] = v
end

return M
