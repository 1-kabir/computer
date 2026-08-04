<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getUsage, type UsageHeatmapEntry, type UsageResponse } from '$lib/apis/chat';
	import { i18next } from '$lib/i18n';
	import { chatModels } from '$lib/stores/chat';
	import { tooltip } from '$lib/tooltip';

	type HeatmapMode = 'daily' | 'weekly' | 'cumulative';
	type HeatmapCell = UsageHeatmapEntry | null;
	type MonthLabel = { label: string; column: number; span: number };

	let usage = $state<UsageResponse | null>(null);
	let loading = $state(true);
	let heatmapMode = $state<HeatmapMode>('daily');
	let heatmapContainerWidth = $state(0);

	const HEATMAP_MIN_CELL_PX = 8;
	const HEATMAP_MAX_CELL_PX = 10;
	const HEATMAP_GAP_PX = 4;
	const MIN_HEATMAP_COLUMNS = 26;
	const DEFAULT_HEATMAP_COLUMNS = 26;
	const MIN_MONTH_LABEL_GAP = 6;
	const tr = (key: string, opts?: Record<string, unknown>) => i18next.t(key, opts) as string;

	const heatmapModes: Array<{ value: HeatmapMode; label: string }> = [
		{ value: 'daily', label: 'usage.daily' },
		{ value: 'weekly', label: 'usage.weekly' },
		{ value: 'cumulative', label: 'usage.cumulative' }
	];

	const palettes = [
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-green-100 dark:bg-green-900/40',
			'bg-green-300 dark:bg-green-700/60',
			'bg-green-500 dark:bg-green-600/80',
			'bg-green-700 dark:bg-green-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-blue-100 dark:bg-blue-900/40',
			'bg-blue-300 dark:bg-blue-700/60',
			'bg-blue-500 dark:bg-blue-600/80',
			'bg-blue-700 dark:bg-blue-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-purple-100 dark:bg-purple-900/40',
			'bg-purple-300 dark:bg-purple-700/60',
			'bg-purple-500 dark:bg-purple-600/80',
			'bg-purple-700 dark:bg-purple-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-orange-100 dark:bg-orange-900/40',
			'bg-orange-300 dark:bg-orange-700/60',
			'bg-orange-500 dark:bg-orange-600/80',
			'bg-orange-700 dark:bg-orange-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-rose-100 dark:bg-rose-900/40',
			'bg-rose-300 dark:bg-rose-700/60',
			'bg-rose-500 dark:bg-rose-600/80',
			'bg-rose-700 dark:bg-rose-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-yellow-100 dark:bg-yellow-900/40',
			'bg-yellow-300 dark:bg-yellow-700/60',
			'bg-yellow-500 dark:bg-yellow-600/80',
			'bg-yellow-700 dark:bg-yellow-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-cyan-100 dark:bg-cyan-900/40',
			'bg-cyan-300 dark:bg-cyan-700/60',
			'bg-cyan-500 dark:bg-cyan-600/80',
			'bg-cyan-700 dark:bg-cyan-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-red-100 dark:bg-red-900/40',
			'bg-red-300 dark:bg-red-700/60',
			'bg-red-500 dark:bg-red-600/80',
			'bg-red-700 dark:bg-red-500'
		],
		[
			'bg-gray-100 dark:bg-gray-800',
			'bg-neutral-200 dark:bg-neutral-800',
			'bg-neutral-400 dark:bg-neutral-600',
			'bg-neutral-600 dark:bg-neutral-400',
			'bg-neutral-800 dark:bg-neutral-200'
		]
	];

	const modelNames = $derived(
		new Map($chatModels.map((model) => [model.id, model.name || model.id]))
	);
	const dailyHeatmap = $derived(usage?.heatmap ?? []);
	const weeklyHeatmap = $derived(
		new Map((usage?.weekly_heatmap ?? []).map((entry) => [entry.date, entry]))
	);
	const cumulativeHeatmap = $derived(
		new Map((usage?.cumulative_heatmap ?? []).map((entry) => [entry.date, entry]))
	);
	const heatmapData = $derived(
		buildDisplayHeatmap(dailyHeatmap, heatmapMode, weeklyHeatmap, cumulativeHeatmap)
	);
	const totalHeatmapColumns = $derived(Math.max(Math.ceil(heatmapData.length / 7), 1));
	const maxHeatmapColumns = $derived(
		heatmapContainerWidth
			? Math.max(
					Math.min(totalHeatmapColumns, MIN_HEATMAP_COLUMNS),
					Math.floor(
						(heatmapContainerWidth + HEATMAP_GAP_PX) / (HEATMAP_MIN_CELL_PX + HEATMAP_GAP_PX)
					)
				)
			: Math.min(totalHeatmapColumns, DEFAULT_HEATMAP_COLUMNS)
	);
	const visibleHeatmapColumns = $derived(Math.min(totalHeatmapColumns, maxHeatmapColumns));
	const heatmapCells = $derived(buildHeatmapCells(heatmapData, visibleHeatmapColumns));
	const heatmapColumns = $derived(Math.max(Math.ceil(heatmapCells.length / 7), 1));
	const heatmapGridWidth = $derived(
		`${heatmapColumns * HEATMAP_MAX_CELL_PX + (heatmapColumns - 1) * HEATMAP_GAP_PX}px`
	);
	const monthLabels = $derived(buildMonthLabels(heatmapCells, heatmapColumns));
	const hasUsage = $derived(
		(usage?.totals.messages ?? 0) > 0 || (usage?.totals.lifetime_tokens ?? 0) > 0
	);

	onMount(loadUsage);

	async function loadUsage() {
		loading = true;
		try {
			usage = await getUsage();
		} catch {
			usage = null;
			toast.error(tr('usage.failedToLoad'));
		} finally {
			loading = false;
		}
	}

	function weekStart(dateString: string) {
		const date = new Date(`${dateString}T00:00:00`);
		const daysSinceMonday = (date.getDay() + 6) % 7;
		date.setDate(date.getDate() - daysSinceMonday);
		const month = `${date.getMonth() + 1}`.padStart(2, '0');
		const day = `${date.getDate()}`.padStart(2, '0');
		return `${date.getFullYear()}-${month}-${day}`;
	}

	function buildDisplayHeatmap(
		data: UsageHeatmapEntry[],
		mode: HeatmapMode,
		weekly: Map<string, UsageHeatmapEntry>,
		cumulative: Map<string, UsageHeatmapEntry>
	) {
		if (mode === 'daily') return data;
		return data.map((day) => {
			const aggregate =
				mode === 'weekly' ? weekly.get(weekStart(day.date)) : cumulative.get(day.date);
			return aggregate ? { ...aggregate, date: day.date } : day;
		});
	}

	function buildHeatmapCells(data: UsageHeatmapEntry[], visibleColumns: number) {
		if (data.length === 0) return [];
		const cells = data.slice(-Math.max(7, visibleColumns * 7));
		const trailingBlanks = Array.from({ length: (7 - (cells.length % 7)) % 7 }, () => null);
		return [...cells, ...trailingBlanks];
	}

	function buildMonthLabels(cells: HeatmapCell[], columns: number): MonthLabel[] {
		const labels: MonthLabel[] = [];
		let currentMonth = '';
		let lastLabelColumn = -MIN_MONTH_LABEL_GAP;

		cells.forEach((entry, index) => {
			if (!entry) return;
			const month = entry.date.slice(0, 7);
			if (month === currentMonth) return;

			const column = Math.floor(index / 7) + 1;
			if (column - lastLabelColumn < MIN_MONTH_LABEL_GAP) {
				currentMonth = month;
				return;
			}

			labels.push({
				label: new Date(`${entry.date}T00:00:00`).toLocaleString(undefined, { month: 'short' }),
				column,
				span: Math.min(MIN_MONTH_LABEL_GAP, columns - column + 1)
			});
			lastLabelColumn = column;
			currentMonth = month;
		});

		return labels;
	}

	function topItem(entry: UsageHeatmapEntry) {
		return Object.entries(entry.models ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
	}

	function modelPalette(modelId: string | null) {
		if (!modelId) return palettes[0];
		const index = Math.max(0, modelId.toLowerCase().charCodeAt(0) - 97) % palettes.length;
		return palettes[index];
	}

	function intensity(entry: UsageHeatmapEntry) {
		if (entry.messages >= 20) return 4;
		if (entry.messages >= 10) return 3;
		if (entry.messages >= 5) return 2;
		if (entry.messages > 0) return 1;
		return 0;
	}

	function tooltipFor(entry: UsageHeatmapEntry) {
		const label =
			heatmapMode === 'weekly'
				? `${tr('usage.weekOf')} ${weekStart(entry.date)}`
				: heatmapMode === 'cumulative'
					? `${tr('usage.through')} ${entry.date}`
					: entry.date;
		const model = topItem(entry);
		const models = Object.entries(entry.models ?? {}).sort((a, b) => b[1] - a[1]);
		const modelSummary =
			models.length > 0
				? models
						.slice(0, 3)
						.map(([id, count]) => `${modelName(id)} ${count}`)
						.join(', ')
				: tr('usage.noModelData');

		return `${label}: ${formatNumber(entry.tokens)} ${tr('usage.tokens')}, ${entry.messages.toLocaleString()} ${tr('usage.messages')}, ${entry.chats.toLocaleString()} ${tr('usage.chats')}${model ? ` (${modelName(model)})` : ''}. ${modelSummary}`;
	}

	function formatDuration(seconds: number) {
		if (!seconds) return '0m';
		const days = Math.floor(seconds / 86400);
		const hours = Math.floor((seconds % 86400) / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);

		if (days > 0) return `${days}d ${hours}h`;
		if (hours > 0) return `${hours}h ${minutes}m`;
		return `${minutes}m`;
	}

	function formatNumber(value: number) {
		return new Intl.NumberFormat(undefined, {
			notation: 'compact',
			maximumFractionDigits: 1
		}).format(value);
	}

	function modelName(id: string | null) {
		return id ? (modelNames.get(id) ?? id) : '-';
	}
</script>

<div class="flex h-full min-h-0 flex-col">
	<div class="mb-4">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">{tr('usage.title')}</h2>
	</div>

	{#if loading}
		<div class="flex flex-1 items-center justify-center">
			<Spinner size={20} />
		</div>
	{:else if !usage}
		<div class="flex flex-1 items-center justify-center text-xs text-gray-500">
			{tr('usage.failedToLoad')}
		</div>
	{:else}
		<div class="scrollbar-hover min-h-0 flex-1 overflow-y-auto pr-1.5">
			<section class="w-full">
				<h3 class="mb-2 text-xs text-gray-600 dark:text-gray-400">{tr('usage.overview')}</h3>
				<div class="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-5">
					<div>
						<div class="text-sm font-medium text-gray-900 dark:text-white">
							{formatNumber(usage.totals.lifetime_tokens)}
						</div>
						<div class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{tr('usage.lifetimeTokens')}
						</div>
					</div>
					<div>
						<div class="text-sm font-medium text-gray-900 dark:text-white">
							{formatNumber(usage.totals.peak_daily_tokens)}
						</div>
						<div class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{tr('usage.peakTokens')}
						</div>
					</div>
					<div>
						<div class="text-sm font-medium text-gray-900 dark:text-white">
							{formatDuration(usage.totals.longest_chat_seconds)}
						</div>
						<div class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{tr('usage.longestActiveChat')}
						</div>
					</div>
					<div>
						<div class="text-sm font-medium text-gray-900 dark:text-white">
							{usage.totals.current_streak.toLocaleString()}
						</div>
						<div class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{tr('usage.currentStreak')}
						</div>
					</div>
					<div>
						<div class="text-sm font-medium text-gray-900 dark:text-white">
							{usage.totals.longest_streak.toLocaleString()}
						</div>
						<div class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{tr('usage.longestStreak')}
						</div>
					</div>
				</div>
			</section>

			<section class="mt-4 w-full">
				<div class="mb-2 flex min-w-0 items-center justify-between gap-3">
					<h3 class="min-w-0 shrink truncate text-xs text-gray-400 dark:text-gray-600">
						{tr('usage.tokenActivity')}
					</h3>
					<div
						class="flex min-w-0 max-w-[70%] shrink items-center gap-3 overflow-hidden whitespace-nowrap"
					>
						{#each heatmapModes as mode}
							<button
								type="button"
								class="min-w-0 shrink truncate whitespace-nowrap text-xs transition-colors {heatmapMode ===
								mode.value
									? 'text-gray-900 dark:text-white'
									: 'text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}"
								onclick={() => (heatmapMode = mode.value)}
							>
								{tr(mode.label)}
							</button>
						{/each}
					</div>
				</div>

				<div class="pb-1">
					<div class="w-full min-w-0 overflow-hidden" bind:clientWidth={heatmapContainerWidth}>
						<div
							class="mx-auto grid grid-flow-col"
							style="width: min(100%, {heatmapGridWidth}); gap: {HEATMAP_GAP_PX}px; aspect-ratio: {heatmapColumns} / 7; grid-template-columns: repeat({heatmapColumns}, minmax(0, 1fr)); grid-template-rows: repeat(7, minmax(0, 1fr));"
						>
							{#each heatmapCells as entry}
								{#if entry}
									<div
										class="h-full w-full rounded-[2px] {modelPalette(topItem(entry))[
											intensity(entry)
										]}"
										aria-label={tooltipFor(entry)}
										use:tooltip={tooltipFor(entry)}
									></div>
								{:else}
									<div class="h-full w-full"></div>
								{/if}
							{/each}
						</div>

						<div
							class="mx-auto mt-2 grid text-[0.6875rem] leading-none text-gray-400 dark:text-gray-600"
							style="width: min(100%, {heatmapGridWidth}); column-gap: {HEATMAP_GAP_PX}px; grid-template-columns: repeat({heatmapColumns}, minmax(0, 1fr));"
						>
							{#each monthLabels as month}
								<div class="truncate" style="grid-column: {month.column} / span {month.span};">
									{month.label}
								</div>
							{/each}
						</div>
					</div>

					{#if usage.top_models.length > 0}
						<div
							class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[0.6875rem] text-gray-400 dark:text-gray-600"
						>
							{#each usage.top_models.slice(0, 6) as model}
								<div class="flex min-w-0 items-center gap-1.5">
									<span class="size-2 shrink-0 rounded-[2px] {modelPalette(model.model_id)[3]}"
									></span>
									<span class="max-w-28 truncate">{modelName(model.model_id)}</span>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			</section>

			{#if !hasUsage}
				<section class="mt-4 w-full">
					<h3 class="mb-2 text-xs text-gray-600 dark:text-gray-400">{tr('usage.activity')}</h3>
					<div class="text-xs text-gray-500 dark:text-gray-400">{tr('usage.noData')}</div>
				</section>
			{:else}
				<section class="mt-4 w-full">
					<h3 class="mb-2 text-xs text-gray-600 dark:text-gray-400">
						{tr('usage.activityInsights')}
					</h3>
					<div class="flex flex-col gap-2.5">
						<div class="flex items-center justify-between gap-2.5">
							<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
								>{tr('usage.models')}</span
							>
							<span class="shrink-0 text-xs text-gray-900 dark:text-white"
								>{usage.totals.models_used.toLocaleString()}</span
							>
						</div>
						<div class="flex items-center justify-between gap-2.5">
							<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
								>{tr('usage.averageTokensPerChat')}</span
							>
							<span class="shrink-0 text-xs text-gray-900 dark:text-white"
								>{formatNumber(usage.insights.average_tokens_per_chat)}</span
							>
						</div>
						<div class="flex items-center justify-between gap-2.5">
							<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
								>{tr('usage.averageMessagesPerActiveDay')}</span
							>
							<span class="shrink-0 text-xs text-gray-900 dark:text-white"
								>{usage.insights.average_messages_per_active_day.toLocaleString()}</span
							>
						</div>
						<div class="flex items-center justify-between gap-2.5">
							<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
								>{tr('usage.userMessages')}</span
							>
							<span class="shrink-0 text-xs text-gray-900 dark:text-white"
								>{usage.totals.user_messages.toLocaleString()} · {usage.insights
									.user_message_share}%</span
							>
						</div>
						<div class="flex items-center justify-between gap-2.5">
							<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
								>{tr('usage.assistantMessages')}</span
							>
							<span class="shrink-0 text-xs text-gray-900 dark:text-white"
								>{usage.totals.assistant_messages.toLocaleString()} · {usage.insights
									.assistant_message_share}%</span
							>
						</div>
						<div class="flex items-center justify-between gap-2.5">
							<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
								>{tr('usage.totalChats')}</span
							>
							<span class="shrink-0 text-xs text-gray-900 dark:text-white"
								>{usage.totals.total_chats.toLocaleString()}</span
							>
						</div>
					</div>
				</section>

				<section class="mt-4 w-full">
					<h3 class="mb-2 text-xs text-gray-600 dark:text-gray-400">{tr('usage.topModels')}</h3>
					{#if usage.top_models.length === 0}
						<div class="text-xs text-gray-500 dark:text-gray-400">{tr('usage.noModelUsage')}</div>
					{:else}
						<div class="flex flex-col gap-2.5">
							{#each usage.top_models as model}
								<div class="flex items-center justify-between gap-2.5">
									<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
										>{modelName(model.model_id)}</span
									>
									<span class="shrink-0 text-xs text-gray-500 dark:text-gray-400">
										{model.messages.toLocaleString()}
										{tr('usage.messages')} · {formatNumber(model.total_tokens)}
									</span>
								</div>
							{/each}
						</div>
					{/if}
				</section>

				{#if usage.top_tools.length > 0}
					<section class="mt-4 w-full">
						<h3 class="mb-2 text-xs text-gray-600 dark:text-gray-400">
							{tr('usage.mostUsedTools')}
						</h3>
						<div class="flex flex-col gap-2.5">
							{#each usage.top_tools as tool}
								<div class="flex items-center justify-between gap-2.5">
									<span class="min-w-0 truncate text-xs text-gray-600 dark:text-gray-400"
										>{tool.name}</span
									>
									<span class="shrink-0 text-xs text-gray-500 dark:text-gray-400">
										{tool.count.toLocaleString()}
										{tr('usage.runs')}
									</span>
								</div>
							{/each}
						</div>
					</section>
				{/if}
			{/if}

			<div class="mt-4 text-right text-[0.6875rem] text-gray-400 dark:text-gray-600">
				{tr('usage.estimateNote')}
			</div>
		</div>
	{/if}
</div>
