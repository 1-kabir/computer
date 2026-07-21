<script lang="ts">
	import { readFile } from '$lib/apis/files';
	import MarkdownRenderer from '$lib/components/markdown/MarkdownRenderer.svelte';
	import CsvTable from '$lib/components/preview/CsvTable.svelte';
	import HtmlPreview from '$lib/components/preview/HtmlPreview.svelte';
	import ImagePreview from '$lib/components/preview/ImagePreview.svelte';
	import JsonTreeView from '$lib/components/preview/JsonTreeView.svelte';
	import OfficePreview from '$lib/components/preview/OfficePreview.svelte';
	import PDFViewer from '$lib/components/preview/PDFViewer.svelte';
	import SvgPreview from '$lib/components/preview/SvgPreview.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		file: any;
		filePath: string;
	}

	let { file, filePath }: Props = $props();

	const MAX_TEXT_PREVIEW_BYTES = 200_000;

	let loading = $state(false);
	let error = $state('');
	let content = $state('');
	let jsonData = $state<unknown>(null);

	const kind = $derived(file?.kind || 'binary');
	const name = $derived(file?.name || filePath.split('/').pop() || '');
	const viewUrl = $derived(`/api/workspace/files/view?path=${encodeURIComponent(filePath)}`);
	const shouldReadText = $derived(
		['markdown', 'text', 'json', 'csv', 'html', 'svg'].includes(kind) &&
			(typeof file?.size !== 'number' || file.size <= MAX_TEXT_PREVIEW_BYTES)
	);

	$effect(() => {
		let cancelled = false;
		const path = filePath;

		async function load() {
			content = '';
			jsonData = null;
			error = '';
			if (!path || !shouldReadText) return;

			loading = true;
			try {
				const res = await readFile(path);
				if (!res.ok) {
					const body = await res.json().catch(() => ({}));
					throw new Error(body.detail || `Error ${res.status}`);
				}
				const data = await res.json();
				if (cancelled) return;
				content = data.content || '';
				if (kind === 'json') {
					try {
						jsonData = JSON.parse(content);
					} catch {
						jsonData = null;
					}
				}
			} catch (e: any) {
				if (!cancelled) error = e.message || $t('preview.failedToLoad');
			} finally {
				if (!cancelled) loading = false;
			}
		}

		void load();
		return () => {
			cancelled = true;
		};
	});
</script>

{#if loading}
	<div class="preview-state">{$t('common.loading')}</div>
{:else if error}
	<div class="preview-state">{error}</div>
{:else if kind === 'image'}
	<div class="preview-box image-box">
		<ImagePreview src={viewUrl} alt={name} />
	</div>
{:else if kind === 'pdf'}
	<div class="preview-box tall">
		<PDFViewer src={viewUrl} />
	</div>
{:else if kind === 'office'}
	<div class="preview-box tall">
		<OfficePreview src={viewUrl} fileName={name} />
	</div>
{:else if kind === 'html' && content}
	<div class="preview-box tall">
		<HtmlPreview {content} {filePath} />
	</div>
{:else if kind === 'svg' && content}
	<div class="preview-box tall">
		<SvgPreview {content} />
	</div>
{:else if kind === 'markdown' && content}
	<div class="text-preview">
		<MarkdownRenderer {content} />
	</div>
{:else if kind === 'json' && jsonData !== null}
	<div class="text-preview mono-preview">
		<JsonTreeView data={jsonData} />
	</div>
{:else if kind === 'csv' && content}
	<div class="preview-box">
		<CsvTable {content} separator={name.endsWith('.tsv') ? '\t' : ','} />
	</div>
{:else if kind === 'text' && content}
	<pre class="text-preview mono-preview">{content}</pre>
{:else if kind === 'audio'}
	<div class="media-preview">
		<audio controls preload="metadata" src={viewUrl}></audio>
	</div>
{:else if kind === 'video'}
	<div class="media-preview video-preview">
		<!-- svelte-ignore a11y_media_has_caption -->
		<video controls preload="metadata" src={viewUrl}></video>
	</div>
{/if}

<style>
	@reference "../../../app.css";

	.preview-box {
		height: 18rem;
		max-height: 75vh;
		min-height: 10rem;
		overflow: hidden;
		resize: vertical;
		background: var(--app-bg);
	}

	.preview-box.tall {
		height: 24rem;
	}

	.image-box {
		height: 22rem;
	}

	.text-preview {
		height: 18rem;
		max-height: 75vh;
		min-height: 6rem;
		overflow: auto;
		resize: vertical;
		padding: 0.875rem 1rem;
		background: var(--app-bg);
	}

	.mono-preview {
		margin: 0;
		white-space: pre-wrap;
		word-break: break-word;
		font-family: var(--font-mono);
		font-size: 0.75rem;
		line-height: 1.35;
		color: var(--app-fg);
	}

	.media-preview {
		padding: 0.75rem 1rem;
		background: var(--app-bg);
	}

	.video-preview {
		height: 18rem;
		max-height: 75vh;
		min-height: 6rem;
		overflow: auto;
		resize: vertical;
	}

	.media-preview audio,
	.media-preview video {
		width: 100%;
		height: 100%;
		max-height: 100%;
		object-fit: contain;
	}

	.preview-state {
		padding: 0.75rem 1rem;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
	}
</style>
