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
