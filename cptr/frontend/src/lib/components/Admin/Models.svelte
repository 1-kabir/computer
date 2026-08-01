<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getModelConfig,
		refreshModelList,
		updateModelConfig,
		getAdminConfig,
		updateConfig,
		type ModelConfigEntry
	} from '$lib/apis/admin';
	import { t } from '$lib/i18n';
	import { tooltip } from '$lib/tooltip';
	import { refreshChatState } from '$lib/stores/chat';
	import Icon from '../Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ModelSelector from '$lib/components/common/ModelSelector.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';

	type ParamRow = { key: string; value: string };
	type BuiltinToolsConfig = Record<string, boolean>;
	type ModelEntry = {
		id: string;
		name: string;
		provider: string;
		is_active: boolean;
		rows: ParamRow[];
		systemPrompt: string;
		compactTokenThreshold: number | null;
		builtinTools: BuiltinToolsConfig;
		dirty: boolean;
	};

	let loading = $state(true);
	let saving = $state(false);
	let refreshing = $state(false);
	let models = $state<ModelEntry[]>([]);
	let selectedId = $state<string | null>(null);

	let globalRows = $state<ParamRow[]>([]);
	let globalSystemPrompt = $state('');
	let globalCompactTokenThreshold = $state<number | null>(null);
	let globalBuiltinTools = $state<BuiltinToolsConfig>({});
	let globalDirty = $state(false);
	let globalExpanded = $state(false);
	let showVariables = $state(false);

	// Default model
	let defaultModelId = $state('');

	const TEMPLATE_VARIABLES = [
		{ name: 'CPTR_CONTEXT', desc: 'Runtime, machine, workspace, and tool context' },
		{ name: 'RUNTIME_ENV', desc: 'Runtime environment (host or container)' },
		{ name: 'HOSTNAME', desc: 'Machine hostname' },
		{ name: 'WORKSPACE_NAME', desc: 'Workspace folder name' },
		{ name: 'WORKSPACE_PATH', desc: 'Full workspace path' },
		{ name: 'FILE_TREE', desc: 'File listing (top-level + 1 depth)' },
		{ name: 'INSTRUCTIONS', desc: 'MEMORY.md / AGENTS.md / CLAUDE.md content' },
		{ name: 'OS', desc: 'Operating system (macOS, Linux, Windows)' },
		{ name: 'PLATFORM', desc: 'Detailed platform string' },
		{ name: 'ARCH', desc: 'Machine architecture' },
		{ name: 'SHELL', desc: 'Default shell path' },
		{ name: 'HOME', desc: 'Home directory' },
		{ name: 'CPTR_VERSION', desc: 'Computer version' },
		{ name: 'DATE', desc: 'Current date (ISO format)' },
		{ name: 'MODEL', desc: 'Model ID being used' }
	];

	const BUILTIN_TOOL_GROUPS = [
		{ id: 'files', label: 'models.builtinTools.files', desc: 'models.builtinTools.filesDesc' },
		{
			id: 'terminal',
			label: 'models.builtinTools.terminal',
			desc: 'models.builtinTools.terminalDesc'
		},
		{ id: 'web', label: 'models.builtinTools.web', desc: 'models.builtinTools.webDesc' },
		{
			id: 'browser',
			label: 'models.builtinTools.browser',
			desc: 'models.builtinTools.browserDesc'
		},
		{ id: 'memory', label: 'models.builtinTools.memory', desc: 'models.builtinTools.memoryDesc' },
		{ id: 'chats', label: 'models.builtinTools.chats', desc: 'models.builtinTools.chatsDesc' },
		{ id: 'skills', label: 'models.builtinTools.skills', desc: 'models.builtinTools.skillsDesc' },
		{ id: 'tasks', label: 'models.builtinTools.tasks', desc: 'models.builtinTools.tasksDesc' },
		{
			id: 'automations',
			label: 'models.builtinTools.automations',
			desc: 'models.builtinTools.automationsDesc'
		},
		{ id: 'images', label: 'models.builtinTools.images', desc: 'models.builtinTools.imagesDesc' },
		{
			id: 'subagents',
			label: 'models.builtinTools.subagents',
			desc: 'models.builtinTools.subagentsDesc'
		},
		{
			id: 'notifications',
			label: 'models.builtinTools.notifications',
			desc: 'models.builtinTools.notificationsDesc'
		}
	];

	const DEFAULT_PROMPT_PLACEHOLDER = `You are Computer, a helpful assistant running inside the user's computer interface. You have access to tools to read, search, and modify files in the workspace, run commands, and use configured tools. Use them to help the user directly. Approach hard requests with initiative and persistence: make the best possible attempt, adapt as needed, and keep going unless a real constraint prevents progress.

{{CPTR_CONTEXT}}

{{INSTRUCTIONS}}

{{SKILLS}}

Workspace: {{WORKSPACE_NAME}}
Files:
{{FILE_TREE}}`;

	let hasDirty = $derived(globalDirty || models.some((m) => m.dirty));

	function parseRows(config: ModelConfigEntry | undefined): ParamRow[] {
		const rp = config?.params?.request_params;
		if (!rp || typeof rp !== 'object') return [];
		return Object.entries(rp).map(([key, value]) => ({
			key,
			value: typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
		}));
	}

	function parseCompactTokenThreshold(config: ModelConfigEntry | undefined): number | null {
		const value = config?.params?.compact_token_threshold;
		const threshold = typeof value === 'number' ? value : Number(value);
		return Number.isFinite(threshold) && threshold > 0 ? Math.floor(threshold) : null;
	}

	function parseBuiltinTools(config: ModelConfigEntry | undefined): BuiltinToolsConfig {
		const value = config?.params?.builtin_tools;
		if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
		return Object.fromEntries(
			Object.entries(value).filter(([, enabled]) => typeof enabled === 'boolean')
		);
	}

	function builtinEnabled(
		tools: BuiltinToolsConfig,
		group: string,
		inherited: BuiltinToolsConfig | null = null
	): boolean {
		if (tools[group] !== undefined) return tools[group] !== false;
		if (inherited?.[group] !== undefined) return inherited[group] !== false;
		return true;
	}

	function setBuiltinEnabled(
		tools: BuiltinToolsConfig,
		group: string,
		enabled: boolean,
		inherited: BuiltinToolsConfig | null = null
	): BuiltinToolsConfig {
		const next = { ...tools };
		const inheritedEnabled = inherited?.[group] !== undefined ? inherited[group] !== false : true;
		if (enabled === inheritedEnabled) {
			delete next[group];
		} else {
			next[group] = enabled;
		}
		return next;
	}

	function serializeBuiltinTools(
		tools: BuiltinToolsConfig,
		inherited: BuiltinToolsConfig | null = null
	): BuiltinToolsConfig | null {
		const result: BuiltinToolsConfig = {};
		for (const group of BUILTIN_TOOL_GROUPS) {
			if (tools[group.id] === undefined) continue;
			const inheritedEnabled =
				inherited?.[group.id] !== undefined ? inherited[group.id] !== false : true;
			if (tools[group.id] !== inheritedEnabled) {
				result[group.id] = tools[group.id];
			}
		}
		return Object.keys(result).length ? result : null;
	}

	function configuredRowCount(rows: ParamRow[]): number {
		return rows.filter((row) => row.key.trim()).length;
	}

	function rowsToRequestParams(rows: ParamRow[]): Record<string, unknown> {
		const result: Record<string, unknown> = {};
		for (const { key, value } of rows) {
			if (!key.trim()) continue;
			try {
				result[key.trim()] = JSON.parse(value);
			} catch {
				result[key.trim()] = value;
			}
		}
		return result;
	}

	function applyModelConfig(
		data: Awaited<ReturnType<typeof getModelConfig>>,
		preserveDirty = false
	) {
		const config = data.config || {};
		const previousById = new Map(models.map((model) => [model.id, model]));

		if (!preserveDirty || !globalDirty) {
			globalRows = parseRows(config['*']);
			globalSystemPrompt = config['*']?.params?.system_prompt || '';
			globalCompactTokenThreshold = parseCompactTokenThreshold(config['*']);
			globalBuiltinTools = parseBuiltinTools(config['*']);
			globalExpanded =
				globalRows.length > 0 ||
				!!globalSystemPrompt ||
				globalCompactTokenThreshold !== null ||
				Object.keys(globalBuiltinTools).length > 0;
		}

		models = data.models.map((m) => {
			const previous = previousById.get(m.id);
			if (preserveDirty && previous?.dirty) {
				return { ...previous, ...m };
			}

			const mc = config[m.id];
			return {
				...m,
				is_active: mc?.is_active !== false,
				rows: parseRows(mc),
				systemPrompt: (mc?.params as any)?.system_prompt || '',
				compactTokenThreshold: parseCompactTokenThreshold(mc),
				builtinTools: parseBuiltinTools(mc),
				dirty: false
			};
		});

		if (selectedId && !models.some((model) => model.id === selectedId)) {
			selectedId = null;
		}
	}

	async function loadModelConfig() {
		try {
			applyModelConfig(await getModelConfig());
		} catch {
			toast.error($t('models.failedToLoad'));
		} finally {
			loading = false;
		}
	}

	onMount(async () => {
		await loadModelConfig();

		// Load default model
		try {
			const adminCfg = await getAdminConfig();
			defaultModelId =
				typeof adminCfg['chat.default_model'] === 'string' ? adminCfg['chat.default_model'] : '';
		} catch {}
	});

	async function refreshModels() {
		refreshing = true;
		try {
			applyModelConfig(await refreshModelList(), true);
			await refreshChatState();
			toast.success($t('models.refreshed'));
		} catch {
			toast.error($t('models.refreshFailed'));
		} finally {
			refreshing = false;
		}
	}

	async function toggleModel(model: ModelEntry, newVal: boolean) {
		model.is_active = newVal;
		models = [...models];
		try {
			await updateModelConfig(model.id, { is_active: newVal });
		} catch {
			model.is_active = !newVal;
			models = [...models];
			toast.error($t('models.failedToToggle'));
		}
	}

	function readThresholdInput(e: Event): number | null {
		const value = (e.target as HTMLInputElement).value;
		if (!value.trim()) return null;
		const threshold = Number(value);
		return Number.isFinite(threshold) && threshold > 0 ? Math.floor(threshold) : null;
	}

	function buildParams(
		rows: ParamRow[],
		systemPrompt: string,
		compactThreshold: number | null,
		builtinTools: BuiltinToolsConfig,
		inheritedBuiltinTools: BuiltinToolsConfig | null = null
	): Record<string, unknown> {
		const params: Record<string, unknown> = {};
		const rp = rowsToRequestParams(rows);
		const bt = serializeBuiltinTools(builtinTools, inheritedBuiltinTools);
		if (Object.keys(rp).length) params.request_params = rp;
		if (systemPrompt.trim()) params.system_prompt = systemPrompt.trim();
		if (compactThreshold && compactThreshold > 0) params.compact_token_threshold = compactThreshold;
		if (bt) params.builtin_tools = bt;
		return params;
	}

	async function saveAll() {
		saving = true;
		try {
			const promises: Promise<unknown>[] = [];
			if (globalDirty) {
				promises.push(
					updateModelConfig('*', {
						params: buildParams(
							globalRows,
							globalSystemPrompt,
							globalCompactTokenThreshold,
							globalBuiltinTools
						)
					})
				);
			}
			for (const model of models) {
				if (model.dirty) {
					promises.push(
						updateModelConfig(model.id, {
							params: buildParams(
								model.rows,
								model.systemPrompt,
								model.compactTokenThreshold,
								model.builtinTools,
								globalBuiltinTools
							)
						})
					);
				}
			}
			await Promise.all(promises);
			globalDirty = false;
			models.forEach((m) => (m.dirty = false));

			await updateConfig({ 'chat.default_model': defaultModelId });

			toast.success($t('settings.saved'));
		} catch {
			toast.error($t('models.failedToSave'));
		} finally {
			saving = false;
		}
	}
</script>

{#snippet systemPromptField(value: string, onInput: (v: string) => void, placeholder: string)}
	<div class="mb-2">
		<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 uppercase tracking-wide"
			>{$t('models.systemPrompt')}</span
		>
		<textarea
			class="w-full mt-1 bg-gray-50 dark:bg-white/4 border border-gray-200 dark:border-white/8 rounded-lg px-2.5 py-2 text-[0.6875rem] font-mono text-gray-600 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none resize-y leading-relaxed"
			rows="6"
			{placeholder}
			{value}
			oninput={(e) => onInput((e.target as HTMLTextAreaElement).value)}
			spellcheck="false"
		></textarea>
		<div class="flex items-center gap-2 mt-1">
			<button
				class="text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
				onclick={() => (showVariables = !showVariables)}
			>
				{$t('models.templateVariables')}
				{showVariables ? '▾' : '▸'}
			</button>
			{#if value.trim()}
				<button
					class="text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
					onclick={() => onInput('')}
				>
					{$t('models.resetToDefault')}
				</button>
			{/if}
		</div>
		{#if showVariables}
			<div
				class="mt-1 rounded-lg bg-gray-50 dark:bg-white/3 border border-gray-100 dark:border-white/5 px-2.5 py-2"
			>
				{#each TEMPLATE_VARIABLES as v}
					<div class="flex items-baseline gap-2 h-5">
						<code
							class="text-[0.625rem] font-mono text-gray-500 dark:text-gray-500 shrink-0 select-all"
							>{`{{${v.name}}}`}</code
						>
						<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate">{v.desc}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

{#snippet paramRows(
	rows: ParamRow[],
	onInput: () => void,
	onRemove: (i: number) => void,
	onAdd: () => void
)}
	<div class="mb-2">
		<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 uppercase tracking-wide"
			>{$t('models.requestParams')}</span
		>
		{#each rows as row, i}
			<div class="group/row flex items-center gap-1.5 h-6">
				<input
					type="text"
					placeholder={$t('models.paramKey')}
					bind:value={row.key}
					oninput={onInput}
					autocomplete="off"
					spellcheck="false"
					class="w-24 shrink-0 bg-transparent text-[0.6875rem] font-mono text-gray-500 dark:text-gray-500 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none"
				/>
				<input
					type="text"
					placeholder={$t('models.paramValue')}
					bind:value={row.value}
					oninput={onInput}
					autocomplete="off"
					spellcheck="false"
					class="flex-1 min-w-0 bg-transparent text-[0.6875rem] font-mono text-gray-500 dark:text-gray-500 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none"
				/>
				<button
					type="button"
					onclick={() => onRemove(i)}
					class="shrink-0 text-gray-300 dark:text-gray-700 opacity-0 group-hover/row:opacity-100 hover:text-gray-500 dark:hover:text-gray-400 transition-colors duration-75"
					aria-label={$t('common.remove')}
				>
					<Icon name="xmark" size={8} />
				</button>
			</div>
		{/each}
		<button
			class="flex items-center gap-1 h-6 text-[0.6875rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
			onclick={onAdd}
		>
			<Icon name="plus" size={10} />
			<span>{$t('common.add')}</span>
		</button>
	</div>
{/snippet}

{#snippet compactThresholdField(
	inputId: string,
	value: number | null,
	onInput: (value: number | null) => void
)}
	<div class="mb-2">
		<label
			class="text-[0.625rem] text-gray-400 dark:text-gray-600 uppercase tracking-wide"
			for={inputId}>{$t('admin.compactTokenThreshold')}</label
		>
		<div class="flex items-center gap-1.5 mt-1">
			<input
				id={inputId}
				type="number"
				value={value ?? ''}
				oninput={(e) => onInput(readThresholdInput(e))}
				min="1"
				step="1"
				placeholder={$t('admin.compactTokenThreshold')}
				class="w-24 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
			/>
			<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
				>{$t('admin.compactTokenThresholdUnit')}</span
			>
		</div>
	</div>
{/snippet}

{#snippet builtinToolsField(
	tools: BuiltinToolsConfig,
	inherited: BuiltinToolsConfig | null,
	onChange: (tools: BuiltinToolsConfig) => void,
	resetLabel: string
)}
	<div class="mb-2">
		<div class="flex items-center justify-between mb-1">
			<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 uppercase tracking-wide"
				>{$t('models.builtinTools')}</span
			>
			{#if Object.keys(tools).length > 0}
				<button
					class="text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
					onclick={() => onChange({})}
				>
					{resetLabel}
				</button>
			{/if}
		</div>
		<div class="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1">
			{#each BUILTIN_TOOL_GROUPS as group}
				<div class="flex items-center justify-between gap-2 min-h-7">
					<div class="min-w-0">
						<div class="text-[0.75rem] text-gray-600 dark:text-gray-400 truncate">
							{$t(group.label)}
						</div>
						<div class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate">
							{$t(group.desc)}
						</div>
					</div>
					<ToggleSwitch
						value={builtinEnabled(tools, group.id, inherited)}
						onchange={(enabled) => onChange(setBuiltinEnabled(tools, group.id, enabled, inherited))}
					/>
				</div>
			{/each}
		</div>
	</div>
{/snippet}

<div class="flex flex-col h-full">
	{#if loading}
		<div class="flex justify-center py-8"><Spinner size={16} /></div>
	{:else}
		<div class="flex-1 min-h-0 overflow-y-auto">
			<div class="flex items-center justify-between mb-4">
				<h2 class="text-sm font-medium text-gray-900 dark:text-white">
					{$t('admin.models')}
				</h2>
				<button
					class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-50 disabled:hover:text-gray-400 dark:text-gray-600 dark:hover:text-gray-300 dark:disabled:hover:text-gray-600 transition-colors duration-75"
					onclick={refreshModels}
					disabled={refreshing || saving}
					aria-label={$t('models.refresh')}
					use:tooltip={$t('models.refresh')}
				>
					<Icon name="refresh" size={13} class={refreshing ? 'animate-spin' : ''} />
				</button>
			</div>

			<div class="mb-1">
				<div class="flex items-center justify-between gap-3">
					<h3 class="min-w-0 text-xs text-gray-600 dark:text-gray-400">
						{$t('models.defaultModel')}
					</h3>
					<div class="shrink-0">
						<ModelSelector bind:selectedModel={defaultModelId} preferAbove={false} />
					</div>
				</div>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
					{$t('models.defaultModelHint')}
				</p>
			</div>

			<!-- Global defaults -->
			<button
				class="group flex items-center gap-2 w-full h-7 text-left"
				onclick={() => (globalExpanded = !globalExpanded)}
			>
				<span class="flex-1 text-[0.8125rem] text-gray-500 dark:text-gray-400"
					>{$t('models.defaults')}</span
				>
				{#if configuredRowCount(globalRows) > 0 || globalSystemPrompt.trim()}
					<span class="text-[0.625rem] text-gray-400 dark:text-gray-600">
						{#if globalSystemPrompt.trim()}prompt{/if}
						{#if configuredRowCount(globalRows) > 0}
							{configuredRowCount(globalRows)} params
						{/if}
					</span>
				{/if}
				<Icon
					name={globalExpanded ? 'chevron-down' : 'chevron-right'}
					size={10}
					class="shrink-0 text-gray-300 dark:text-gray-700"
				/>
			</button>

			{#if globalExpanded}
				{@render compactThresholdField(
					'global-compact-threshold',
					globalCompactTokenThreshold,
					(value) => {
						globalCompactTokenThreshold = value;
						globalDirty = true;
					}
				)}
				{@render systemPromptField(
					globalSystemPrompt,
					(v) => {
						globalSystemPrompt = v;
						globalDirty = true;
					},
					DEFAULT_PROMPT_PLACEHOLDER
				)}
				{@render builtinToolsField(
					globalBuiltinTools,
					null,
					(tools) => {
						globalBuiltinTools = tools;
						globalDirty = true;
					},
					$t('models.resetToDefault')
				)}
				{@render paramRows(
					globalRows,
					() => (globalDirty = true),
					(i) => {
						globalRows = globalRows.filter((_, idx) => idx !== i);
						globalDirty = true;
					},
					() => {
						globalRows = [...globalRows, { key: '', value: '' }];
						globalDirty = true;
					}
				)}
			{/if}

			<!-- Per-model list -->
			{#each models as model}
				<div class="group flex items-center gap-2 w-full h-7 text-left">
					<button
						type="button"
						class="flex-1 min-w-0 text-left text-[0.8125rem] truncate {model.is_active
							? 'text-gray-700 dark:text-gray-300'
							: 'text-gray-400 dark:text-gray-600'}"
						onclick={() => (selectedId = selectedId === model.id ? null : model.id)}
					>
						{model.name}
					</button>
					<ToggleSwitch value={model.is_active} onchange={(value) => toggleModel(model, value)} />
				</div>

				{#if selectedId === model.id}
					{@render compactThresholdField(
						`model-${model.id}-compact-threshold`,
						model.compactTokenThreshold,
						(value) => {
							model.compactTokenThreshold = value;
							model.dirty = true;
						}
					)}
					{@render systemPromptField(
						model.systemPrompt,
						(v) => {
							model.systemPrompt = v;
							model.dirty = true;
						},
						$t('models.systemPromptInherited')
					)}
					{@render builtinToolsField(
						model.builtinTools,
						globalBuiltinTools,
						(tools) => {
							model.builtinTools = tools;
							model.dirty = true;
							models = [...models];
						},
						$t('models.resetToDefault')
					)}
					{@render paramRows(
						model.rows,
						() => (model.dirty = true),
						(i) => {
							model.rows = model.rows.filter((_, idx) => idx !== i);
							model.dirty = true;
						},
						() => {
							model.rows = [...model.rows, { key: '', value: '' }];
							model.dirty = true;
						}
					)}
				{/if}
			{/each}

			{#if models.length === 0}
				<p class="text-[0.8125rem] text-gray-400 dark:text-gray-600 py-4">
					{$t('models.noModels')}
				</p>
			{/if}
		</div>

		<div class="shrink-0 pt-3 flex justify-end">
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100"
				onclick={saveAll}
			>
				{#if saving}{$t('settings.saving')}{:else}{$t('settings.save')}{/if}
			</button>
		</div>
	{/if}
</div>
