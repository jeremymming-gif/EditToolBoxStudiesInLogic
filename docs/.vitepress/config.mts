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
  // docs/public/templates/ 下是公开下载资产（Word/LaTeX 模板，非 VitePress 页面）。
  // VitePress 只把 KNOWN_EXTENSIONS（含 docx/pdf/tex/bib）内的链接当资产，.sty 不在其中，
  // 会按页面链接做死链检测；因此 .sty 必须逐条放行本页实际提供的两个文件（精确路径，不设通配），
  // 其余下载链接仍按 /templates/ 正则放行——只放行可下载资产，避免链接失效时被静默放过（404 无人发现）。
  ignoreDeadLinks: [
    /^\/templates\/.+\.(docx|pdf|tex|bib)(\?.*)?$/,
    /^\/templates\/SLCN-MIX\.sty(\?.*)?$/,
    /^\/templates\/SLEN\.sty(\?.*)?$/
  ],
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
      { text: '模板', link: '/templates/', activeMatch: '/templates/' },
      { text: '参考', link: '/reference/faq', activeMatch: '/(reference|maintain)/' },
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
        text: '模板',
        items: [
          { text: '模板总览与下载', link: '/templates/' },
          { text: 'Word 模板与填写说明', link: '/templates/word' },
          { text: 'LaTeX 模板与编译链', link: '/templates/latex' },
          { text: '本刊参考文献体例', link: '/templates/reference-style' },
          { text: '投稿检查清单', link: '/templates/checklist' }
        ]
      },
      {
        text: '参考',
        items: [
          { text: '常见问题', link: '/reference/faq' },
          { text: '已知限制与绕行', link: '/reference/known-limitations' },
          { text: '环境检查与诊断', link: '/reference/environment-check' },
          { text: '维护者接入', link: '/maintain/quickstart' },
          { text: '故障速查（维护者）', link: '/maintain/troubleshooting' },
          { text: '版本记录', link: '/reference/changelog' }
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
      message: 'MIT License · 为《逻辑学研究》编辑部工作流而建',
      copyright: '编辑部工具箱'
    }
  }
})
