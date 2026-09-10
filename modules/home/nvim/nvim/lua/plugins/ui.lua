return {
  {
    'lukas-reineke/indent-blankline.nvim',
    event = { 'BufReadPre', 'BufNewFile' },
    main = 'ibl',
    ---@module "ibl"
    ---@type ibl.config
    opts = {},
    config = function()
      require('ibl').setup {
        indent = {
          char = '▏',
        },
        scope = {
          enabled = true,
          show_start = false,
          show_end = false,
          char = '▏',
        },
      }
    end,
  },
  -- lua/plugins/treesitter.lua (or wherever your lazy specs live)
  {
    {
      'nvim-treesitter/nvim-treesitter',
      branch = 'main',
      lazy = false, -- main branch: "does not support lazy-loading" :contentReference[oaicite:5]{index=5}
      build = ':TSUpdate',
      config = function()
        local ts = require 'nvim-treesitter'

        if vim.env.NVIM_NIX_PACKAGED == '1' then
          -- Home Manager puts the selected, precompiled parsers on the runtime
          -- path. Never create mutable parser state or contact GitHub here.
          ts.setup {}
        else
          -- Preserve the standalone vim.pack workflow for non-Nix installs.
          ts.setup {
            install_dir = vim.fn.stdpath 'data' .. '/site',
          }
          ts.install {
            'bash',
            'c',
            'diff',
            'typescript',
            'tsx',
            'html',
            'lua',
            'zsh',
            'luadoc',
            'markdown',
            'markdown_inline',
            'query',
            'vim',
            'vimdoc',
            'comment',
            'python',
            'cpp',
            'just',
            'nix',
            'tmux',
            'yaml',
          }
        end

        -- Enable TS highlighting (provided by Neovim) :contentReference[oaicite:8]{index=8}
        vim.api.nvim_create_autocmd('FileType', {
          pattern = {
            'typescript',
            'tsx',
            'bash',
            'sh',
            'zsh',
            'c',
            'diff',
            'html',
            'lua',
            'markdown',
            'vim',
            'python',
            'cpp',
            'nix',
            'tmux',
            'yaml',
            'just',
          },
          callback = function()
            vim.treesitter.start()
          end,
        })

        -- Optional: TS indentation (experimental; provided by nvim-treesitter main) :contentReference[oaicite:9]{index=9}
        vim.api.nvim_create_autocmd('FileType', {
          pattern = {
            'typescript',
            'tsx',
            'bash',
            'sh',
            'zsh',
            'c',
            'html',
            'lua',
            'python',
            'cpp',
            'nix',
            'yaml',
            'just',
            'vim',
            'tmux',
            'markdown',
          },
          callback = function()
            vim.bo.indentexpr = "v:lua.require'nvim-treesitter'.indentexpr()"
          end,
        })
      end,
    },

    {
      'nvim-treesitter/nvim-treesitter-textobjects',
      branch = 'main',
      lazy = false,
      init = function()
        -- From textobjects README main branch :contentReference[oaicite:10]{index=10}
        vim.g.no_plugin_maps = true
      end,
      config = function()
        -- Configure behavior (lookahead, set_jumps, etc.) :contentReference[oaicite:11]{index=11}
        require('nvim-treesitter-textobjects').setup {
          select = {
            lookahead = true,
            include_surrounding_whitespace = false,
          },
          move = {
            set_jumps = true,
          },
        }

        -- === SELECT keymaps (your mappings) ===
        local sel = require 'nvim-treesitter-textobjects.select'
        local function xomap(lhs, capture, group)
          vim.keymap.set({ 'x', 'o' }, lhs, function()
            sel.select_textobject(capture, group)
          end)
        end

        xomap('ah', '@block.outer', 'textobjects')
        xomap('ih', '@block.inner', 'textobjects')
        xomap('af', '@function.outer', 'textobjects')
        xomap('if', '@function.inner', 'textobjects')
        xomap('ac', '@class.outer', 'textobjects')
        xomap('ic', '@class.inner', 'textobjects')
        xomap('ai', '@conditional.outer', 'textobjects')
        xomap('ii', '@conditional.inner', 'textobjects')
        xomap('al', '@loop.outer', 'textobjects')
        xomap('il', '@loop.inner', 'textobjects')
        xomap('as', '@local.scope', 'locals') -- locals group example :contentReference[oaicite:12]{index=12}

        -- === MOVE keymaps (your mappings) ===
        local mv = require 'nvim-treesitter-textobjects.move'
        local function nxo(lhs, fn)
          vim.keymap.set({ 'n', 'x', 'o' }, lhs, fn)
        end

        nxo(']f', function()
          mv.goto_next_start('@function.outer', 'textobjects')
        end)
        nxo(']h', function()
          mv.goto_next_start('@block.outer', 'textobjects')
        end)
        nxo(']c', function()
          mv.goto_next_start('@class.outer', 'textobjects')
        end)
        nxo(']i', function()
          mv.goto_next_start('@conditional.outer', 'textobjects')
        end)
        nxo(']l', function()
          mv.goto_next_start({ '@loop.inner', '@loop.outer' }, 'textobjects')
        end)

        nxo(']F', function()
          mv.goto_next_end('@function.outer', 'textobjects')
        end)
        nxo(']H', function()
          mv.goto_next_end('@block.outer', 'textobjects')
        end)
        nxo(']C', function()
          mv.goto_next_end('@class.outer', 'textobjects')
        end)
        nxo(']I', function()
          mv.goto_next_end('@conditional.outer', 'textobjects')
        end)
        nxo(']L', function()
          mv.goto_next_end({ '@loop.inner', '@loop.outer' }, 'textobjects')
        end)

        nxo('[f', function()
          mv.goto_previous_start('@function.outer', 'textobjects')
        end)
        nxo('[h', function()
          mv.goto_previous_start('@block.outer', 'textobjects')
        end)
        nxo('[c', function()
          mv.goto_previous_start('@class.outer', 'textobjects')
        end)
        nxo('[i', function()
          mv.goto_previous_start('@conditional.outer', 'textobjects')
        end)
        nxo('[l', function()
          mv.goto_previous_start({ '@loop.inner', '@loop.outer' }, 'textobjects')
        end)

        nxo('[F', function()
          mv.goto_previous_end('@function.outer', 'textobjects')
        end)
        nxo('[H', function()
          mv.goto_previous_end('@block.outer', 'textobjects')
        end)
        nxo('[C', function()
          mv.goto_previous_end('@class.outer', 'textobjects')
        end)
        nxo('[I', function()
          mv.goto_previous_end('@conditional.outer', 'textobjects')
        end)
        nxo('[L', function()
          mv.goto_previous_end({ '@loop.inner', '@loop.outer' }, 'textobjects')
        end)
      end,
    },
  },
  {
    'catgoose/nvim-colorizer.lua',
    event = { 'BufReadPre', 'BufNewFile' },
    opts = {
      lazy_load = true,
      options = {
        parsers = {
          css = true,
          names = {
            enable = false,
          },
          hsl = {
            enable = false,
          },
          oklch = {
            enable = false,
          },
        },
      },
    },
    config = function(_, opts)
      require('colorizer').setup(opts)
    end,
  },
  {
    'nvim-treesitter/nvim-treesitter-context',
    event = { 'BufReadPre', 'BufNewFile' },
    config = function()
      require('treesitter-context').setup {
        enable = true, -- Enable this plugin (Can be enabled/disabled later via commands)
        multiwindow = false, -- Enable multiwindow support.
        max_lines = 2, -- How many lines the window should span. Values <= 0 mean no limit.
        min_window_height = 0, -- Minimum editor window height to enable context. Values <= 0 mean no limit.
        line_numbers = true,
        multiline_threshold = 20, -- Maximum number of lines to show for a single context
        trim_scope = 'inner', -- Which context lines to discard if `max_lines` is exceeded. Choices: 'inner', 'outer'
        mode = 'cursor', -- Line used to calculate context. Choices: 'cursor', 'topline'
        -- Separator between context and content. Should be a single character string, like '-'.
        -- When separator is set, the context will only show up when there are at least 2 lines above cursorline.
        separator = nil,
        zindex = 20, -- The Z-index of the context window
        on_attach = nil, -- (fun(buf: integer): boolean) return false to disable attaching
      }
    end,
  },
  {
    'Bekaboo/dropbar.nvim',
    config = function()
      local dropbar_api = require 'dropbar.api'
      vim.keymap.set('n', '<leader>;', dropbar_api.pick, { desc = 'Pick symbols in winbar' })
      vim.keymap.set('n', '[;', dropbar_api.goto_context_start, { desc = 'Go to start of current context' })
      vim.keymap.set('n', '];', dropbar_api.select_next_context, { desc = 'Select next context' })
    end,
  },
  {
    'ausbxuse/snappy.nvim',
    priority = 2000, -- Make sure to load this before all the other start plugins.
    config = function()
      local ok = pcall(vim.cmd.colorscheme, 'snappy')
      if not ok then
        vim.notify('snappy colorscheme is unavailable; skipping theme', vim.log.levels.WARN)
      end
    end,
  },
  { 'eandrju/cellular-automaton.nvim' },
  -- {
  --   'm4xshen/hardtime.nvim',
  --   lazy = false,
  --   opts = {},
  -- },
  {
    'serhez/bento.nvim',
    event = 'VeryLazy',
    opts = {
      main_keymap = '\\', -- Main toggle/expand key
    },
    config = function(_, opts)
      require('bento').setup(opts)
    end,
  },
}
