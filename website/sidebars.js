// @ts-check

/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    {
      type: 'category',
      label: 'Start Here',
      collapsed: false,
      items: [
        'intro',
        'get-started/concepts',
        'get-started/quickstart',
      ],
    },
    {
      type: 'category',
      label: 'Operate',
      collapsed: false,
      items: [
        'operate/configuration-model',
        'operate/deployment',
        'operate/security',
      ],
    },
    {
      type: 'category',
      label: 'Use',
      collapsed: false,
      items: [
        'use/smtp',
        'use/imap',
      ],
    },
    {
      type: 'category',
      label: 'Reference Manual',
      collapsed: false,
      items: [
        'operate/server-reference',
        'use/cli-reference',
      ],
    },
    {
      type: 'category',
      label: 'Extend',
      collapsed: false,
      items: ['extend/plugins'],
    },
    {
      type: 'category',
      label: 'Media',
      collapsed: false,
      items: ['media/terminal-recordings'],
    },
    {
      type: 'category',
      label: 'Maintain',
      collapsed: true,
      items: ['maintain/architecture', 'maintain/testing-release'],
    },
  ],
};

module.exports = sidebars;
