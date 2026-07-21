<script lang="ts">
	import { tick } from 'svelte';
	import { get } from 'svelte/store';
	import MarkdownRenderer from '$lib/components/markdown/MarkdownRenderer.svelte';
	import OutputEditView from './OutputEditView.svelte';
	import ChatFilePreview from './ChatFilePreview.svelte';
	import ConsecutiveActivityGroup from './ConsecutiveActivityGroup.svelte';
	import MessageTimestamp from './MessageTimestamp.svelte';
	import ReasoningCollapsible from './ReasoningCollapsible.svelte';
	import ToolCallCollapsible from './ToolCallCollapsible.svelte';
	import { currentWorkspace, openFileTab } from '$lib/stores';
	import { ttsConfigured, ttsEnabled } from '$lib/stores/audio';
	import { tooltip } from '$lib/tooltip';
	import { fileIconName } from '$lib/utils/fileIcon';
	import Icon from '../Icon.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		content: string;
		meta?: Record<string, any> | null;
		done: boolean;
		output: any[] | null;
		usage: Record<string, number> | null;
		chatId: string | null;
		messageId: string;
		createdAt?: number | null;
		siblingIndex?: number;
		siblingTotal?: number;
		speaking?: boolean;
		onapprove: (messageId: string, callId: string, approved: boolean) => void;
		onnavigate?: (direction: -1 | 1) => void;
		onfork?: () => void;
		onregenerate?: () => void;
		onedit?: (content: string, output: any[] | null, submit: boolean) => void;
		onspeak?: () => void;
	}
	let {
		content,
		meta = null,
		done,
		output,
		usage,
		chatId,
		messageId,
		createdAt = null,
		siblingIndex = 0,
		siblingTotal = 1,
		speaking = false,
		onapprove,
		onnavigate,
		onfork,
		onregenerate,
		onedit,
		onspeak
	}: Props = $props();

	let edit = $state(false);
	let editedContent = $state('');
	let editedOutput = $state<any[] | null>(null);
	let copied = $state(false);
	let showUsageTooltip = $state(false);
	let collapsedFiles = $state<Record<string, boolean>>({});
	let textareaEl: HTMLTextAreaElement;

	async function startEdit() {
		edit = true;
		if (output?.length) {
			editedOutput = JSON.parse(JSON.stringify(output));
		} else {
			editedContent = content;
		}
		await tick();
		if (!editedOutput && textareaEl) {
			textareaEl.style.height = '';
			textareaEl.style.height = `${textareaEl.scrollHeight}px`;
			textareaEl.focus();
		}
	}

	function cancelEdit() {
		edit = false;
		editedContent = '';
		editedOutput = null;
	}

	function saveEdit() {
		if (editedOutput) {
			onedit?.(content, editedOutput, false);
		} else {
			onedit?.(editedContent, null, false);
		}
		edit = false;
		editedContent = '';
		editedOutput = null;
	}

	function saveAsCopy() {
		if (editedOutput) {
			onedit?.(content, editedOutput, true);
		} else {
			onedit?.(editedContent, null, true);
		}
		edit = false;
		editedContent = '';
		editedOutput = null;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') cancelEdit();
	}

	function autoResize(e: Event) {
		const el = e.target as HTMLTextAreaElement;
		el.style.height = '';
		el.style.height = `${el.scrollHeight}px`;
	}

	function copyContent() {
		const text =
			(output || [])
				.filter((i: any) => i.type === 'message')
				.flatMap((i: any) => i.content || [])
				.map((c: any) => c.text)
				.join('') || content;
		navigator.clipboard.writeText(text);
		copied = true;
		setTimeout(() => {
			copied = false;
		}, 1500);
	}

	function shareContent() {
		const text =
			(output || [])
				.filter((i: any) => i.type === 'message')
				.flatMap((i: any) => i.content || [])
				.map((c: any) => c.text)
				.join('') || content;
		if ('share' in navigator) {
			navigator.share({ text }).catch(() => {});
		}
	}

	/** Shorten a file path to just the basename for compact display */
	function shortPath(p: string | undefined): string {
		if (!p) return '?';
		const parts = p.split('/').filter(Boolean);
		if (parts.length === 0) return p;
		return parts[parts.length - 1];
	}

	function resolvedFilePath(file: any): string {
		const path = String(file?.full_path || file?.path || '');
		if (!path || path.startsWith('/')) return path;
		const workspace = file?.workspace || get(currentWorkspace)?.path || '';
		return workspace ? `${workspace.replace(/\/$/, '')}/${path.replace(/^\/+/, '')}` : path;
	}

	function toggleFile(key: string) {
		collapsedFiles = { ...collapsedFiles, [key]: !collapsedFiles[key] };
	}

	function openArtifact(artifact: any) {
		const workspace = get(currentWorkspace);
		const path =
			workspace && artifact.path
				? `${workspace.path.replace(/\/$/, '')}/${artifact.path}`
				: artifact.path;
		if (path) openFileTab(path);
	}

	/** Human-readable label for a tool call */
	function toolLabel(name: string, args: any): string {
		const _t = $t;
		switch (name) {
			case 'read_file': {
				const p = shortPath(args.path);
				if (args.start_line)
					return _t('chat.tool.readFileRange', {
						path: p,
						range: `${args.start_line}–${args.end_line || 'end'}`
					});
				return _t('chat.tool.readFile', { path: p });
			}
			case 'edit_file':
				return _t('chat.tool.editFile', { path: shortPath(args.path) });
			case 'multi_edit_file':
				return _t('chat.tool.multiEditFile', { path: shortPath(args.path) });
			case 'create_file':
				return _t('chat.tool.createFile', { path: shortPath(args.path) });
			case 'write_file':
				return _t('chat.tool.writeFile', { path: shortPath(args.path) });
			case 'display_file':
				return `Display ${shortPath(args.path)}`;
			case 'list_directory':
				return args.recursive
					? _t('chat.tool.listDirectoryRecursive', { path: shortPath(args.path) })
					: _t('chat.tool.listDirectory', { path: shortPath(args.path) });
			case 'search_files': {
				const scope = args.include
					? _t('chat.tool.searchFilesScope', { include: args.include })
					: '';
				return _t('chat.tool.searchFiles', { query: args.query || '?', scope });
			}
			case 'run_command':
				return args.background
					? _t('chat.tool.backgroundCommand', { command: args.command || '?' })
					: args.command || '?';
			case 'check_task':
				return _t('chat.tool.checkTask', { id: args.task_id || '?' });
			case 'kill_task':
				return _t('chat.tool.killTask', { id: args.task_id || '?' });
			case 'image_generate':
				return args.image || args.images?.length ? 'Edit image' : 'Generate image';
			case 'ask_user': {
				const count = Array.isArray(args.questions) ? args.questions.length : 0;
				return `Asked ${count} question${count === 1 ? '' : 's'}`;
			}
			case 'web_search':
				return _t('chat.tool.webSearch', { query: args.query || '?' });
			case 'read_url': {
				try {
					return _t('chat.tool.fetchUrl', { hostname: new URL(args.url).hostname });
				} catch {
					return _t('chat.tool.fetchUrlFallback');
				}
			}
			case 'delegate_task': {
				const t = args.task || '?';
				const label = args.background ? 'Background sub-agent' : 'Sub-agent';
				return `${label}: "${t.length > 60 ? t.slice(0, 60) + '…' : t}"`;
			}
			case 'agent_tool':
				return args.title || 'Agent tool';
			default: {
				// External tool: {server_id}_{tool_name} → "tool_name (server_id)"
				const idx = name.indexOf('_');
				if (idx > 0) {
					const serverId = name.slice(0, idx);
					const toolName = name.slice(idx + 1);
					return `${toolName} (${serverId})`;
				}
				return name;
			}
		}
	}

	// ── Consecutive grouping logic ────────────────────────────────
	// Groups consecutive assistant activity (reasoning + tool calls), breaking on message items.

	interface ActivityGroup {
		type: 'activity_group';
		entries: any[]; // ordered reasoning/function_call items
		calls: any[]; // function_call items
		reasoning: any[];
		outputs: Map<string, any>; // call_id → function_call_output
	}

	interface MessageItem {
		type: 'message_item';
		item: any;
	}

	interface ArtifactItem {
		type: 'artifact_item';
		item: any;
	}

	interface ImageItem {
		type: 'image_item';
		item: any;
	}

	interface FileItem {
		type: 'file_item';
		item: any;
		index: number;
	}

	type DisplayItem = ActivityGroup | MessageItem | ArtifactItem | ImageItem | FileItem;

	const outputText = $derived.by((): string => {
		return (output || [])
			.filter((i: any) => i.type === 'message')
			.filter((i: any) => messageItemText(i).trim())
			.flatMap((i: any) => i.content || [])
			.map((c: any) => c.text || '')
			.join('');
	});

	const structuredImageUrls = $derived.by((): string[] => {
		return (output || [])
			.filter((i: any) => i.type === 'image')
			.flatMap((i: any) => i.images || [])
			.map((image: any) => image?.url)
			.filter((url: any): url is string => typeof url === 'string' && url.length > 0);
	});

	const unrenderedContent = $derived.by((): string => {
		if (!content) return '';
		if (!outputText) return content;
		return content.startsWith(outputText) ? content.slice(outputText.length) : '';
	});

	function messageItemText(item: any): string {
		return (item.content || []).map((c: any) => c.text || '').join('');
	}

	function escapeRegExp(value: string): string {
		return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	}

	function stripStructuredImageMarkdown(text: string): string {
		let cleaned = text;
		for (const url of structuredImageUrls) {
			const escapedUrl = escapeRegExp(url);
			cleaned = cleaned.replace(new RegExp(`!\\[[^\\]]*\\]\\(${escapedUrl}\\)`, 'g'), '');
			cleaned = cleaned.replace(
				new RegExp(`(^|\\n)[ \\t]*${escapedUrl}[ \\t]*(?=\\n|$)`, 'g'),
				'$1'
			);
		}
		return cleaned.replace(/\n{3,}/g, '\n\n').trim();
	}

	const displayItems = $derived.by((): DisplayItem[] => {
		if (!output?.length) return [];

		const items: DisplayItem[] = [];
		let currentGroup: ActivityGroup | null = null;

		// Collect all function_call_output items for lookup
		const outputMap = new Map<string, any>();
		for (const item of output) {
			if (item.type === 'function_call_output' && item.call_id) {
				outputMap.set(item.call_id, item);
			}
		}

		const ensureGroup = () => {
			if (!currentGroup) {
				currentGroup = {
					type: 'activity_group',
					entries: [],
					calls: [],
					reasoning: [],
					outputs: outputMap
				};
			}
		};

		const flushGroup = () => {
			if (currentGroup && (currentGroup.calls.length > 0 || currentGroup.reasoning.length > 0)) {
				items.push(currentGroup);
				currentGroup = null;
			}
		};

		for (const [index, item] of output.entries()) {
			if (item.type === 'function_call') {
				if (item.name !== 'ask_user' || item.status !== 'pending') {
					ensureGroup();
					currentGroup!.entries.push(item);
					currentGroup!.calls.push(item);
				}
			} else if (item.type === 'reasoning') {
				ensureGroup();
				currentGroup!.entries.push(item);
				currentGroup!.reasoning.push(item);
			} else if (item.type === 'message') {
				// Some providers emit empty message shells between reasoning/tool items.
				// Only visible assistant text should break a consecutive activity run.
				if (messageItemText(item).trim()) {
					flushGroup();
					items.push({ type: 'message_item', item });
				}
			} else if (item.type === 'artifact') {
				flushGroup();
				items.push({ type: 'artifact_item', item });
			} else if (item.type === 'image') {
				flushGroup();
				items.push({ type: 'image_item', item });
			} else if (item.type === 'file') {
				flushGroup();
				items.push({ type: 'file_item', item, index });
			}
			// function_call_output items are handled via outputMap, skip standalone render
		}
		flushGroup();

		return items;
	});

	/** Format usage data for tooltip display */
	function formatUsageLabel(key: string): string {
		return key.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
	}

	function formatUsageValue(key: string, value: number): string {
		if (key.includes('time') || key.includes('duration') || key.includes('latency')) {
			if (value >= 1000) return `${(value / 1000).toFixed(2)}s`;
			return `${value.toFixed(0)}ms`;
		}
		if (key.includes('token') || key.includes('count')) {
			return value.toLocaleString();
		}
		if (Number.isInteger(value)) return value.toLocaleString();
		return value.toFixed(2);
	}
</script>

<div class="flex flex-col gap-1">
	{#if edit}
		<!-- Edit mode -->
		<div class="w-full">
			<div class="app-subtle-surface rounded-xl border px-3.5 py-2.5">
				{#if editedOutput}
					<OutputEditView
						output={editedOutput}
						onChange={(updated: any) => {
							editedOutput = updated;
						}}
					/>
				{:else}
					<textarea
						bind:this={textareaEl}
						bind:value={editedContent}
						class="w-full bg-transparent outline-none resize-none text-[0.8125rem] leading-relaxed text-gray-900 dark:text-gray-200"
						oninput={autoResize}
						onkeydown={handleKeydown}
						rows="1"
					></textarea>
				{/if}
			</div>
			<div class="flex justify-between mt-2 text-[0.75rem] font-medium">
				<button
					class="px-3 py-1 rounded-lg text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={saveAsCopy}>{$t('chat.saveAs')}</button
				>
				<div class="flex gap-1.5">
					<button
						class="px-3 py-1 rounded-lg text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={cancelEdit}>{$t('common.cancel')}</button
					>
					<button
						class="px-3 py-1 rounded-lg bg-gray-900 dark:bg-white text-white dark:text-black hover:bg-gray-700 dark:hover:bg-gray-200 transition-colors duration-100"
						onclick={saveEdit}>{$t('common.save')}</button
					>
				</div>
			</div>
		</div>
	{:else}
		<!-- Normal display -->
		<div>
			{#if !done && (!output || output.length === 0)}
				<MarkdownRenderer {content} /><span
					class="inline-block w-[0.125rem] h-3.5 bg-gray-400 dark:bg-gray-500 ml-0.5 animate-pulse align-text-bottom"
				></span>
			{:else}
				{#if done && displayItems.length === 0 && content}
					<MarkdownRenderer {content} />
				{/if}
				{#each displayItems as displayItem, groupIdx}
					{#if displayItem.type === 'message_item'}
						{@const messageText = stripStructuredImageMarkdown(messageItemText(displayItem.item))}
						{#if messageText}
							<MarkdownRenderer content={messageText} />
						{/if}
					{:else if displayItem.type === 'artifact_item'}
						{@const artifact = displayItem.item}
						{@const preview = (artifact.content || '').replace(/^#.*\n*/m, '').trim()}
						<button
							class="block w-full min-w-0 overflow-hidden text-left my-2 rounded-xl cursor-pointer
						border border-gray-200 dark:border-white/8
						hover:border-gray-300 dark:hover:border-white/12
						hover:bg-gray-50/50 dark:hover:bg-white/[0.03]
						transition-colors duration-150 {preview ? 'h-[4.375rem]' : 'h-[2.375rem]'}"
							onclick={() => openArtifact(artifact)}
						>
							<div class="h-full min-w-0 overflow-hidden px-3 py-2.5">
								<div
									class="h-4 truncate text-xs leading-4 font-medium text-gray-800 dark:text-gray-100"
								>
									{artifact.title || $t('chat.artifact')}
								</div>
								{#if preview}
									<div class="h-8 overflow-hidden">
										<div
											class="line-clamp-2 break-words text-[0.625rem] leading-4 font-normal text-gray-400 dark:text-gray-500"
										>
											{preview}
										</div>
									</div>
								{/if}
							</div>
						</button>
					{:else if displayItem.type === 'image_item'}
						<div class="my-2 grid max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
							{#each displayItem.item.images || [] as image}
								<a
									href={image.url}
									target="_blank"
									rel="noreferrer"
									class="block overflow-hidden rounded-lg border border-gray-200 dark:border-white/8 bg-gray-50 dark:bg-white/[0.03]"
								>
									<img
										src={image.url}
										alt={image.name || 'Generated image'}
										class="max-h-96 w-full object-contain"
									/>
								</a>
							{/each}
						</div>
					{:else if displayItem.type === 'file_item'}
						{@const file = displayItem.item}
						{@const filePath = resolvedFilePath(file)}
						{@const fileKey = filePath || file.path || `file-${displayItem.index}`}
						{@const collapsed = Boolean(collapsedFiles[fileKey])}
						<div
							class="my-2 w-full max-w-2xl overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-white/8 dark:bg-gray-950/20"
						>
							<div
								class="flex h-8 items-center {collapsed
									? ''
									: 'border-b border-gray-100 dark:border-white/8'}"
							>
								<button
									type="button"
									class="flex h-full min-w-0 flex-1 items-center gap-2 px-2.5 text-left"
									onclick={() => toggleFile(fileKey)}
									aria-expanded={!collapsed}
								>
									<div
										class="flex size-5 shrink-0 items-center justify-center text-gray-500 dark:text-gray-400"
									>
										<Icon name={fileIconName(file.name || file.path || '', 'file')} size={14} />
									</div>
									<div
										class="min-w-0 flex-1 truncate text-xs font-medium text-gray-800 dark:text-gray-100"
									>
										{file.name || shortPath(file.path)}
									</div>
								</button>
								<button
									type="button"
									class="mr-1 flex size-6 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-200"
									onclick={(event) => {
										event.stopPropagation();
										if (filePath) openFileTab(filePath);
									}}
									aria-label={$t('directory.open', { name: file.name || shortPath(file.path) })}
									use:tooltip={$t('chat.openAsTab')}
								>
									<Icon name="external-link" size={13} />
								</button>
							</div>
							{#if !collapsed}
								<ChatFilePreview {file} {filePath} />
							{/if}
						</div>
					{:else if displayItem.type === 'activity_group'}
						{#if displayItem.entries.length === 1}
							{@const item = displayItem.entries[0]}
							{#if item.type === 'reasoning'}
								<ReasoningCollapsible {item} fallbackId={`reasoning-${groupIdx}-0`} />
							{:else}
								<ToolCallCollapsible
									{item}
									pairedOutput={displayItem.outputs.get(item.call_id)}
									{done}
									{chatId}
									{messageId}
									{toolLabel}
									{onapprove}
								/>
							{/if}
						{:else}
							<ConsecutiveActivityGroup
								entries={displayItem.entries}
								calls={displayItem.calls}
								reasoning={displayItem.reasoning}
								outputs={displayItem.outputs}
								{done}
								{chatId}
								{messageId}
								{groupIdx}
								{toolLabel}
								{onapprove}
							/>
						{/if}
					{/if}
				{/each}
				{#if done && unrenderedContent && displayItems.length > 0}
					{@const leftoverText = stripStructuredImageMarkdown(unrenderedContent)}
					{#if leftoverText}
						<MarkdownRenderer content={leftoverText} />
					{/if}
				{:else if !done}
					{@const leftoverText = stripStructuredImageMarkdown(unrenderedContent)}
					{#if leftoverText}
						<MarkdownRenderer content={leftoverText} />
					{/if}
					<span
						class="inline-block w-[0.125rem] h-3.5 bg-gray-400 dark:bg-gray-500 ml-0.5 animate-pulse align-text-bottom"
					></span>
				{/if}
			{/if}
		</div>

		<!-- Controls toolbar -->
		{#if done || siblingTotal > 1}
			<div class="group/timestamp-toolbar flex items-center gap-1 mt-1 -ml-0.5">
				{#if siblingTotal > 1}
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-30 disabled:cursor-default transition-colors duration-100"
						disabled={siblingIndex === 0}
						onclick={() => onnavigate?.(-1)}
						aria-label={$t('chat.prevResponse')}
						use:tooltip={$t('chat.prevResponse')}
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
						aria-label={$t('chat.nextResponse')}
						use:tooltip={$t('chat.nextResponse')}
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
				{#if done && onedit}
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={startEdit}
						aria-label={$t('chat.editResponse')}
						use:tooltip={$t('chat.editResponse')}
					>
						<svg
							class="w-3.5 h-3.5"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
							stroke-width="2"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125"
							/></svg
						>
					</button>
				{/if}
				{#if done}
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={copyContent}
						aria-label={$t('chat.copyResponse')}
						use:tooltip={copied ? $t('about.copied') : $t('chat.copyResponse')}
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
				{/if}
				{#if done && $ttsEnabled && $ttsConfigured}
					<button
						class="p-0.5 rounded transition-colors duration-100
							{speaking
							? 'text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-white/10'
							: 'text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300'}"
						onclick={onspeak}
						aria-label={speaking ? $t('chat.stopSpeaking') : $t('chat.speakResponses')}
						use:tooltip={speaking ? $t('chat.stopSpeaking') : $t('chat.speakResponses')}
					>
						<Icon name="speaker" size={14} />
					</button>
				{/if}
				{#if done && onregenerate}
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={onregenerate}
						aria-label={$t('chat.regenerateResponse')}
						use:tooltip={$t('chat.regenerateResponse')}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
							/></svg
						>
					</button>
				{/if}
				{#if done && onfork}
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={onfork}
						aria-label={$t('chat.forkResponse')}
						use:tooltip={$t('chat.forkResponse')}
					>
						<Icon name="chat-fork" size={14} strokeWidth={1.8} />
					</button>
				{/if}
				{#if done && usage && Object.keys(usage).length > 0}
					<div class="relative flex items-center">
						<button
							class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
							onclick={() => (showUsageTooltip = !showUsageTooltip)}
							onmouseenter={() => (showUsageTooltip = true)}
							onmouseleave={() => (showUsageTooltip = false)}
							aria-label={$t('chat.usageInfo')}
							use:tooltip={showUsageTooltip ? null : $t('chat.usageInfo')}
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
									d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z"
								/>
							</svg>
						</button>
						{#if showUsageTooltip}
							<div
								class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 z-50
									app-surface rounded-lg shadow-lg px-2.5 py-1.5 text-[0.625rem] font-mono
									whitespace-nowrap pointer-events-none
									min-w-[10rem] border"
							>
								<div class="space-y-0.5">
									{#each Object.entries(usage) as [key, value]}
										<div class="flex justify-between gap-4">
											<span class="app-muted">{formatUsageLabel(key)}</span>
											<span class="tabular-nums">{formatUsageValue(key, value)}</span>
										</div>
									{/each}
								</div>
								<!-- Arrow -->
								<div
									class="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0
									border-l-[0.3125rem] border-l-transparent
									border-r-[0.3125rem] border-r-transparent
									border-t-[0.3125rem]"
									style="border-top-color: var(--app-bg);"
								></div>
							</div>
						{/if}
					</div>
				{/if}
				{#if done && 'share' in navigator}
					<button
						class="p-0.5 rounded text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={shareContent}
						aria-label={$t('chat.share')}
						use:tooltip={$t('chat.share')}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12M12 4l-4 4M12 4l4 4"
							/></svg
						>
					</button>
				{/if}
				<MessageTimestamp {createdAt} />
			</div>
		{/if}
	{/if}
</div>
