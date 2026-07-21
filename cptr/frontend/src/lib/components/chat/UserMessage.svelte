<script lang="ts">
	import { tick } from 'svelte';
	import Icon from '../Icon.svelte';
	import MarkdownRenderer from '$lib/components/markdown/MarkdownRenderer.svelte';
	import MessageTimestamp from './MessageTimestamp.svelte';
	import { fileIconName } from '$lib/utils/fileIcon';
	import { openFileTab, setFileBrowserCwd, setActiveTab } from '$lib/stores';
	import { tooltip } from '$lib/tooltip';
	import { t } from '$lib/i18n';

	interface Props {
		content: string;
		meta?: Record<string, any> | null;
		createdAt?: number | null;
		siblingIndex?: number;
		siblingTotal?: number;
		onedit?: (content: string, submit: boolean) => void;
		onnavigate?: (direction: -1 | 1) => void;
	}
	let {
		content,
		meta = null,
		createdAt = null,
		siblingIndex = 0,
		siblingTotal = 1,
		onedit,
		onnavigate
	}: Props = $props();

	type Segment = { type: 'text'; value: string } | { type: 'file'; label: string; path: string };

	// Matches both [label](file://path) and TipTap's [@ id="path" label="name"]
	const FILE_LINK_RE = /\[([^\]]+)\]\(file:\/\/([^)]+)\)/g;
	const TIPTAP_MENTION_RE = /\[@\s+id="([^"]+)"\s+label="([^"]+)"\]/g;

	const parsedContent: Segment[] = $derived.by(() => {
		// Normalize TipTap format to markdown links first
		const normalized = content.replace(
			TIPTAP_MENTION_RE,
			(_: string, id: string, label: string) => `[${label}](file://${id})`
		);
		const segments: Segment[] = [];
		let last = 0;
		for (const m of normalized.matchAll(FILE_LINK_RE)) {
			if (m.index > last) segments.push({ type: 'text', value: normalized.slice(last, m.index) });
			segments.push({ type: 'file', label: m[1], path: m[2] });
			last = m.index + m[0].length;
		}
		if (last < normalized.length) segments.push({ type: 'text', value: normalized.slice(last) });
		return segments.length ? segments : [{ type: 'text' as const, value: content }];
	});

	let edit = $state(false);
	let editedContent = $state('');
	let copied = $state(false);
	let textareaEl: HTMLTextAreaElement;
	let asyncExpanded = $state(false);
	let timerExpanded = $state(false);
	const isAsyncSubagentResult = $derived(
		(meta?.internal === true && meta?.type === 'subagent') || meta?.async_subagent_result === true
	);
	const isTimer = $derived(meta?.internal === true && meta?.type === 'timer');
	const delegationId = $derived(meta?.delegation_id || '');
	const delegationIds = $derived(Array.isArray(meta?.delegation_ids) ? meta.delegation_ids : []);
	const delegationLabel = $derived(
		delegationId || (delegationIds.length > 1 ? `${delegationIds.length} tasks` : '')
	);
	const asyncSummary = $derived.by(() => {
		const line = content
			.split('\n')
			.map((s) => s.trim())
			.find((s) => s && !s.startsWith('['));
		if (!line) return delegationLabel || '';
		return line.length > 96 ? `${line.slice(0, 96)}...` : line;
	});

	async function startEdit() {
		edit = true;
		editedContent = content;
		await tick();
		if (textareaEl) {
			textareaEl.style.height = '';
			textareaEl.style.height = `${textareaEl.scrollHeight}px`;
			textareaEl.focus();
		}
	}

	function cancelEdit() {
		edit = false;
		editedContent = '';
	}

	function saveEdit() {
		onedit?.(editedContent, false);
		edit = false;
		editedContent = '';
	}

	function sendEdit() {
		onedit?.(editedContent, true);
		edit = false;
		editedContent = '';
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			cancelEdit();
		} else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
			sendEdit();
		}
	}

	function autoResize(e: Event) {
		const el = e.target as HTMLTextAreaElement;
		el.style.height = '';
		el.style.height = `${el.scrollHeight}px`;
	}

	function copyContent() {
		navigator.clipboard.writeText(content);
		copied = true;
		setTimeout(() => {
			copied = false;
		}, 1500);
	}
</script>

<div class="group">
	{#if isTimer}
		<div class="w-full min-w-0">
			<button
				type="button"
				class="w-full min-w-0 flex items-center gap-2 text-left text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
				aria-expanded={timerExpanded}
				onclick={() => (timerExpanded = !timerExpanded)}
			>
				<span class="text-[0.75rem] font-medium shrink-0">{$t('chat.timer')}</span>
				<span class="text-[0.75rem] truncate min-w-0 flex-1">{content}</span>
				<Icon
					name="chevron-down"
					size={12}
					class="text-gray-400 dark:text-gray-600 shrink-0 transition-transform duration-150 {timerExpanded
						? 'rotate-180'
						: ''}"
				/>
			</button>
			{#if timerExpanded}
				<div
					class="mt-2 ml-3 border-l border-gray-100 dark:border-white/8 pl-3 text-[0.78125rem] leading-relaxed text-gray-600 dark:text-gray-400 whitespace-pre-wrap break-words"
				>
					{content}
				</div>
			{/if}
		</div>
	{:else if isAsyncSubagentResult}
		<div class="w-full min-w-0">
			<button
				type="button"
				class="w-full min-w-0 flex items-center gap-2 text-left text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
				aria-expanded={asyncExpanded}
				onclick={() => (asyncExpanded = !asyncExpanded)}
			>
				<span class="w-1.5 h-1.5 rounded-full bg-gray-300 dark:bg-gray-600 shrink-0"></span>
				<span class="text-[0.75rem] font-medium shrink-0">{$t('chat.asyncSubagentComplete')}</span>
				{#if asyncSummary}
					<span class="text-[0.75rem] truncate min-w-0 flex-1">{asyncSummary}</span>
				{/if}
				{#if delegationLabel}
					<span
						class="hidden sm:inline text-[0.6875rem] font-mono text-gray-400 dark:text-gray-600 shrink-0"
						>{delegationLabel}</span
					>
				{/if}
				<Icon
					name="chevron-down"
					size={12}
					class="text-gray-400 dark:text-gray-600 shrink-0 transition-transform duration-150 {asyncExpanded
						? 'rotate-180'
						: ''}"
				/>
			</button>
			{#if asyncExpanded}
				<div
					class="mt-2 ml-3 border-l border-gray-100 dark:border-white/8 pl-3 text-[0.78125rem] leading-relaxed text-gray-600 dark:text-gray-400 whitespace-pre-wrap break-words"
				>
					{content}
				</div>
			{/if}
		</div>
	{:else if edit}
		<!-- Edit mode: full width -->
		<div class="w-full">
			<div class="app-subtle-surface rounded-xl border px-3.5 py-2.5">
				<textarea
					bind:this={textareaEl}
					bind:value={editedContent}
					class="w-full bg-transparent outline-none resize-none text-[0.8125rem] leading-relaxed text-gray-900 dark:text-gray-200"
					oninput={autoResize}
					onkeydown={handleKeydown}
					rows="1"
				></textarea>
			</div>
			<div class="flex justify-between mt-2 text-[0.75rem] font-medium">
				<button
					class="px-3 py-1 rounded-lg text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 bg-gray-100 dark:bg-white/6 hover:bg-gray-200 dark:hover:bg-white/10 transition-colors duration-100"
					onclick={saveEdit}>{$t('common.save')}</button
				>
				<div class="flex gap-1.5">
					<button
						class="px-3 py-1 rounded-lg text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={cancelEdit}>{$t('common.cancel')}</button
					>
					<button
						class="px-3 py-1 rounded-lg bg-gray-900 dark:bg-white text-white dark:text-black hover:bg-gray-700 dark:hover:bg-gray-200 transition-colors duration-100"
						onclick={sendEdit}>{$t('chat.send')}</button
					>
				</div>
			</div>
		</div>
	{:else}
		<!-- Bubble: right-aligned -->
		{#if meta?.files?.length > 0}
			<div class="mb-1 w-full flex flex-col justify-end overflow-x-auto gap-1 flex-wrap">
				{#each meta.files as upload}
					<div class="self-end">
						{#if upload.type === 'image'}
							<img src={upload.url} alt={upload.name || 'image'} class="max-h-96 rounded-lg" />
						{:else}
							<div
								class="app-surface relative group py-1.5 px-2 w-48 flex items-center gap-1.5 border rounded-xl text-left flex-shrink-0 shadow-sm"
							>
								<div class="shrink-0">
									<Icon name="page-text" size={14} class="app-icon-muted" />
								</div>
								<div class="flex flex-col justify-center w-full overflow-hidden">
									<div class="text-xs flex justify-between items-center w-full gap-2">
										<div class="font-medium truncate flex-1">{upload.name || 'File'}</div>
										<div class="app-muted text-[0.625rem] capitalize shrink-0">
											{upload.type === 'file' ? 'File' : upload.type || 'File'}
										</div>
									</div>
								</div>
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
		<div class="flex justify-end">
			<div class="max-w-[90%] px-4 py-2 rounded-3xl app-interactive-active">
				<div class="text-[0.8125rem] leading-relaxed text-gray-900 dark:text-gray-200 break-words">
					{#each parsedContent as segment}{#if segment.type === 'text'}<MarkdownRenderer
								content={segment.value}
							/>{:else}{@const isDir = segment.path.endsWith('/')}{@const cleanPath = isDir
								? segment.path.slice(0, -1)
								: segment.path}<button
								class="inline-flex items-center gap-0.5 bg-blue-500/10 text-blue-400 rounded px-1.5 py-px mx-0.5 text-xs font-mono align-baseline border-none cursor-pointer hover:bg-blue-500/20 transition-colors"
								title={cleanPath}
								onclick={(e) => {
									e.preventDefault();
									if (isDir) {
										setFileBrowserCwd(cleanPath);
										setActiveTab('files');
									} else {
										openFileTab(cleanPath);
									}
								}}
								><Icon
									name={isDir ? 'folder' : fileIconName(segment.label, 'file')}
									size={11}
								/>{segment.label}</button
							>{/if}{/each}
				</div>
			</div>
		</div>
		{#if siblingTotal > 1 || onedit}
			<div
				class="group/timestamp-toolbar flex w-full justify-end items-center gap-1 mt-1 invisible group-hover:visible"
			>
				<MessageTimestamp {createdAt} side="left" />
				{#if siblingTotal > 1}
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-30 disabled:cursor-default transition-colors duration-100"
						disabled={siblingIndex === 0}
						onclick={() => onnavigate?.(-1)}
						aria-label={$t('chat.prevMessage')}
						use:tooltip={$t('chat.prevMessage')}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M15 19l-7-7 7-7"
							/></svg
						>
					</button>
					<span class="text-[0.6875rem] tabular-nums text-gray-400 dark:text-gray-600 select-none"
						>{siblingIndex + 1}/{siblingTotal}</span
					>
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-30 disabled:cursor-default transition-colors duration-100"
						disabled={siblingIndex === siblingTotal - 1}
						onclick={() => onnavigate?.(1)}
						aria-label={$t('chat.nextMessage')}
						use:tooltip={$t('chat.nextMessage')}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M9 5l7 7-7 7"
							/></svg
						>
					</button>
				{/if}
				{#if onedit}
					<button
						class="p-1 rounded-md text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={startEdit}
						aria-label={$t('chat.editMessage')}
						use:tooltip={$t('chat.editMessage')}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							class="w-3.5 h-3.5"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125"
							/>
						</svg>
					</button>
				{/if}
				<button
					class="p-1 rounded-md text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={copyContent}
					aria-label={$t('chat.copyMessage')}
					use:tooltip={copied ? $t('about.copied') : $t('chat.copyMessage')}
				>
					{#if copied}
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2.5"
							stroke="currentColor"
							class="w-3.5 h-3.5"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M4.5 12.75l6 6 9-13.5"
							/></svg
						>
					{:else}
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							class="w-3.5 h-3.5"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184"
							/></svg
						>
					{/if}
				</button>
			</div>
		{/if}
	{/if}
</div>
