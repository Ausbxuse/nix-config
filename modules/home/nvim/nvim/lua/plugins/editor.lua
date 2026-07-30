return {
  ---@type LazySpec
  {
    'mikavilpas/yazi.nvim',
    event = 'VeryLazy',
    dependencies = {
      'nvim-lua/plenary.nvim',
    },
    keys = {
      {
        '<leader>N',
        '<cmd>Yazi<cr>',
        desc = 'Open yazi at the current file',
      },
      {
        '<leader>n',
        '<cmd>Yazi cwd<cr>',
        desc = "Open the file manager in nvim's working directory",
      },
    },
    ---@type YaziConfig
    opts = {
      open_for_directories = true,
      keymaps = {
        show_help = '<f1>',
      },
    },
    config = function(_, opts)
      require('yazi').setup(opts)
    end,
  },
  { 'mbbill/undotree' },
  {
    'kevinhwang91/nvim-bqf',
    ft = 'qf',
    opts = {
      auto_enable = true,
      preview = {
        auto_preview = false,
      },
    },
    config = function(_, opts)
      require('bqf').setup(opts)
    end,
  },
  {
    'oskarrrrrrr/symbols.nvim',
    keys = {
      {
        '<leader>s',
        '<cmd>SymbolsToggle<CR>',
        desc = 'Toggle symbols',
      },
    },
    config = function()
      local r = require 'symbols.recipes'
      require('symbols').setup(r.DefaultFilters, r.AsciiSymbols, {
        -- custom settings here
        -- e.g. hide_cursor = false
        show_details_pop_up = true,
        keymaps = {
          -- Jumps to symbol in the source window.
          ['l'] = 'goto-symbol',
        },
      })
    end,
  },
  {
    'olimorris/codecompanion.nvim',
    enabled = false,
    config = function()
      vim.cmd [[cab cc CodeCompanion]]
      vim.keymap.set('n', '<leader>cc', '<cmd>CodeCompanionChat<CR>')
      require('codecompanion').setup {
        strategies = {
          chat = {
            adapter = {
              name = 'gemini',
              model = 'gemini-2.5-flash',
            },
          },
          inline = {
            adapter = 'gemini',
          },
        },
      }
    end,
    dependencies = {
      'nvim-lua/plenary.nvim',
      'nvim-treesitter/nvim-treesitter',
    },
  },
  {
    'zbirenbaum/copilot.lua',
    cmd = 'Copilot',
    event = 'InsertEnter',
    config = function()
      require('copilot').setup {

        filetypes = {
          yaml = false, -- allow specific filetype
          -- typescript = true, -- allow specific filetype
          -- ["*"] = false, -- disable for all other filetypes and ignore default `filetypes`
        },
        suggestion = {
          enabled = false,
        },
        panel = { enabled = false },
      }

      vim.keymap.set('n', '<leader>c', '<cmd>Copilot toggle<CR>')
    end,
  },
  {
    'chomosuke/typst-preview.nvim',
    ft = 'typst',
    config = function()
      require('typst-preview').setup {
        -- Setting this true will enable logging debug information to
        -- `vim.fn.stdpath 'data' .. '/typst-preview/log.txt'`
        debug = false,

        -- Custom format string to open the output link provided with %s
        -- Example: open_cmd = 'firefox %s -P typst-preview --class typst-preview'
        open_cmd = nil,

        -- Custom port to open the preview server. Default is random.
        -- Example: port = 8000
        port = 0,

        -- Setting this to 'always' will invert black and white in the preview
        -- Setting this to 'auto' will invert depending if the browser has enable
        -- dark mode
        -- Setting this to '{"rest": "<option>","image": "<option>"}' will apply
        -- your choice of color inversion to images and everything else
        -- separately.
        invert_colors = 'never',

        -- Whether the preview will follow the cursor in the source file
        follow_cursor = true,

        -- Provide the path to binaries for dependencies.
        -- Setting this will skip the download of the binary by the plugin.
        -- Warning: Be aware that your version might be older than the one
        -- required.
        dependencies_bin = {
          ['tinymist'] = nil,
          ['websocat'] = nil,
        },

        -- A list of extra arguments (or nil) to be passed to previewer.
        -- For example, extra_args = { "--input=ver=draft", "--ignore-system-fonts" }
        extra_args = nil,

        -- This function will be called to determine the root of the typst project
        get_root = function(path_of_main_file)
          local root = os.getenv 'TYPST_ROOT'
          if root then
            return root
          end
          return vim.fn.fnamemodify(path_of_main_file, ':p:h')
        end,

        -- This function will be called to determine the main file of the typst
        -- project.
        get_main_file = function(path_of_buffer)
          return path_of_buffer
        end,
      }
    end,
  },
  {
    'zk-org/zk-nvim',
    config = function()
      require('zk').setup {
        picker = 'fzf', -- or "telescope" if you use that
        lsp = {
          config = {
            cmd = { 'zk', 'lsp' },
            name = 'zk',
          },
          auto_attach = {
            enabled = true,
            filetypes = { 'markdown' },
          },
        },
      }
    end,
  },
  -- {
  --   'uhs-robert/sshfs.nvim',
  --   opts = {
  --     -- Refer to the configuration section below
  --     -- or leave empty for defaults
  --   },
  -- },
}
