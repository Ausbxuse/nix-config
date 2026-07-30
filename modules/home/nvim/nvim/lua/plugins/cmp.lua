return {
  {
    'saghen/blink.cmp',
    event = { 'InsertEnter', 'CmdlineEnter' },
    dependencies = {
      {
        'fang2hou/blink-copilot',
        opts = {
          max_completions = 1, -- Global default for max completions
          max_attempts = 2, -- Global default for max attempts
          kind_icon = '',
        },
        config = function(_, opts)
          require('blink-copilot').setup(opts)
        end,
      },
    },

    branch = 'v1',
    ---@module 'blink.cmp'
    ---@type blink.cmp.Config
    opts = {
      signature = { enabled = true },

      cmdline = { enabled = true },
      completion = {
        menu = {
          winblend = 10, -- WARN: causes different nerfont icon sizes
          auto_show = function(ctx)
            return not vim.tbl_contains({ '/', '?' }, vim.fn.getcmdtype())
          end,
          draw = {
            -- columns = { { 'label', 'label_description', gap = 1 }, { 'kind_icon', 'kind', gap = 1 } },
            treesitter = { 'lsp' },
          },
        },
        ghost_text = {
          enabled = true,
        },
        -- auto_show = true,
        -- trigger = { prefetch_on_insert = false },
      },
      keymap = {
        preset = 'super-tab',
        ['<C-s>'] = { 'show', 'show_documentation', 'hide_documentation' },
      },

      appearance = {
        use_nvim_cmp_as_default = true,
        nerd_font_variant = 'mono',
      },
      fuzzy = {
        implementation = 'lua',
      },
      sources = {
        default = { 'lsp', 'path', 'snippets', 'buffer', 'copilot' },
        providers = {
          path = {
            opts = {
              get_cwd = function()
                return vim.fn.getcwd()
              end,
            },
          },
          copilot = {
            name = 'copilot',
            module = 'blink-copilot',
            score_offset = 100,
            async = true,
            opts = {
              -- Local options override global ones
              max_completions = 3, -- Override global max_completions

              -- Final settings:
              -- * max_completions = 3
              -- * max_attempts = 2
              -- * all other options are default
            },
          },
        },
      },
    },
    opts_extend = { 'sources.default' },
    config = function(_, opts)
      require('blink.cmp').setup(opts)
    end,
  },
}
