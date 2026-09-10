-- -- Adds custom words to lsp
-- local words = {}
-- local spellfile = vim.fn.stdpath 'config' .. '/spell/en.utf-8.add'
-- local file = io.open(spellfile, 'r')
-- if file then
--   for line in file:lines() do
--     table.insert(words, line)
--   end
--   file:close()
-- else
--   print('Error: Unable to open spell file at ' .. spellfile)
-- end
--
-- -- local lspconfig_defaults = require('lspconfig').util.default_config
-- -- lspconfig_defaults.capabilities = require('blink.cmp').get_lsp_capabilities(lspconfig_defaults.capabilities)
--
-- vim.api.nvim_create_autocmd('LspAttach', {
--   callback = function(ev)
--     local client = vim.lsp.get_client_by_id(ev.data.client_id)
--     if client:supports_method 'textDocument/completion' then
--       vim.lsp.completion.enable(true, client.id, ev.buf, { autotrigger = true })
--     end
--   end,
-- })

vim.api.nvim_create_user_command('LspInfo', function()
  vim.cmd 'silent checkhealth vim.lsp'
end, {
  desc = 'Get all the information about all LSP attached',
})

local function with_fzf(method)
  return function()
    local ok, fzf = pcall(require, 'fzf-lua')
    if not ok then
      vim.notify('fzf-lua is not available yet', vim.log.levels.WARN)
      return
    end

    return fzf[method]()
  end
end

vim.api.nvim_create_autocmd('LspAttach', {
  desc = 'LSP actions',
  callback = function(event)
    local bufnr = event.buf
    local client = vim.lsp.get_client_by_id(event.data.client_id)
    if not client then
      return
    end
    ---@diagnostic disable-next-line need-check-nil
    if client.server_capabilities.completionProvider then
      vim.bo[bufnr].omnifunc = 'v:lua.vim.lsp.omnifunc'
      -- vim.bo[bufnr].omnifunc = "v:lua.MiniCompletion.completefunc_lsp"
    end
    ---@diagnostic disable-next-line need-check-nil
    if client.server_capabilities.definitionProvider then
      vim.bo[bufnr].tagfunc = 'v:lua.vim.lsp.tagfunc'
    end

    --- Disable semantic tokens
    ---@diagnostic disable-next-line need-check-nil
    client.server_capabilities.semanticTokensProvider = nil
    local map = function(keys, func, desc, mode)
      mode = mode or 'n'
      vim.keymap.set(mode, keys, func, { buffer = event.buf, desc = 'LSP: ' .. desc })
    end

    map('gd', vim.lsp.buf.definition, '[G]oto [D]efinition')
    map('gD', vim.lsp.buf.declaration, '[G]oto [D]eclaration')
    map('<leader>ld', with_fzf 'lsp_typedefs', 'Type [D]efinition')
    map('<leader>lS', with_fzf 'lsp_live_workspace_symbols', '[W]orkspace [S]ymbols')
    vim.keymap.set('n', '<leader>lq', vim.diagnostic.setloclist, { desc = 'Open diagnostic Quickfix [l]ist' })
    vim.keymap.set('n', 'K', function()
      vim.lsp.buf.hover { border = 'none', max_width = 60, max_height = 40 }
    end, { desc = 'LSP Hover' })

    vim.keymap.set('i', '<C-k>', function()
      vim.lsp.buf.signature_help { border = 'none', max_width = 60, max_height = 40 }
    end, { desc = 'LSP Signature Help' })

    -- stylua: ignore start
    local keymap = vim.keymap.set
    local lsp = vim.lsp
    local opts = { silent = true }
    local function opt(desc, others)
      return vim.tbl_extend("force", opts, { desc = desc }, others or {})
    end
    keymap("n", "gd", lsp.buf.definition, opt("Go to definition"))
    keymap("n", "gD", function()
      local ok, diag = pcall(require, "rj.extras.definition")
      if ok then
        diag.get_def()
      end
    end, opt("Get the definition in a float"))
    keymap("n", "gi", function() lsp.buf.implementation({ border = "none" })  end, opt("Go to implementation"))
    -- keymap("n", "gr", lsp.buf.references, opt("Show References"))
    keymap("n", "gl", vim.diagnostic.open_float, opt("Open diagnostic in float"))
    keymap("n", "<Leader>lF", vim.cmd.FormatToggle, opt("Toggle AutoFormat"))
    keymap("n", "<Leader>la", lsp.buf.code_action, opt("Code Action"))
    keymap("n", "<Leader>lh", function() lsp.inlay_hint.enable(not lsp.inlay_hint.is_enabled({})) end, opt("Toggle Inlayhints"))
    keymap("n", "<Leader>ll", lsp.codelens.run, opt("Run CodeLens"))
    keymap("n", "<Leader>ls", lsp.buf.document_symbol, opt("Doument Symbols"))
    -- stylua: ignore end

    if client and client:supports_method(vim.lsp.protocol.Methods.textDocument_documentHighlight) then
      local highlight_augroup = vim.api.nvim_create_augroup('kickstart-lsp-highlight', { clear = false })
      vim.api.nvim_create_autocmd({ 'CursorHold', 'CursorHoldI' }, {
        buffer = event.buf,
        group = highlight_augroup,
        callback = vim.lsp.buf.document_highlight,
      })

      vim.api.nvim_create_autocmd({ 'CursorMoved', 'CursorMovedI' }, {
        buffer = event.buf,
        group = highlight_augroup,
        callback = vim.lsp.buf.clear_references,
      })

      vim.api.nvim_create_autocmd('LspDetach', {
        group = vim.api.nvim_create_augroup('kickstart-lsp-detach', { clear = true }),
        callback = function(event2)
          vim.lsp.buf.clear_references()
          vim.api.nvim_clear_autocmds { group = 'kickstart-lsp-highlight', buffer = event2.buf }
        end,
      })
    end

    if client and client:supports_method(vim.lsp.protocol.Methods.textDocument_inlayHint) then
      map('<leader>lh', function()
        vim.lsp.inlay_hint.enable(not vim.lsp.inlay_hint.is_enabled { bufnr = event.buf })
      end, 'Inlay [H]ints')
    end
  end,
})

-- Diagnostics {{{
vim.diagnostic.config {
  float = {
    focusable = false,
    style = 'minimal',
    border = 'none',
    source = 'always',
    header = '',
    prefix = '',
    suffix = '',
  },
  underline = true,
  update_in_insert = false,

  -- virtual_lines = {
  --   -- Only show virtual line diagnostics for the current cursor line
  --   current_line = true,
  -- },
  virtual_text = {
    spacing = 4,
    source = 'if_many',
    prefix = '●',
  },
  signs = {
    active = true, -- turn _on_ your custom signs
    text = { -- here are your icons
      [vim.diagnostic.severity.ERROR] = '',
      [vim.diagnostic.severity.WARN] = '',
      [vim.diagnostic.severity.INFO] = '',
      [vim.diagnostic.severity.HINT] = '',
    },
    texthl = { -- make sure the highlight groups line up, too
      [vim.diagnostic.severity.ERROR] = 'DiagnosticError',
      [vim.diagnostic.severity.WARN] = 'DiagnosticWarn',
      [vim.diagnostic.severity.INFO] = 'DiagnosticInfo',
      [vim.diagnostic.severity.HINT] = 'DiagnosticHint',
    },
    numhl = {
      [vim.diagnostic.severity.WARN] = 'DiagnosticWarn',
      [vim.diagnostic.severity.ERROR] = 'DiagnosticError',
      [vim.diagnostic.severity.INFO] = 'DiagnosticInfo',
      [vim.diagnostic.severity.HINT] = 'DiagnosticHint',
    },
  },
  -- signs = true,
  severity_sort = true,
}
-- }}}

-- }}}

-- Servers {{{

-- Lua {{{
vim.lsp.config.lua_ls = {
  cmd = { 'lua-language-server' },
  filetypes = { 'lua' },
  root_markers = { '.luarc.json', '.git', vim.uv.cwd() },
  settings = {
    Lua = {
      telemetry = {
        enable = false,
      },
    },
  },
}
vim.lsp.enable 'lua_ls'
-- }}}

-- C/C++ {{{
vim.lsp.config.clangd = {
  cmd = {
    'clangd',
    '-j=' .. 2,
    '--background-index',
    '--clang-tidy',
    '--inlay-hints',
    '--fallback-style=llvm',
    '--all-scopes-completion',
    '--completion-style=detailed',
    '--header-insertion=iwyu',
    '--header-insertion-decorators',
    '--pch-storage=memory',
  },
  filetypes = { 'c', 'cpp', 'objc', 'objcpp', 'cuda', 'proto' },
  root_markers = {
    'CMakeLists.txt',
    '.clangd',
    '.clang-tidy',
    '.clang-format',
    'compile_commands.json',
    'compile_flags.txt',
    'configure.ac',
    '.git',
    vim.uv.cwd(),
  },
}
vim.lsp.enable 'clangd'
-- }}}

-- Bash {{{
vim.lsp.config.bashls = {
  cmd = { 'bash-language-server', 'start' },
  filetypes = { 'bash', 'sh', 'zsh' },
  root_markers = { '.git', vim.uv.cwd() },
  settings = {
    bashIde = {
      globPattern = vim.env.GLOB_PATTERN or '*@(.sh|.inc|.bash|.command)',
    },
  },
}
vim.lsp.enable 'bashls'
-- }}}

-- Web-dev {{{
-- TSServer {{{
-- vim.lsp.config.ts_ls = {
--   cmd = { 'typescript-language-server', '--stdio' },
--   filetypes = { 'javascript', 'javascriptreact', 'javascript.jsx', 'typescript', 'typescriptreact', 'typescript.tsx' },
--   root_markers = { 'tsconfig.json', 'jsconfig.json', 'package.json', '.git' },
--
--   init_options = {
--     hostInfo = 'neovim',
--   },
-- }
-- }}}

-- CSSls {{{
vim.lsp.config.cssls = {
  cmd = { 'vscode-css-language-server', '--stdio' },
  filetypes = { 'css', 'scss' },
  root_markers = { 'package.json', '.git' },
  init_options = {
    provideFormatter = true,
  },
}
-- }}}

-- HTML {{{
vim.lsp.config.html = {
  cmd = { 'vscode-html-language-server', '--stdio' },
  filetypes = { 'html' },
  root_markers = { 'package.json', '.git', vim.uv.cwd() },
  init_options = {
    provideFormatter = true,
  },
}
-- }}}

-- TailwindCss {{{
vim.lsp.config.tailwindcssls = {
  cmd = { 'tailwindcss-language-server', '--stdio' },
  filetypes = {
    'ejs',
    'html',
    'css',
    'scss',
    'javascript',
    'javascriptreact',
    'typescript',
    'typescriptreact',
  },
  root_markers = {
    'tailwind.config.js',
    'tailwind.config.cjs',
    'tailwind.config.mjs',
    'tailwind.config.ts',
    'postcss.config.js',
    'postcss.config.cjs',
    'postcss.config.mjs',
    'postcss.config.ts',
    'package.json',
    'node_modules',
  },
  settings = {
    tailwindCSS = {
      classAttributes = { 'class', 'className', 'class:list', 'classList', 'ngClass' },
      includeLanguages = {
        eelixir = 'html-eex',
        eruby = 'erb',
        htmlangular = 'html',
        templ = 'html',
      },
      lint = {
        cssConflict = 'warning',
        invalidApply = 'error',
        invalidConfigPath = 'error',
        invalidScreen = 'error',
        invalidTailwindDirective = 'error',
        invalidVariant = 'error',
        recommendedVariantOrder = 'warning',
      },
      validate = true,
    },
  },
}
-- }}}

-- }}}

vim.lsp.enable { 'cssls', 'html', 'tailwindcssls' }

-- }}}

-- }}}

vim.lsp.config.basedpyright = {
  cmd = { 'basedpyright-langserver', '--stdio' },
  filetypes = { 'python' },
  root_markers = { 'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile', 'pyrightconfig.json', '.git' },
  settings = {
    python = {
      pythonPath = vim.fn.getcwd() .. '/.venv/bin/python',
    },
    basedpyright = {
      analysis = {
        autoSearchPaths = true,
        autoImportCompletions = true,
        useLibraryCodeForTypes = true,
        diagnosticMode = 'openFilesOnly', -- or "workspace"
        typeCheckingMode = 'standard', -- "off" | "basic" | "standard" | "strict"
        inlayHints = {
          variableTypes = true,
          callArgumentNames = true,
          functionReturnTypes = true,
          genericTypes = false,
        },
      },
    },
  },
}

vim.lsp.enable 'basedpyright'

-- Markup and config files {{{
vim.lsp.config.jsonls = {
  cmd = { 'vscode-json-language-server', '--stdio' },
  filetypes = { 'json', 'jsonc' },
  root_markers = { 'package.json', '.git', vim.uv.cwd() },
  init_options = {
    provideFormatter = true,
  },
}

vim.lsp.config.yamlls = {
  cmd = { 'yaml-language-server', '--stdio' },
  filetypes = { 'yaml', 'yml' },
  root_markers = { '.git', vim.uv.cwd() },
  settings = {
    yaml = {
      keyOrdering = false,
    },
  },
}

vim.lsp.config.taplo = {
  cmd = { 'taplo', 'lsp', 'stdio' },
  filetypes = { 'toml' },
  root_markers = { 'taplo.toml', '.git', vim.uv.cwd() },
}

vim.lsp.config.marksman = {
  cmd = { 'marksman', 'server' },
  filetypes = { 'markdown', 'markdown.mdx' },
  root_markers = { '.marksman.toml', '.git', vim.uv.cwd() },
}

vim.lsp.enable { 'jsonls', 'yamlls', 'taplo', 'marksman' }
-- }}}

vim.lsp.config.typos = {
  cmd = { 'typos-lsp' },
  filetypes = { 'markdown', 'text', 'tex', 'plaintex', 'rst' },
  -- root_markers = { '.git', vim.uv.cwd() },
  settings = {
    typos = {
      language = 'en-US',
      -- dictionary = words,
      -- completion = {
      --   enabled = true,
      -- }
    },
  },
}
vim.lsp.enable 'typos'

vim.lsp.config.harper = {
  cmd = { 'harper-ls', '-s' },
  filetypes = { 'markdown', 'text', 'rst' },
}
vim.lsp.enable 'harper'

vim.lsp.config.nil_lsp = {
  cmd = { 'nil', '--stdio' },
  filetypes = { 'nix' },
}
vim.lsp.enable 'nil_lsp'
vim.lsp.enable 'copilot'
return {
  {
    'folke/lazydev.nvim',
    ft = 'lua',
    opts = {
      library = {
        -- Load luvit types when the `vim.uv` word is found
        { path = vim.env.NVIM_LUVIT_META_PATH or 'luvit-meta/library', words = { 'vim%.uv' } },
      },
    },
    config = function(_, opts)
      require('lazydev').setup(opts)
    end,
  },
}
