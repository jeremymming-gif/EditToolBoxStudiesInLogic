import { defineConfig } from 'vitepress'

// 项目主页：VitePress 站点（与参考的 DeepSeek Harness 文档同一套视觉语言）。
// 部署：.github/workflows/docs.yml 构建后经 GitHub Pages（Actions 源）发布。
// 本地：cd docs && npm install && npm run docs:dev
export default defineConfig({
  lang: 'zh-CN',
  title: '编辑部工具箱',
  description:
    '《逻辑学研究》编辑部的稿件处理工具箱：Word/LaTeX 初排、参考文献核实、整期装配与编辑部引用库。',
  base: '/EditToolBoxStudiesInLogic/',
  cleanUrls: true,
  lastUpdated: true,
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/EditToolBoxStudiesInLogic/logo.svg' }],
    ['meta', { name: 'theme-color', content: '#3451b2' }],
    ['meta', { name: 'author', content: 'SLCN Editorial Toolbox' }]
  ],
  themeConfig: {
    logo: '/logo.svg',
    siteTitle: '编辑部工具箱',
    nav: [
      { text: '入门', link: '/guide/quickstart', activeMatch: '/guide/' },
      { text: '功能', link: '/features/single', activeMatch: '/features/' },
      { text: '参考', link: '/reference/faq', activeMatch: '/reference/' },
      { text: '下载', link: 'https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest' }
    ],
    sidebar: [
      {
        text: '入门',
        items: [
          { text: '快速上手', link: '/guide/quickstart' },
          { text: '安装说明', link: '/guide/install' }
        ]
      },
      {
        text: '功能',
        items: [
          { text: '单篇稿件', link: '/features/single' },
          { text: '整期装配', link: '/features/assembly' },
          { text: '编辑部引用库', link: '/features/bibdb' }
        ]
      },
      {
        text: '参考',
        items: [
          { text: '常见问题', link: '/reference/faq' },
          { text: '版本记录', link: '/reference/changelog' },
          { text: '开发与构建', link: '/reference/development' }
        ]
      }
    ],
    socialLinks: [
      {
        icon: 'github',
        link: 'https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic'
      }
    ],
    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
          modal: {
            noResultsText: '没有找到相关结果',
            resetButtonTitle: '清除条件',
            footer: {
              selectText: '选择',
              navigateText: '切换',
              closeText: '关闭'
            }
          }
        }
      }
    },
    outline: { level: [2, 3], label: '本页目录' },
    docFooter: { prev: '上一篇', next: '下一篇' },
    editLink: {
      pattern: 'https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/edit/main/docs/:path',
      text: '在 GitHub 上编辑此页'
    },
    lastUpdated: { text: '最后更新于' },
    footer: {
      message: 'MIT License · 仅用于《逻辑学研究》编辑部内部流程',
      copyright: '编辑部工具箱'
    }
  }
})
