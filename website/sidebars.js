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
        {
          type: 'category',
          label: 'Deployment',
          link: {type: 'doc', id: 'operate/deployment'},
          items: [
            'operate/deployment/docker-prepare',
            'operate/deployment/linux-install',
            'operate/deployment/bundle-deep-dive',
            'operate/deployment/docker-helper-reference',
          ],
        },
        'operate/security',
      ],
    },
    {
      type: 'category',
      label: 'Plugins',
      collapsed: false,
      link: {type: 'doc', id: 'plugins/index'},
      items: [
        {
          type: 'category',
          label: 'SMTP',
          link: {type: 'doc', id: 'plugins/smtp/index'},
          items: [
            'plugins/smtp/configure',
            'plugins/smtp/behavior',
            'plugins/smtp/reference',
          ],
        },
        {
          type: 'category',
          label: 'IMAP',
          link: {type: 'doc', id: 'plugins/imap/index'},
          items: [
            'plugins/imap/configure',
            'plugins/imap/behavior',
            'plugins/imap/reference',
          ],
        },
      ],
    },
    {
      type: 'category',
      label: 'Reference Manual',
      collapsed: false,
      items: [
        'reference/server',
        'reference/client',
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
      items: [
        'maintain/architecture',
        'maintain/testing-release',
        'maintain/release-process',
      ],
    },
  ],
};

module.exports = sidebars;
