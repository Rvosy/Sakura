// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
	site: 'https://sakura.cialloo.cn',
	integrations: [
		starlight({
			title: 'Sakura Desktop Pet',
			description: '一个能主动感知屏幕内容与系统事件的通用桌宠 Agent 框架',
			favicon: '/favicon.svg',
			customCss: ['./src/styles/starlight.css'],
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/Rvosy/sakura' }],
			expressiveCode: {
				themes: ['github-dark', 'github-light'],
				styleOverrides: {
					borderRadius: '14px',
					borderColor: 'var(--sakura-hairline)',
					frames: {
						shadowColor: 'transparent',
						editorTabBarBackground: 'var(--sakura-code-tabbar)',
						editorActiveTabBackground: 'var(--sakura-code-bg)',
						editorBackground: 'var(--sakura-code-bg)',
						terminalBackground: 'var(--sakura-code-bg)',
						terminalTitlebarBackground: 'var(--sakura-code-tabbar)',
					},
				},
			},
			sidebar: [
				{
					label: '开始使用',
					items: [
						{ label: '安装与配置', slug: 'start/setup' },
						{ label: 'API 配置', slug: 'start/api-config' },
						{ label: 'macOS 指南', slug: 'platform/macos' },
					],
				},
				{
					label: '产品能力',
					items: [
						{ label: '功能概览', slug: 'product/features' },
						{ label: '技术架构', slug: 'developers/architecture' },
					],
				},
				{
					label: '扩展开发',
					items: [
						{ label: '角色包制作', slug: 'developers/character-pack' },
						{ label: '插件 SDK', slug: 'developers/plugin-sdk' },
					],
				},
			],
		}),
	],
});
