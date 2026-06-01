// @ts-check

const {themes} = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Agent Arbiter',
  tagline: 'Policy-controlled service access for agents',
  favicon: 'img/logo-mark.svg',

  url: 'https://arbiter.yadan.net',
  baseUrl: '/',

  organizationName: 'omry',
  projectName: 'agent-arbiter',

  onBrokenLinks: 'throw',
  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },
  themes: ['@docusaurus/theme-mermaid'],

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve('./sidebars.js'),
          routeBasePath: 'docs',
          editUrl:
            'https://github.com/omry/agent-arbiter/tree/main/website/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/logo.svg',
      colorMode: {
        defaultMode: 'dark',
        disableSwitch: true,
        respectPrefersColorScheme: false,
      },
      navbar: {
        title: 'Agent Arbiter',
        logo: {
          alt: 'Agent Arbiter logo',
          src: 'img/logo-mark.svg',
        },
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'docsSidebar',
            position: 'left',
            label: 'Docs',
          },
          {
            to: '/docs/get-started/quickstart',
            label: 'Quickstart',
            position: 'left',
          },
          {
            href: 'https://github.com/omry/agent-arbiter',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              {
                label: 'Quickstart',
                to: '/docs/get-started/quickstart',
              },
              {
                label: 'Configuration Model',
                to: '/docs/operate/configuration-model',
              },
              {
                label: 'Plugin authors',
                to: '/docs/extend/plugins',
              },
            ],
          },
          {
            title: 'Project',
            items: [
              {
                label: 'GitHub',
                href: 'https://github.com/omry/agent-arbiter',
              },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} Agent Arbiter contributors.`,
      },
      prism: {
        theme: themes.dracula,
        darkTheme: themes.dracula,
        additionalLanguages: ['bash', 'toml', 'yaml'],
      },
    }),
};

module.exports = config;
