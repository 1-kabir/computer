<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Icon from '../Icon.svelte';
	import CreateBotModal from './CreateBotModal.svelte';
	import { onMount } from 'svelte';
	import { listBots, deleteBot, startBot, stopBot, type BotData } from '$lib/apis/bots';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import { fetchJSON } from '$lib/apis';
	import { updateConfig } from '$lib/apis/admin';

	let bots = $state<BotData[]>([]);
	let loading = $state(true);
	let systemPrompt = $state('');
	let promptSaving = $state(false);
	let showVariables = $state(false);

	// Mirrors the default system prompt users get without an override (same
	// text as the Models tab), so the placeholder shows what "empty" means.
	const DEFAULT_PROMPT_PLACEHOLDER = `You are Computer, a helpful assistant running inside the user's computer interface. You have access to tools to read, search, and modify files in the workspace, run commands, and use configured tools. Use them to help the user directly. Approach hard requests with initiative and persistence: make the best possible attempt, adapt as needed, and keep going unless a real constraint prevents progress.

{{CPTR_CONTEXT}}

{{MEMORY}}

{{INSTRUCTIONS}}

{{SKILLS}}

Workspace: {{WORKSPACE_NAME}}
Files:
{{FILE_TREE}}`;

	const TEMPLATE_VARIABLES = [
		{ name: 'CPTR_CONTEXT', desc: 'Runtime, machine, workspace, and tool context' },
		{ name: 'WORKSPACE_NAME', desc: 'Workspace folder name' },
		{ name: 'FILE_TREE', desc: 'File listing (top-level + 1 depth)' },
		{ name: 'INSTRUCTIONS', desc: 'MEMORY.md / AGENTS.md / CLAUDE.md content' },
		{ name: 'MEMORY', desc: 'Saved memory content' },
		{ name: 'SKILLS', desc: 'Available skills list' }
	];

	async function loadSystemPrompt() {
		try {
			const cfg = await fetchJSON<{ config: Record<string, unknown> }>(
				'/api/admin/config/gateway'
			);
			systemPrompt =
				typeof cfg.config['gateway.system_prompt'] === 'string'
					? (cfg.config['gateway.system_prompt'] as string)
					: '';
		} catch {
			// non-fatal: the prompt stays editable with the default (empty) value
		}
	}

	async function saveSystemPrompt() {
		promptSaving = true;
		try {
			await updateConfig({ 'gateway.system_prompt': systemPrompt });
		} catch {
			toast.error($t('messaging.failedToLoad'));
		} finally {
			promptSaving = false;
		}
	}

	let showCreate = $state(false);
	let editBot = $state<BotData | null>(null);

	async function load() {
		try {
			bots = await listBots();
		} catch {
			toast.error($t('messaging.failedToLoad'));
		} finally {
			loading = false;
		}
	}

	function handleSaved() {
		showCreate = false;
		editBot = null;
		load();
	}

	async function toggleRunning(bot: BotData) {
		const wasRunning = bot.is_running;
		// Optimistic
		bot.is_running = !wasRunning;
		bots = [...bots];
		try {
			if (wasRunning) {
				await stopBot(bot.id);
			} else {
				await startBot(bot.id);
			}
			await load();
		} catch {
			bot.is_running = wasRunning;
			bots = [...bots];
			toast.error($t('messaging.failedToToggle'));
		}
	}

	async function handleDelete(e: Event, bot: BotData) {
		e.stopPropagation();
		try {
			await deleteBot(bot.id);
			await load();
		} catch {
			toast.error($t('messaging.failedToDelete'));
		}
	}

	onMount(() => {
		load();
		loadSystemPrompt();
	});
</script>

<div class="mb-5 border-b border-gray-100 dark:border-white/5 pb-4">
	<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 uppercase tracking-wide">
		{$t('admin.messaging.systemPrompt')}
	</span>
	<textarea
		class="w-full mt-1 bg-gray-50 dark:bg-white/4 border border-gray-200 dark:border-white/8 rounded-lg px-2.5 py-2 text-[0.6875rem] font-mono text-gray-600 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none resize-y leading-relaxed"
		rows="6"
		bind:value={systemPrompt}
		placeholder={DEFAULT_PROMPT_PLACEHOLDER}
		spellcheck="false"
	></textarea>
	<p class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-1">
		{$t('admin.messaging.systemPromptHint')}
	</p>
	<div class="flex items-center justify-between mt-1 gap-2">
		<button
			class="text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
			onclick={() => (showVariables = !showVariables)}
		>
			{$t('models.templateVariables')}
			{showVariables ? '▾' : '▸'}
		</button>
		<div class="flex items-center gap-2">
			{#if systemPrompt.trim()}
				<button
					class="text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
					onclick={() => (systemPrompt = '')}
				>
					{$t('models.resetToDefault')}
				</button>
			{/if}
			<button
				type="button"
				class="text-[0.625rem] px-2 py-1 rounded-md bg-gray-900 text-white dark:bg-white dark:text-gray-900 disabled:opacity-50"
				disabled={promptSaving}
				onclick={saveSystemPrompt}
			>
				{promptSaving ? $t('settings.saving') : $t('settings.save')}
			</button>
		</div>
	</div>
	{#if showVariables}
		<div
			class="mt-1 rounded-lg bg-gray-50 dark:bg-white/3 border border-gray-100 dark:border-white/5 px-2.5 py-2"
		>
			{#each TEMPLATE_VARIABLES as v}
				<div class="flex items-baseline gap-2 h-5">
					<code
						class="text-[0.625rem] font-mono text-gray-500 dark:text-gray-500 shrink-0 select-all"
					>
						{'{{' + v.name + '}}'}
					</code>
					<span class="text-[0.625rem] text-gray-400 dark:text-gray-600">{v.desc}</span>
				</div>
			{/each}
		</div>
	{/if}
</div>

<div class="flex items-center justify-between mb-4">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('admin.messaging')}</h2>
	<button
		class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
		onclick={() => (showCreate = true)}
	>
		<Icon name="plus" size={14} />
	</button>
</div>

{#if loading}
	<div class="flex justify-center py-8">
		<Spinner size={16} />
	</div>
{:else}
	<div>
		{#each bots as bot}
			<div class="group flex items-center gap-2 w-full h-7">
				<!-- Platform icon -->
				<span
					class="shrink-0
					{bot.is_running ? 'text-gray-400 dark:text-gray-500' : 'text-gray-300 dark:text-gray-700'}"
				>
					<Icon name={bot.platform} size={14} />
				</span>

				<!-- Name (clickable to edit) -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<span
					class="flex-1 text-[0.8125rem] truncate cursor-pointer
					{bot.is_running ? 'text-gray-700 dark:text-gray-300' : 'text-gray-400 dark:text-gray-600'}"
					onclick={() => (editBot = bot)}
					onkeydown={() => {}}
				>
					{bot.name}
				</span>

				<!-- Delete (hover) -->
				<button
					type="button"
					class="opacity-0 group-hover:opacity-100 text-gray-300 dark:text-gray-700 hover:text-red-500 dark:hover:text-red-400 transition-all p-0.5"
					onclick={(e) => handleDelete(e, bot)}
				>
					<Icon name="trash" size={11} />
				</button>

				<ToggleSwitch value={bot.is_running} onchange={() => toggleRunning(bot)} />
			</div>
		{/each}

		{#if bots.length === 0}
			<p class="text-[0.8125rem] text-gray-400 dark:text-gray-600 py-4">{$t('messaging.noBots')}</p>
		{/if}
	</div>
{/if}

{#if showCreate}
	<CreateBotModal onclose={() => (showCreate = false)} onsave={handleSaved} />
{/if}

{#if editBot}
	<CreateBotModal bot={editBot} onclose={() => (editBot = null)} onsave={handleSaved} />
{/if}
