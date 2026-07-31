<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Editor } from '@tiptap/core';
	import StarterKit from '@tiptap/starter-kit';
	import { Markdown } from '@tiptap/markdown';
	import TaskList from '@tiptap/extension-task-list';
	import TaskItem from '@tiptap/extension-task-item';
	import ImageExt from '@tiptap/extension-image';
	import { Table } from '@tiptap/extension-table';
	import TableRow from '@tiptap/extension-table-row';
	import TableCell from '@tiptap/extension-table-cell';
	import TableHeader from '@tiptap/extension-table-header';
	import Link from '@tiptap/extension-link';
	import Placeholder from '@tiptap/extension-placeholder';
	import Typography from '@tiptap/extension-typography';
	import Highlight from '@tiptap/extension-highlight';
	import Underline from '@tiptap/extension-underline';
	import Superscript from '@tiptap/extension-superscript';
	import Subscript from '@tiptap/extension-subscript';
	import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
	import { all, createLowlight } from 'lowlight';

	import EditorToolbar from './EditorToolbar.svelte';
	import { uploadFiles } from '$lib/apis/files';

	interface Props {
		content: string;
		filePath: string;
		workspacePath: string;
		onchange?: () => void;
		onsave?: () => void;
	}

	let { content, filePath, workspacePath, onchange, onsave }: Props = $props();

	let editorEl: HTMLDivElement | undefined = $state();
	let editor: Editor | null = $state(null);

	const lowlight = createLowlight(all);

	// Gracefully handle unregistered language tags (e.g. ```nginx, ```mermaid, etc.)
	const _origHighlight = lowlight.highlight.bind(lowlight);
	lowlight.highlight = (lang: string, value: string, options?: Record<string, unknown>) => {
		if (!lowlight.registered(lang)) {
			return lowlight.highlightAuto(value);
		}
		return _origHighlight(lang, value, options);
	};

	function isDarkMode(): boolean {
		return typeof document !== 'undefined' && document.documentElement.classList.contains('dark');
	}

	// Get the directory containing the current .md file
	function getFileDir(): string {
		const parts = filePath.split('/');
		parts.pop();
		return parts.join('/') || '/';
	}

	onMount(() => {
		if (!editorEl) return;

		editor = new Editor({
			element: editorEl,
			extensions: [
				StarterKit.configure({
					codeBlock: false, // Use CodeBlockLowlight instead
					heading: { levels: [1, 2, 3, 4, 5, 6] }
				}),
				Markdown,
				TaskList,
				TaskItem.configure({ nested: true }),
				ImageExt.configure({ inline: true, allowBase64: false }),
				Table.configure({ resizable: false }),
				TableRow,
				TableCell,
				TableHeader,
				Link.configure({
					openOnClick: false,
					autolink: true,
					linkOnPaste: true
				}),
				Placeholder.configure({
					placeholder: 'Start writing…'
				}),
				Typography,
				Highlight.configure({ multicolor: false }),
				Underline,
				Superscript,
				Subscript,
				CodeBlockLowlight.configure({ lowlight })
			],
			content: content,
			contentType: 'markdown',
			autofocus: false,
			editorProps: {
				attributes: {
					class: 'rte-prosemirror',
					spellcheck: 'true'
				},
				// Handle image drops: upload to same directory as .md file
				handleDrop: (view, event, _slice, moved) => {
					if (moved || !event.dataTransfer?.files?.length) return false;

					const files = event.dataTransfer.files;
					for (const file of Array.from(files)) {
						if (!file.type.startsWith('image/')) continue;
						handleImageUpload(file);
					}
					return true;
				},
				handlePaste: (view, event) => {
					const items = event.clipboardData?.items;
					if (!items) return false;

					for (const item of Array.from(items)) {
						if (!item.type.startsWith('image/')) continue;
						const file = item.getAsFile();
						if (file) {
							handleImageUpload(file);
							return true;
						}
					}
					return false;
				}
			},
			onUpdate: () => {
				onchange?.();
			}
		});

		// ⌘S / Ctrl+S save
		const handleKeydown = (e: KeyboardEvent) => {
			if ((e.metaKey || e.ctrlKey) && e.key === 's') {
				e.preventDefault();
				onsave?.();
			}
		};
		editorEl.addEventListener('keydown', handleKeydown);

		// Theme observer: update editor class on theme change
		themeObserver = new MutationObserver(() => {
			if (editorEl) {
				editorEl.closest('.rte-container')?.classList.toggle('dark-mode', isDarkMode());
			}
		});
		themeObserver.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['class']
		});
	});

	let themeObserver: MutationObserver | null = null;

	onDestroy(() => {
		themeObserver?.disconnect();
		editor?.destroy();
		editor = null;
	});

	async function handleImageUpload(file: File) {
		const dir = getFileDir();
		const form = new FormData();
		form.append('file', file);
		form.append('directory', dir);
		try {
			await uploadFiles(dir, form);
			// Insert image at current cursor position
			const imgPath = `/api/workspace/files/view?path=${encodeURIComponent(dir + '/' + file.name)}`;
			editor?.chain().focus().setImage({ src: imgPath, alt: file.name }).run();
		} catch (e) {
			console.error('Image upload failed:', e);
		}
	}

	// Public API: @tiptap/markdown augments Editor with getMarkdown()
	export function getMarkdown(): string {
		if (!editor) return content;
		return (editor as any).getMarkdown();
	}

	export function getEditor(): Editor | null {
		return editor;
	}
</script>

<div class="rte-container" class:dark-mode={isDarkMode()}>
	{#if editor}
		<EditorToolbar {editor} />
	{/if}
	<div class="rte-scroll">
		<div bind:this={editorEl} class="rte-mount"></div>
	</div>
</div>

<style>
	@reference "../../../app.css";

	.rte-container {
		display: flex;
		flex-direction: column;
		height: 100%;
		overflow: hidden;
	}

	.rte-scroll {
		flex: 1;
		overflow-y: auto;
	}

	.rte-mount {
		min-height: 100%;
	}

	/* ── ProseMirror core ──────────────────────────── */

	.rte-container :global(.rte-prosemirror) {
		padding: 1.5rem 2rem;
		min-height: 100%;
		font-size: 0.875rem;
		line-height: 1.7;
		color: var(--app-fg);
		outline: none;
		word-wrap: break-word;
		overflow-wrap: break-word;
	}

	.rte-container.dark-mode :global(.rte-prosemirror) {
		caret-color: var(--app-fg);
	}

	/* ── Placeholder ──────────────────────────────── */

	.rte-container :global(.rte-prosemirror p.is-editor-empty:first-child::before) {
		content: attr(data-placeholder);
		float: left;
		color: var(--app-fg-subtle);
		pointer-events: none;
		height: 0;
	}

	/* ── Headings (match MarkdownRenderer sizes) ──── */

	.rte-container :global(.rte-prosemirror h1) {
		font-size: 1.5rem;
		font-weight: 600;
		margin: 0 0 0.5rem;
		letter-spacing: -0.02em;
	}

	.rte-container :global(.rte-prosemirror h2) {
		font-size: 1.25rem;
		font-weight: 600;
		margin: 1.5rem 0 0.5rem;
		letter-spacing: -0.01em;
	}

	.rte-container :global(.rte-prosemirror h3) {
		font-size: 1rem;
		font-weight: 600;
		margin: 1.25rem 0 0.375rem;
	}

	.rte-container :global(.rte-prosemirror h4) {
		font-size: 0.875rem;
		font-weight: 600;
		margin: 1rem 0 0.25rem;
	}

	.rte-container :global(.rte-prosemirror h5),
	.rte-container :global(.rte-prosemirror h6) {
		font-size: 0.8125rem;
		font-weight: 600;
		margin: 1rem 0 0.25rem;
	}

	/* ── Paragraph ─────────────────────────────────── */

	.rte-container :global(.rte-prosemirror p) {
		margin: 0 0 0.75rem;
	}

	/* ── Lists ─────────────────────────────────────── */

	.rte-container :global(.rte-prosemirror ul),
	.rte-container :global(.rte-prosemirror ol) {
		margin: 0 0 0.75rem;
		padding-left: 1.25rem;
	}

	.rte-container :global(.rte-prosemirror ul) {
		list-style-type: disc;
	}

	.rte-container :global(.rte-prosemirror ol) {
		list-style-type: decimal;
	}

	.rte-container :global(.rte-prosemirror li) {
		margin: 0.25rem 0;
	}

	.rte-container :global(.rte-prosemirror li > p) {
		margin-bottom: 0.25rem;
	}

	/* ── Task lists ────────────────────────────────── */

	.rte-container :global(.rte-prosemirror ul[data-type='taskList']) {
		list-style: none;
		padding-left: 0.25rem;
	}

	.rte-container :global(.rte-prosemirror ul[data-type='taskList'] li) {
		display: flex;
		align-items: flex-start;
		gap: 0.375rem;
	}

	.rte-container :global(.rte-prosemirror ul[data-type='taskList'] li label) {
		margin-top: 0.1875rem;
	}

	.rte-container :global(.rte-prosemirror ul[data-type='taskList'] li > div) {
		flex: 1;
	}

	/* ── Blockquote ────────────────────────────────── */

	.rte-container :global(.rte-prosemirror blockquote) {
		margin: 0 0 0.75rem;
		padding: 0.25rem 1rem;
		border-left: 0.1875rem solid var(--app-border);
		color: var(--app-fg-muted);
	}

	/* ── Horizontal rule ──────────────────────────── */

	.rte-container :global(.rte-prosemirror hr) {
		border: none;
		border-top: 1px solid var(--app-border);
		margin: 1.25rem 0;
	}

	/* ── Code (inline) ─────────────────────────────── */

	.rte-container :global(.rte-prosemirror code) {
		background: var(--app-hover);
		border-radius: 0.1875rem;
		padding: 0.0625rem 0.3125rem;
		font-size: 0.78125rem;
		font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
	}

	/* ── Code blocks ───────────────────────────────── */

	.rte-container :global(.rte-prosemirror pre) {
		background: var(--app-hover);
		border-radius: 0.375rem;
		padding: 0.75rem 1rem;
		overflow-x: auto;
		margin: 0 0 0.75rem;
		font-size: 0.8125rem;
		font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
	}

	.rte-container :global(.rte-prosemirror pre code) {
		background: none;
		padding: 0;
		border-radius: 0;
		font-size: inherit;
	}

	/* ── Tables ─────────────────────────────────────── */

	.rte-container :global(.rte-prosemirror table) {
		border-collapse: collapse;
		width: 100%;
		margin: 0 0 1rem;
		font-size: 0.8125rem;
	}

	.rte-container :global(.rte-prosemirror th),
	.rte-container :global(.rte-prosemirror td) {
		border: 1px solid var(--app-border);
		padding: 0.375rem 0.625rem;
		text-align: left;
	}

	.rte-container :global(.rte-prosemirror th) {
		font-weight: 600;
		background: var(--app-hover);
	}

	/* ── Links ──────────────────────────────────────── */

	.rte-container :global(.rte-prosemirror a) {
		color: var(--app-fg);
		text-decoration: none;
		cursor: pointer;
	}

	.rte-container :global(.rte-prosemirror a:hover) {
		text-decoration: underline;
	}

	/* ── Images ─────────────────────────────────────── */

	.rte-container :global(.rte-prosemirror img) {
		max-width: 100%;
		border-radius: 0.375rem;
		margin: 0.5rem 0;
	}

	/* ── Strong / emphasis ─────────────────────────── */

	.rte-container :global(.rte-prosemirror strong) {
		font-weight: 600;
	}

	/* ── Highlight (==text==) ──────────────────────── */

	.rte-container :global(.rte-prosemirror mark) {
		background: rgba(255, 213, 79, 0.4);
		border-radius: 0.125rem;
		padding: 0 0.125rem;
	}

	.rte-container.dark-mode :global(.rte-prosemirror mark) {
		background: rgba(255, 213, 79, 0.2);
	}

	/* ── Selection ──────────────────────────────────── */

	.rte-container :global(.rte-prosemirror ::selection) {
		background: rgba(59, 130, 246, 0.2);
	}

	.rte-container.dark-mode :global(.rte-prosemirror ::selection) {
		background: rgba(59, 130, 246, 0.3);
	}

	/* ── Syntax highlighting in code blocks ────────── */

	.rte-container :global(.hljs-keyword) {
		color: #c678dd;
	}
	.rte-container :global(.hljs-string) {
		color: #98c379;
	}
	.rte-container :global(.hljs-number) {
		color: #d19a66;
	}
	.rte-container :global(.hljs-comment) {
		color: #5c6370;
		font-style: italic;
	}
	.rte-container :global(.hljs-function) {
		color: #61afef;
	}
	.rte-container :global(.hljs-title) {
		color: #61afef;
	}
	.rte-container :global(.hljs-built_in) {
		color: #e5c07b;
	}
	.rte-container :global(.hljs-attr) {
		color: #d19a66;
	}
	.rte-container :global(.hljs-tag) {
		color: #e06c75;
	}
	.rte-container :global(.hljs-name) {
		color: #e06c75;
	}
	.rte-container :global(.hljs-selector-class) {
		color: #d19a66;
	}
	.rte-container :global(.hljs-variable) {
		color: #e06c75;
	}
	.rte-container :global(.hljs-type) {
		color: #e5c07b;
	}
</style>
